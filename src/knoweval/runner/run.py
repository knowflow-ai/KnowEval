"""一次评测运行：入库 → 每遍逐题提问并判分 → 写 RAGAS 实验文件与汇总。

建在 RAGAS 的 @experiment 上，补上它缺的几件事：
- 异常不外抛：RAGAS 会把抛异常的行直接丢掉（experiment.py:183-187）；
- 并发上限：RAGAS 一次把所有行同时发出去；
- 检查点续跑：RAGAS 只在结尾保存一次。回答一拿到就先落盘，判分失败续跑时不必重新问系统。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel
from ragas import Dataset, experiment
from ragas.backends.inmemory import InMemoryBackend
from ragas.backends.local_jsonl import LocalJSONLBackend
from ragas.experiment import Experiment

from knoweval.adapters.base import Adapter, AdapterError, IngestReport, KbHandle
from knoweval.adapters.template import param_refs
from knoweval.dataset import Dataset as KeDataset
from knoweval.judge.judge import Judgement, TextJudge, score_three_tier
from knoweval.runner.checkpoint import Checkpoint
from knoweval.runner.lock import RunDirLock
from knoweval.runner.summary import write_summary
from knoweval.schema import Answer, Question
from knoweval.stats import Summary, compute_summary, noise_floor

# 只影响怎么跑、不影响跑出什么的配置：改了它们既不重新入库，也不算换了一次运行
RUNTIME_KEYS = {"concurrency", "ask_timeout_s", "ingest_timeout_s"}
# 这些异常是被测系统的失败，记为这道题的 ERROR；其余异常是 KnowEval 自己的缺陷，整轮失败
SYSTEM_ERRORS = (AdapterError, httpx.HTTPError, asyncio.TimeoutError, OSError)


class RunIncompleteError(RuntimeError):
    """有题没有得到判定。这是 KnowEval 自己的缺陷，整轮作废，不能带着缺口出分数。"""


class RunConfigMismatchError(RuntimeError):
    """运行目录里已有另一份数据集或另一套系统配置跑出的结果，不能混在一起续跑。"""


class RunRecord(BaseModel):
    qid: str
    repeat: int
    answer: Answer
    judgement: Judgement


@dataclass
class RunResult:
    repeats: list[list[RunRecord]]
    summaries: list[Summary]
    noise: list[str]
    ingest: IngestReport


@dataclass
class _RepeatContext:
    questions: dict[str, Question]
    adapter: Adapter
    kb: KbHandle
    sem: asyncio.Semaphore
    ask_timeout_s: float
    text_judge: TextJudge | None
    answers: Checkpoint
    checkpoint: Checkpoint
    saved_answers: dict[str, dict]
    repeat: int
    fatal: list[str] = field(default_factory=list)


async def safe_ask(adapter: Adapter, kb: KbHandle, question: str, timeout_s: float) -> Answer:
    """被测系统的失败（接口报错、网络、超时）变成一个 error 回答；KnowEval 自己的异常照常抛出。"""
    try:
        return await asyncio.wait_for(adapter.ask(kb, question), timeout_s)
    except asyncio.TimeoutError:
        return Answer(error=f"TimeoutError: 超过 {timeout_s}s 未返回")
    except SYSTEM_ERRORS as e:
        return Answer(error=f"{type(e).__name__}: {e}")


async def _get_answer(q: Question, ctx: _RepeatContext) -> Answer:
    if q.id in ctx.saved_answers:
        return Answer.model_validate(ctx.saved_answers[q.id]["answer"])
    async with ctx.sem:
        answer = await safe_ask(ctx.adapter, ctx.kb, q.question, ctx.ask_timeout_s)
    await ctx.answers.append({"qid": q.id, "answer": answer.model_dump(mode="json")})
    return answer


@experiment()
async def _ask_and_judge(row: dict, ctx: _RepeatContext) -> dict | None:
    q = ctx.questions[row["qid"]]
    try:
        answer = await _get_answer(q, ctx)
        judgement = await score_three_tier(q, answer, ctx.text_judge)
        record = RunRecord(qid=q.id, repeat=ctx.repeat, answer=answer, judgement=judgement).model_dump(mode="json")
        await ctx.checkpoint.append(record)
        return record
    except Exception as e:  # noqa: BLE001 - 记下来，跑完后让整轮失败，不交给 RAGAS 静默丢弃
        ctx.fatal.append(f"{q.id}: {type(e).__name__}: {e}")
        return None


def _hash(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _identity(dataset: KeDataset, system_config: dict) -> dict[str, str]:
    run_config = {k: v for k, v in system_config.items() if k not in RUNTIME_KEYS}
    return {"dataset_hash": dataset.content_hash(), "config_hash": _hash(run_config)}


def _ingest_config_hash(system_config: dict) -> str:
    """入库用到的那部分配置。自研系统的问答参数（chat）一般只在提问时用，不影响建库，不算在内；
    KnowFlow/RAGFlow 的助手在入库时就建好，助手参数要算在内。"""
    skip = set(RUNTIME_KEYS)
    if system_config.get("adapter") == "http":
        # 问答参数用在全局请求头或入库模板里（如租户）时会影响建库，这时要算在内
        outside_ask = {k: v for k, v in (system_config.get("http") or {}).items() if k != "ask"}
        if not any(ref.startswith("chat.") for ref in param_refs(outside_ask)):
            skip.add("chat")
    return _hash({k: v for k, v in system_config.items() if k not in skip})


def _ingest_key(dataset: KeDataset, identity: dict[str, str]) -> dict[str, str]:
    """入库只取决于文档与配置：同一批文档换一组题（或跑子集）复用同一个知识库。"""
    return {"docs_hash": dataset.docs_hash(), "config_hash": identity["config_hash"]}


def _check_resume(run_dir: Path, identity: dict[str, str]) -> dict | None:
    """续跑前核对身份；返回上次记下的系统配置快照（没有已存结果时返回 None），开跑后还要再比一次。"""
    meta_path = run_dir / "run.json"
    has_results = any(run_dir.glob("checkpoint.r*.jsonl")) or any(run_dir.glob("answers.r*.jsonl"))
    if not meta_path.exists() or not has_results:
        return None
    saved = json.loads(meta_path.read_text(encoding="utf-8"))
    if saved.get("identity", {}) != identity:
        raise RunConfigMismatchError(
            f"运行目录 {run_dir} 里已有用别的数据集或系统配置跑出的结果，换一个 --out 目录，或者先删掉旧结果")
    return saved.get("system_snapshot") if isinstance(saved.get("system_snapshot"), dict) else None


def _comparable(config: Any, top: bool = True) -> Any:
    """算指纹时不看名字（改名不算改配置），也不看助手自己的 id。"""
    if isinstance(config, dict):
        return {k: _comparable(v, False) for k, v in config.items() if k != "name" and not (top and k == "id")}
    if isinstance(config, list):
        return [_comparable(v, False) for v in config]
    return config


async def _snapshot(adapter: Adapter, kb: KbHandle, run_dir: Path, saved: dict | None) -> dict | None:
    """被测系统此刻的配置（如 KnowFlow 助手的设置）。抓不到不影响新的评测，记下原因。

    问的是用户已有的助手时（适配器不复用入库），续跑前后配置必须一致：前后半截问的不是同一个配置，
    合在一起的分数说明不了任何一个；抓不到快照也就没法确认，同样不续跑。新建的助手由评测台按配置建，
    续跑时缓存的助手被删了会重建（id 变了），不比。
    """
    take = getattr(adapter, "snapshot", None)
    if take is None:
        return None
    strict = bool(saved and saved.get("hash")) and not getattr(adapter, "reuse_ingest", True)
    try:
        config = await take(kb)
    except (AdapterError, httpx.HTTPError) as e:
        if strict:
            raise RunConfigMismatchError(
                f"抓不到被测助手的配置快照（{e}），没法确认配置和前一段结果一致，不能接着跑；稍后再试或换一个目录重新评测"
            ) from e
        return {"error": f"抓取配置快照失败：{e}"}
    if config is None:
        return None
    snap = {"hash": _hash(_comparable(config)), "config": config}
    if strict and saved["hash"] != snap["hash"]:
        raise RunConfigMismatchError(
            f"运行目录 {run_dir} 的前一段结果是在另一套配置下跑的，被测助手的配置变了，不能接着跑；换一个目录重新评测")
    return snap


async def _ingest(dataset: KeDataset, adapter: Adapter, run_dir: Path, identity: dict[str, str],
                  cache_dir: Path | None) -> IngestReport:
    """同一批文档、同一套入库配置只入库一次；缓存的知识库被删了就重新入库。"""
    identity = _ingest_key(dataset, identity)
    name = f"ingest-{identity['docs_hash'][:16]}-{identity['config_hash'][:16]}.json"
    candidates = [p for p in (run_dir / "ingest.json", cache_dir / name if cache_dir else None) if p]
    # 已有助手这类入库结果随时会变（助手挂的库被改了），每次都重新核对，不读缓存
    for path in candidates if getattr(adapter, "reuse_ingest", True) else []:
        if not path.exists():
            continue
        saved = json.loads(path.read_text(encoding="utf-8"))
        if {k: saved.get(k) for k in identity} != identity:
            continue
        report = IngestReport.model_validate(saved["report"])
        alive = getattr(adapter, "kb_alive", None)
        if alive is None or await alive(report.kb):
            _write_json(run_dir / "ingest.json", saved)
            return report
    # 名字只是给人看的，身份靠上面的哈希。加随机后缀：同一数据集、同一配置再入库一次时（缓存失效、库被删），
    # 不会因为被测系统不许重名（如 KnowFlow 的助手名）而失败
    kb_name = (f"knoweval-{dataset.manifest.name}-{identity['docs_hash'][:8]}-{identity['config_hash'][:6]}"
               f"-{secrets.token_hex(3)}")
    report = await adapter.ingest(dataset.doc_paths(), kb_name=kb_name)
    payload = {**identity, "report": report.model_dump(mode="json")}
    for path in candidates:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, payload)
    return report


async def _run_repeat(dataset: KeDataset, ctx: _RepeatContext, run_dir: Path) -> list[RunRecord]:
    done = ctx.checkpoint.load()
    pending = [{"qid": q.id} for q in dataset.questions if q.id not in done]
    if pending:
        rows = Dataset(name=f"r{ctx.repeat}-pending", backend=InMemoryBackend(), data=pending)
        await _ask_and_judge.arun(rows, name=f"r{ctx.repeat}-part", backend=InMemoryBackend(), ctx=ctx)
    if ctx.fatal:
        raise RunIncompleteError("以下题目没有得到判定：" + "；".join(ctx.fatal))
    done = ctx.checkpoint.load()
    missing = [q.id for q in dataset.questions if q.id not in done]
    if missing:
        raise RunIncompleteError(f"第 {ctx.repeat} 遍缺少题目：{missing}")
    ordered = [done[q.id] for q in dataset.questions]
    write_experiment(run_dir, ctx.repeat, ordered)
    return [RunRecord.model_validate(r) for r in ordered]


def write_experiment(run_dir: Path, repeat: int, rows: list[dict]) -> None:
    exp = Experiment(name=f"r{repeat}", backend=LocalJSONLBackend(root_dir=str(run_dir)))
    for row in rows:
        exp.append(row)
    exp.save()


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def summarize(dataset: KeDataset, repeats: list[list[RunRecord]]) -> tuple[list[Summary], list[str]]:
    by_id = {q.id: q for q in dataset.questions}
    summaries = [compute_summary([(by_id[r.qid], r.judgement) for r in records], dataset.doc_formats())
                 for records in repeats]
    noise = noise_floor([{r.qid: r.judgement.verdict for r in records} for records in repeats])
    return summaries, noise


async def run_eval(dataset: KeDataset, adapter: Adapter, run_dir: str | Path, *, system_config: dict,
                   repeats: int = 2, concurrency: int = 4, ask_timeout_s: float = 180,
                   text_judge: TextJudge | None = None, cache_dir: str | Path | None = None,
                   subset: str | None = None) -> RunResult:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    with RunDirLock(run_dir):
        return await _run_locked(dataset, adapter, run_dir, system_config=system_config, repeats=repeats,
                                 concurrency=concurrency, ask_timeout_s=ask_timeout_s, text_judge=text_judge,
                                 cache_dir=Path(cache_dir) if cache_dir else None, subset=subset)


async def _run_locked(dataset: KeDataset, adapter: Adapter, run_dir: Path, *, system_config: dict, repeats: int,
                      concurrency: int, ask_timeout_s: float, text_judge: TextJudge | None,
                      cache_dir: Path | None, subset: str | None = None) -> RunResult:
    identity = _identity(dataset, system_config)
    saved_snapshot = _check_resume(run_dir, identity)
    meta = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "identity": identity,
        "dataset": {"ref": dataset.ref, "content_hash": dataset.content_hash(), "canary": dataset.manifest.canary},
        "system": system_config,
        "judge": getattr(text_judge, "version", None),
        "repeats": repeats,
        "subset": subset,
        "versions": {"knoweval": version("knoweval"), "ragas": version("ragas")},
    }
    if saved_snapshot:
        # 先带上上次的快照：这次开跑中途失败（入库出错、续跑被拒），下次续跑仍有指纹可比
        meta["system_snapshot"] = saved_snapshot
    _write_json(run_dir / "run.json", meta)

    report = await _ingest(dataset, adapter, run_dir, {**identity, "config_hash": _ingest_config_hash(system_config)},
                           cache_dir)
    try:
        return await _answer_all(dataset, adapter, run_dir, report, meta, saved_snapshot, repeats=repeats,
                                 concurrency=concurrency, ask_timeout_s=ask_timeout_s, text_judge=text_judge)
    finally:
        await release_quietly(adapter, report.kb)


async def release_quietly(adapter: Adapter, kb: KbHandle) -> None:
    """评测台为提问临时建的东西（如参数对照的助手）交给适配器删掉；删不掉不影响结果。"""
    release = getattr(adapter, "release", None)
    if release is not None:
        with contextlib.suppress(Exception):
            await release(kb)


async def _answer_all(dataset: KeDataset, adapter: Adapter, run_dir: Path, report: IngestReport, meta: dict,
                      saved_snapshot: dict | None, *, repeats: int, concurrency: int, ask_timeout_s: float,
                      text_judge: TextJudge | None) -> RunResult:
    """入库之后：记配置快照，逐遍提问、判分，写汇总。"""
    snapshot = await _snapshot(adapter, report.kb, run_dir, saved_snapshot)
    # 抓快照失败时保留上次成功的那份，不拿报错盖掉它
    if snapshot and not (snapshot.get("error") and saved_snapshot):
        meta["system_snapshot"] = snapshot
    if report.manual:
        meta["manual_ingest"] = True
    if snapshot or report.manual:
        _write_json(run_dir / "run.json", meta)
    questions = {q.id: q for q in dataset.questions}
    sem = asyncio.Semaphore(concurrency)
    all_repeats: list[list[RunRecord]] = []
    for r in range(1, repeats + 1):
        answers = Checkpoint(run_dir / f"answers.r{r}.jsonl")
        ctx = _RepeatContext(questions=questions, adapter=adapter, kb=report.kb, sem=sem, ask_timeout_s=ask_timeout_s,
                             text_judge=text_judge, answers=answers, saved_answers=answers.load(),
                             checkpoint=Checkpoint(run_dir / f"checkpoint.r{r}.jsonl"), repeat=r)
        all_repeats.append(await _run_repeat(dataset, ctx, run_dir))

    summaries, noise = summarize(dataset, all_repeats)
    write_summary(run_dir, dataset, all_repeats, ingest=report.model_dump(mode="json"))
    _write_json(run_dir / "run.json", {**meta, "status": "finished",
                                       "finished_at": datetime.now(timezone.utc).isoformat()})
    return RunResult(repeats=all_repeats, summaries=summaries, noise=noise, ingest=report)

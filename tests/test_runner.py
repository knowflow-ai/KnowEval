"""运行器：失败是结果、每遍分开存、断点续跑、并发上限、入库复用、判分 bug 让整轮失败。"""

import asyncio
import json
from pathlib import Path

import pytest

from knoweval.adapters.base import AdapterError, DocIngest, IngestReport, KbHandle
from knoweval.dataset import load_dataset
from knoweval.runner.run import RunIncompleteError, run_eval
from knoweval.schema import Answer, Verdict

DEMO = load_dataset(Path(__file__).resolve().parents[1] / "datasets" / "demo")
QID = {q.question: q.id for q in DEMO.questions}


class FakeAdapter:
    name = "fake"
    supports_citations = True

    def __init__(self, answers=None, fail=(), slow=(), delay=0.0):
        self.answers = answers or {}
        self.fail, self.slow, self.delay = set(fail), set(slow), delay
        self.ingests = 0
        self.asked: list[str] = []
        self.in_flight = self.max_in_flight = 0
        self.alive = True

    async def kb_alive(self, kb):
        return self.alive

    async def ingest(self, files, kb_name):
        self.ingests += 1
        return IngestReport(kb=KbHandle(system="fake", ids={"kb": "1"}),
                            docs=[DocIngest(name=f.name, ok=True, seconds=0.1) for f in files])

    async def ask(self, kb, question):
        qid = QID[question]
        self.asked.append(qid)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            if qid in self.fail:
                raise AdapterError("服务端 500")
            if qid in self.slow:
                await asyncio.sleep(10)
            answer = self.answers.get(qid, "知识库中未找到您要的答案！")
            if callable(answer):
                answer = answer()
            return Answer(text=answer)
        finally:
            self.in_flight -= 1

    async def aclose(self):
        pass


def _read(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def _run(tmp_path, adapter, **kw):
    kw.setdefault("repeats", 1)
    return await run_eval(DEMO, adapter, tmp_path, system_config={"adapter": "fake"}, **kw)


async def test_failures_and_timeouts_are_errors_in_the_denominator(tmp_path):
    adapter = FakeAdapter(answers={"demo-002": "首席架构师"}, fail={"demo-003"}, slow={"demo-004"})
    result = await _run(tmp_path, adapter, ask_timeout_s=0.2)
    by_id = {r.qid: r for r in result.repeats[0]}
    assert len(by_id) == 20
    assert by_id["demo-003"].judgement.verdict is Verdict.ERROR
    assert "500" in by_id["demo-003"].answer.error
    assert by_id["demo-004"].judgement.verdict is Verdict.ERROR
    assert by_id["demo-002"].judgement.verdict is Verdict.CORRECT
    s = result.summaries[0]
    assert s.error_count == 2
    assert s.accuracy.n == 17  # 20 题减 3 道无答案题，出错的两题都在分母里


async def test_repeats_are_kept_separately_and_noise_is_counted(tmp_path):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        return "首席架构师" if calls["n"] == 1 else "不清楚"

    result = await _run(tmp_path, FakeAdapter(answers={"demo-002": flaky}), repeats=2)
    assert len(result.repeats) == 2
    assert result.noise == ["demo-002"]
    assert (tmp_path / "experiments" / "r1.jsonl").exists()
    assert (tmp_path / "experiments" / "r2.jsonl").exists()
    assert len(_read(tmp_path / "experiments" / "r1.jsonl")) == 20


def _truncate(path: Path, keep: int) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:keep]) + "\n", encoding="utf-8")


async def test_resume_only_asks_unfinished_questions(tmp_path):
    await _run(tmp_path, FakeAdapter())
    _truncate(tmp_path / "checkpoint.r1.jsonl", 15)
    _truncate(tmp_path / "answers.r1.jsonl", 15)
    second = FakeAdapter()
    result = await _run(tmp_path, second)
    assert len(second.asked) == 5
    assert len(result.repeats[0]) == 20


async def test_resume_reuses_saved_answers_without_asking_again(tmp_path):
    await _run(tmp_path, FakeAdapter())
    _truncate(tmp_path / "checkpoint.r1.jsonl", 15)
    second = FakeAdapter()
    result = await _run(tmp_path, second)
    assert second.asked == []
    assert len(result.repeats[0]) == 20


async def test_concurrency_is_capped(tmp_path):
    adapter = FakeAdapter(delay=0.05)
    await _run(tmp_path, adapter, concurrency=3)
    assert adapter.max_in_flight <= 3


async def test_ingest_is_reused_for_the_same_dataset_and_config(tmp_path):
    adapter = FakeAdapter()
    await _run(tmp_path, adapter)
    await _run(tmp_path, adapter)
    assert adapter.ingests == 1
    # 换了入库配置就是另一次运行：换目录，并且重新入库
    await run_eval(DEMO, adapter, tmp_path / "other", system_config={"adapter": "fake", "chunk": "other"}, repeats=1)
    assert adapter.ingests == 2


async def test_third_party_ask_params_do_not_force_a_reingest(tmp_path):
    """自研系统的问答参数只影响提问，不影响建库：只换问答参数的几份配置共用一次入库。"""
    adapter, cache = FakeAdapter(), tmp_path / "cache"
    base = {"adapter": "http", "dataset": {"chunk": 512}}
    await run_eval(DEMO, adapter, tmp_path / "a", system_config={**base, "chat": {"top_k": 5}}, repeats=1, cache_dir=cache)
    await run_eval(DEMO, adapter, tmp_path / "b", system_config={**base, "chat": {"top_k": 10}}, repeats=1, cache_dir=cache)
    assert adapter.ingests == 1
    await run_eval(DEMO, adapter, tmp_path / "c", system_config={**base, "dataset": {"chunk": 1024}}, repeats=1,
                   cache_dir=cache)
    assert adapter.ingests == 2
    # KnowFlow/RAGFlow 的助手在入库时建，助手参数变了要重新建
    await run_eval(DEMO, adapter, tmp_path / "d", system_config={"adapter": "knowflow", "chat": {"a": 1}}, repeats=1,
                   cache_dir=cache)
    await run_eval(DEMO, adapter, tmp_path / "e", system_config={"adapter": "knowflow", "chat": {"a": 2}}, repeats=1,
                   cache_dir=cache)
    assert adapter.ingests == 4


async def test_third_party_reingests_when_ask_params_reach_the_ingest_requests(tmp_path):
    """问答参数用在全局请求头或入库模板里（如租户）时，它会影响建库，换了值就要重新入库。"""
    adapter, cache = FakeAdapter(), tmp_path / "cache"
    http = {"headers": {"X-Tenant": "{{chat.tenant}}"}, "ask": {"path": "/chat"}}
    for i, tenant in enumerate(("t1", "t2")):
        await run_eval(DEMO, adapter, tmp_path / f"r{i}", repeats=1, cache_dir=cache,
                       system_config={"adapter": "http", "http": http, "chat": {"tenant": tenant}})
    assert adapter.ingests == 2


async def test_a_judging_bug_fails_the_whole_run(tmp_path, monkeypatch):
    from knoweval.runner import run as run_mod

    async def broken(*a, **k):
        raise RuntimeError("判分程序出错")

    monkeypatch.setattr(run_mod, "score_three_tier", broken)
    with pytest.raises(RunIncompleteError, match="判分程序出错"):
        await _run(tmp_path, FakeAdapter())


async def test_run_json_records_versions_and_no_secret(tmp_path):
    await run_eval(DEMO, FakeAdapter(), tmp_path, repeats=1,
                   system_config={"adapter": "fake", "api_key_env": "FAKE_KEY"})
    meta = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert meta["dataset"]["ref"] == "demo@0.0"
    assert meta["status"] == "finished"
    assert "ragas" in meta["versions"] and "knoweval" in meta["versions"]
    assert meta["system"]["api_key_env"] == "FAKE_KEY"


async def test_summary_json_is_written(tmp_path):
    await _run(tmp_path, FakeAdapter(answers={"demo-002": "首席架构师"}))
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["repeats"][0]["accuracy"]["k"] == 1


class SnapshotAdapter(FakeAdapter):
    """已有助手：每次开跑都重新核对、重新抓配置快照。"""
    reuse_ingest = False

    def __init__(self, snap):
        super().__init__()
        self.snap = snap

    async def snapshot(self, kb):
        return self.snap


async def test_run_records_the_system_snapshot_and_refuses_to_resume_after_it_changed(tmp_path, monkeypatch):
    from knoweval.runner.run import RunConfigMismatchError
    adapter = SnapshotAdapter({"prompt": {"use_colpali": True}})
    cfg = {"adapter": "knowflow", "existing_chat": {"id": "c1", "name": "线上"}}
    await run_eval(DEMO, adapter, tmp_path, system_config=cfg, repeats=1, cache_dir=tmp_path / "cache")
    await run_eval(DEMO, adapter, tmp_path / "again", system_config=cfg, repeats=1, cache_dir=tmp_path / "cache")
    assert adapter.ingests == 2   # 不复用入库缓存：助手挂的库可能变了
    meta = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert meta["system_snapshot"]["config"] == {"prompt": {"use_colpali": True}}
    assert len(meta["system_snapshot"]["hash"]) == 64
    # 有结果的运行目录里，助手配置变了就不能接着跑：前后半截问的不是同一个助手
    (tmp_path / "run.json").write_text(json.dumps({**meta, "status": "running"}), encoding="utf-8")
    adapter.snap = {"prompt": {"use_colpali": False}}
    with pytest.raises(RunConfigMismatchError, match="助手的配置变了"):
        await run_eval(DEMO, adapter, tmp_path, system_config=cfg, repeats=1)


async def test_snapshot_hash_survives_a_failed_resume_and_ignores_renames(tmp_path):
    """续跑被拒或中途失败都不能把上次的指纹抹掉；改名字不算改配置；抓不到快照时不敢续跑。"""
    from knoweval.runner.run import RunConfigMismatchError
    cfg = {"adapter": "knowflow", "existing_chat": {"id": "c1", "name": "线上"}}
    adapter = SnapshotAdapter({"id": "c1", "name": "线上", "prompt": {"top_n": 8}, "datasets": [{"id": "kb1", "name": "手册"}]})
    await run_eval(DEMO, adapter, tmp_path, system_config=cfg, repeats=1)
    meta = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    (tmp_path / "run.json").write_text(json.dumps({**meta, "status": "running"}), encoding="utf-8")
    adapter.snap = {**adapter.snap, "name": "线上（改名）", "datasets": [{"id": "kb1", "name": "手册v2"}]}
    await run_eval(DEMO, adapter, tmp_path, system_config=cfg, repeats=1)   # 只改了名字：照常续跑
    adapter.snap = {**adapter.snap, "prompt": {"top_n": 3}}
    for _ in range(2):   # 第二次也要拦住：第一次被拒不能把指纹抹掉
        with pytest.raises(RunConfigMismatchError):
            await run_eval(DEMO, adapter, tmp_path, system_config=cfg, repeats=1)

    async def broken(kb):
        raise AdapterError("连不上")

    adapter.snapshot = broken
    with pytest.raises(RunConfigMismatchError, match="快照"):
        await run_eval(DEMO, adapter, tmp_path, system_config=cfg, repeats=1)


async def test_new_assistant_runs_resume_even_if_the_rebuilt_assistant_differs(tmp_path):
    """新建库、新建助手的运行，续跑时缓存的助手被删了会重建，id 变了不该被当成配置变了。"""
    adapter = SnapshotAdapter({"id": "c1", "prompt": {"top_n": 8}})
    adapter.reuse_ingest = True
    cfg = {"adapter": "knowflow", "dataset": {"chunk_method": "smart"}}
    await run_eval(DEMO, adapter, tmp_path, system_config=cfg, repeats=1)
    meta = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    (tmp_path / "run.json").write_text(json.dumps({**meta, "status": "running"}), encoding="utf-8")
    adapter.snap = {"id": "c2", "prompt": {"top_n": 8}, "datasets": [{"id": "kb-new"}]}
    await run_eval(DEMO, adapter, tmp_path, system_config=cfg, repeats=1)


async def test_temporary_assistant_is_released_after_the_run(tmp_path):
    """评测台为提问临时建的助手（参数对照等），跑完就交给适配器删掉。"""
    released = []

    class Temp(FakeAdapter):
        async def release(self, kb):
            released.append(kb.ids)

    await run_eval(DEMO, Temp(), tmp_path, system_config={"adapter": "fake"}, repeats=1)
    assert released == [{"kb": "1"}]

"""试问检查：评测开始前，对不需要建库的对比项抽几道有答案的题问一下。

评测台不知道被测系统里到底有没有测试集的文档；没导入就开跑，会得到一个虚低的分数，用户还以为是系统差。
大多数回答是「找不到」或接口报错，就提示用户先确认。只用问答接口，对任何系统都适用。
要新建知识库的对比项（按配置建库、按入库模板建库）入库完成前没得问，跳过。
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets

from knoweval.config import SystemConfig
from knoweval.dataset import Dataset
from knoweval.judge.abstain import detect_abstain
from knoweval.judge.normalize import strip_noise
from knoweval.runner.run import release_quietly, safe_ask

SAMPLE = 3

# 接口报错的常见原因：按报错里的关键词给出说明（先匹配到的优先）
_CAUSES = [
    (("connection refused", "errno 61", "errno 111", "connecterror", "name or service not known", "nodename nor servname"),
     "被测系统连不上它依赖的服务（如 KnowFlow 的 agent worker、向量库、模型服务），或者评测台连不上被测系统：看看这些服务有没有启动"),
    (("not authorized", "model(", "no default"),
     "知识库或助手用的模型在被测系统里没有配置（比如嵌入模型被删了）：在被测系统的模型设置里加回来"),
    (("401", "403", "api key", "apikey", "unauthorized", "authentication"),
     "API Key 不对或没有权限：检查 .env 里的 Key"),
    (("timeout", "timed out", "超时"),
     "被测系统回答超时：可能负载太高，或者依赖的模型服务很慢"),
    (("404", "doesn't exist", "not found", "找不到助手"),
     "要问的助手或知识库在被测系统里找不到了"),
]


def explain_error(error: str) -> str:
    """接口报错 → 一句原因说明，帮用户往对的方向查（不是笼统地猜「文档没导入」）。"""
    low = error.lower()
    for keys, cause in _CAUSES:
        if any(k in low for k in keys):
            return cause
    return "被测系统的接口报错：看报错原文，或者在被测系统里直接问一句试试"


def _needs_new_kb(cfg: SystemConfig) -> bool:
    if cfg.existing_kbs or cfg.existing_chat:
        return False
    if cfg.adapter == "http":
        return bool(cfg.http.get("ingest"))
    return True


def _pick(ds: Dataset) -> list:
    """有答案、最好带引文的题，取前几道（按题号，结果可复现）。"""
    answerable = [q for q in ds.questions if not q.is_no_answer]
    with_evidence = [q for q in answerable if q.evidence]
    return (with_evidence or answerable)[:SAMPLE]


async def _check(ds: Dataset, name: str, cfg: SystemConfig, factory) -> dict:
    base = {"name": name}
    if _needs_new_kb(cfg):
        return {**base, "status": "skipped", "message": "评测时会新建知识库，入库完成后才能提问，这里不试问"}
    questions = _pick(ds)
    if not questions:
        return {**base, "status": "skipped", "message": "这套题都是无答案题，没法试问"}
    adapter = factory(cfg)
    try:
        try:
            # 名字带随机后缀：几组参数对照同时试问各建一个临时助手，KnowFlow 不许重名
            report = await adapter.ingest(ds.doc_paths(), kb_name=f"knoweval-preflight-{ds.manifest.name}-{secrets.token_hex(3)}")
        except Exception as e:  # noqa: BLE001 - 连不上、库里缺文档：都是要提醒用户的
            return {**base, "status": "suspect", "asked": 0, "abstained": 0, "errors": 0, "samples": [],
                    "message": f"连接知识库时出错：{e}"}
        try:
            answers = await asyncio.gather(*(safe_ask(adapter, report.kb, q.question, cfg.ask_timeout_s) for q in questions))
        finally:
            await release_quietly(adapter, report.kb)
    finally:
        with contextlib.suppress(Exception):
            await adapter.aclose()
    abstained = errors = 0
    samples = []
    for q, a in zip(questions, answers, strict=True):
        if a.error:
            errors += 1
        else:
            # 只是开跑前的提醒，不是判分：按「找不到」「无法回答」这类说法粗看
            abstained += detect_abstain(strip_noise(a.text or "")).abstained or not (a.text or "").strip()
        samples.append({"question": q.question, "answer": (a.error or a.text or "")[:200], "gold": q.answer})
    bad = abstained + errors
    all_errors = errors == len(questions)
    out = {**base, "asked": len(questions), "abstained": abstained, "errors": errors, "samples": samples,
           "all_errors": all_errors}
    if bad * 3 < len(questions) * 2:
        return {**out, "status": "ok", "message": f"试问 {len(questions)} 道题，回答正常"}
    if errors * 2 >= bad:
        # 以接口报错为主：同样的报错只说一次，按报错内容说原因
        distinct = list(dict.fromkeys(a.error for a in answers if a.error))
        detail = "；".join(distinct[:2])
        message = (f"试问 {len(questions)} 道有答案的题，{errors} 道接口报错。{explain_error(distinct[0])}。"
                   f"报错原文：{detail}")
    else:
        message = (f"试问 {len(questions)} 道有答案的题，{abstained} 道回答找不到相关内容。"
                   "多半是题目依据的文档不在被测系统里（没导入、没解析完，或者分块没进向量库）")
    # 页面上逐题只列回答；报错原文已经在上面说过一次，不再每题重复
    samples = [{**s, "answer": "（接口报错，见上）" if a.error else s["answer"]} for s, a in zip(samples, answers, strict=True)]
    return {**out, "samples": samples, "status": "suspect", "message": message}


def preflight(ds: Dataset, participants, factory) -> list[dict]:
    """participants：workspace.plan_participants 的结果；factory：SystemConfig → 适配器。"""
    async def run():
        return await asyncio.gather(*(_check(ds, p.name, SystemConfig.model_validate(p.config), factory) for p in participants))
    return list(asyncio.run(run()))


PROBE_QUESTION = "你好，请简单介绍一下知识库里有哪些内容。"


async def _probe(name: str, cfg: SystemConfig, factory) -> dict:
    base = {"name": name}
    if not cfg.existing_chat:
        return {**base, "ok": True, "skipped": True, "message": "不是已有助手，评测时才建库，这里不试问"}
    adapter = factory(cfg)
    try:
        try:
            report = await adapter.ingest([], kb_name=f"knoweval-probe-{secrets.token_hex(3)}")
        except Exception as e:  # noqa: BLE001 - 助手不在了、没挂知识库：都要提醒
            return {**base, "ok": False, "message": f"{explain_error(str(e))}。报错原文：{e}"}
        try:
            answer = await safe_ask(adapter, report.kb, PROBE_QUESTION, cfg.ask_timeout_s)
        finally:
            await release_quietly(adapter, report.kb)
    finally:
        with contextlib.suppress(Exception):
            await adapter.aclose()
    if answer.error:
        return {**base, "ok": False, "message": f"{explain_error(answer.error)}。报错原文：{answer.error}"}
    return {**base, "ok": True, "message": "助手回答正常"}


def probe_assistants(participants, factory) -> list[dict]:
    """出题前（以及出完题开评测前）先问已有助手一句：接口报错（如嵌入模型没了、对话模型下线）当场发现，
    不白等几分钟出题、跑完一轮全是错。只问已有助手；按配置建库的对比项要先建库，不在这里试问。"""
    async def run():
        return await asyncio.gather(*(_probe(p.name, SystemConfig.model_validate(p.config), factory) for p in participants))
    return list(asyncio.run(run()))

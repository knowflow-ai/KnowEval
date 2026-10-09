"""自动校验：证据逐字核对、闭卷过滤、开卷复答、无答案题确实查不到。只打回，不改题。

开卷复答：给模型题干和证据所在页（前后各一页）、不给标准答案，答出来与标准答案不一致就打回。
闭卷过滤挡「不看文档也能答对」的题，开卷复答挡「看了文档也答不出标准答案」的题——题干有歧义、
标准答案取错了口径或列、证据不足以支持答案。实验里它也会误杀（模型自己读错图表），所以可一键恢复。
"""

from __future__ import annotations

import asyncio
import re

import httpx
import openai
from pydantic import ValidationError

from knoweval.build.generators import ask_json
from knoweval.build.project import Project
from knoweval.judge.judge import judge_answer
from knoweval.judge.llm_judge import ChatClient, OpenAICompatibleJudge
from knoweval.schema import Answer, Question, Verdict

_CLOSED = "请不查阅任何资料，仅凭你已有的知识直接回答下面的问题，答不出就说不知道。\n问题：{q}"
_OPEN = ("下面是一份文档的若干页原文。请只根据这些原文回答问题，给出明确的结论；原文里找不到答案就说找不到。\n"
         "回答尽量简短，直接写结论。\n\n{ctx}\n\n问题：{q}")
_ABSENCE = ("题型：absence\n判断下面的资料里是否能找到这个问题的答案。只看资料本身，不要用常识推测。\n"
            '输出 JSON：{{"found": true 或 false, "reason": "一句话"}}\n问题：{q}\n资料：\n{text}')


def _as_question(c) -> Question:
    data = c.model_dump(include=set(Question.model_fields))
    return Question.model_validate({**data, "domain": "build"})


def _keywords(question: str) -> list[str]:
    clean = re.sub(r"[^一-鿿A-Za-z0-9]", "", question)
    return [clean[i:i + 2] for i in range(len(clean) - 1)]


def _top_pages(project: Project, question: str, k: int = 3) -> list[str]:
    """按二字词重叠挑最相关的几页，给无答案检查用。"""
    kws = _keywords(question)
    scored = sorted(((sum(kw in p.text for kw in kws), p.text) for pages in project.pages.values() for p in pages),
                    key=lambda x: x[0], reverse=True)
    return [t for score, t in scored[:k] if score > 0]


def _evidence_pages(project: Project, c, limit: int = 12000) -> str:
    """证据所在页及前后各一页；没有页码的文档取整篇。"""
    seen: list[tuple[str, int | None]] = []
    for ev in c.evidence:
        pages = [ev.page - 1, ev.page, ev.page + 1] if ev.page else [None]
        seen += [(ev.doc, pg) for pg in pages if (pg is None or pg >= 1) and (ev.doc, pg) not in seen]
    parts = [f"--- {doc}{f' 第 {pg} 页' if pg else ''} ---\n{text}" for doc, pg in seen if (text := project.page_text(doc, pg))]
    return "\n\n".join(parts)[:limit]


async def _open_book(project: Project, q: Question, c, reader: tuple[ChatClient, str]) -> tuple[bool, dict, str]:
    client, model = reader
    text = await client.complete([{"role": "user", "content": _OPEN.format(ctx=_evidence_pages(project, c), q=q.question)}], model)
    j = await judge_answer(q, Answer(text=text), OpenAICompatibleJudge(model, client))
    answer = " ".join(text.split())[:120]
    ok = j.verdict is Verdict.CORRECT
    note = "" if ok else (f"开卷作答得到「{answer}」，与标准答案「{q.answer}{q.unit or ''}」不一致："
                          "题干可能有歧义、标准答案可能取错，或证据不足以支持答案")
    return ok, {"answer": answer, "verdict": j.verdict.value}, note


_MODEL_ERROR_NOTE = "校验时模型调用失败："
# 模型调用失败（超时、连不上、接口报错）：大模型读长原文常超时。这类失败只让这一道题留在待校验，不让整批中断
MODEL_ERRORS = (asyncio.TimeoutError, httpx.HTTPError, openai.APIError)


async def _model_checks(project: Project, q: Question, c, checks: dict, notes: list[str],
                        closed_book: list[tuple[ChatClient, str]], absence: tuple[ChatClient, str] | None,
                        open_book: tuple[ChatClient, str] | None) -> bool:
    """要调模型的三项校验：闭卷过滤、开卷复答、无答案查证。返回是否打回；checks、notes 原地补充。"""
    if not q.is_no_answer and closed_book:
        correct = 0
        for client, model in closed_book:
            text = await client.complete([{"role": "user", "content": _CLOSED.format(q=q.question)}], model)
            j = await judge_answer(q, Answer(text=text), OpenAICompatibleJudge(model, client))
            correct += j.verdict is Verdict.CORRECT
        answerable = correct == len(closed_book)
        checks["closed_book"] = {"models": [m for _, m in closed_book], "correct": correct, "answerable": answerable}
        if answerable:
            notes.append("闭卷可答：所有闭卷模型都答对了，这题考不到检索")
            return True
    if not q.is_no_answer and c.evidence and open_book:
        ok, checks["open_book"], note = await _open_book(project, q, c, open_book)
        if not ok:
            notes.append(note)
            return True
    if q.is_no_answer and absence:
        client, model = absence
        for text in _top_pages(project, q.question):
            data = await ask_json(client, model, _ABSENCE.format(q=q.question, text=text[:2500]))
            if data.get("found") is True:
                checks["absence"] = {"found": True, "reason": data.get("reason", "")}
                notes.append("无答案题在资料里找到了答案")
                return True
        checks.setdefault("absence", {"found": False})
    return False


async def verify(project: Project, closed_book: list[tuple[ChatClient, str]],
                 absence: tuple[ChatClient, str] | None = None,
                 open_book: tuple[ChatClient, str] | None = None, only: set[str] | None = None) -> dict[str, int]:
    """逐题校验。模型调用失败的题留在原状态（待校验）并记下原因，计入 errors，重新校验时再补。
    only：只校验这些题（给已有测试集补题时，只校验新出的，用户改过的题不动）。"""
    stats = {"verified": 0, "rejected": 0, "errors": 0}
    for c in list(project.candidates):
        if c.status not in ("generated", "edited") or (only is not None and c.id not in only):
            continue
        before = list(c.notes)
        checks = dict(c.checks)
        c = project.check_quotes(c) if c.evidence else c
        added = c.notes[len(before):]   # 核对引文时新加的说明
        # 上次校验时模型调用失败留下的说明，这次重新校验就不要了
        notes = [n for n in before if not n.startswith(_MODEL_ERROR_NOTE)] + added
        rejected = c.status == "rejected_auto"
        try:
            q = _as_question(c)
        except ValidationError as e:
            # 标准答案格式不合规（如日期缺年份）：这道题打回，不让整批校验中断
            msg = "; ".join(err["msg"] for err in e.errors())[:200]
            project.replace(c.model_copy(update={"status": "rejected_auto", "notes": notes + [f"题目格式不合规：{msg}"]}))
            project.log("verify", c.id, by="auto", status="rejected_auto")
            stats["rejected"] += 1
            continue
        if not rejected:
            try:
                rejected = await _model_checks(project, q, c, checks, notes, closed_book, absence, open_book)
            except MODEL_ERRORS as e:
                reason = f"{_MODEL_ERROR_NOTE}{type(e).__name__}: {e}"[:300]
                project.replace(c.model_copy(update={"notes": notes + [reason]}))
                project.log("verify_error", c.id, by="auto", note=reason)
                stats["errors"] += 1
                continue
        status = "rejected_auto" if rejected else ("edited" if c.status == "edited" else "verified")
        project.replace(c.model_copy(update={"status": status, "checks": {**checks, **c.checks}, "notes": notes}))
        project.log("verify", c.id, by="auto", status=status)
        stats["rejected" if rejected else "verified"] += 1
    return stats

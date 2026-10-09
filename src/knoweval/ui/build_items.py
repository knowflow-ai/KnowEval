"""人工出题与改题：表单内容 → 候选题。存之前把能确定性查的都查完，查不过就不存。"""

from __future__ import annotations

import re

from pydantic import ValidationError

from knoweval.build.project import Candidate, Project
from knoweval.schema import QTYPES, Evidence, Question
from knoweval.ui.workspace import WorkspaceError

HUMAN = "human"
EDITED_NOTE = "人工修改"
# 页面上能人工出的题型：需要图的 V、I 要带图片证据，表单暂不支持
MANUAL_QTYPES = QTYPES - {"V", "I"}
_STAGE = {"F": "retrieve", "T": "retrieve", "L": "chunk", "N": "honesty"}


def _norm_question(text: str) -> str:
    return re.sub(r"[\s，。？?！!、：:；;,.]", "", text)


def _evidence(project: Project, rows: list[dict]) -> list[Evidence]:
    docs = {d.name for d in project.docs}
    out = []
    for row in rows:
        doc, quote = str(row.get("doc", "")), str(row.get("quote", "")).strip()
        if doc not in docs:
            raise WorkspaceError(f"项目里没有这份文档：{doc}")
        if not quote:
            raise WorkspaceError("证据引文不能为空")
        page = project.locate(doc, quote)
        if page is False:
            raise WorkspaceError(f"证据在原文里找不到，请直接从原文里选中复制：{quote[:30]}")
        out.append(Evidence(doc=doc, quote=quote, page=page))
    return out


_NUMBER_WITH_UNIT = re.compile(r"\s*([-+]?\d+(?:\.\d+)?)\s*(\D.*)?\s*")
_DATE = re.compile(r"\d{4}(-\d{2}(-\d{2})?)?")


def _infer_type(answer: str) -> str:
    """没指定答案类型时按答案推断：判分交给裁判模型，类型只决定数值题带不带允许误差。
    数字开头（可以带单位，如「358.6 亿元」）按数值；写成 2024、2024-03、2024-03-31 的按日期；其余按文本。"""
    if _DATE.fullmatch(answer):
        return "date"
    if _NUMBER_WITH_UNIT.fullmatch(answer.replace(",", "")):
        return "number"
    return "text"

def build_item(project: Project, data: dict, existing: Candidate | None) -> Candidate:
    """existing 为空是新题（人工出题，直接算已确认）；否则是修改原题（保留来历，标为已修改）。"""
    if existing is not None and existing.status == "rejected":
        raise WorkspaceError("删掉的题先恢复再改")
    qtype = str(data.get("qtype") or "")
    if isinstance(data.get("qtype"), list):
        qtype = (data["qtype"] or [""])[0]
    if qtype not in MANUAL_QTYPES:
        raise WorkspaceError(f"题型不对：{qtype or '未选'}")
    question = str(data.get("question", "")).strip()
    if not question:
        raise WorkspaceError("题目不能为空")
    same = next((c for c in project.candidates
                 if c.status != "rejected" and (existing is None or c.id != existing.id)
                 and _norm_question(c.question) == _norm_question(question)), None)
    if same:
        raise WorkspaceError(f"已经有一道一样的题：{same.id}")

    no_answer = qtype == "N"
    rows = [] if no_answer else list(data.get("evidence") or [])
    if not no_answer and not rows:
        raise WorkspaceError("有答案的题至少要有一条证据：在原文里选中答案所在的那段话")
    evidence = _evidence(project, rows)
    tolerance = data.get("tolerance")
    answer, unit = str(data.get("answer", "")).strip(), str(data.get("unit") or "").strip()
    answer_type = "text" if no_answer else data.get("answer_type") or _infer_type(answer)
    if answer_type == "number" and not unit:
        # 数值答案可以直接写「358.6 亿元」：拆成数字和单位（标准答案的数字不带千分位）
        m = _NUMBER_WITH_UNIT.fullmatch(answer.replace(",", ""))
        if m:
            answer, unit = m.group(1), m.group(2).strip()
    fields = {
        "qtype": [qtype],
        "question": question,
        "answer": "" if no_answer else answer,
        "answer_type": answer_type,
        "answer_aliases": [] if no_answer else [a.strip() for a in data.get("answer_aliases") or [] if str(a).strip()],
        "unit": (unit or None) if answer_type == "number" else None,
        "tolerance": (float(tolerance) if tolerance not in (None, "") else 0.0) if answer_type == "number" else None,
        "evidence": evidence,
        "hops": max(1, len({e.doc for e in evidence})),
        "stage": _STAGE.get(qtype, "reason"),
    }
    if not no_answer and not fields["answer"]:
        raise WorkspaceError("标准答案不能为空；文档里没有答案的题请选「无答案」题型")
    try:
        Question.model_validate({**fields, "id": "check", "domain": "check"})
    except ValidationError as e:
        raise WorkspaceError("；".join(err["msg"].removeprefix("Value error, ") for err in e.errors())) from e

    if existing is None:
        return Candidate(id=project.next_id("h"), generator=HUMAN, status="approved",
                         checks={"quote_ok": True}, **fields)
    if existing.generator == HUMAN:   # 人出的题再改，还是人出的题
        return existing.model_copy(update=fields)
    # 自动校验留下的提示（引文找不到、闭卷答对等）针对的是改之前的题，改过就作废；原记录在日志里
    return existing.model_copy(update={**fields, "status": "edited", "notes": [EDITED_NOTE],
                                       "checks": {**existing.checks, "quote_ok": True}})

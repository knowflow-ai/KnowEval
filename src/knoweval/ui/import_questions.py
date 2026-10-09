"""导入已有题目：CSV 或 JSONL，一行一题。题目、答案必填（单位直接写在答案里；答案空着＝文档里没有答案，
应当说不知道），文档、原文可选。判分交给裁判模型，单位、写法差异它都认得，下面这些列一般不用填，填了照样认：

列名中英文都认：题目/问题/question、答案/标准答案/answer、答案类型/answer_type、单位/unit、
也算对/别名/answer_aliases（用 | 分隔）、题型/qtype、文档/doc、原文/引文/证据/quote。
"""

from __future__ import annotations

import csv
import io
import json
import re

from pydantic import ValidationError

from knoweval.schema import QTYPES, Question
from knoweval.ui.workspace import WorkspaceError

COLUMNS = {
    "question": ["question", "题目", "问题", "问句"],
    "answer": ["answer", "答案", "标准答案", "参考答案"],
    "answer_type": ["answer_type", "答案类型", "类型"],
    "unit": ["unit", "单位"],
    "answer_aliases": ["answer_aliases", "aliases", "也算对", "别名", "其它说法", "其他说法"],
    "qtype": ["qtype", "题型"],
    "doc": ["doc", "文档", "文档名", "来源文档"],
    "quote": ["quote", "原文", "引文", "证据"],
}
_ALIAS = {name.lower(): key for key, names in COLUMNS.items() for name in names}
_NUMBER = re.compile(r"-?\d+(\.\d+)?")
# 判分交给裁判模型，单位、不同写法它都认得：模板只留必要的四列，单位直接写在答案里。
# 「单位」「也算对」等列仍然认（老文件、或者确实有裁判不可能知道的说法，比如内部代号）
TEMPLATE_CSV = "题目,答案,文档,原文\n年假有几天,10 天,员工手册.pdf,员工每年享有带薪年假 10 天\n公司在火星有分部吗,,,\n"


def _records(filename: str, data: bytes) -> list[tuple[int, dict | None, str | None]]:
    """读出 (行号, 记录, 读不出的原因)。CSV 行号从表头下一行算起是 2。"""
    name = filename.lower()
    text = data.decode("utf-8-sig", errors="replace")
    if name.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(text))
        return [(i + 2, row, None) for i, row in enumerate(reader)]
    if name.endswith((".jsonl", ".ndjson")):
        out = []
        for i, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                out.append((i, row, None) if isinstance(row, dict) else (i, None, "这一行不是 JSON 对象"))
            except json.JSONDecodeError:
                out.append((i, None, "这一行不是合法的 JSON"))
        return out
    raise WorkspaceError("题目文件只支持 CSV 或 JSONL（Excel 请另存为 CSV，选 UTF-8 编码）")


def _normalize(raw: dict) -> dict:
    return {_ALIAS[k.strip().lower()]: (v if v is not None else "") for k, v in raw.items() if k and k.strip().lower() in _ALIAS}


def _row(rec: dict) -> dict:
    question = str(rec.get("question", "")).strip()
    if not question:
        raise ValueError("题目是空的")
    answer = str(rec.get("answer", "")).strip()
    qtype = str(rec.get("qtype", "")).strip().upper()[:1] or ("N" if not answer else "F")
    if qtype not in QTYPES:
        raise ValueError(f"题型 {qtype} 不认识")
    if qtype == "N":
        answer = ""
    answer_type = str(rec.get("answer_type", "")).strip().lower() or ("number" if _NUMBER.fullmatch(answer) else "text")
    unit = str(rec.get("unit", "")).strip()
    if unit and answer and answer_type != "number" and not answer.endswith(unit):
        answer = f"{answer}{unit}"     # 文字答案没有单独的单位字段：拼进答案，不丢
    aliases = rec.get("answer_aliases") or []
    if isinstance(aliases, str):
        aliases = [a.strip() for a in re.split(r"[|｜；;]", aliases) if a.strip()]
    row = {
        "question": question, "answer": answer, "answer_type": "text" if qtype == "N" else answer_type,
        "unit": (unit or None) if answer_type == "number" else None,
        "tolerance": 0.0 if answer_type == "number" and qtype != "N" else None,
        "answer_aliases": [str(a) for a in aliases], "qtype": qtype,
        "doc": str(rec.get("doc", "")).strip(), "quote": str(rec.get("quote", "")).strip(),
    }
    try:
        Question.model_validate({k: row[k] for k in ("question", "answer", "answer_type", "unit", "tolerance", "answer_aliases")}
                                | {"id": "check", "domain": "check", "qtype": [qtype]})
    except ValidationError as e:
        raise ValueError("；".join(err["msg"].removeprefix("Value error, ") for err in e.errors())) from e
    return row


def parse_table(filename: str, data: bytes) -> tuple[list[dict], list[dict]]:
    """返回 (能用的题, 用不了的行及原因)。同一道题出现两次只留第一次。"""
    records = _records(filename, data)
    if records and records[0][1] is not None and "question" not in _normalize(records[0][1]):
        raise WorkspaceError(f"没找到题目列：表头要有「题目」或 question，现在的表头是 {list(records[0][1])}")
    rows, problems, seen = [], [], set()
    for line, raw, error in records:
        if error:
            problems.append({"row": line, "reason": error})
            continue
        try:
            row = _row(_normalize(raw))
        except ValueError as e:
            problems.append({"row": line, "reason": str(e)})
            continue
        key = re.sub(r"\s", "", row["question"])
        if key in seen:
            problems.append({"row": line, "reason": "和前面的题重复"})
            continue
        seen.add(key)
        rows.append({"row": line, **row})
    return rows, problems

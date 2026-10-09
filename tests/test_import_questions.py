"""导入已有题目：团队手上已有的问答对（客服记录、业务验收）转成测试集。CSV 或 JSONL，题目、答案必填。"""

import json
import shutil
from pathlib import Path

import pytest

from knoweval.dataset import load_dataset
from knoweval.ui.builds import BuildService
from knoweval.ui.import_questions import parse_table
from knoweval.ui.workspace import Workspace, WorkspaceError

DOCS = Path(__file__).resolve().parents[1] / "datasets" / "demo-md" / "docs"


def test_csv_with_chinese_headers_and_inferred_types():
    csv = "问题,答案,单位,也算对,文档,原文\n年假有几天,10,天,十|10天,制度.md,员工每年享有带薪年假 10 天\n火星上有分公司吗,,,,,\n总部在哪,北京,,,,\n".encode("utf-8-sig")
    rows, problems = parse_table("q.csv", csv)
    assert problems == []
    assert rows[0] == {"row": 2, "question": "年假有几天", "answer": "10", "answer_type": "number", "unit": "天", "tolerance": 0.0,
                       "answer_aliases": ["十", "10天"], "qtype": "F", "doc": "制度.md", "quote": "员工每年享有带薪年假 10 天"}
    assert rows[1]["qtype"] == "N" and rows[1]["answer"] == ""          # 答案空着＝文档里没有答案，应当说不知道
    assert rows[2]["answer_type"] == "text"


def test_jsonl_and_bad_rows_are_reported_not_fatal():
    jsonl = "\n".join([
        json.dumps({"question": "上线日期", "answer": "2024-03", "answer_type": "date"}, ensure_ascii=False),
        json.dumps({"question": "", "answer": "x"}),
        "不是 JSON",
        json.dumps({"question": "日期", "answer": "三月", "answer_type": "date"}, ensure_ascii=False),
    ]).encode()
    rows, problems = parse_table("q.jsonl", jsonl)
    assert [r["question"] for r in rows] == ["上线日期", "日期"]     # 日期写法不再强制，交给裁判模型
    assert [p["row"] for p in problems] == [2, 3]
    assert "题目是空的" in problems[0]["reason"]


def test_unknown_format_and_empty_table_say_what_is_expected():
    with pytest.raises(WorkspaceError, match="CSV 或 JSONL"):
        parse_table("q.xlsx", b"x")
    with pytest.raises(WorkspaceError, match="题目"):
        parse_table("q.csv", "名字,年龄\n张三,3\n".encode())


@pytest.fixture
def ws(tmp_path):
    return Workspace(tmp_path)


def test_import_builds_a_test_set_with_documents_and_checks_quotes(ws):
    doc = sorted(DOCS.glob("*.md"))[0]
    quote = next(line.strip() for line in doc.read_text(encoding="utf-8").splitlines() if len(line.strip()) > 20 and not line.startswith("#"))
    csv = (f"question,answer,doc,quote\n这句说了什么,{quote[:6]},{doc.name},{quote}\n"
           f"另一句,答案,{doc.name},原文里根本没有的一句话\n没有答案的题,,,\n").encode()
    svc = BuildService(ws, llm=lambda: (_ for _ in ()).throw(AssertionError("导入不调模型")))
    meta = svc.create_from_import("导入集", "0.1", ("q.csv", csv), [(doc.name, doc.read_bytes()), (DOCS.glob("*.md").__next__().name + ".bak", b"x")])
    svc.join(60)
    d = svc.detail(meta.id)
    assert d["status"] == "ready", d["error"]
    assert d["source"]["kind"] == "import" and d["import_report"]["imported"] == 3
    items = {x["question"]: x for x in d["items"]}
    assert items["这句说了什么"]["evidence"][0]["doc"] == doc.name and items["这句说了什么"]["status"] == "approved"
    assert items["另一句"]["evidence"] == [] and "引文" in "".join(items["另一句"]["notes"])   # 引文对不上：保留题目、去掉证据并说明
    from tests.test_ui_builds import _snapshot
    out = _snapshot(svc, meta.id)
    ds = load_dataset(ws.root / out["path"])
    assert len(ds.questions) == 3
    assert [f.name for f in ds.manifest.files] == [doc.name]                          # 导入的文档全部带进数据集（不认得的格式跳过）
    assert "导入题目 3 题" in json.loads((ws.root / out["path"] / "manifest.json").read_text(encoding="utf-8"))["build"]["review"]


def test_import_without_documents_still_works(ws):
    svc = BuildService(ws, llm=lambda: (_ for _ in ()).throw(AssertionError("导入不调模型")))
    meta = svc.create_from_import("纯题目", "0.1", ("q.csv", "题目,答案\n年假几天,10天\n".encode()), [])
    svc.join(60)
    assert svc.detail(meta.id)["status"] == "ready"
    with pytest.raises(WorkspaceError, match="没有一行能用"):
        svc.create_from_import("坏", "0.1", ("q.csv", "题目,答案\n,x\n".encode()), [])


def test_template_has_only_the_columns_people_need():
    """判分交给裁判模型：模板只要题目、答案（单位直接写在答案里）、文档、原文。"""
    from knoweval.ui.import_questions import TEMPLATE_CSV
    header = TEMPLATE_CSV.splitlines()[0].split(",")
    assert header == ["题目", "答案", "文档", "原文"]
    rows, problems = parse_table("模板.csv", TEMPLATE_CSV.encode("utf-8"))
    assert not problems and rows[0]["answer"] == "10 天" and rows[1]["qtype"] == "N"


def test_unit_column_is_still_understood_and_never_dropped():
    """老文件里的「单位」列照样认：数字答案单独存单位（判分时拼回去），文字答案把单位拼进答案，不丢。"""
    csv = "题目,答案,单位\n年假几天,10,天\n年假几天（中文）,十,天\n"
    rows, _ = parse_table("老.csv", csv.encode("utf-8"))
    assert (rows[0]["answer"], rows[0]["unit"]) == ("10", "天")
    assert (rows[1]["answer"], rows[1]["unit"]) == ("十天", None)

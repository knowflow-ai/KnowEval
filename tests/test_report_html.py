"""单文件 HTML 报告：Python 端组装数据并嵌进打包好的前端模板。"""

import json

import pytest

from knoweval.report_html import PLACEHOLDER, build_compare_data, build_report_data, render_html
from knoweval.runner.run import run_eval
from tests.test_runner import DEMO, FakeAdapter

TEMPLATE = (f'<html><head><script>const s = "{PLACEHOLDER}";</script></head>'
            f'<body><script id="knoweval-data" type="application/json">{PLACEHOLDER}</script></body></html>')


@pytest.fixture
async def run_dir(tmp_path):
    await run_eval(DEMO, FakeAdapter(answers={"demo-002": "首席架构师</script><script>alert(1)</script>"}),
                   tmp_path / "a", repeats=2, system_config={"adapter": "fake"})
    return tmp_path / "a"


async def test_report_data_has_questions_records_and_summary(run_dir):
    data = build_report_data(run_dir, DEMO)
    assert data["kind"] == "report"
    assert data["name"] == "a"
    assert len(data["questions"]) == 20
    assert set(data["records"]) == {"1", "2"}
    rec = data["records"]["1"]["demo-002"]
    assert rec["judgement"]["verdict"] == "CORRECT" and "answer" in rec
    assert data["summary"]["repeats"][0]["accuracy"]["n"] == 17
    assert data["meta"]["dataset"]["ref"] == "demo@0.0"
    assert data["reviews"] == []


async def test_render_escapes_script_breakers(run_dir):
    html = render_html(build_report_data(run_dir, DEMO), template=TEMPLATE)
    assert html.count("</script>") == 2
    assert f'const s = "{PLACEHOLDER}";' in html  # 代码里的同名字符串不能被替换
    payload = html.split('type="application/json">')[1].split("</script>")[0]
    data = json.loads(payload)
    assert "</script>" in data["records"]["1"]["demo-002"]["answer"]["text"]


async def test_compare_data(run_dir, tmp_path):
    await run_eval(DEMO, FakeAdapter(), tmp_path / "b", repeats=1, system_config={"adapter": "fake"})
    data = build_compare_data([run_dir, tmp_path / "b"], DEMO)
    assert data["kind"] == "compare"
    assert [s["name"] for s in data["compare"]["systems"]] == ["a", "b"]
    assert len(data["questions"]) == 20


def test_packaged_template_has_the_placeholder():
    from importlib.resources import files
    tpl = files("knoweval").joinpath("web/report.html").read_text(encoding="utf-8")
    assert PLACEHOLDER in tpl and 'id="knoweval-data"' in tpl


async def test_cli_report_html(run_dir, tmp_path):
    from typer.testing import CliRunner
    from knoweval.cli import app
    from tests.test_cli import DEMO_DIR
    out = tmp_path / "r.html"
    result = CliRunner().invoke(app, ["report", str(run_dir), "--html", str(out), "--dataset", str(DEMO_DIR)])
    assert result.exit_code == 0, result.output
    assert out.exists() and out.stat().st_size > 1000


def test_terminal_report_tolerates_summaries_from_older_versions(tmp_path):
    from knoweval.report import print_report
    rate = {"k": 1, "n": 2, "value": 0.5, "ci": [0.1, 0.9]}
    old = {k: rate for k in ("accuracy", "confident_wrong", "abstain_correct", "over_abstain",
                             "citation_verifiable", "citation_precision")}
    old.update(error_count=0, needs_review=0, total=2, by_qtype={})
    (tmp_path / "run.json").write_text(json.dumps({"system": {"adapter": "x"}, "dataset": {"ref": "d"}}), encoding="utf-8")
    (tmp_path / "summary.json").write_text(json.dumps({"repeats": [old], "noise": []}), encoding="utf-8")
    print_report(tmp_path)


async def test_compare_data_carries_each_systems_answers_for_question_details(run_dir, tmp_path):
    """对比页逐题对照要能展开看各项的回答、引用、判定依据：带上每项第 1 遍的逐题记录（和颜色块同一遍）。"""
    await run_eval(DEMO, FakeAdapter(), tmp_path / "b", repeats=1, system_config={"adapter": "fake"})
    data = build_compare_data([run_dir, tmp_path / "b"], DEMO)
    assert len(data["records"]) == 2
    a, b = data["records"]
    assert "首席架构师" in a["demo-002"]["answer"]["text"] and a["demo-002"]["judgement"]["verdict"] == "CORRECT"
    assert b["demo-002"]["judgement"]["verdict"] != "CORRECT"
    assert set(a) == set(data["compare"]["matrix"])

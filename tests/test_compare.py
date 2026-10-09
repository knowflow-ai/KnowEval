"""多系统对比、两次运行对比、CI 模式。"""

import json

import pytest
from typer.testing import CliRunner

from knoweval.cli import app
from knoweval.compare import CompareError, compare_runs, diff_runs, mcnemar_p
from knoweval.runner.run import run_eval
from tests.test_cli import DEMO_DIR
from tests.test_runner import DEMO, FakeAdapter

GOOD = {"demo-002": "首席架构师", "demo-003": "2015 年", "demo-005": "韩知微", "demo-016": "9091",
        "demo-004": "两年", "demo-008": "120 万条/秒"}


async def _run(path, answers, **kw):
    await run_eval(DEMO, FakeAdapter(answers=answers), path, repeats=kw.pop("repeats", 1),
                   system_config={"adapter": "fake", **kw})
    return path


def test_mcnemar_exact():
    assert mcnemar_p(0, 0) == 1.0
    assert mcnemar_p(10, 0) == pytest.approx(0.001953125)
    assert mcnemar_p(3, 2) == pytest.approx(1.0)


async def test_compare_reports_metrics_matrix_and_significance(tmp_path):
    a = await _run(tmp_path / "a", GOOD, name="A")
    b = await _run(tmp_path / "b", {"demo-002": "首席架构师"}, name="B")
    result = compare_runs([a, b], DEMO)
    assert [s["name"] for s in result["systems"]] == ["a", "b"]
    assert result["systems"][0]["accuracy"]["k"] == 6 and result["systems"][1]["accuracy"]["k"] == 1
    pair = result["pairs"][0]
    assert pair["only_first"] == 5 and pair["only_second"] == 0
    assert pair["p_value"] == pytest.approx(0.0625)
    assert pair["significant"] is False
    assert result["matrix"]["demo-003"] == ["CORRECT", "ABSTAIN"]
    assert result["groups"]["only_a"] == ["demo-003", "demo-004", "demo-005", "demo-008", "demo-016"]


async def test_compare_refuses_different_datasets(tmp_path):
    import shutil
    from knoweval.dataset import load_dataset
    a = await _run(tmp_path / "a", GOOD)
    copy = tmp_path / "ds"
    shutil.copytree(DEMO.root, copy)
    q = copy / "questions.jsonl"
    q.write_text(q.read_text(encoding="utf-8").replace("首席架构师", "总架构师"), encoding="utf-8")
    await run_eval(load_dataset(copy), FakeAdapter(), tmp_path / "b", repeats=1, system_config={"adapter": "fake"})
    with pytest.raises(CompareError, match="数据集"):
        compare_runs([a, tmp_path / "b"], DEMO)


async def test_diff_lists_flips_and_compares_to_noise(tmp_path):
    before = await _run(tmp_path / "before", GOOD)
    after_answers = dict(GOOD)
    after_answers["demo-016"] = "9092"
    after_answers["demo-009"] = "1.8 亿元"
    after = await _run(tmp_path / "after", after_answers)
    d = diff_runs(before, after, DEMO)
    assert d["to_wrong"] == ["demo-016"]
    assert d["to_correct"] == ["demo-009"]
    assert d["within_noise"] is False


async def test_cli_compare_and_diff(tmp_path):
    a = await _run(tmp_path / "a", GOOD)
    b = await _run(tmp_path / "b", {})
    runner = CliRunner()
    out = runner.invoke(app, ["compare", str(a), str(b), "--dataset", str(DEMO_DIR)])
    assert out.exit_code == 0, out.output
    assert "差异" in out.output and (tmp_path / "a" / "compare.json").exists() is False
    out = runner.invoke(app, ["diff", str(a), str(b), "--dataset", str(DEMO_DIR)])
    assert out.exit_code == 0 and "由对变错" in out.output


def test_smoke_subset_takes_two_per_qtype():
    from knoweval.dataset import load_dataset
    sub = load_dataset(DEMO_DIR).subset("smoke")
    counts = {}
    for q in sub.questions:
        counts[q.qtype[0]] = counts.get(q.qtype[0], 0) + 1
    assert max(counts.values()) <= 2 and set(counts) == {q.qtype[0] for q in DEMO.questions}
    assert sub.content_hash() != DEMO.content_hash()


def test_fail_under_parsing_and_check():
    from knoweval.ci import check_thresholds, parse_thresholds
    th = parse_thresholds(["accuracy=0.8", "confident_wrong<=0.2"])
    summary = {"accuracy": {"value": 0.75}, "confident_wrong": {"value": 0.1}}
    failures = check_thresholds(summary, th)
    assert failures == ["准确率 75.0% 低于 80.0%"]
    with pytest.raises(ValueError):
        parse_thresholds(["nope=1"])


def test_cli_run_with_smoke_subset_and_fail_under(tmp_path, monkeypatch):
    from knoweval import cli as cli_mod

    cfg = tmp_path / "sys.yaml"
    cfg.write_text("adapter: ragflow\nbase_url: http://x\napi_key_env: K\n", encoding="utf-8")
    monkeypatch.setattr(cli_mod, "build_adapter", lambda c: FakeAdapter(answers=GOOD))
    runner = CliRunner()
    ok = runner.invoke(app, ["run", "--system", str(cfg), "--dataset", str(DEMO_DIR), "--out", str(tmp_path / "r"),
                             "--repeat", "1", "--subset", "smoke", "--cache-dir", str(tmp_path / "c"),
                             "--fail-under", "accuracy=0.1"])
    assert ok.exit_code == 0, ok.output
    meta = json.loads((tmp_path / "r" / "run.json").read_text(encoding="utf-8"))
    assert meta["subset"] == "smoke"
    bad = runner.invoke(app, ["run", "--system", str(cfg), "--dataset", str(DEMO_DIR), "--out", str(tmp_path / "r"),
                              "--repeat", "1", "--subset", "smoke", "--cache-dir", str(tmp_path / "c"),
                              "--fail-under", "accuracy=0.99"])
    assert bad.exit_code == 1 and "低于" in bad.output


async def test_rejudge_and_review_work_on_subset_runs(tmp_path):
    from knoweval.review import add_review
    from knoweval.runner.rejudge import rejudge
    ds = DEMO.subset("smoke")
    await run_eval(ds, FakeAdapter(answers=GOOD), tmp_path, repeats=1, system_config={"adapter": "fake"}, subset="smoke")
    await rejudge(tmp_path, DEMO)
    assert "demo-002" in {q.id for q in ds.questions}
    add_review(tmp_path, qid="demo-002", repeat=1, verdict="CORRECT", reason="确认", by="r")
    result = CliRunner().invoke(app, ["review", str(tmp_path), "--qid", "demo-002", "--verdict", "CORRECT",
                                      "--by", "r", "--dataset", str(DEMO_DIR)])
    assert result.exit_code == 0, result.output


def test_dataset_hash_matches_only_the_same_questions_and_docs():
    assert DEMO.matches_hash(DEMO.content_hash())
    assert not DEMO.subset("smoke").matches_hash(DEMO.content_hash())
    assert not DEMO.matches_hash("0" * 64)


async def test_compare_by_qtype_uses_abstain_rate_for_no_answer_questions(tmp_path):
    a = await _run(tmp_path / "a", GOOD)
    b = await _run(tmp_path / "b", {})
    result = compare_runs([a, b], DEMO)
    assert result["systems"][0]["by_qtype"]["N"]["k"] == 3

"""人工改判与一致率：人工每看一题记一条判定，和机器一致算确认，不一致算改判。"""

import json

import pytest
from typer.testing import CliRunner

from knoweval.cli import app
from knoweval.review import ReviewError, add_review, load_reviews, review_stats
from knoweval.runner.run import run_eval
from tests.test_runner import DEMO, FakeAdapter
from tests.test_cli import DEMO_DIR


@pytest.fixture
async def run_dir(tmp_path):
    await run_eval(DEMO, FakeAdapter(answers={"demo-002": "首席架构师"}), tmp_path, repeats=1,
                   system_config={"adapter": "fake"})
    return tmp_path


async def test_add_review_records_confirm_and_override(run_dir):
    add_review(run_dir, qid="demo-002", repeat=1, verdict="CORRECT", reason="确认", by="r01")
    add_review(run_dir, qid="demo-003", repeat=1, verdict="CORRECT", reason="其实答对了", by="r01")
    reviews = load_reviews(run_dir)
    assert [r.agrees for r in reviews] == [True, False]
    stats = review_stats(reviews)
    assert stats["reviewed"] == 2 and stats["agreed"] == 1 and stats["agreement"] == 0.5


async def test_later_review_of_the_same_item_replaces_the_earlier_one(run_dir):
    add_review(run_dir, qid="demo-003", repeat=1, verdict="CORRECT", reason="a", by="r01")
    add_review(run_dir, qid="demo-003", repeat=1, verdict="ABSTAIN", reason="b", by="r02")
    reviews = load_reviews(run_dir)
    assert len(reviews) == 1 and reviews[0].verdict == "ABSTAIN" and reviews[0].by == "r02"


async def test_review_of_unknown_question_is_rejected(run_dir):
    with pytest.raises(ReviewError):
        add_review(run_dir, qid="nope", repeat=1, verdict="CORRECT", reason="", by="r01")
    with pytest.raises(ReviewError):
        add_review(run_dir, qid="demo-002", repeat=9, verdict="CORRECT", reason="", by="r01")


async def test_override_requires_a_reason(run_dir):
    with pytest.raises(ReviewError, match="理由"):
        add_review(run_dir, qid="demo-003", repeat=1, verdict="CORRECT", reason="", by="r01")


async def test_summary_has_before_and_after_and_agreement(run_dir):
    runner = CliRunner()
    result = runner.invoke(app, ["review", str(run_dir), "--qid", "demo-003", "--repeat", "1",
                                 "--verdict", "CORRECT", "--reason", "回答其实对", "--by", "r01",
                                 "--dataset", str(DEMO_DIR)])
    assert result.exit_code == 0, result.output
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    before = summary["repeats"][0]["accuracy"]["k"]
    after = summary["repeats_reviewed"][0]["accuracy"]["k"]
    assert after == before + 1
    assert summary["human_review"]["reviewed"] == 1 and summary["human_review"]["agreement"] == 0.0
    out = runner.invoke(app, ["report", str(run_dir)]).output
    assert "改判后" in out and "一致率" in out

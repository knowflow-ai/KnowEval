"""三档判分：接口出错记为出错，回答为空记为拒答，其余全部交给裁判模型。不再用规则比对。"""

import pytest

from knoweval.judge.judge import gold_text, judge_answer, three_tier_metric
from knoweval.schema import Answer, Citation, Question, Verdict


def q(**over) -> Question:
    base = dict(id="q", domain="demo", qtype=["F"], question="问题？", answer="9400",
                answer_type="number", tolerance=0,
                evidence=[{"doc": "05.pdf", "page": 1, "quote": "默认管理端口由 9300 调整为 9400"}])
    base.update(over)
    return Question(**base)


class ScriptedJudge:
    """按预设给判定，记下收到的问题、标准答案与回答。"""

    version = "fake-v1"

    def __init__(self, verdict: str):
        self.result = verdict
        self.seen: list[tuple[str, str, str]] = []

    async def verdict(self, question, gold, answer):
        self.seen.append((question, gold, answer))
        return self.result, "裁判输出"


async def test_failed_answer_is_error_without_asking_the_judge():
    judge = ScriptedJudge("CORRECT")
    j = await judge_answer(q(), Answer(error="TimeoutError: 60s"), judge)
    assert j.verdict is Verdict.ERROR and "TimeoutError" in j.reason and judge.seen == []


async def test_empty_answer_is_abstain_without_asking_the_judge():
    judge = ScriptedJudge("CORRECT")
    j = await judge_answer(q(), Answer(text="  [ID:1] "), judge)
    assert j.verdict is Verdict.ABSTAIN and judge.seen == []


@pytest.mark.parametrize("verdict", ["CORRECT", "WRONG", "ABSTAIN"])
async def test_every_other_answer_is_judged_by_the_model(verdict):
    """「358.6亿元」对标准答案 358.6（单位亿元）这类写法差异，交给模型判。"""
    judge = ScriptedJudge(verdict)
    question = q(answer="358.6", unit="亿元", question="2023年市场规模是多少亿元？")
    text = "2023年市场规模为358.6亿元，较2022年增长234.5%。"
    j = await judge_answer(question, Answer(text=text), judge)
    assert j.verdict is Verdict(verdict) and j.method == "llm" and "fake-v1" in j.reason
    assert judge.seen == [("2023年市场规模是多少亿元？", "358.6亿元", text)]


def test_gold_text_tells_the_judge_units_tolerance_aliases_and_distractors():
    assert gold_text(q(answer="1.5", unit="万元", tolerance=0.1, answer_aliases=["一万五千元"], answer_distractors=["1.2"])) == \
        "1.5万元；允许误差 ±0.1万元；也算对的说法：一万五千元；答成这些算错：1.2"
    assert gold_text(q(qtype=["N"], answer="", answer_type="text", tolerance=None, evidence=[])).startswith("文档里没有答案")


@pytest.mark.real_judge_wiring
async def test_without_a_judge_the_answer_is_flagged_for_review():
    j = await judge_answer(q(), Answer(text="9400"))
    assert j.verdict is Verdict.WRONG and j.needs_review and "设置" in j.reason


async def test_citation_checked_only_for_correct_answers():
    cited = [Citation(doc="05.pdf", text="默认管理端口由 9300 调整为 9400")]
    good = await judge_answer(q(), Answer(text="9400", citations=cited), ScriptedJudge("CORRECT"))
    assert good.citation is not None and good.citation.verifiable is True
    bad = await judge_answer(q(), Answer(text="9500", citations=cited), ScriptedJudge("WRONG"))
    assert bad.citation is None


async def test_ragas_metric_returns_verdict_and_full_judgement():
    result = await three_tier_metric.ascore(question=q().model_dump(), answer={"text": "9400"}, text_judge=ScriptedJudge("CORRECT"))
    assert result.value == "CORRECT"
    assert result.traces["output"]["method"] == "llm"


async def test_score_three_tier_raises_instead_of_returning_none(monkeypatch):
    from knoweval.judge import judge as judge_mod

    async def boom(*a, **k):
        raise RuntimeError("判分代码出错")

    monkeypatch.setattr(judge_mod, "judge_answer", boom)
    with pytest.raises(judge_mod.MetricExecutionError):
        await judge_mod.score_three_tier(q(), Answer(text="9400"))


async def test_score_three_tier_passes_text_judge_through():
    from knoweval.judge.judge import score_three_tier

    judge = ScriptedJudge("CORRECT")
    j = await score_three_tier(q(answer="首席架构师", answer_type="text", tolerance=None), Answer(text="总架构负责人"), judge)
    assert len(judge.seen) == 1 and j.verdict is Verdict.CORRECT

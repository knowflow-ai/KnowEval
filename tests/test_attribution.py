"""错题归因：标准证据在不在系统的检索上下文里。"""

from knoweval.judge.judge import judge_answer
from knoweval.schema import Answer, Citation, Question, Verdict
from knoweval.stats import compute_summary


def _q(**over):
    base = dict(id="q", domain="d", qtype=["M"], question="?", answer="韩知微", answer_type="text",
                evidence=[{"doc": "02.pdf", "page": 1, "quote": "沈墨目前任职于岚图研究院"},
                          {"doc": "03.pdf", "page": 1, "quote": "岚图研究院由计算机科学家韩知微于二〇一五年创办"}])
    base.update(over)
    return Question(**base)


CTX_ALL = [Citation(doc="02.pdf", text="职业经历：沈墨目前任职于岚图研究院，担任首席架构师。"),
           Citation(doc="03.pdf", text="岚图研究院由计算机科学家韩知微于二〇一五年创办。")]


async def test_wrong_with_all_evidence_retrieved_is_a_generation_error():
    j = await judge_answer(_q(), Answer(text="创办人是沈墨。", contexts=CTX_ALL))
    assert j.verdict is Verdict.WRONG and j.attribution == "generation"


async def test_wrong_with_missing_evidence_is_a_retrieval_miss():
    j = await judge_answer(_q(), Answer(text="创办人是沈墨。", contexts=CTX_ALL[:1]))
    assert j.attribution == "retrieval"


async def test_over_abstain_is_attributed_too():
    j = await judge_answer(_q(), Answer(text="知识库中未找到您要的答案！", contexts=CTX_ALL))
    assert j.verdict is Verdict.ABSTAIN and j.attribution == "generation"


async def test_no_contexts_means_no_attribution():
    j = await judge_answer(_q(), Answer(text="创办人是沈墨。"))
    assert j.attribution is None


async def test_correct_and_no_answer_questions_are_not_attributed():
    assert (await judge_answer(_q(), Answer(text="韩知微", contexts=CTX_ALL))).attribution is None
    n = _q(qtype=["N"], answer="", evidence=[])
    assert (await judge_answer(n, Answer(text="是沈墨", contexts=CTX_ALL))).attribution is None


def test_summary_counts_attribution():
    from knoweval.judge.judge import Judgement
    recs = [(_q(), Judgement(verdict=Verdict.WRONG, reason="", method="rule", attribution="retrieval")),
            (_q(), Judgement(verdict=Verdict.WRONG, reason="", method="rule", attribution="generation")),
            (_q(), Judgement(verdict=Verdict.ABSTAIN, reason="", method="rule", attribution="retrieval"))]
    s = compute_summary(recs)
    assert s.attribution == {"retrieval": 2, "generation": 1}

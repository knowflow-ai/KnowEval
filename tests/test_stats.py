"""统计：四个主要指标、出错留在分母、按题型、两遍之间的噪声底线。"""

import pytest

from knoweval.judge.citation import CitationCheck
from knoweval.judge.judge import Judgement
from knoweval.schema import Question, Verdict
from knoweval.stats import compute_summary, noise_floor, wilson


def test_wilson_interval_matches_known_values():
    lo, hi = wilson(226, 315)
    assert lo == pytest.approx(0.6654, abs=1e-4)
    assert hi == pytest.approx(0.7643, abs=1e-4)


def test_wilson_with_no_samples_is_none():
    assert wilson(0, 0) is None


def _q(qid, qtype="F"):
    if qtype == "N":
        return Question(id=qid, domain="d", qtype=["N"], question="?", answer="", answer_type="text")
    return Question(id=qid, domain="d", qtype=[qtype], question="?", answer="1", answer_type="number",
                    tolerance=0, evidence=[{"doc": "a.pdf", "page": 1, "quote": "x"}])


def _j(verdict, cite=None, review=False):
    return Judgement(verdict=Verdict(verdict), reason="", method="rule", needs_review=review, citation=cite)


def test_error_stays_in_the_denominator():
    records = [
        (_q("a"), _j("CORRECT")),
        (_q("b"), _j("WRONG")),
        (_q("c"), _j("ERROR")),
    ]
    s = compute_summary(records)
    assert s.accuracy.k == 1 and s.accuracy.n == 3
    assert s.error_count == 1
    assert s.confident_wrong.k == 1 and s.confident_wrong.n == 2


def test_no_answer_questions_feed_abstain_and_confident_wrong():
    records = [
        (_q("a"), _j("CORRECT")),
        (_q("b"), _j("ABSTAIN")),
        (_q("n1", "N"), _j("ABSTAIN")),
        (_q("n2", "N"), _j("WRONG")),
    ]
    s = compute_summary(records)
    assert s.accuracy.n == 2
    assert s.over_abstain.k == 1 and s.over_abstain.n == 2
    assert s.abstain_correct.k == 1 and s.abstain_correct.n == 2
    # 无答案题上编造的结论也是自信答错（规范第 5 节 WRONG 的定义）：1 道有答案题答对 + 1 道无答案题编造
    assert s.confident_wrong.k == 1 and s.confident_wrong.n == 2


def test_citation_metrics_use_correct_answers_only():
    records = [
        (_q("a"), _j("CORRECT", CitationCheck(verifiable=True, matched=1, total=2))),
        (_q("b"), _j("CORRECT", CitationCheck(verifiable=False, matched=0, total=1))),
        (_q("c"), _j("WRONG")),
    ]
    s = compute_summary(records)
    assert s.citation_verifiable.k == 1 and s.citation_verifiable.n == 2
    assert s.citation_precision.k == 1 and s.citation_precision.n == 3


def test_by_qtype_counts_a_question_in_each_of_its_types():
    q = Question(id="m", domain="d", qtype=["M", "T"], question="?", answer="1", answer_type="number",
                 tolerance=0, evidence=[{"doc": "a.pdf", "page": 1, "quote": "x"}])
    s = compute_summary([(q, _j("CORRECT"))])
    assert s.by_qtype["M"].accuracy.k == 1
    assert s.by_qtype["T"].accuracy.k == 1


def test_needs_review_is_counted():
    s = compute_summary([(_q("a"), _j("WRONG", review=True))])
    assert s.needs_review == 1


def test_noise_floor_counts_questions_whose_verdict_differs_between_runs():
    r1 = {"a": Verdict.CORRECT, "b": Verdict.WRONG, "c": Verdict.ABSTAIN}
    r2 = {"a": Verdict.CORRECT, "b": Verdict.CORRECT, "c": Verdict.ABSTAIN}
    flips = noise_floor([r1, r2])
    assert flips == ["b"]


def _qe(qid, hops=1, modality="text", page_end=None, doc="a.pdf"):
    return Question(id=qid, domain="d", qtype=["M" if hops > 1 else "F"], question="?", answer="1",
                    answer_type="number", tolerance=0, hops=hops,
                    evidence=[{"doc": doc, "page": 1, "page_end": page_end, "modality": modality, "quote": "x"}])


def test_groups_by_hops_modality_cross_page_and_format():
    records = [
        (_qe("a", hops=1, modality="text"), _j("CORRECT")),
        (_qe("b", hops=2, modality="table", page_end=2, doc="scan.pdf"), _j("WRONG")),
        (_qe("c", hops=5, modality="chart"), _j("CORRECT")),
    ]
    s = compute_summary(records, doc_formats={"a.pdf": "pdf", "scan.pdf": "scanned_pdf"})
    assert s.groups["hops"]["1"].accuracy.k == 1
    assert s.groups["hops"]["2"].accuracy.k == 0
    assert "4+" in s.groups["hops"]
    assert s.groups["modality"]["table"].accuracy.n == 1
    assert s.groups["cross_page"]["跨页"].accuracy.n == 1
    assert s.groups["cross_page"]["不跨页"].accuracy.n == 2
    assert s.groups["format"]["scanned_pdf"].accuracy.k == 0
    assert s.groups["format"]["pdf"].accuracy.k == 2


def test_no_answer_questions_stay_out_of_layout_groups():
    s = compute_summary([(_q("n", "N"), _j("ABSTAIN"))])
    assert s.groups["modality"] == {}


def test_summary_groups_by_question_origin_when_known():
    """题目带来源时，报告按来源分组：能看出自己导入的题和模型出的题各自的得分。"""
    from knoweval.judge.judge import Judgement
    from knoweval.schema import Question, Verdict
    from knoweval.stats import compute_summary
    qs = [Question(id=f"q{i}", domain="d", qtype=["F"], question=f"问{i}", answer="a", answer_type="text",
                   origin=o) for i, o in enumerate(["imported", "imported", "generated"])]
    js = [Judgement(verdict=Verdict.CORRECT if i < 2 else Verdict.WRONG, reason="", method="llm") for i in range(len(qs))]
    s = compute_summary(list(zip(qs, js)))
    assert set(s.groups["origin"]) == {"导入", "模型出题"}
    assert s.groups["origin"]["导入"].accuracy.value == 1.0 and s.groups["origin"]["模型出题"].accuracy.value == 0.0
    plain = compute_summary([(q.model_copy(update={"origin": None}), j) for q, j in zip(qs, js)])
    assert "origin" not in plain.groups

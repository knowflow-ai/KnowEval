"""题目与回答的数据结构校验。"""

import pytest
from pydantic import ValidationError

from knoweval.schema import Answer, Citation, Question, Verdict


def _q(**over):
    base = dict(
        id="q-1", domain="demo", qtype=["F"], question="问题？", answer="23",
        answer_type="number", unit="dBm", tolerance=0,
        evidence=[{"doc": "a.pdf", "page": 1, "quote": "最大发射功率 23 dBm"}],
    )
    base.update(over)
    return Question(**base)


def test_answer_type_unit_and_tolerance_are_optional_hints_for_the_judge():
    """判分全交裁判模型：类型、单位、误差只是提示，不再按规则强制格式。"""
    assert _q(tolerance=None).tolerance is None
    assert _q(answer="约一千亿", answer_type="number").answer == "约一千亿"
    assert _q(answer="2023/03/15", answer_type="date").answer_type == "date"
    bare = Question(id="q-2", domain="demo", qtype=["F"], question="问题？", answer="首席架构师")
    assert bare.answer_type == "text" and bare.unit is None


def test_no_answer_question_must_have_empty_answer_and_evidence():
    with pytest.raises(ValidationError):
        _q(qtype=["N"], answer="23")
    q = _q(qtype=["N"], answer="", answer_type="text", unit=None, evidence=[])
    assert q.is_no_answer


def test_unknown_qtype_rejected():
    with pytest.raises(ValidationError):
        _q(qtype=["Z"])


def test_answer_error_and_text_are_mutually_exclusive():
    with pytest.raises(ValidationError):
        Answer(text="x", error="timeout")
    assert Answer(error="timeout").failed
    assert not Answer(text="ok").failed


def test_citation_and_verdict_values():
    c = Citation(doc="a.pdf", page=3, text="片段")
    assert c.page == 3
    assert {v.value for v in Verdict} == {"CORRECT", "ABSTAIN", "WRONG", "ERROR"}


def test_new_qtypes_and_evidence_features():
    q = _q(qtype=["I", "T"], hop_kind="chain",
           evidence=[{"doc": "a.pdf", "page": 3, "page_end": 4, "modality": "table", "quote": "x"}],
           answer_images=[{"doc": "a.pdf", "page": 3, "bbox": [0, 0, 100, 100], "phash": "f0f0f0f0f0f0f0f0"}])
    assert q.evidence[0].cross_page is True
    assert q.answer_images[0].phash == "f0f0f0f0f0f0f0f0"
    assert _q(qtype=["G"]).qtype == ["G"]


def test_unknown_modality_rejected():
    with pytest.raises(ValidationError):
        _q(evidence=[{"doc": "a.pdf", "page": 1, "modality": "video", "quote": "x"}])


def test_page_end_before_page_rejected():
    with pytest.raises(ValidationError):
        _q(evidence=[{"doc": "a.pdf", "page": 4, "page_end": 3, "quote": "x"}])


def test_answer_carries_image_refs():
    a = Answer(text="见下图", images=[{"doc": "a.pdf", "page": 2, "phash": "00ff00ff00ff00ff"}])
    assert a.images[0].page == 2


def test_dataset_design_fields():
    q = _q(stage="chunk", traps=["version", "paraphrase"], tier="core")
    assert q.stage == "chunk" and q.traps == ["version", "paraphrase"]
    n = _q(qtype=["N"], answer="", answer_type="text", unit=None, evidence=[], n_kind="false_premise", stage="honesty")
    assert n.n_kind == "false_premise"
    with pytest.raises(ValidationError):
        _q(stage="parsing")
    with pytest.raises(ValidationError):
        _q(traps=["typo"])


def test_missing_non_redistributed_document_says_how_to_get_it(tmp_path):
    """不随仓库分发的文档（如上市公司年报）缺了：说清楚是要自己下载，不是数据集坏了。"""
    import json

    import pytest

    from knoweval.dataset import DatasetIntegrityError, load_dataset

    root = tmp_path / "ds"
    (root / "docs").mkdir(parents=True)
    (root / "questions.jsonl").write_text("", encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({
        "name": "x", "version": "0.1", "license": "l", "canary": "c",
        "files": [{"name": "年报.md", "sha256": "0" * 64, "format": "md", "redistribute": False,
                   "source_url": "https://example.com/a.pdf"}]}), encoding="utf-8")
    with pytest.raises(DatasetIntegrityError, match="不随数据集分发.*build"):
        load_dataset(root)

"""demo 数据集的完整性：每条证据都能在原文里逐字找到，文件哈希与清单一致。"""

import hashlib
from pathlib import Path

import pytest

from knoweval.dataset import load_dataset, normalize_for_quote

DEMO = Path(__file__).resolve().parents[1] / "datasets" / "demo"


@pytest.fixture(scope="module")
def demo():
    return load_dataset(DEMO)


def test_demo_has_twenty_questions_with_unique_ids(demo):
    ids = [q.id for q in demo.questions]
    assert len(ids) == 20
    assert len(set(ids)) == 20


def test_every_evidence_quote_exists_verbatim_in_its_source(demo):
    for q in demo.questions:
        for ev in q.evidence:
            source = (DEMO / "src" / (Path(ev.doc).stem + ".md")).read_text(encoding="utf-8")
            assert normalize_for_quote(ev.quote) in normalize_for_quote(source), (q.id, ev.quote)


def test_every_evidence_has_a_page_found_in_the_pdf(demo):
    for q in demo.questions:
        for ev in q.evidence:
            assert ev.page is not None and ev.page >= 1, (q.id, ev.doc)


def test_no_answer_questions_have_empty_answer_and_evidence(demo):
    n_questions = [q for q in demo.questions if "N" in q.qtype]
    assert len(n_questions) >= 2
    for q in n_questions:
        assert q.answer == ""
        assert q.evidence == []


def test_answerable_questions_have_evidence(demo):
    for q in demo.questions:
        if "N" not in q.qtype:
            assert q.evidence, q.id


def test_manifest_hashes_match_files(demo):
    assert demo.manifest.files, "清单里没有文件"
    for entry in demo.manifest.files:
        data = (DEMO / "docs" / entry.name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry.sha256, entry.name


def test_manifest_carries_a_canary_string(demo):
    assert demo.manifest.canary.startswith("KNOWEVAL-CANARY-")


def test_changed_document_fails_loudly(tmp_path):
    import shutil

    from knoweval.dataset import DatasetIntegrityError

    copy = tmp_path / "demo"
    shutil.copytree(DEMO, copy)
    (copy / "docs" / "02-沈墨.pdf").write_bytes(b"%PDF-1.4 tampered")
    with pytest.raises(DatasetIntegrityError, match="02-沈墨.pdf"):
        load_dataset(copy)
    (copy / "docs" / "02-沈墨.pdf").unlink()
    with pytest.raises(DatasetIntegrityError, match="缺少"):
        load_dataset(copy)

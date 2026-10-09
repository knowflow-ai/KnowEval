"""demo-md：与 demo 同内容、同题目的 Markdown 版。两版成绩差就是文档格式这一个变量的影响（规范 §4.1）。"""

from pathlib import Path

from knoweval.dataset import load_dataset, normalize_for_quote

ROOT = Path(__file__).resolve().parents[1] / "datasets"


def test_md_variant_mirrors_the_pdf_dataset():
    pdf, md = load_dataset(ROOT / "demo"), load_dataset(ROOT / "demo-md")
    assert md.ref == "demo-md@0.0"
    assert [q.id for q in md.questions] == [q.id for q in pdf.questions]
    assert [q.question for q in md.questions] == [q.question for q in pdf.questions]
    assert {f.format for f in md.manifest.files} == {"md"}
    assert all(f.name.endswith(".md") for f in md.manifest.files)


def test_md_evidence_points_at_md_files_without_pages():
    md = load_dataset(ROOT / "demo-md")
    for q in md.questions:
        for ev in q.evidence:
            assert ev.doc.endswith(".md") and ev.page is None
            text = (ROOT / "demo-md" / "docs" / ev.doc).read_text(encoding="utf-8")
            assert normalize_for_quote(ev.quote) in normalize_for_quote(text), (q.id, ev.quote)

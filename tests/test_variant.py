"""同内容多形态（数据集设计 §6）：扫描版变体，题目与页码不变，文档换成无文本层的扫描 PDF。"""

import pymupdf
from typer.testing import CliRunner

from knoweval.cli import app
from knoweval.dataset import load_dataset
from knoweval.variants import scan_pdf
from tests.test_cli import DEMO_DIR


def test_scan_pdf_has_no_text_layer_and_same_page_count(tmp_path):
    src = DEMO_DIR / "docs" / "04-潮汐系统技术参数手册.pdf"
    dst = tmp_path / "scan.pdf"
    scan_pdf(src, dst, seed=1)
    with pymupdf.open(src) as a, pymupdf.open(dst) as b:
        assert len(a) == len(b)
        assert all(not p.get_text().strip() for p in b)
        assert all(p.get_images() for p in b)


def test_dataset_variant_keeps_questions_and_marks_format(tmp_path):
    out = tmp_path / "demo-scanned"
    r = CliRunner().invoke(app, ["dataset", "variant", str(DEMO_DIR), "--kind", "scanned", "--out", str(out)])
    assert r.exit_code == 0, r.output
    orig, scanned = load_dataset(DEMO_DIR), load_dataset(out)
    assert scanned.ref == "demo-scanned@0.0"
    assert [q.model_dump() for q in scanned.questions] == [q.model_dump() for q in orig.questions]
    assert {f.format for f in scanned.manifest.files} == {"scanned_pdf"}
    assert scanned.docs_hash() != orig.docs_hash()


def test_variant_rejects_non_pdf_datasets(tmp_path):
    r = CliRunner().invoke(app, ["dataset", "variant", str(DEMO_DIR.parent / "demo-md"), "--kind", "scanned",
                                 "--out", str(tmp_path / "x")])
    assert r.exit_code != 0

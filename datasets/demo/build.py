"""构建 demo 数据集：src/*.md → docs/*.pdf，补全证据页码，写出 questions.jsonl 与 manifest.json。

同时产出 Markdown 版 datasets/demo-md：同内容、同题目，文档换成 md，证据不带页码。
两版成绩差就是文档格式这一个变量的影响（规范 §3.3「同一内容多种形态」）。

用法：python datasets/demo/build.py
依赖：reportlab、pypdf（pip install -e ".[dev]"）。

PDF 渲染见 knoweval.build.render（内置 CID 字体、关闭时间戳，产出逐字节稳定）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1] / "src"))
from knoweval.build.render import md_to_pdf  # noqa: E402
from knoweval.dataset import normalize_for_quote  # noqa: E402

CANARY = "KNOWEVAL-CANARY-7f3a9c2e-demo-0.0"
MD_ROOT = ROOT.parent / "demo-md"


def page_texts(pdf: Path) -> list[str]:
    return [normalize_for_quote(p.extract_text() or "") for p in PdfReader(str(pdf)).pages]


def find_page(pages: list[str], quote: str) -> int:
    needle = normalize_for_quote(quote)
    for i, text in enumerate(pages, start=1):
        if needle in text:
            return i
    raise SystemExit(f"证据在 PDF 里找不到：{quote!r}")


def main() -> None:
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    pages: dict[str, list[str]] = {}
    for md in sorted((ROOT / "src").glob("*.md")):
        pdf = docs / f"{md.stem}.pdf"
        md_to_pdf(md.read_text(encoding="utf-8"), pdf)
        pages[pdf.name] = page_texts(pdf)

    out_lines = []
    for line in (ROOT / "questions.src.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        q = json.loads(line)
        q.setdefault("domain", "demo")
        for ev in q.get("evidence", []):
            ev["page"] = find_page(pages[ev["doc"]], ev["quote"])
        out_lines.append(json.dumps(q, ensure_ascii=False))
    (ROOT / "questions.jsonl").write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    manifest = {
        "name": "demo", "version": "0.0", "license": "Apache-2.0", "canary": CANARY,
        "files": [
            {"name": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(docs.glob("*.pdf"))
        ],
    }
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(manifest['files'])} 份文档，{len(out_lines)} 道题")
    build_md_variant(out_lines, manifest)


def build_md_variant(pdf_lines: list[str], pdf_manifest: dict) -> None:
    docs = MD_ROOT / "docs"
    if MD_ROOT.exists():
        shutil.rmtree(MD_ROOT)
    docs.mkdir(parents=True)
    for md in sorted((ROOT / "src").glob("*.md")):
        shutil.copyfile(md, docs / md.name)
    lines = []
    for line in pdf_lines:
        q = json.loads(line)
        for ev in q.get("evidence", []):
            ev["doc"] = Path(ev["doc"]).stem + ".md"
            ev.pop("page", None)
        lines.append(json.dumps(q, ensure_ascii=False))
    (MD_ROOT / "questions.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {
        **pdf_manifest, "name": "demo-md", "canary": CANARY.replace("demo-", "demo-md-"),
        "files": [
            {"name": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "format": "md"}
            for p in sorted(docs.glob("*.md"))
        ],
    }
    (MD_ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Markdown 版：{len(manifest['files'])} 份文档，{len(lines)} 道题")


if __name__ == "__main__":
    main()

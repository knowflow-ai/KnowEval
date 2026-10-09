"""Markdown → PDF。用 reportlab 内置的 CID 字体 STSong-Light，不依赖系统字体，换机器渲染结果一致；
关闭时间戳（invariant），同样的源文件产出逐字节相同的 PDF，数据集清单里的哈希才稳定。"""

from __future__ import annotations

from pathlib import Path

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

rl_config.invariant = 1
FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT))
H1 = ParagraphStyle("h1", fontName=FONT, fontSize=18, leading=26, spaceAfter=10)
H2 = ParagraphStyle("h2", fontName=FONT, fontSize=14, leading=22, spaceBefore=10, spaceAfter=6)
BODY = ParagraphStyle("body", fontName=FONT, fontSize=10.5, leading=18, spaceAfter=6)


def _table(rows: list[list[str]]) -> Table:
    t = Table(rows, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
    ]))
    return t


def md_to_pdf(md: str, out: Path) -> None:
    """极简 markdown：# / ## 标题、| 表格、其余为段落。"""
    story, table = [], []
    for line in md.strip().splitlines() + [""]:
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not all(set(c) <= {"-"} for c in cells):
                table.append(cells)
            continue
        if table:
            story += [_table(table), Spacer(1, 0.3 * cm)]
            table = []
        if not line.strip():
            continue
        if line.startswith("## "):
            story.append(Paragraph(line[3:], H2))
        elif line.startswith("# "):
            story.append(Paragraph(line[2:], H1))
        else:
            story.append(Paragraph(line, BODY))
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, bottomMargin=2 * cm, title=Path(out).stem, author="KnowEval")
    doc.build(story)

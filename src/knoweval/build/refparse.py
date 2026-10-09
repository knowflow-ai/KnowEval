"""参考解析：取每页文本，只用于出题与校验证据，不交给被测系统。

用 PyMuPDF 读 PDF 文本层，与所有参评系统的解析链路无关（出题不用 MinerU 等被测系统也在用的解析器，
免得题目偏向它们）。PyMuPDF 默认按内容流顺序出字，图表里的标签和数值会被拆成两列，出题模型只能猜着
配对，标准答案就会配错；所以只有一行的短文本块按所在的行拼起来，多行的段落块保持原样（见 _page_rows）。
扫描件没有文本层，取到的是空文本，
这些页上的证据要人工确认。Markdown、TXT、HTML 没有页码，整篇算一页（page 为 None）。
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

TEXT_FORMATS = {".md": "md", ".markdown": "md", ".txt": "md", ".html": "html", ".htm": "html"}
OFFICE_FORMATS = (".docx", ".pptx", ".xlsx")
_COMMENTS = re.compile(r"<!--.*?-->", re.S)
_TAGS = re.compile(r"<[^>]+>")


class PageText(BaseModel):
    page: int | None
    text: str


def doc_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in TEXT_FORMATS:
        return TEXT_FORMATS[suffix]
    if suffix in OFFICE_FORMATS:
        return suffix[1:]
    if suffix in (".doc", ".ppt", ".xls"):
        raise ValueError(f"{path.name} 是旧版 Office 格式，请另存为 .{suffix[1:]}x 再上传")
    raise ValueError(f"暂不支持的文档格式：{path.name}")


SECTION_CHARS = 2000


def split_sections(text: str, target: int = SECTION_CHARS) -> list[str]:
    """没有页码的长文本按段落切成约 target 字的片段，当作「页」用。

    整篇当一页时，出题只看得到开头、无答案核查也只查开头（几十万字的年报尤其如此）。
    只在空行处切、片段之间不重叠，拼起来就是原文；证据引文是一句话，基本不会被切断。
    遇到标题且当前片段已有一定篇幅时提前切，让片段尽量是完整的一节。
    """
    if len(text) <= target:
        return [text]
    out: list[str] = []
    cur: list[str] = []
    size = 0
    for para in text.split("\n\n"):
        heading = para.lstrip().startswith("#")
        if cur and (size + len(para) > target or (heading and size >= target * 0.4)):
            out.append("\n\n".join(cur))
            cur, size = [], 0
        cur.append(para)
        size += len(para) + 2
    if cur:
        out.append("\n\n".join(cur))
    return out


def _page_rows(page) -> str:
    """按行重排一页：单行文本块按纵向位置聚成行、行内按横向排序，多行段落块原样插回它所在的位置。

    图表的标签、数值、图例多是各自一个单行块，聚成行后「快手  恋爱  34.5%」落在同一行；
    双栏正文是多行块，不会被拦腰拼到隔壁栏。
    """
    singles, blocks = [], []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        lines = [t for t in ("".join(s["text"] for s in ln["spans"]).strip() for ln in b["lines"]) if t]
        if not lines:
            continue
        x0, y0, _, y1 = b["bbox"]
        if len(lines) == 1:
            singles.append(((y0 + y1) / 2, max(y1 - y0, 1.0), x0, lines[0]))
        else:
            blocks.append((y0, "\n".join(lines)))
    rows: list[list[tuple]] = []
    for item in sorted(singles):
        last = rows[-1][-1] if rows else None
        if last and abs(item[0] - last[0]) <= max(item[1], last[1]) * 0.6:
            rows[-1].append(item)
        else:
            rows.append([item])
    placed = [(min(i[0] - i[1] / 2 for i in r), "  ".join(i[3] for i in sorted(r, key=lambda i: i[2]))) for r in rows]
    return "\n".join(text for _, text in sorted(placed + blocks, key=lambda x: x[0]))


def parse_document(path: Path) -> list[PageText]:
    fmt = doc_format(path)
    if fmt == "pdf":
        import pymupdf

        with pymupdf.open(path) as doc:
            return [PageText(page=i + 1, text=_page_rows(page)) for i, page in enumerate(doc)]
    if fmt in ("md", "html"):
        text = path.read_text(encoding="utf-8")
        text = _TAGS.sub(" ", text) if fmt == "html" else text
        return [PageText(page=None, text=t) for t in split_sections(text)]
    if fmt in ("docx", "pptx", "xlsx"):
        return [PageText(page=None, text=t) for t in split_sections(_office_text(path))]
    raise ValueError(f"暂不支持的文档格式：{path.name}")


# 各格式 zip 包里必须有的部分：先认格式再转，不然 MarkItDown 解析失败会退回当纯文本读，把乱码当正文
_OFFICE_PART = {"docx": "word/document.xml", "pptx": "ppt/presentation.xml", "xlsx": "xl/workbook.xml"}


def check_office(path: Path) -> None:
    """Office 文件读得出来才往下走；损坏、加密（加密后不是 zip）、扩展名和内容对不上都报错。"""
    import zipfile

    part = _OFFICE_PART[doc_format(path)]
    try:
        with zipfile.ZipFile(path) as z:
            ok = part in z.namelist()
    except zipfile.BadZipFile:
        ok = False
    if not ok:
        raise ValueError(f"{path.name} 读不出文字（可能损坏、加了密码，或者扩展名和内容对不上）")


def _office_text(path: Path) -> str:
    """Word、PPT、Excel 转成 Markdown 文字（MarkItDown，纯 CPU、不调模型）。表格转成 Markdown 表格，
    PPT 按幻灯片、Excel 按工作表依次排开；去掉转换器插的注释（如幻灯片编号）。没有页码，整篇按段落切页。"""
    from markitdown import MarkItDown

    check_office(path)
    try:
        text = MarkItDown(enable_plugins=False).convert(str(path)).text_content
    except Exception as e:  # noqa: BLE001 - 文件损坏、加密等：说清楚是哪个文件
        raise ValueError(f"{path.name} 读不出文字（可能损坏或加了密码）：{e}") from e
    return re.sub(r"\n{3,}", "\n\n", _COMMENTS.sub("", text)).strip()

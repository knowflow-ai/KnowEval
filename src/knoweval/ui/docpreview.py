"""评测结果里预览原始文档：原文件交给浏览器直接看，另给一份解析后的文本用来高亮引文。"""

from __future__ import annotations

from pathlib import Path

from knoweval.build.refparse import doc_format, parse_document

TEXT_LIMIT = 2_000_000
# 浏览器能直接打开的格式；Office 文件浏览器打不开，只给文本
_MEDIA = {
    ".pdf": "application/pdf",
    ".md": "text/markdown; charset=utf-8",
    ".markdown": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".html": "text/plain; charset=utf-8",  # 按源码显示，不在评测台的页面里执行别人的 HTML
    ".htm": "text/plain; charset=utf-8",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def media_type(path: Path) -> str:
    return _MEDIA.get(path.suffix.lower(), "application/octet-stream")


def doc_preview(path: Path) -> dict:
    """解析后的全文。PDF 带「第 N 页」分隔，和系统引用的页码对得上；文本类按原样给，引文才能逐字找到。"""
    fmt = doc_format(path)
    if fmt == "md":
        text = path.read_text(encoding="utf-8")
    elif fmt == "pdf":
        text = "\n\n".join(f"--- 第 {p.page} 页 ---\n{p.text}" for p in parse_document(path))
    else:
        text = "\n\n".join(p.text for p in parse_document(path))
    return {"name": path.name, "format": fmt, "pages": fmt == "pdf", "text": text[:TEXT_LIMIT],
            "truncated": len(text) > TEXT_LIMIT}

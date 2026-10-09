"""判分前对回答做的统一清洗：去掉引用标记、图片、HTML、地址，免得裁判模型和拒答识别被它们干扰。"""

from __future__ import annotations

import re

_MARKERS = re.compile(r"[\[［]\s*(?:ID|id)\s*[:：]\s*\d+\s*[\]］]|\[\d+\]")
_NOISE = [
    re.compile(r"!\[[^\]]*\]\([^)]*\)"),        # markdown 图片
    re.compile(r"<[^>]+>"),                        # HTML 标签（含 <img src=...>）
    re.compile(r"https?://\S+|/minio/\S+"),       # 地址
]


def strip_noise(text: str) -> str:
    """去掉引用标记、图片、HTML、地址。"""
    text = _MARKERS.sub(" ", text)
    for pattern in _NOISE:
        text = pattern.sub(" ", text)
    return text

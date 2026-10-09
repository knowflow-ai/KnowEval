"""图文答案核对（规范第 6 节「图文答案」）：64 位 dHash 感知哈希，比较回答带出的图与期望的图。

dHash：缩成 9×8 灰度，比较每行相邻像素的明暗，得到 64 位。缩放、轻度压缩、调色后基本不变，
足以判断「是不是同一张图」；只存哈希，不存图片本身。
"""

from __future__ import annotations

import io

from PIL import Image
from pydantic import BaseModel

from knoweval.judge.citation import doc_key
from knoweval.schema import Answer, Question

HAMMING_THRESHOLD = 10


def dhash(data: bytes) -> str:
    img = Image.open(io.BytesIO(data)).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = img.tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


class ImageCheck(BaseModel):
    expected: int
    matched: int
    hit: bool          # 期望的图全部带出
    returned: int      # 回答带出的图（含下载失败的）
    unrelated: int     # 带出的图里与本题无关的


def _close(a: str | None, b: str | None) -> bool:
    return bool(a and b) and hamming(a, b) <= HAMMING_THRESHOLD


def check_images(q: Question, a: Answer) -> ImageCheck:
    want = q.answer_images
    got = a.images
    matched = sum(any(_close(w.phash, g.phash) for g in got) for w in want)
    evidence_pages = {(doc_key(ev.doc), ev.page) for ev in q.evidence}
    unrelated = 0
    for g in got:
        if any(_close(w.phash, g.phash) for w in want):
            continue
        if g.doc and (doc_key(g.doc), g.page) in evidence_pages:
            continue  # 标准证据页上的其它图不算误配，可能只是同页的另一张示意图
        unrelated += 1
    return ImageCheck(expected=len(want), matched=matched, hit=bool(want) and matched == len(want),
                      returned=len(got), unrelated=unrelated)

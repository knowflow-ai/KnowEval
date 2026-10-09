"""回答里带出的图：从正文里找出图片地址，下载后算感知哈希（规范第 6 节「图文答案」）。各适配器共用。"""

from __future__ import annotations

import re

import httpx

from knoweval.images import dhash
from knoweval.schema import ImageRef

_IMG_TAG = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.I)
_MD_IMG = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")
MAX_IMAGES = 10
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def refs_in_text(text: str) -> list[ImageRef]:
    """正文里的 <img> 与 Markdown 图片，按出现顺序。"""
    return [ImageRef(source=src) for src in _IMG_TAG.findall(text or "") + _MD_IMG.findall(text or "")]


def dedupe(refs: list[ImageRef]) -> list[ImageRef]:
    seen, out = set(), []
    for r in refs:
        if r.source not in seen:
            seen.add(r.source)
            out.append(r)
    return out[:MAX_IMAGES]


def _url(source: str, image_base_url: str | None) -> str:
    if source.startswith(("http://", "https://")) or not image_base_url:
        return source
    return image_base_url.rstrip("/") + "/" + source.lstrip("/")


async def hash_ref(client: httpx.AsyncClient, ref: ImageRef, headers: dict[str, str],
                   image_base_url: str | None = None) -> ImageRef:
    """下载一张图算感知哈希。下载失败、不是图片都只记来源、不带哈希，不让整道题出错。

    相对地址按 image_base_url 拼；不给就交给 client 的 base_url。很多系统的图片由网关（如 nginx 的
    /minio/）提供，和问答接口不在同一个端口，拼错了会拿回一张网页，所以要看 Content-Type。
    """
    try:
        resp = await client.get(_url(ref.source, image_base_url), headers=headers)
        kind = resp.headers.get("content-type", "")
        if resp.status_code >= 400 or len(resp.content) > MAX_IMAGE_BYTES or (kind and not kind.startswith("image/")):
            return ref
        return ref.model_copy(update={"phash": dhash(resp.content)})
    except (httpx.HTTPError, OSError, ValueError):
        return ref

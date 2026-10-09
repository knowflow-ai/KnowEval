"""同内容多形态（数据集设计 §6）：扫描版。

把原生 PDF 每页渲染成灰度图片，加轻微倾斜、噪点与 JPEG 压缩，再封装成没有文本层的 PDF。
页数与页码不变，题目原样沿用；原版与扫描版的成绩差就是「扫描」这一个变量的影响。
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import shutil
from pathlib import Path

import pymupdf
from PIL import Image, ImageFilter

from knoweval.dataset import load_dataset


def scan_pdf(src: Path, dst: Path, seed: int, dpi: int = 150) -> None:
    rng = random.Random(f"{seed}-{Path(src).name}")
    out = pymupdf.open()
    with pymupdf.open(src) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
            img = img.rotate(rng.uniform(-1.2, 1.2), resample=Image.Resampling.BICUBIC, fillcolor=255)
            px = img.load()
            for _ in range(img.width * img.height // 400):  # 少量椒盐噪点
                x, y = rng.randrange(img.width), rng.randrange(img.height)
                px[x, y] = rng.choice((0, 255))
            img = img.filter(ImageFilter.GaussianBlur(radius=0.4))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=60)
            new = out.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, stream=buf.getvalue())
    out.set_metadata({"producer": "KnowEval scanned variant", "creator": "KnowEval"})
    out.save(dst, garbage=4, deflate=True)
    out.close()


def make_variant(dataset_dir: Path, kind: str, out: Path, seed: int = 0) -> Path:
    if kind != "scanned":
        raise ValueError(f"未知变体 {kind}，目前只有 scanned")
    ds = load_dataset(dataset_dir)
    if any(f.format != "pdf" for f in ds.manifest.files):
        raise ValueError("扫描版只能从原生 PDF 生成，这个数据集里有非 PDF 文档")
    out = Path(out)
    (out / "docs").mkdir(parents=True, exist_ok=True)
    files = []
    for f in ds.manifest.files:
        dst = out / "docs" / f.name
        scan_pdf(ds.root / "docs" / f.name, dst, seed=seed)
        files.append({**f.model_dump(), "sha256": hashlib.sha256(dst.read_bytes()).hexdigest(), "format": "scanned_pdf",
                      "source_url": None})
    shutil.copyfile(ds.root / "questions.jsonl", out / "questions.jsonl")
    name = f"{ds.manifest.name}-scanned"
    manifest = {**ds.manifest.model_dump(), "name": name, "files": files,
                "canary": ds.manifest.canary.replace(ds.manifest.name, name, 1)}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out

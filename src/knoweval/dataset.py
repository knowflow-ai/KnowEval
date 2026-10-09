"""数据集加载：questions.jsonl + manifest.json + docs/ 下的原始文件。"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from knoweval.schema import Question

_QUOTE_NOISE = re.compile(r"[\s|]+")
_TAGS = re.compile(r"<[^>]+>")


def normalize_for_quote(text: str) -> str:
    """证据比对用的规范化：全角转半角，去掉 HTML 标签、空白与表格竖线。

    表格行在 PDF 抽出的文本里没有竖线、换行位置不固定；MinerU 的表格分块是 <td> 形式的 HTML。
    """
    return _QUOTE_NOISE.sub("", _TAGS.sub("", unicodedata.normalize("NFKC", text)))


DocFormat = Literal["pdf", "scanned_pdf", "docx", "pptx", "xlsx", "html", "md", "image"]


class ManifestFile(BaseModel):
    name: str
    sha256: str
    source_url: str | None = None
    format: DocFormat = "pdf"
    # 图文混排、多栏、图标、统计图表、手写、旋转（规范 §4.1）
    features: list[str] = Field(default_factory=list)
    # False：版权原因不随数据集分发（如上市公司年报），只给清单，用户按 build/ 里的脚本自己下载
    redistribute: bool = True


class Manifest(BaseModel):
    name: str
    version: str
    license: str
    canary: str
    files: list[ManifestFile] = Field(default_factory=list)
    # 评测台新建测试集时写的来历（来源系统与知识库、出题方式），官方数据集没有
    build: dict | None = None


class Dataset(BaseModel):
    root: Path
    manifest: Manifest
    questions: list[Question]

    @property
    def ref(self) -> str:
        return f"{self.manifest.name}@{self.manifest.version}"

    def doc_formats(self) -> dict[str, str]:
        return {f.name: f.format for f in self.manifest.files}

    def doc_paths(self) -> list[Path]:
        return [self.root / "docs" / f.name for f in self.manifest.files]

    def docs_hash(self) -> str:
        """文档集合的哈希：只看清单里每个文件的哈希。入库只取决于文档，题目变了不必重新入库。"""
        h = hashlib.sha256()
        for f in sorted(self.manifest.files, key=lambda f: f.name):
            h.update(f"{f.name}:{f.sha256}\n".encode())
        return h.hexdigest()

    def content_hash(self) -> str:
        """数据集内容哈希：全部题目（含子集筛选）+ 文档集合。续跑与对比时判定是不是同一份数据集。"""
        payload = json.dumps([q.model_dump(mode="json") for q in self.questions], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256((payload + self.docs_hash()).encode()).hexdigest()

    def for_run(self, meta: dict) -> Dataset:
        """按某次运行的 run.json 还原它实际用的题目（那次跑的是子集就取同一个子集）。"""
        return self.subset(meta["subset"]) if meta.get("subset") else self

    def matches_hash(self, recorded: str) -> bool:
        """运行记录里的数据集哈希与这份数据集是否一致。"""
        return recorded == self.content_hash()

    def subset(self, name: str) -> Dataset:
        """子集。smoke：每个主题型（qtype 的第一个）取前 2 题，几分钟跑完，用于 CI 冒烟。"""
        if name != "smoke":
            raise ValueError(f"未知子集 {name}，目前只有 smoke")
        picked, seen = [], {}
        for q in self.questions:
            t = q.qtype[0]
            if seen.get(t, 0) < 2:
                picked.append(q)
                seen[t] = seen.get(t, 0) + 1
        return self.model_copy(update={"questions": picked})


class DatasetIntegrityError(RuntimeError):
    pass


def load_dataset(root: str | Path, verify_files: bool = True) -> Dataset:
    root = Path(root)
    manifest = Manifest.model_validate_json((root / "manifest.json").read_text(encoding="utf-8"))
    questions = [
        Question.model_validate(json.loads(line))
        for line in (root / "questions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    seen: set[str] = set()
    dupes = sorted({q.id for q in questions if q.id in seen or seen.add(q.id)})
    if dupes:
        raise DatasetIntegrityError(f"题号重复：{dupes}")
    if verify_files:
        _verify_files(root, manifest)
    return Dataset(root=root, manifest=manifest, questions=questions)


def _verify_files(root: Path, manifest: Manifest) -> None:
    """文件缺失或哈希不对直接失败：文档一变，题目就可能失效，不能拿别的文件顶替。"""
    for f in manifest.files:
        path = root / "docs" / f.name
        if not path.exists():
            if not f.redistribute:
                raise DatasetIntegrityError(
                    f"缺少文档 {f.name}：它不随数据集分发（版权原因只给清单），先用数据集 build/ 目录里的下载脚本取回，"
                    f"或运行 knoweval dataset pull {root}")
            raise DatasetIntegrityError(f"缺少文档 {f.name}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != f.sha256:
            raise DatasetIntegrityError(f"文档 {f.name} 的哈希与清单不一致")


def find_datasets(paths: list[Path]) -> list[Dataset]:
    """在这些目录下找数据集（含 manifest.json 与 questions.jsonl 的子目录）。不校验文件，缺文件的也列出来。"""
    out = []
    for base in paths:
        base = Path(base)
        if not base.exists():
            continue
        for manifest in sorted(base.glob("*/manifest.json")):
            if (manifest.parent / "questions.jsonl").exists():
                out.append(load_dataset(manifest.parent, verify_files=False))
    return out


class PullResult(BaseModel):
    downloaded: list[str] = Field(default_factory=list)
    present: list[str] = Field(default_factory=list)


async def pull_documents(root: str | Path, client=None) -> PullResult:
    """按清单下载缺失的第三方文档并校验哈希（规范第 9 节：年报、厂商手册只给下载清单，不重新分发）。

    下载到临时文件，哈希对上才改名落地；对不上直接报错，不用别的文件顶替。
    """
    import httpx

    root = Path(root)
    manifest = Manifest.model_validate_json((root / "manifest.json").read_text(encoding="utf-8"))
    (root / "docs").mkdir(exist_ok=True)
    own = client is None
    client = client or httpx.AsyncClient(timeout=120, follow_redirects=True)
    result = PullResult()
    try:
        for f in manifest.files:
            path = root / "docs" / f.name
            if path.exists():
                result.present.append(f.name)
                continue
            if not f.source_url:
                raise DatasetIntegrityError(f"缺少文档 {f.name}，清单里也没有 source_url 可以下载")
            resp = await client.get(f.source_url)
            if resp.status_code >= 400:
                raise DatasetIntegrityError(f"下载 {f.name} 失败：HTTP {resp.status_code}（{f.source_url}）")
            actual = hashlib.sha256(resp.content).hexdigest()
            if actual != f.sha256:
                raise DatasetIntegrityError(
                    f"{f.name} 的哈希与清单不一致（来源文件可能已被更新）：期望 {f.sha256[:12]}…，实际 {actual[:12]}…")
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(resp.content)
            tmp.replace(path)
            result.downloaded.append(f.name)
    finally:
        if own:
            await client.aclose()
    return result

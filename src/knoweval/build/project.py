"""出题项目：一个目录，里面是文档、参考解析结果、候选题与操作日志。所有状态都是文件。"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from knoweval.build.refparse import PageText, doc_format, parse_document
from knoweval.dataset import normalize_for_quote
from knoweval.schema import Evidence, HopKind, NKind, Stage, Trap

Status = Literal["generated", "verified", "rejected_auto", "approved", "edited", "rejected"]


class DocEntry(BaseModel):
    name: str
    sha256: str
    format: str
    pages: int


class Candidate(BaseModel):
    """候选题：字段与正式题目一致，另加状态、自动检查结果与来历。"""

    id: str
    qtype: list[str]
    question: str
    answer: str = ""
    answer_type: Literal["text", "number", "date", "list", "bool"] = "text"
    answer_aliases: list[str] = Field(default_factory=list)
    answer_distractors: list[str] = Field(default_factory=list)
    unit: str | None = None
    tolerance: float | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    hops: int = 1
    hop_kind: HopKind | None = None
    stage: Stage | None = None
    traps: list[Trap] = Field(default_factory=list)
    n_kind: NKind | None = None
    status: Status = "generated"
    generator: str
    checks: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Project:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.meta = json.loads((self.root / "project.json").read_text(encoding="utf-8"))
        self.docs = [DocEntry.model_validate(d) for d in self.meta["docs"]]
        self.pages: dict[str, list[PageText]] = {}
        for line in (self.root / "pages.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            self.pages.setdefault(row["doc"], []).append(PageText(page=row["page"], text=row["text"]))
        path = self.root / "candidates.jsonl"
        self.candidates = [Candidate.model_validate_json(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] \
            if path.exists() else []

    # ---- 文档与证据 ----
    def page_text(self, doc: str, page: int | None) -> str:
        if page is None:  # 没有页码的格式被切成了多段，取整篇时拼回原文
            return "\n\n".join(p.text for p in self.pages.get(doc, []) if p.page is None)
        return next((p.text for p in self.pages.get(doc, []) if p.page == page), "")

    def locate(self, doc: str, quote: str) -> int | None | bool:
        """证据引文在哪一页：返回页码；没有页码的格式返回 None；找不到返回 False。"""
        needle = normalize_for_quote(quote)
        if not needle:
            return False
        for p in self.pages.get(doc, []):
            if needle in normalize_for_quote(p.text):
                return p.page
        return False

    def check_quotes(self, c: Candidate) -> Candidate:
        """逐条核对证据引文，补上页码；有一条找不到就自动打回（规范 §4：quote 必须逐字存在）。"""
        missing = []
        evidence = []
        for ev in c.evidence:
            page = self.locate(ev.doc, ev.quote)
            if page is False:
                missing.append(f"{ev.doc}：{ev.quote[:30]}")
                evidence.append(ev)
            else:
                evidence.append(ev.model_copy(update={"page": page}))
        checks = {**c.checks, "quote_ok": not missing}
        update: dict[str, Any] = {"evidence": evidence, "checks": checks}
        if missing:
            update["status"] = "rejected_auto"
            update["notes"] = c.notes + [f"证据在原文里找不到：{'；'.join(missing)}"]
        return c.model_copy(update=update)

    # ---- 候选题 ----
    def get(self, cid: str) -> Candidate:
        for c in self.candidates:
            if c.id == cid:
                return c
        raise KeyError(f"没有候选题 {cid}")

    def next_id(self, prefix: str) -> str:
        used = {c.id for c in self.candidates}
        i = 1
        while f"{prefix}-{i:04d}" in used:
            i += 1
        return f"{prefix}-{i:04d}"

    def add(self, c: Candidate) -> Candidate:
        if any(x.id == c.id for x in self.candidates):
            raise ValueError(f"候选题 {c.id} 已存在")
        self.candidates.append(c)
        self.save()
        self.log("add", c.id, by=c.generator)
        return c

    def replace(self, c: Candidate) -> None:
        self.candidates = [c if x.id == c.id else x for x in self.candidates]
        self.save()

    def review(self, cid: str, action: Literal["approve", "reject"], by: str, note: str = "") -> Candidate:
        if action not in ("approve", "reject"):
            raise ValueError(f"未知操作 {action!r}，只能是 approve 或 reject")
        if action == "reject" and not note.strip():
            raise ValueError("删除候选题必须写理由")
        c = self.get(cid)
        c = c.model_copy(update={"status": "approved" if action == "approve" else "rejected",
                                 "notes": c.notes + ([note] if note else [])})
        self.replace(c)
        self.log(action, cid, by=by, note=note)
        return c

    def save(self) -> None:
        tmp = self.root / "candidates.jsonl.tmp"
        tmp.write_text("".join(c.model_dump_json() + "\n" for c in self.candidates), encoding="utf-8")
        tmp.replace(self.root / "candidates.jsonl")

    def log(self, action: str, cid: str, by: str, **extra: Any) -> None:
        with (self.root / "log.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": _now(), "action": action, "id": cid, "by": by, **extra}, ensure_ascii=False) + "\n")


def init_project(root: Path, docs_dir: Path, *, name: str, version: str, license: str = "Apache-2.0") -> Project:
    """建出题项目：复制文档、算哈希、做参考解析。"""
    root = Path(root)
    if (root / "project.json").exists():
        raise FileExistsError(f"{root} 已经是出题项目")
    (root / "docs").mkdir(parents=True, exist_ok=True)
    entries, rows = [], []
    for src in sorted(Path(docs_dir).iterdir()):
        if src.is_dir() or src.name.startswith("."):
            continue
        dst = root / "docs" / src.name
        shutil.copyfile(src, dst)
        pages = parse_document(dst)
        entries.append(DocEntry(name=src.name, sha256=hashlib.sha256(dst.read_bytes()).hexdigest(),
                                format=doc_format(dst), pages=len(pages)).model_dump())
        rows += [{"doc": src.name, "page": p.page, "text": p.text} for p in pages]
    (root / "project.json").write_text(json.dumps(
        {"name": name, "version": version, "license": license, "created_at": _now(), "docs": entries},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "pages.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return Project(root)


def load_project(root: Path) -> Project:
    return Project(Path(root))

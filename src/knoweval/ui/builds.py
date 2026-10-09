"""评测台里的测试集：取文档 → 导入题目 / 按题型配比出题 → 自动校验 → 页面上浏览、改题、删掉不好的。

复用出题流水线（knoweval.build）：每个测试集是工作区 builds/<id>/ 下的一个出题项目，后台线程跑完出题与校验。
文档两种来源：页面上传；或从已接入的 KnowFlow / RAGFlow 知识库下载原文（只拉原文，出题仍以原文为准，
证据引文要逐字核对）。评测开始时把可用的题快照成一份数据集（snapshot），之后再改题不影响那次评测。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from knoweval.build.export import export_dataset
from knoweval.build.generators import AUTO, generate
from knoweval.build.project import Project, init_project, load_project
from knoweval.build.refparse import check_office, doc_format
from knoweval.build.verify import verify
from knoweval.judge.llm_judge import OpenAIChatClient
from knoweval.build.project import Candidate
from knoweval.dataset import normalize_for_quote
from knoweval.schema import Evidence, Question
from knoweval.ui.build_items import HUMAN, build_item
from knoweval.ui.import_questions import parse_table
from knoweval.ui.sourcekb import source_kb
from knoweval.ui.workspace import Workspace, WorkspaceError

DEFAULT_MIX = {"F": 6, "T": 4, "L": 4, "C": 3, "M": 3, "K": 3, "N": 3}
OVERSAMPLE = 1.5            # 多出一些抵消自动打回
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
DELETED_NOTE = "页面上删除"
PARSEABLE = {"pdf", "md", "html", "docx", "pptx", "xlsx"}
BUSY = ("importing", "generating", "verifying")   # 出题线程还在跑的状态  # 参考解析认得的格式（txt 归入 md）


def _fact_key(c) -> str | None:
    """同一个事实：标准答案相同、证据引文也相同。题干换个说法仍是同一道题。无答案题没有证据，不算。"""
    if not c.evidence or not c.answer:
        return None
    quotes = sorted(normalize_for_quote(ev.quote) for ev in c.evidence)
    return "fact:" + normalize_for_quote(f"{c.answer}{c.unit or ''}") + "|" + "|".join(quotes)


def dedupe(project: Project) -> int:
    """题干一样（去掉空白与标点后相同），或考的是同一个事实（答案与证据引文都相同）的候选题只留第一道，
    其余自动打回并写明与哪道重复。导入的、手动加的题是用户自己的，优先保留：模型出的题和它们重复就打回。"""
    seen: dict[str, str] = {}
    dropped = 0
    for c in project.candidates:
        if c.generator in (IMPORTED, HUMAN) and c.status in USABLE:
            keys = [k for k in (re.sub(r"[\s，。？?！!、：:；;,.]", "", c.question), _fact_key(c)) if k]
            seen.update({k: c.id for k in keys if k not in seen})
    for c in project.candidates:
        if c.status not in ("generated", "verified"):
            continue
        keys = [k for k in (re.sub(r"[\s，。？?！!、：:；;,.]", "", c.question), _fact_key(c)) if k]
        dup = next((seen[k] for k in keys if k in seen), None)
        if dup:
            project.replace(c.model_copy(update={"status": "rejected_auto", "notes": c.notes + [f"与 {dup} 重复"]}))
            project.log("dedupe", c.id, by="auto", duplicate_of=dup)
            dropped += 1
        else:
            seen.update(dict.fromkeys(keys, c.id))
    return dropped


class BuildMeta(BaseModel):
    id: str
    name: str
    version: str = "0.1"
    source: dict
    mix: dict[str, int]
    status: Literal["importing", "generating", "verifying", "ready", "failed"] = "importing"
    error: str | None = None
    skipped: list[str] = Field(default_factory=list)   # 不支持的格式，没进项目
    created_at: str
    # 导入已有题目：多少行导进来了、哪些行用不了及原因
    import_report: dict | None = None
    # 出完题要开始的评测（新建评测时的勾选与设置）；开始后记下 eval_id，失败记下 error
    after_ready: dict | None = None
    # 上一轮校验时模型调用失败（多半是超时）、还没校验完的题数；「重新校验」只补这些
    verify_errors: int = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(name: str) -> str:
    """文件名只取最后一段并去掉路径分隔与控制字符，防止上传或下载的文件名逃出目录。"""
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f]", "", name).strip().lstrip(".")
    return name or f"doc-{secrets.token_hex(3)}"


IMPORTED = "import"
_IMPORT_STAGE = {"F": "retrieve", "T": "retrieve", "L": "chunk", "N": "honesty"}


USABLE = ("verified", "approved", "edited")   # 可用的题：通过自动校验、人工出题、人工改过的


def _review_text(candidates) -> str:
    """写进数据集清单的来历：导入、人工出、大模型出各几题。"""
    usable = [c for c in candidates if c.status in USABLE]
    imported = sum(c.generator == IMPORTED for c in usable)
    human = sum(c.generator == HUMAN for c in usable)
    model = len(usable) - imported - human
    parts = [f"导入题目 {imported} 题" if imported else "", f"人工出题 {human} 题" if human else "",
             f"大模型出题 {model} 题（自动校验）" if model else ""]
    return "；".join(x for x in parts if x)


class BuildService:
    def __init__(self, workspace: Workspace, llm=None):
        """llm：返回 (客户端, 出题模型, 闭卷模型列表) 的函数，测试时注入假模型；默认读设置页。"""
        self.ws = workspace
        self._llm = llm
        self.root = workspace.root / "builds"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        # 出完题后开始评测的回调（UiApi 挂上）：收到构建元数据，返回评测编号
        self.on_ready = None
        # 正在跑的出题任务（事件循环、任务），停止出题时从别的线程取消它
        self._active: tuple[asyncio.AbstractEventLoop, asyncio.Task] | None = None
        self._stop_requested: str | None = None
        self._mark_interrupted()

    def _mark_interrupted(self) -> None:
        """评测台重启前正在出题的测试集：线程已经没了，标成中断，可以删除或重新出题。"""
        for path in sorted(self.root.glob("bd-*/build.json")):
            meta = BuildMeta.model_validate_json(path.read_text(encoding="utf-8"))
            if meta.status in BUSY:
                self._save(meta.model_copy(update={"status": "failed", "error": "评测台重启，出题中断：可以删除，或重新出题"}))

    # ---------- 读写元数据 ----------

    def _dir(self, build_id: str) -> Path:
        if not re.fullmatch(r"bd-[0-9a-f]{6}", build_id):
            raise WorkspaceError("没有这个测试集构建")
        return self.root / build_id

    def load(self, build_id: str) -> BuildMeta:
        path = self._dir(build_id) / "build.json"
        if not path.exists():
            raise WorkspaceError("没有这个测试集构建")
        return BuildMeta.model_validate_json(path.read_text(encoding="utf-8"))

    def _save(self, meta: BuildMeta) -> None:
        path = self._dir(meta.id) / "build.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(meta.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

    def _update(self, build_id: str, **fields) -> BuildMeta:
        with self._lock:
            meta = self.load(build_id).model_copy(update=fields)
            self._save(meta)
            return meta

    def list(self) -> list[dict]:
        out = []
        for path in sorted(self.root.glob("bd-*/build.json")):
            meta = BuildMeta.model_validate_json(path.read_text(encoding="utf-8"))
            out.append({**meta.model_dump(mode="json"), **self._counts(meta.id), "source_kb": self.source_kb(meta)})
        return sorted(out, key=lambda m: m["created_at"], reverse=True)

    def source_kb(self, meta: BuildMeta) -> dict | None:
        return source_kb(meta.source)

    def project(self, build_id: str) -> Project | None:
        root = self._dir(build_id) / "project"
        return load_project(root) if (root / "project.json").exists() else None

    def _counts(self, build_id: str) -> dict:
        p = self.project(build_id)
        if p is None:
            return {"docs": 0, "candidates": 0, "passed": 0, "rejected_auto": 0, "deleted": 0, "confirmed": 0, "pending_verify": 0}
        cs = p.candidates
        return {
            "docs": len(p.docs),
            "candidates": len(cs),
            "passed": sum(c.status in ("verified", "approved", "edited") for c in cs),
            "rejected_auto": sum(c.status == "rejected_auto" for c in cs),
            "deleted": sum(c.status == "rejected" for c in cs),
            "confirmed": sum(c.status in ("approved", "edited") for c in cs),
            "pending_verify": sum(c.status == "generated" for c in cs),
        }

    def detail(self, build_id: str) -> dict:
        meta = self.load(build_id)
        p = self.project(build_id)
        target = sum(max(n, round(n * OVERSAMPLE)) for n in meta.mix.values())
        return {
            **meta.model_dump(mode="json"), **self._counts(build_id), "target": target,
            "source_kb": self.source_kb(meta),
            "running": self.running_id() == build_id,
            "docs_list": [d.model_dump(mode="json") for d in p.docs] if p else [],
            "items": [c.model_dump(mode="json") for c in p.candidates] if p else [],
        }

    def running_id(self) -> str | None:
        t = self._thread
        return t.name.removeprefix("knoweval-build-") if t and t.is_alive() else None

    # ---------- 创建 ----------

    def _new(self, name: str, version: str, source: dict, mix: dict[str, int], after_ready: dict | None = None) -> BuildMeta:
        name = name.strip()
        if not name:
            raise WorkspaceError("给测试集起个名字")
        if re.search(r"[\\/]", name):
            raise WorkspaceError("名字里不能有斜杠")
        bad = set(mix) - AUTO
        if bad:
            raise WorkspaceError(f"这些题型还不能自动生成：{sorted(bad)}")
        mix = {t: int(n) for t, n in mix.items() if int(n) > 0}   # 全为 0：只导入文档，题目全部人工出
        if self.running_id():
            raise WorkspaceError("已有测试集在生成，等它完成后再开始")
        meta = BuildMeta(id=f"bd-{secrets.token_hex(3)}", name=name, version=version or "0.1", source=source,
                         mix=mix, created_at=_now(), after_ready=after_ready)
        (self._dir(meta.id) / "src").mkdir(parents=True)
        self._save(meta)
        return meta

    def create_from_files(self, name: str, version: str, files: list[tuple[str, bytes]], mix: dict[str, int],
                          after_ready: dict | None = None, table: tuple[str, bytes] | None = None) -> BuildMeta:
        """table：同时导入自己的题目（CSV/JSONL），先导入，再按 mix 让模型补题，放在同一套里。"""
        if not files:
            raise WorkspaceError("至少上传一份文档")
        rows = self._parse_import(table)
        if sum(len(b) for _, b in files) > MAX_UPLOAD_BYTES:
            raise WorkspaceError("上传的文档合计超过 200 MB")
        meta = self._new(name, version, {"kind": "upload", "files": len(files)}, mix, after_ready=after_ready)
        self._save_import(meta.id, rows)
        src = self._dir(meta.id) / "src"
        for fname, data in files:
            (src / _safe_name(fname)).write_bytes(data)
        self._start(meta.id, download=None)
        return self.load(meta.id)

    @staticmethod
    def _parse_import(table: tuple[str, bytes] | None) -> tuple[list[dict], list[dict]] | None:
        """附带导入的题目文件：先解析，一行都用不了就在建测试集之前报错。"""
        if not table:
            return None
        rows, problems = parse_table(*table)
        if not rows:
            reasons = "；".join(f"第 {p['row']} 行：{p['reason']}" for p in problems[:5])
            raise WorkspaceError(f"题目文件里没有一行能用：{reasons or '文件是空的'}")
        return rows, problems

    def _save_import(self, build_id: str, parsed: tuple[list[dict], list[dict]] | None) -> None:
        """出题流水线建好项目后先导入这些题（import.json），再按配比让模型补题。"""
        if not parsed:
            return
        rows, problems = parsed
        (self._dir(build_id) / "import.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        self._update(build_id, import_report={"rows": len(rows) + len(problems), "imported": 0, "problems": problems})

    def create_from_import(self, name: str, version: str, table: tuple[str, bytes], docs: list[tuple[str, bytes]]) -> BuildMeta:
        """导入已有题目（CSV/JSONL）。docs 是题目依据的文档，可以不给；给了会逐字核对引文。不调模型。"""
        rows, problems = parse_table(*table)
        if not rows:
            reasons = "；".join(f"第 {p['row']} 行：{p['reason']}" for p in problems[:5])
            raise WorkspaceError(f"题目文件里没有一行能用：{reasons or '文件是空的'}")
        if sum(len(b) for _, b in docs) > MAX_UPLOAD_BYTES:
            raise WorkspaceError("上传的文档合计超过 200 MB")
        meta = self._new(name, version, {"kind": "import", "files": len(docs), "rows": len(rows) + len(problems)}, {})
        src = self._dir(meta.id) / "src"
        for fname, data in docs:
            (src / _safe_name(fname)).write_bytes(data)
        (self._dir(meta.id) / "import.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        self._update(meta.id, import_report={"rows": len(rows) + len(problems), "imported": 0, "problems": problems})
        self._start(meta.id, download=None)
        return self.load(meta.id)

    def _add_imported(self, build_id: str, project: Project, rows: list[dict] | None = None) -> None:
        """把导入的题加进项目。rows 不给就读建测试集时存的 import.json；给了是往已有测试集里追加的一批。"""
        appending = rows is not None
        rows = rows if appending else json.loads((self._dir(build_id) / "import.json").read_text(encoding="utf-8"))
        meta = self.load(build_id)
        for r in rows:
            notes, evidence = [], []
            if r["qtype"] != "N" and r["doc"] and r["quote"]:
                page = project.locate(r["doc"], r["quote"]) if r["doc"] in project.pages else False
                if page is False:
                    notes.append(f"第 {r['row']} 行的引文在文档「{r['doc']}」里没逐字找到，已去掉证据；题目照常评测，只是不查引用")
                else:
                    evidence = [Evidence(doc=r["doc"], quote=r["quote"], page=page)]
            project.add(Candidate(
                id=project.next_id("i"), qtype=[r["qtype"]], question=r["question"], answer=r["answer"],
                answer_type=r["answer_type"], unit=r["unit"], tolerance=r["tolerance"], answer_aliases=r["answer_aliases"],
                evidence=evidence, hops=max(1, len({e.doc for e in evidence})), stage=_IMPORT_STAGE.get(r["qtype"], "reason"),
                status="approved", generator=IMPORTED, checks={"quote_ok": bool(evidence)}, notes=notes))
        report = meta.import_report or {}
        if not appending:
            self._update(build_id, import_report={**report, "imported": len(rows)})

    def create_from_kb(self, name: str, version: str, system_id: str, kb_ids: list[str], mix: dict[str, int],
                       kb_names: dict[str, str] | None = None, after_ready: dict | None = None,
                       table: tuple[str, bytes] | None = None) -> BuildMeta:
        """从已接入系统的一个或多个知识库取原文。kb_names 是页面已知的库名，取文档前就能显示。
        table：同时导入自己的题目，先导入，再按 mix 让模型补题。"""
        kb_ids = [k for k in dict.fromkeys(kb_ids) if k]   # 去重、保序
        rows = self._parse_import(table)
        if not kb_ids:
            raise WorkspaceError("至少选一个知识库")
        system = next((s for s in self.ws.systems() if s.id == system_id), None)
        if system is None or system.adapter not in ("knowflow", "ragflow"):
            raise WorkspaceError("只能从已接入的 KnowFlow 或 RAGFlow 知识库取文档")
        api_key = os.environ.get(system.api_key_env)
        if not api_key:
            raise WorkspaceError(f"环境变量 {system.api_key_env} 没有设置")
        # kb_names：取完文档前就能显示库名（取完后 source.kbs 带上文档数）
        meta = self._new(name, version, {"kind": "kb", "system": system.name, "system_id": system.id, "kb_ids": kb_ids,
                                         "kb_names": {k: (kb_names or {}).get(k) or k for k in kb_ids}}, mix,
                         after_ready=after_ready)
        self._save_import(meta.id, rows)
        self._start(meta.id, download=(system.base_url, api_key, kb_ids))
        return self.load(meta.id)

    # ---------- 后台：取文档 → 出题 → 校验 ----------

    def _start(self, build_id: str, download: tuple[str, str, list[str]] | None) -> None:
        t = threading.Thread(target=self._run, args=(build_id, download), name=f"knoweval-build-{build_id}", daemon=True)
        self._thread = t
        t.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run_cancellable(self, build_id: str, coro):
        """在自己的事件循环里跑出题协程，记下任务，stop() 能从别的线程取消。"""
        loop = asyncio.new_event_loop()
        try:
            task = loop.create_task(coro)
            self._active = (loop, task)
            if self._stop_requested == build_id:   # 还没开始跑就被要求停
                task.cancel()
            return loop.run_until_complete(task)
        finally:
            self._active = None
            self._stop_requested = None
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def stop(self, build_id: str) -> None:
        """停止出题：取消正在跑的出题或校验。已出的候选题留着，测试集标成失败，可以删除。"""
        if self.running_id() != build_id:
            raise WorkspaceError("这个测试集没有在出题")
        self._stop_requested = build_id
        active = self._active
        if active:
            loop, task = active
            loop.call_soon_threadsafe(task.cancel)

    def _run(self, build_id: str, download: tuple[str, str, list[str]] | None) -> None:
        try:
            self._run_cancellable(build_id, self._pipeline(build_id, download))
            self._start_after_ready(build_id)
        except asyncio.CancelledError:
            self._update(build_id, status="failed", error="已停止出题：可以删除，或重新出题")
        except Exception as e:  # noqa: BLE001 - 任何失败都写回 build.json，页面上能看到原因
            error = f"{type(e).__name__}: {e}"[:500]
            self._update(build_id, status="failed", error=error)

    # ---------- 已有测试集继续加题 ----------

    def _check_idle(self, build_id: str) -> BuildMeta:
        meta = self.load(build_id)
        if self.running_id():
            raise WorkspaceError("已有测试集在生成，等它完成后再加题")
        if meta.status != "ready":
            raise WorkspaceError("测试集还没出完题，或者出题失败了，不能加题")
        return meta

    def import_more(self, build_id: str, table: tuple[str, bytes]) -> BuildMeta:
        """往已有的测试集里再导入一批题（CSV/JSONL）。不调模型；带了引文的到文档里逐字核对。"""
        meta = self._check_idle(build_id)
        rows, problems = self._parse_import(table)
        project = self.project(build_id)
        if project is None:
            raise WorkspaceError("这套测试集还没有项目，不能加题")
        self._add_imported(build_id, project, rows)
        report = meta.import_report or {}
        return self._update(build_id, import_report={
            "rows": report.get("rows", 0) + len(rows) + len(problems), "imported": report.get("imported", 0) + len(rows),
            "problems": report.get("problems", []) + problems})

    def generate_more(self, build_id: str, mix: dict[str, int]) -> BuildMeta:
        """让模型按配比再补一批题。只校验新出的题；补的题和已有的题重复会被打回（导入的、手动加的优先保留）。"""
        self._check_idle(build_id)
        mix = {t: int(n) for t, n in (mix or {}).items() if int(n) > 0}
        if not mix:
            raise WorkspaceError("选至少一种题型和数量")
        bad = set(mix) - AUTO
        if bad:
            raise WorkspaceError(f"这些题型还不能自动生成：{sorted(bad)}")
        project = self.project(build_id)
        if project is None or not project.docs:
            raise WorkspaceError("这套题没有文档，不能让模型补题：只导入了题目的测试集请先在新建时上传文档")
        meta = self._update(build_id, status="generating", error=None)
        t = threading.Thread(target=self._more_run, args=(build_id, mix), name=f"knoweval-build-{build_id}", daemon=True)
        self._thread = t
        t.start()
        return meta

    def _more_run(self, build_id: str, mix: dict[str, int]) -> None:
        async def run() -> dict[str, int]:
            client, model, closed = self._models()
            project = load_project(self._dir(build_id) / "project")
            before = {c.id for c in project.candidates}
            oversampled = {t: max(n, round(n * OVERSAMPLE)) for t, n in mix.items()}
            await generate(project, client, model, oversampled, seed=int(build_id[3:], 16) + len(before))
            dedupe(project)
            new = {c.id for c in project.candidates} - before
            self._update(build_id, status="verifying")
            return await verify(project, closed_book=[(client, m) for m in closed], absence=(client, model),
                                open_book=(client, model), only=new)
        try:
            stats = self._run_cancellable(build_id, run())
        except asyncio.CancelledError:
            self._update(build_id, status="ready", error="已停止补题：已经出的题留着，没校验完的还在待校验")
            return
        except Exception as e:  # noqa: BLE001 - 补题失败不影响原来的题，测试集照常可用
            self._update(build_id, status="ready", error=f"补题失败：{type(e).__name__}: {e}"[:500])
            return
        self._update(build_id, status="ready", verify_errors=stats["errors"], error=None)

    def reverify(self, build_id: str) -> BuildMeta:
        """重新校验：只补上一轮因模型调用失败没校验完的题，补完后接着开始当初要的评测。"""
        meta = self.load(build_id)
        if meta.status != "ready":
            raise WorkspaceError("测试集还在生成，或者生成失败了，不能重新校验")
        if self.running_id():
            raise WorkspaceError("已有测试集在生成，等它完成后再重新校验")
        pending = self.detail(build_id)["pending_verify"]
        if not pending:
            raise WorkspaceError("没有待校验的题")
        plan = meta.after_ready and {**meta.after_ready, "error": None}
        meta = self._update(build_id, status="verifying", after_ready=plan)
        t = threading.Thread(target=self._reverify_run, args=(build_id,), name=f"knoweval-build-{build_id}", daemon=True)
        self._thread = t
        t.start()
        return meta

    def _reverify_run(self, build_id: str) -> None:
        async def run() -> dict[str, int]:
            client, model, closed = self._models()
            project = load_project(self._dir(build_id) / "project")
            # 只补没校验完的题（模型出的、还在待校验）；用户改过的题不重新校验，免得被自动打回
            pending = {c.id for c in project.candidates if c.status == "generated"}
            return await verify(project, closed_book=[(client, m) for m in closed], absence=(client, model),
                                open_book=(client, model), only=pending)
        try:
            stats = self._run_cancellable(build_id, run())
        except asyncio.CancelledError:
            self._update(build_id, status="ready", error="已停止重新校验：没校验完的题还在待校验")
            return
        except Exception as e:  # noqa: BLE001 - 失败写回测试集；题目保持原状，可以再试
            self._update(build_id, status="ready", error=f"重新校验失败：{type(e).__name__}: {e}"[:500])
            return
        self._update(build_id, status="ready", verify_errors=stats["errors"], error=None)
        self._start_after_ready(build_id)

    def _start_after_ready(self, build_id: str) -> None:
        """出完题开始当初要的那次评测。开不起来（助手被删了、题全被打回）记下原因，测试集本身照常可用。"""
        meta = self.load(build_id)
        plan = meta.after_ready
        if not plan or plan.get("eval_id") or meta.status != "ready" or self.on_ready is None:
            return
        if meta.verify_errors:
            # 题没校验完就开评测，题目不全；等重新校验补完再开
            self._update(build_id, after_ready={**plan, "error": f"有 {meta.verify_errors} 道题校验时模型调用失败（多半是超时），"
                                                                    "还没开始评测：点「重新校验」补完后会自动开始"})
            return
        try:
            result = self.on_ready(meta)
            started = result if isinstance(result, dict) else {"eval_id": result}
            self._update(build_id, after_ready={**plan, **started, "error": None})
        except Exception as e:  # noqa: BLE001 - 失败写回测试集，页面上提示后手动开始
            with contextlib.suppress(Exception):   # 连写回都失败时，也不能把已就绪的测试集标成失败
                self._update(build_id, after_ready={**plan, "error": f"没能自动开始评测：{e}"[:500]})

    async def _pipeline(self, build_id: str, download: tuple[str, str, list[str]] | None) -> None:
        src = self._dir(build_id) / "src"
        if download:
            kbs = await self._download_kbs(src, *download)
            self._update(build_id, source={**self.load(build_id).source, "kbs": kbs})
        skipped = []
        for f in list(src.iterdir()):
            try:
                fmt = doc_format(f)
                ok = fmt in PARSEABLE
                if ok and fmt in ("docx", "pptx", "xlsx"):
                    check_office(f)     # 损坏、加密的 Office 文件跳过，不让整套题失败
            except ValueError:
                ok = False
            if not ok:  # 出题要逐字核对引文，只收参考解析认得的格式（扫描版 PDF 没有文字层，出不了题）
                skipped.append(f.name)
                f.unlink()
        importing = self.load(build_id).source.get("kind") == "import"
        if not any(src.iterdir()) and not importing:
            raise WorkspaceError(f"没有可出题的文档（目前支持 PDF、Word、PPT、Excel、Markdown、TXT、HTML）；跳过了：{skipped}")
        meta = self._update(build_id, skipped=skipped)
        # 用户自己的文档：许可不能套公开数据集的默认值
        project = init_project(self._dir(build_id) / "project", src, name=meta.name, version=meta.version,
                               license="私有文档，仅内部使用（版权归文档所有者）")
        if (self._dir(build_id) / "import.json").exists():   # 导入的题（单独导入，或和模型出题一起）
            self._add_imported(build_id, project)
        if not meta.mix:
            self._update(build_id, status="ready")
            return
        client, model, closed = self._models()
        self._update(build_id, status="generating")
        mix = {t: max(n, round(n * OVERSAMPLE)) for t, n in meta.mix.items()}
        await generate(project, client, model, mix, seed=int(build_id[3:], 16))
        dedupe(project)
        self._update(build_id, status="verifying")
        stats = await verify(project, closed_book=[(client, m) for m in closed], absence=(client, model), open_book=(client, model))
        self._update(build_id, status="ready", verify_errors=stats["errors"])

    def _models(self) -> tuple[OpenAIChatClient, str, list[str]]:
        if self._llm:
            return self._llm()
        s = self.ws.settings()
        if not s.model or not s.base_url:
            raise WorkspaceError("先在「设置」页配好模型：出题与校验都用它")
        key = s.api_key()
        if not key:
            raise WorkspaceError(f"环境变量 {s.api_key_env} 没有设置")
        # 只用设置页里那一个模型：不看文档也答对就剔除。误删的题可以在页面上恢复
        # 本地部署的大模型读长原文（开卷复答最多 1.2 万字）常要一两分钟，给足时间
        return s.client(key, timeout_s=300), s.model, [s.model]

    @staticmethod
    async def _download_kbs(dest: Path, base_url: str, api_key: str, kb_ids: list[str]) -> list[dict]:
        """依次下载各知识库的原文。内容相同的文档只留一份；不同库里同名但内容不同的，后来的加序号避免覆盖。

        返回每个库的名字与文档数，写进来源记录。
        """
        headers = {"Authorization": f"Bearer {api_key}"}
        seen_hash: dict[str, str] = {}
        summary = []
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=120) as client:
            names = {}
            listing = (await client.get("/api/v1/datasets", params={"page": 1, "page_size": 1000}, headers=headers)).json()
            if listing.get("code") == 0:
                names = {d["id"]: d["name"] for d in listing.get("data") or []}
            for kb_id in kb_ids:
                body = (await client.get(f"/api/v1/datasets/{kb_id}/documents", params={"page": 1, "page_size": 1000},
                                         headers=headers)).json()
                if body.get("code") != 0:
                    raise WorkspaceError(f"取知识库 {names.get(kb_id, kb_id)} 的文档列表失败：{body.get('message')}")
                docs = body["data"].get("docs", [])
                kept = 0
                for d in docs:
                    r = await client.get(f"/api/v1/datasets/{kb_id}/documents/{d['id']}", headers=headers)
                    if r.status_code >= 400 or r.headers.get("content-type", "").startswith("application/json"):
                        raise WorkspaceError(f"下载 {d.get('name')} 失败：{r.text[:200]}")
                    digest = hashlib.sha256(r.content).hexdigest()
                    if digest in seen_hash:      # 同一份文档出现在多个库里
                        continue
                    base = Path(_safe_name(d.get("name") or d["id"]))
                    target, n = dest / base.name, 2
                    while target.exists():       # 同名不同内容：保留原名，依次加 -2、-3
                        target, n = dest / f"{base.stem}-{n}{base.suffix}", n + 1
                    target.write_bytes(r.content)
                    seen_hash[digest] = target.name
                    kept += 1
                summary.append({"id": kb_id, "name": names.get(kb_id, kb_id), "documents": len(docs), "kept": kept})
        if not seen_hash:
            raise WorkspaceError("选中的知识库里没有文档")
        return summary

    # ---------- 浏览与删题 ----------

    def review(self, build_id: str, cid: str, action: str) -> dict:
        """页面上的操作：delete 删掉（不进数据集）、restore 恢复、confirm 人工确认（可选）。"""
        meta = self.load(build_id)
        p = self.project(build_id)
        if p is None:
            raise WorkspaceError("项目还没建好")
        c = p.get(cid)
        if action == "delete":
            if c.status == "rejected_auto":
                raise WorkspaceError("自动打回的题本来就不进数据集")
            p.review(cid, "reject", by="ui", note=DELETED_NOTE)
        elif action == "restore":
            if c.status == "rejected":
                p.replace(c.model_copy(update={"status": "verified", "notes": [n for n in c.notes if n != DELETED_NOTE]}))
            elif c.status == "rejected_auto":
                # 自动校验会误杀（如开卷复答时模型读错图表）：人看过觉得没问题就恢复，打回原因留着备查
                try:
                    Question.model_validate({**c.model_dump(include=set(Question.model_fields)), "domain": "check"})
                except ValidationError as e:
                    raise WorkspaceError(f"这道题格式不合规，先修改再恢复：{e.errors()[0]['msg']}") from e
                p.replace(c.model_copy(update={"status": "verified", "notes": c.notes + ["人工恢复"]}))
            else:
                raise WorkspaceError("只有删掉的题和自动打回的题可以恢复")
            p.log("restore", cid, by="ui")
        elif action == "confirm":
            if c.status not in ("verified", "approved"):
                raise WorkspaceError("只有通过校验的题可以确认")
            p.review(cid, "approve", by="ui")
        else:
            raise WorkspaceError(f"未知操作：{action}")
        return p.get(cid).model_dump(mode="json")

    def review_many(self, build_id: str, ids: list[str], action: str) -> dict:
        """批量删除 / 恢复 / 确认。状态不合适的题跳过并写明原因，不影响其它题。"""
        if action not in ("delete", "restore", "confirm"):
            raise WorkspaceError(f"未知操作：{action}")
        done, skipped = 0, []
        for cid in dict.fromkeys(ids):
            try:
                self.review(build_id, cid, action)
                done += 1
            except (WorkspaceError, KeyError) as e:
                skipped.append({"id": cid, "reason": str(e).strip("'")})
        return {"done": done, "skipped": skipped}

    def save_item(self, build_id: str, cid: str | None, data: dict) -> dict:
        """人工出题（cid 为空）或修改一道题。"""
        meta = self.load(build_id)
        if meta.status != "ready":
            raise WorkspaceError("出题和校验完成后才能人工出题或改题")
        p = self.project(build_id)
        existing = p.get(cid) if cid else None
        item = build_item(p, data, existing)
        if existing is None:
            p.add(item)
        else:
            p.replace(item)
            p.log("edit", item.id, by="ui")
        return item.model_dump(mode="json")

    def doc_text(self, build_id: str, name: str) -> dict:
        p = self.project(build_id)
        if p is None or name not in {d.name for d in p.docs}:
            raise WorkspaceError("项目里没有这份文档")
        return {"name": name, "text": p.page_text(name, None) if p.pages[name][0].page is None
                else "\n\n".join(f"--- 第 {x.page} 页 ---\n{x.text}" for x in p.pages[name])}

    # ---------- 快照 ----------

    def snapshot(self, build_id: str, out: Path) -> Path:
        """把测试集当前可用的题目连同文档存成一份数据集（评测开始时调用）。之后再改题不影响这份快照。"""
        meta = self.load(build_id)
        if meta.status != "ready":
            raise WorkspaceError("测试集还在出题，出完再评测" if meta.status != "failed" else "测试集出题失败了，换一套题再评测")
        p = self.project(build_id)
        if not any(c.status in USABLE for c in p.candidates):
            raise WorkspaceError("这个测试集没有可用的题：在测试集页加题或恢复删掉的题")
        if out.exists():
            shutil.rmtree(out)
        export_dataset(p, out, all_docs=meta.source.get("kind") == "import", statuses=USABLE)
        manifest_path = out / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["build"] = {"review": _review_text(p.candidates), "source": meta.source, "mix": meta.mix, "build_id": meta.id}
        if any(c.status in USABLE and c.generator not in (HUMAN, IMPORTED) for c in p.candidates):
            manifest["build"]["generator_model"] = self.ws.settings().model
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return out

    def delete(self, build_id: str) -> None:
        """删掉测试集（题目、文档、记录）。已有评测各自存了快照，不受影响。"""
        if self.running_id() == build_id:
            raise WorkspaceError("正在出题，等它完成后再删除")
        shutil.rmtree(self._dir(build_id))

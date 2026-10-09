"""评测台后端：本机单人使用的 HTTP 服务，只监听 127.0.0.1。

页面壳子复用 web/ 打包出的模板（kind=ui），数据全走 /api；评测由 EvalExecutor 在后台线程跑，
进度从运行目录的文件里读（见 progress.py），所以服务重启也不丢状态。
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from knoweval.adapters.base import AdapterError
from knoweval.dataset import load_dataset
from knoweval.report_html import build_compare_data, build_report_data, render_html
from knoweval.ui.builds import DEFAULT_MIX, BuildService
from knoweval.ui.docpreview import doc_preview, media_type
from knoweval.ui.executor import EvalExecutor
from knoweval.ui.progress import participant_progress
from knoweval.ui.sourcekb import source_kb
from knoweval.ui.workspace import CHAT_PICK, Workspace, WorkspaceError


def dataset_info(ds, path: str) -> dict:
    domains: dict[str, int] = {}
    qtypes: dict[str, int] = {}
    for q in ds.questions:
        domains[q.domain] = domains.get(q.domain, 0) + 1
        t = q.qtype[0] if q.qtype else "?"
        qtypes[t] = qtypes.get(t, 0) + 1
    return {"ref": ds.ref, "name": ds.manifest.name, "version": ds.manifest.version, "path": path,
            "license": ds.manifest.license, "docs": len(ds.manifest.files), "questions": len(ds.questions),
            "formats": sorted({f.format for f in ds.manifest.files}), "domains": domains, "qtypes": qtypes}


def relabel_compare(result: dict, names: dict[str, str]) -> dict:
    """compare 的系统名取自运行目录名（slug）；按参与者映射回显示名再给前端。"""
    def nm(x: str) -> str:
        return names.get(x, x)

    systems = [{**s, "name": nm(s["name"])} for s in result["systems"]]
    pairs = [{**p, "first": nm(p["first"]), "second": nm(p["second"])} for p in result["pairs"]]
    groups = {("only_" + nm(k[5:]) if k.startswith("only_") else k): v for k, v in result["groups"].items()}
    matrix = {nm(k): v for k, v in result["matrix"].items()}
    return {**result, "systems": systems, "pairs": pairs, "groups": groups, "matrix": matrix}


class UiApi:
    """路由之外的业务；单独成类方便直接在测试里调。"""

    def __init__(self, workspace: Workspace, executor: EvalExecutor | None = None, builds: BuildService | None = None):
        self.ws = workspace
        self.executor = executor or EvalExecutor(workspace)
        self.builds = builds or BuildService(workspace)
        # 为某次评测出的题（如从助手的知识库出题）：出完自动开始那次评测
        self.builds.on_ready = self._eval_after_build

    # ---------- 读 ----------

    def state(self) -> dict:
        evals = []
        for meta in self.ws.list_evals():
            headline = {}
            for p in meta.participants:
                summary = self.ws.run_dir(meta.id, p.id) / "summary.json"
                if summary.exists():
                    first = (json.loads(summary.read_text(encoding="utf-8")).get("repeats") or [{}])[0]
                    headline[p.id] = (first.get("accuracy") or {}).get("value")
            evals.append({**meta.model_dump(mode="json"), "headline": headline})
        return {"running": self.executor.running_id(), "evals": evals}

    def datasets(self) -> list[dict]:
        evals = self.ws.list_evals()
        out = []
        for ds in self.ws.datasets():
            path = self.ws.dataset_path_str(ds)
            out.append({**dataset_info(ds, path), "used_by": [e.name for e in evals if e.dataset_path == path],
                        "source_kb": source_kb((ds.manifest.build or {}).get("source"))})
        return out

    def _dataset(self, path: str):
        """只认工作区里能发现的数据集，防止借路径读到别处的文件。"""
        for ds in self.ws.datasets():
            if self.ws.dataset_path_str(ds) == path:
                return ds
        raise WorkspaceError("没有这个数据集")

    def dataset_detail(self, path: str) -> dict:
        ds = self._dataset(path)
        info = dataset_info(ds, path)
        files = [f.model_dump(mode="json") for f in ds.manifest.files]
        for f in files:
            f["present"] = (ds.root / "docs" / f["name"]).exists()
        return {**info, "files": files, "questions": [q.model_dump(mode="json") for q in ds.questions],
                "source_kb": source_kb((ds.manifest.build or {}).get("source"))}

    def dataset_doc(self, path: str, name: str) -> dict:
        ds = self._dataset(path)
        if name not in {f.name for f in ds.manifest.files}:
            raise WorkspaceError("清单里没有这份文档")
        doc = ds.root / "docs" / name
        if not doc.exists():
            raise WorkspaceError("本地没有这份文档（可能是只给清单的文档，需要先按清单下载或生成）")
        if not name.lower().endswith((".md", ".markdown", ".txt")):
            return {"name": name, "text": None, "message": "这份文档不是文本格式，暂不支持在页面里预览"}
        text = doc.read_text(encoding="utf-8")
        limit = 2_000_000
        return {"name": name, "text": text[:limit], "truncated": len(text) > limit}

    def eval_doc_path(self, eval_id: str, name: str) -> Path:
        """这次评测所用测试集里的一份文档；只认清单里的名字，不让请求读到别的文件。"""
        ds = self.ws.load_dataset_by_path(self.ws.load_eval(eval_id).dataset_path)
        if name not in {f.name for f in ds.manifest.files}:
            raise WorkspaceError("清单里没有这份文档")
        doc = ds.root / "docs" / name
        if not doc.exists():
            raise WorkspaceError("本地没有这份文档（可能是只给清单的文档，需要先按清单下载）")
        return doc

    def eval_doc(self, eval_id: str, name: str) -> dict:
        try:
            return doc_preview(self.eval_doc_path(eval_id, name))
        except ValueError as e:
            raise WorkspaceError(f"这份文档解析不了：{e}") from e

    def eval_detail(self, eval_id: str) -> dict:
        meta = self.ws.load_eval(eval_id)
        total = len(self.ws.load_dataset_by_path(meta.dataset_path).questions)
        progress = {p.id: participant_progress(self.ws.run_dir(eval_id, p.id), total, meta.repeats)
                    for p in meta.participants}
        return {**meta.model_dump(mode="json"), "progress": progress,
                "running": self.executor.running_id() == eval_id}

    def eval_report(self, eval_id: str, participant: str | None = None) -> dict:
        meta = self.ws.load_eval(eval_id)
        ds = self.ws.load_dataset_by_path(meta.dataset_path)
        done = [p for p in meta.participants if (self.ws.run_dir(eval_id, p.id) / "summary.json").exists()]
        if participant:
            if participant not in {p.id for p in done}:
                raise WorkspaceError("这份配置还没有跑完的结果")
            return build_report_data(self.ws.run_dir(eval_id, participant), ds)
        if not done:
            raise WorkspaceError("还没有任何配置跑完")
        if len(done) == 1:
            return build_report_data(self.ws.run_dir(eval_id, done[0].id), ds)
        data = build_compare_data([self.ws.run_dir(eval_id, p.id) for p in done], ds)
        return {**data, "compare": relabel_compare(data["compare"], {p.id: p.name for p in done})}

    # ---------- 写 ----------

    def delete_eval(self, eval_id: str) -> dict:
        """删掉一次评测：结果、检查点和题目快照一起删。用到的测试集、评测台建的知识库不动。"""
        if not re.fullmatch(r"ev-[0-9a-f]+", eval_id):
            raise WorkspaceError("没有这个评测")
        self.ws.load_eval(eval_id)
        if self.executor.running_id() == eval_id:
            raise WorkspaceError("这个评测正在运行，先中止再删除")
        shutil.rmtree(self.ws.evals_dir() / eval_id)
        return {"ok": True}

    def delete_dataset(self, path: str) -> dict:
        """评测结果要读数据集的题目，所以用到它的评测一起删。"""
        ds = self._dataset(path)
        users = [e for e in self.ws.list_evals() if e.dataset_path == path]
        running = self.executor.running_id()
        if running and running in {e.id for e in users}:
            raise WorkspaceError("用到这个数据集的评测正在运行，先停止它再删除")
        for e in users:
            shutil.rmtree(self.ws.evals_dir() / e.id)
        if ds.root.is_symlink():   # 链接到别处的数据集（如仓库里的原始数据）：只去掉链接，不碰原目录
            ds.root.unlink()
        else:
            shutil.rmtree(ds.root)
        return {"ok": True, "evals_deleted": len(users)}

    @staticmethod
    def _parse_picks(raw: list[dict]) -> tuple[list[tuple[str, str]], dict[str, str], dict[str, dict]]:
        """勾选项：{system_id, config_id} 是存好的配置；{system_id, chat_id, chat_name} 是直接选的已有助手；
        再带 variant: {name, overrides} 的是这个助手的参数对照（只改这几项参数）。"""
        picks, chat_names, variants = [], {}, {}
        for p in raw or []:
            if p.get("chat_id"):
                config_id = CHAT_PICK + str(p["chat_id"])
                if p.get("variant") is not None:
                    config_id += f"~{sum(1 for k in variants if k.startswith(config_id + '~')) + 1}"
                    variants[config_id] = p["variant"] if isinstance(p["variant"], dict) else {}
                picks.append((p.get("system_id", ""), config_id))
                chat_names[str(p["chat_id"])] = p.get("chat_name") or ""
            else:
                picks.append((p.get("system_id", ""), p.get("config_id", "")))
        return picks, chat_names, variants

    def preflight(self, body: dict) -> dict:
        """开始评测前的试问检查：结果只给页面提示，不挡评测。"""
        import tempfile
        from knoweval.ui.preflight import preflight
        picks, chat_names, variants = self._parse_picks(body.get("picks", []))
        participants = self.ws.plan_participants(picks, chat_names=chat_names, variants=variants)
        if body.get("build_id"):
            with tempfile.TemporaryDirectory() as tmp:
                ds = load_dataset(self.builds.snapshot(body["build_id"], Path(tmp) / "snap"))
                return {"results": preflight(ds, participants, self.executor.factory)}
        ds = self.ws.load_dataset_by_path(body.get("dataset_path", ""))
        return {"results": preflight(ds, participants, self.executor.factory)}

    def create_eval(self, body: dict, *, start: bool = True) -> dict:
        """测试集（build_id）直接评测：题目快照进评测目录；官方数据集（dataset_path）原样使用。

        start=False 只建不开始（出完题自动评测时，由调用方决定开不开得了）。
        """
        eval_id, dataset_path = None, body.get("dataset_path", "")
        picks, chat_names, variants = self._parse_picks(body.get("picks", []))
        if body.get("build_id"):
            eval_id = f"ev-{secrets.token_hex(3)}"
            out = self.ws.evals_dir() / eval_id / "dataset"
            self.builds.snapshot(body["build_id"], out)
            dataset_path = out.relative_to(self.ws.root).as_posix()
        try:
            meta = self.ws.create_eval(dataset_path=dataset_path, picks=picks, eval_id=eval_id, chat_names=chat_names, variants=variants,
                                       name=body.get("name", ""), repeats=int(body.get("repeats", 2)),
                                       concurrency=int(body.get("concurrency", 4)))
        except Exception:
            if eval_id:
                shutil.rmtree(self.ws.evals_dir() / eval_id, ignore_errors=True)
            raise
        if start:
            try:
                self.executor.start(meta.id)
            except Exception:
                # 开不起来（如已有评测在跑）：不留一个建了一半的评测
                shutil.rmtree(self.ws.evals_dir() / meta.id, ignore_errors=True)
                raise
        return self.ws.load_eval(meta.id).model_dump(mode="json")

    def docs_zip(self, path: str = "", build_id: str = "") -> tuple[str, bytes]:
        """测试集的文档打成 zip：只接问答接口的系统，由用户自己导入。"""
        import io
        import zipfile
        if build_id:
            meta = self.builds.load(build_id)
            docs_dir = self.builds._dir(build_id) / "project" / "docs"
            files = sorted(p for p in docs_dir.iterdir() if p.is_file()) if docs_dir.exists() else []
            label = f"{meta.name}@{meta.version}"
        else:
            ds = self._dataset(path)
            files = [p for p in ds.doc_paths() if p.exists()]
            label = ds.ref
        if not files:
            raise WorkspaceError("这个测试集还没有文档可下载")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                z.write(f, f.name)
        return f"{label}-文档.zip", buf.getvalue()

    def test_system(self, system_id: str) -> dict:
        import os
        system = next((s for s in self.ws.systems() if s.id == system_id), None)
        if system is None:
            raise WorkspaceError("没有这个系统")
        if not os.environ.get(system.api_key_env):
            return {"ok": False, "message": f"环境变量 {system.api_key_env} 没有设置，拿不到 API Key"}
        try:
            resp = httpx.get(system.base_url, timeout=5, follow_redirects=True)
        except httpx.HTTPError as e:
            return {"ok": False, "message": f"连不上 {system.base_url}：{type(e).__name__}: {e}"}
        return {"ok": True, "message": f"服务可达（HTTP {resp.status_code}），API Key 环境变量已设置"}

    def probe(self, body: dict) -> dict:
        """接入表单的「试调」：用表单里还没保存的配置真实调一次对方接口，原样带回返回结果。"""
        import os

        from knoweval.adapters.http import probe

        base_url, key_env = body.get("base_url") or "", body.get("api_key_env") or ""
        if not base_url:
            raise WorkspaceError("先填服务地址")
        api_key = os.environ.get(key_env, "")
        if not api_key:
            raise WorkspaceError(f"环境变量 {key_env} 没有设置：评测台进程里拿不到对方的 API Key")
        try:
            return asyncio.run(probe(base_url, api_key, body.get("http") or {}, body.get("step", ""),
                                     body.get("values") or {}, params=body.get("params") or {}))
        except AdapterError as e:
            raise WorkspaceError(str(e)) from e

    def _list_from_system(self, system_id: str, path: str, what: str, params: dict | None = None) -> list[dict]:
        """从已接入的 KnowFlow / RAGFlow 列出一页资源（知识库、助手）。"""
        import os
        system = next((s for s in self.ws.systems() if s.id == system_id), None)
        if system is None or system.adapter not in ("knowflow", "ragflow"):
            raise WorkspaceError(f"只有 KnowFlow、RAGFlow 系统能列出{what}")
        key = os.environ.get(system.api_key_env)
        if not key:
            raise WorkspaceError(f"环境变量 {system.api_key_env} 没有设置")
        try:
            resp = httpx.get(f"{system.base_url.rstrip('/')}/api/v1/{path}", params=params or {"page": 1, "page_size": 200},
                             headers={"Authorization": f"Bearer {key}"}, timeout=30)
            body = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise WorkspaceError(f"取{what}列表失败：{e}") from e
        if body.get("code") != 0:
            raise WorkspaceError(f"取{what}列表失败：{body.get('message')}")
        return body.get("data") or []

    def system_kbs(self, system_id: str) -> list[dict]:
        """已接入的 KnowFlow / RAGFlow 里有哪些知识库（新建测试集时选文档来源用）。"""
        return [{"id": d["id"], "name": d["name"], "documents": d.get("document_count", 0)}
                for d in self._list_from_system(system_id, "datasets", "知识库")]

    def demo_info(self) -> dict:
        """有没有起演示服务（Docker 里 --profile demo）：有就让系统页给出「一键接入演示系统」。"""
        url = self._demo_alive()
        return {"available": True, "url": url} if url else {"available": False}

    @staticmethod
    def _demo_alive() -> str | None:
        """演示服务的地址，且真的连得上（compose 里总设着地址，服务只在 --profile demo 时才起）。"""
        from knoweval.demo import demo_url
        url = demo_url()
        if not url:
            return None
        try:
            httpx.get(url, timeout=1.5)
        except httpx.HTTPError:
            return None
        return url

    def setup_demo(self) -> dict:
        """一键接入演示系统；已经接过就直接返回那个系统，不重复接入。"""
        from knoweval.demo import DEMO_SYSTEM_NAME, demo_system
        url = self._demo_alive()
        if not url:
            raise WorkspaceError("没有起演示服务：用 ./start.sh demo（或 docker compose --profile demo up -d）启动后再接入")
        existing = next((s for s in self.ws.systems() if s.name == DEMO_SYSTEM_NAME), None)
        system = existing or self.ws.add_system(demo_system(url))
        return system.model_dump(mode="json")

    def system_chat_params(self, system_id: str, chat_id: str) -> dict:
        """一个已有助手的全部参数，用建助手接口的写法：参数对照预填它，用户改哪项就是对照哪项。"""
        from knoweval.adapters.ragflow import clone_chat_body
        found = self._list_from_system(system_id, "chats", "助手", params={"id": chat_id})
        if not found:
            raise WorkspaceError("找不到这个助手")
        return clone_chat_body(found[0])

    def system_chats(self, system_id: str) -> list[dict]:
        """已接入的 KnowFlow / RAGFlow 里有哪些助手（配置「已有助手」时选）。"""
        return [{"id": c["id"], "name": c.get("name", ""), "model": (c.get("llm") or {}).get("model_name", ""),
                 "kbs": [{"id": d["id"], "name": d.get("name", ""), "documents": d.get("doc_num")} for d in c.get("datasets") or []]}
                for c in self._list_from_system(system_id, "chats", "助手")]

    def probe_assistants(self, body: dict) -> dict:
        """先问选中的已有助手一句，接口报错当场发现。"""
        from knoweval.ui.preflight import probe_assistants
        picks, chat_names, variants = self._parse_picks(body.get("picks") or [])
        participants = self.ws.plan_participants(picks, chat_names=chat_names, variants=variants)
        return {"results": probe_assistants(participants, self.executor.factory)}

    def _eval_after_build(self, meta) -> dict:
        """出完题建好评测并开始；正好有别的评测在跑就先建着，记下编号让用户稍后在评测页点开始。

        开评测前再问一次助手：出题这几分钟里助手可能坏了（模型被删、服务停了），坏了就不开跑。
        """
        bad = [r for r in self.probe_assistants(meta.after_ready)["results"] if not r["ok"]]
        if bad:
            raise WorkspaceError("；".join(f"{r['name']} {r['message']}" for r in bad)[:400] + "。处理好后点右上角「评测」手动开始")
        eval_id = self.create_eval({**meta.after_ready, "build_id": meta.id}, start=False)["id"]
        try:
            self.executor.start(eval_id)
        except WorkspaceError as e:
            return {"eval_id": eval_id, "note": f"评测已建好，但没能开始：{e}。到评测页点「开始」即可"}
        return {"eval_id": eval_id, "note": None}

    def _check_after_ready(self, after_ready: dict | None) -> dict | None:
        """出完题要开始的评测：只留认得的字段，现在就核对勾选项和数字，不等几分钟出完题才发现选错了。"""
        if not after_ready:
            return None
        picks, chat_names, variants = self._parse_picks(after_ready.get("picks") or [])
        if not picks:
            raise WorkspaceError("至少选一个提问对象")
        self.ws.plan_participants(picks, chat_names=chat_names, variants=variants)
        try:
            repeats, concurrency = int(after_ready.get("repeats", 2)), int(after_ready.get("concurrency", 4))
        except (TypeError, ValueError) as e:
            raise WorkspaceError("遍数和并发要填整数") from e
        if not 1 <= repeats <= 5 or not 1 <= concurrency <= 16:
            raise WorkspaceError("每项跑 1 到 5 遍，并发 1 到 16")
        return {"picks": after_ready["picks"], "repeats": repeats, "concurrency": concurrency,
                "name": str(after_ready.get("name") or "")}

    def create_build(self, body: dict) -> dict:
        import base64
        mix = body.get("mix") or DEFAULT_MIX
        if body.get("source") == "import" and body.get("after_ready"):
            raise WorkspaceError("导入题目不支持出完自动评测：导入后在测试集页点「评测」")
        after_ready = self._check_after_ready(body.get("after_ready"))
        try:
            # 题目文件：单独导入，或者和模型出题一起（上传文档、从知识库取文档时都能带）
            table = body.get("table")
            table = (table["name"], base64.b64decode(table["content"])) if table else None
        except (KeyError, ValueError, TypeError) as e:
            raise WorkspaceError(f"题目文件格式不对：{e}") from e
        if body.get("source") == "kb":
            kb_ids = body.get("kb_ids") or ([body["kb_id"]] if body.get("kb_id") else [])
            meta = self.builds.create_from_kb(body.get("name", ""), body.get("version", "0.1"), body.get("system_id", ""),
                                              kb_ids, mix, kb_names=body.get("kb_names") or {}, after_ready=after_ready,
                                              table=table)
        else:
            try:
                files = [(f["name"], base64.b64decode(f["content"])) for f in body.get("files") or []]
            except (KeyError, ValueError) as e:
                raise WorkspaceError(f"上传的文件格式不对：{e}") from e
            if body.get("source") == "import":
                if not table:
                    raise WorkspaceError("上传题目文件（CSV 或 JSONL）")
                meta = self.builds.create_from_import(body.get("name", ""), body.get("version", "0.1"), table, files)
                return meta.model_dump(mode="json")
            meta = self.builds.create_from_files(body.get("name", ""), body.get("version", "0.1"), files, mix,
                                                 after_ready=after_ready, table=table)
        return meta.model_dump(mode="json")

    def import_more(self, build_id: str, body: dict) -> dict:
        """往已有测试集里再导入一批题。"""
        import base64
        table = body.get("table")
        try:
            table = (table["name"], base64.b64decode(table["content"]))
        except (KeyError, ValueError, TypeError) as e:
            raise WorkspaceError("上传题目文件（CSV 或 JSONL）") from e
        return self.builds.import_more(build_id, table).model_dump(mode="json")

    def test_judge(self) -> dict:
        import os
        s = self.ws.settings()
        if not s.model or not s.base_url:
            return {"ok": False, "message": "先填好接口地址和模型名"}
        api_key = s.api_key()
        if not api_key:
            return {"ok": False, "message": f"环境变量 {s.api_key_env} 没有设置"}

        async def _ping() -> str:
            client = s.client(api_key)
            return await client.complete([{"role": "user", "content": "只回复两个字：成功"}], s.model)

        hint = "；如果这家接口不认 enable_thinking 参数，关掉「关闭思考」再试" if s.disable_thinking else ""
        started = time.monotonic()
        try:
            reply = asyncio.run(asyncio.wait_for(_ping(), 30))
        except Exception as e:  # noqa: BLE001 - 测试调用的任何失败都原样告诉用户
            return {"ok": False, "message": f"调用失败：{type(e).__name__}: {e}{hint}"}
        took = f"用时 {time.monotonic() - started:.1f} 秒"
        if not reply.strip():
            return {"ok": False, "message": f"调用成功但正文是空的（{took}），可能结论只写进了思考过程，打开「关闭思考」再试"}
        return {"ok": True, "message": f"调用成功，{took}，模型回复：{reply.strip()[:40]}"}


class _Handler(BaseHTTPRequestHandler):
    api: UiApi

    def log_message(self, *args) -> None:  # 安静运行
        pass

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self._responded = True
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # 客户端中途断开（关掉页面、刷新、健康检查不读完）：没人收了，不必报错
            pass

    def _zip(self, name: str, data: bytes) -> None:
        from urllib.parse import quote
        self._responded = True
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name)}")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _inline(self, path: Path) -> None:
        """原文件给浏览器直接打开（PDF 用浏览器自带的阅读器）。"""
        from urllib.parse import quote
        data = path.read_bytes()
        self._responded = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", media_type(path))
            self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{quote(path.name)}")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _error(self, status: int, msg: str) -> None:
        self._json({"error": msg}, status)

    def _route(self, method: str) -> None:
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        body = {}
        if method == "POST" or method == "PUT":
            length = int(self.headers.get("Content-Length") or 0)
            if length > 300 * 1024 * 1024:  # 上传文档走 base64，200 MB 原文约 270 MB
                return self._error(413, "请求太大：上传的文档合计不能超过 200 MB")
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._error(400, "请求体不是 JSON")
        self._responded = False
        try:
            handled = self._dispatch(method, parts, query, body)
        except WorkspaceError as e:
            busy = "已有评测在运行" in str(e)
            return self._error(409 if busy else 400, str(e))
        except FileNotFoundError as e:
            return self._error(404, str(e))
        except ValueError as e:  # 流水线里的业务错误（数据集校验、导出条件等）：说清原因，不让连接断掉
            return self._error(400, str(e))
        if handled is None and not self._responded:
            return self._error(404, "没有这个接口")
        return None

    def _dispatch(self, method: str, parts: list[str], query: dict, body: dict):  # noqa: PLR0911
        api = self.api
        if method == "GET" and parts == []:
            html = render_html({"kind": "ui", "api": "/api"})
            return self._send(200, html.encode(), "text/html; charset=utf-8")
        if parts[:1] != ["api"]:
            return None
        p = parts[1:]
        if method == "GET":
            if p == ["state"]:
                return self._json(api.state())
            if p == ["datasets"]:
                return self._json(api.datasets())
            if p == ["datasets", "detail"]:
                return self._json(api.dataset_detail(query.get("path", "")))
            if p == ["builds", "import-template.csv"]:
                from knoweval.ui.import_questions import TEMPLATE_CSV
                data = ("\ufeff" + TEMPLATE_CSV).encode("utf-8")
                self._responded = True
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''%E9%A2%98%E7%9B%AE%E6%A8%A1%E6%9D%BF.csv")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return None
            if p == ["datasets", "docs.zip"]:
                return self._zip(*api.docs_zip(path=query.get("path", "")))
            if len(p) == 3 and p[0] == "builds" and p[2] == "docs.zip":
                return self._zip(*api.docs_zip(build_id=p[1]))
            if p == ["datasets", "doc"]:
                return self._json(api.dataset_doc(query.get("path", ""), query.get("name", "")))
            if p == ["systems"]:
                return self._json([s.model_dump(mode="json") for s in api.ws.systems()])
            if p == ["settings"]:
                return self._json(api.ws.settings().model_dump(mode="json"))
            if len(p) == 2 and p[0] == "evals":
                return self._json(api.eval_detail(p[1]))
            if p == ["builds"]:
                return self._json({"running": api.builds.running_id(), "builds": api.builds.list(), "default_mix": DEFAULT_MIX})
            if len(p) == 2 and p[0] == "builds":
                return self._json(api.builds.detail(p[1]))
            if len(p) == 3 and p[0] == "builds" and p[2] == "doc":
                return self._json(api.builds.doc_text(p[1], query.get("name", "")))
            if len(p) == 3 and p[0] == "systems" and p[2] == "kbs":
                return self._json(api.system_kbs(p[1]))
            if p == ["health"]:
                return self._json({"ok": True})
            if p == ["demo"]:
                return self._json(api.demo_info())
            if len(p) == 3 and p[0] == "systems" and p[2] == "chats":
                return self._json(api.system_chats(p[1]))
            if len(p) == 5 and p[0] == "systems" and p[2] == "chats" and p[4] == "params":
                return self._json(api.system_chat_params(p[1], p[3]))
            if len(p) == 3 and p[0] == "evals" and p[2] == "doc":
                return self._json(api.eval_doc(p[1], query.get("name", "")))
            if p[:1] == ["evals"] and p[2:] == ["doc", "file"] and len(p) == 4:
                return self._inline(api.eval_doc_path(p[1], query.get("name", "")))
            if len(p) == 3 and p[0] == "evals" and p[2] == "report":
                return self._json(api.eval_report(p[1], query.get("participant")))
        if method == "POST":
            if p == ["systems"]:
                return self._json(api.ws.add_system(body).model_dump(mode="json"))
            if len(p) == 3 and p[0] == "systems" and p[2] == "configs":
                return self._json(api.ws.add_config(p[1], body).model_dump(mode="json"))
            if len(p) == 3 and p[0] == "systems" and p[2] == "test":
                return self._json(api.test_system(p[1]))
            if p == ["systems", "probe"]:
                return self._json(api.probe(body))
            if p == ["settings", "test"]:
                return self._json(api.test_judge())
            if p == ["evals"]:
                return self._json(api.create_eval(body))
            if p == ["demo", "setup"]:
                return self._json(api.setup_demo())
            if p == ["evals", "probe-assistants"]:
                return self._json(api.probe_assistants(body))
            if p == ["evals", "preflight"]:
                return self._json(api.preflight(body))
            if p == ["builds"]:
                return self._json(api.create_build(body))
            if len(p) == 3 and p[0] == "builds" and p[2] == "import":
                return self._json(api.import_more(p[1], body))
            if len(p) == 3 and p[0] == "builds" and p[2] == "generate":
                return self._json(api.builds.generate_more(p[1], body.get("mix") or {}).model_dump(mode="json"))
            if len(p) == 3 and p[0] == "builds" and p[2] == "stop":
                api.builds.stop(p[1])
                return self._json({"ok": True})
            if len(p) == 3 and p[0] == "builds" and p[2] == "verify":
                return self._json(api.builds.reverify(p[1]).model_dump(mode="json"))
            if len(p) == 3 and p[0] == "builds" and p[2] == "items":
                return self._json(api.builds.save_item(p[1], None, body))
            if len(p) == 4 and p[0] == "builds" and p[2:] == ["items", "batch"]:
                return self._json(api.builds.review_many(p[1], body.get("ids") or [], body.get("action", "")))
            if len(p) == 5 and p[0] == "builds" and p[2] == "items":
                return self._json(api.builds.review(p[1], p[3], p[4]))
            if len(p) == 3 and p[0] == "evals" and p[2] == "start":
                api.executor.start(p[1])
                return self._json(api.eval_detail(p[1]))
            if len(p) == 3 and p[0] == "evals" and p[2] == "stop":
                api.executor.stop(p[1])
                return self._json({"ok": True})
        if method == "PUT" and len(p) == 4 and p[0] == "builds" and p[2] == "items":
            return self._json(api.builds.save_item(p[1], p[3], body))
        if method == "PUT" and p == ["settings"]:
            return self._json(api.ws.save_settings(body).model_dump(mode="json"))
        if method == "PUT" and len(p) == 2 and p[0] == "systems":
            return self._json(api.ws.update_system(p[1], body).model_dump(mode="json"))
        if method == "PUT" and len(p) == 4 and p[0] == "systems" and p[2] == "configs":
            return self._json(api.ws.update_config(p[1], p[3], body).model_dump(mode="json"))
        if method == "DELETE" and len(p) == 2 and p[0] == "builds":
            api.builds.delete(p[1])
            return self._json({"ok": True})
        if method == "DELETE":
            if p == ["datasets"]:
                return self._json(api.delete_dataset(query.get("path", "")))
            if len(p) == 2 and p[0] == "evals":
                return self._json(api.delete_eval(p[1]))
            if len(p) == 2 and p[0] == "systems":
                api.ws.delete_system(p[1])
                return self._json({"ok": True})
            if len(p) == 4 and p[0] == "systems" and p[2] == "configs":
                api.ws.delete_config(p[1], p[3])
                return self._json({"ok": True})
        return None

    def do_GET(self) -> None:  # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._route("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._route("DELETE")


def make_server(workspace_root: str | Path, port: int = 8780,
                api: UiApi | None = None, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """默认只监听本机。评测台没有登录，Docker 容器里才监听所有网卡，由端口映射决定谁能访问。"""
    ui_api = api or UiApi(Workspace(workspace_root))
    handler = type("UiHandler", (_Handler,), {"api": ui_api})
    return ThreadingHTTPServer((host, port), handler)

"""通用 HTTP 适配器：企业自研 RAG 用一份 YAML 写清请求模板与取值路径即可接入，不写代码。

两种入库方式：
- **自动入库**：配置里写了 `ingest`（建库、上传、可选的解析状态轮询），每次评测建一个新库，过程可复现。
- **手工入库**：只给 `kb_id`，用户自己把文档传进去。入库过程无法复现，结果在 run.json 里标
  manual_ingest，报告里注明「文档由被测系统自行导入」。

回答侧可选三样：引用列表或正文里的引用标记（如 `[ID:3]`，按编号对回检索上下文）、检索上下文（错题归因用）、
回答带出的图（正文里的 <img> / Markdown 图片，以及 `images` 取值路径）。
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

import httpx

from knoweval.adapters.base import AdapterError, DocIngest, IngestReport, KbHandle
from knoweval.adapters.media import dedupe, hash_ref, refs_in_text
from knoweval.adapters.template import check_query_params, header_values, render, request_kwargs, resolve_params
from knoweval.schema import Answer, Citation, ImageRef

_STEP = re.compile(r"\.([A-Za-z_][\w-]*)|\[(\d+|\*)\]")


def jsonpath(data: Any, path: str) -> Any:
    """JSONPath 的一个小子集：$.a.b、$.a[0]、$.a[*]、$.a[*].b。取不到返回 None。"""
    if not path.startswith("$"):
        raise ValueError(f"取值路径必须以 $ 开头：{path}")
    nodes, fanned = [data], False
    for m in _STEP.finditer(path[1:]):
        key, index = m.group(1), m.group(2)
        nxt = []
        for node in nodes:
            if key is not None:
                if isinstance(node, dict) and key in node:
                    nxt.append(node[key])
            elif index == "*":
                fanned = True
                if isinstance(node, list):
                    nxt.extend(node)
            elif isinstance(node, list) and int(index) < len(node):
                nxt.append(node[int(index)])
        nodes = nxt
    if fanned:
        return nodes
    return nodes[0] if nodes else None


def _page(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float, str)) and str(value).isdigit() else None


def _citations(items: Any, fields: dict[str, str]) -> list[Citation]:
    out = []
    for item in items or []:
        out.append(Citation(doc=str(jsonpath(item, fields.get("doc", "$.doc")) or ""),
                            page=_page(jsonpath(item, fields["page"])) if "page" in fields else None,
                            text=str(jsonpath(item, fields.get("text", "$.text")) or "")))
    return out


def _states(value: Any) -> set[str]:
    return {str(v).lower() for v in (value if isinstance(value, list) else [value])}


class HttpAdapter:
    def __init__(self, base_url: str, api_key: str, config: dict[str, Any], *, name: str = "http",
                 ask_timeout_s: float = 180, ingest_timeout_s: float = 3600, image_base_url: str | None = None,
                 existing_kb: str | None = None, chat_params: dict[str, Any] | None = None,
                 dataset_params: dict[str, Any] | None = None, client: httpx.AsyncClient | None = None):
        """existing_kb：直接向这个已有的库提问（连接已有知识库的配置），不走入库模板。

        chat_params / dataset_params：参评配置的问答参数与建库参数。模板里用 {{chat.x}}、{{dataset.x}} 引用的放进占位符，
        其余深合并进问答、建库的请求体。评测台不理解这些参数，只原样传过去、原样记录。
        """
        self.name = name
        self.existing_kb = existing_kb
        ask = config.get("ask") or {}
        if not ask.get("answer"):
            raise AdapterError("通用 HTTP 适配器需要在 http.ask.answer 里写回答的取值路径")
        self.ingest_cfg = config.get("ingest") or {}
        if self.ingest_cfg:
            _check_ingest(self.ingest_cfg)
        elif not config.get("kb_id") and not existing_kb:
            raise AdapterError("通用 HTTP 适配器需要 http.ingest（自动入库）或 http.kb_id（手工入库）二选一")
        self.chat_params = chat_params or {}
        self.dataset_params = dataset_params or {}
        # 只校验会真正发出的模板：直接问已有的库时不走入库模板，里面引用的建库参数没有值也无妨
        uses_ingest = bool(self.ingest_cfg) and not existing_kb
        in_use = {"headers": config.get("headers"), "ask": ask, "ingest": self.ingest_cfg if uses_ingest else None}
        self._param_values, self._merge = resolve_params(in_use, {"chat": self.chat_params, "dataset": self.dataset_params})
        check_query_params(ask.get("method", "POST"), self._merge["chat"], "问答")
        if uses_ingest:
            check_query_params(self.ingest_cfg["create_kb"].get("method", "POST"), self._merge["dataset"], "建库")
        marker = ask.get("citation_marker")
        if marker and not ask.get("contexts"):
            raise AdapterError("用 citation_marker 从正文取引用时，必须同时配 contexts，编号要对回检索上下文")
        self.config = config
        self.ask_cfg = ask
        self.marker = re.compile(marker) if marker else None
        self.supports_citations = bool(ask.get("citations") or marker)
        self.ingest_timeout_s = ingest_timeout_s
        self.image_base_url = image_base_url
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=ask_timeout_s)

    # ---------- 通用请求 ----------

    def _values(self, **extra: str) -> dict[str, Any]:
        return {"api_key": self._api_key, **self._param_values, **extra}

    def _headers(self, values: dict[str, str]) -> dict[str, str]:
        return header_values(render(self.config.get("headers") or {}, values))

    async def _call(self, spec: dict[str, Any], values: dict[str, Any], what: str, extra: dict[str, Any] | None = None,
                    **kw) -> Any:
        """发请求、取 JSON，按 http.success 判业务是否成功（很多系统出错也回 HTTP 200）。

        extra 是没被占位符用掉的参数：GET 追加到查询串，其余合并进请求体。
        """
        method = spec.get("method", "POST")
        path = render(spec.get("path", "/"), values)
        if extra is not None:
            path, body_kw = request_kwargs(method, path, render(spec.get("body"), values), extra)
            kw.update(body_kw)
        resp = await self._client.request(method, path, headers=self._headers(values), **kw)
        if resp.status_code >= 400:
            raise AdapterError(f"{what}接口返回 {resp.status_code}：{resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as e:
            raise AdapterError(f"{what}接口返回的不是 JSON") from e
        ok = self.config.get("success")
        if ok and jsonpath(data, ok["path"]) != ok.get("equals"):
            msg = jsonpath(data, self.config["error_message"]) if self.config.get("error_message") else None
            raise AdapterError(f"{what}失败：{msg or str(data)[:200]}")
        return data

    # ---------- 入库 ----------

    async def ingest(self, files: list[Path], kb_name: str) -> IngestReport:
        if self.existing_kb:
            return IngestReport(kb=KbHandle(system=self.name, ids={"kb_id": self.existing_kb}),
                                docs=[DocIngest(name=f.name, ok=True) for f in files], existing=True)
        if not self.ingest_cfg:
            return IngestReport(kb=KbHandle(system=self.name, ids={"kb_id": str(self.config["kb_id"])}), manual=True)
        create = self.ingest_cfg["create_kb"]
        named = self._values(kb_name=kb_name)
        created = await self._call(create, named, "建库", extra=self._merge["dataset"])
        kb_id = jsonpath(created, create["id"])
        if not kb_id:
            raise AdapterError(f"按 {create['id']} 取不到新建知识库的 id：{str(created)[:200]}")
        kb_id = str(kb_id)
        upload = self.ingest_cfg["upload"]
        values = self._values(kb_id=kb_id, kb_name=kb_name)
        doc_ids: dict[str, str] = {}
        for f in files:
            data = await self._call(upload, values, f"上传 {f.name} ",
                                    data=render(upload.get("form") or {}, values),
                                    files={upload.get("file_field", "file"): (f.name, f.read_bytes())})
            doc_id = jsonpath(data, upload["doc_id"]) if upload.get("doc_id") else None
            doc_ids[str(doc_id) if doc_id else f.name] = f.name
        kb = KbHandle(system=self.name, ids={"kb_id": kb_id})
        if not self.ingest_cfg.get("status"):
            # 没有状态接口只能当上传即入库完成；被测系统若是异步解析，题目可能问在解析完成之前
            return IngestReport(kb=kb, docs=[DocIngest(name=n, ok=True) for n in doc_ids.values()])
        docs = await self._wait(kb_id, doc_ids)
        if not any(d.ok for d in docs):
            raise AdapterError(f"全部 {len(docs)} 份文档入库失败，例如 {docs[0].name}：{(docs[0].error or '')[-300:]}")
        return IngestReport(kb=kb, docs=docs)

    async def _wait(self, kb_id: str, doc_ids: dict[str, str]) -> list[DocIngest]:
        status = self.ingest_cfg["status"]
        interval = float(self.ingest_cfg.get("poll_interval_s", 3))
        max_failures = int(self.ingest_cfg.get("max_poll_failures", 10))
        start, finished, failures = time.monotonic(), {}, 0
        while True:
            for doc_id, name in doc_ids.items():
                if doc_id in finished:
                    continue
                try:
                    data = await self._call({"method": "GET", **status}, self._values(kb_id=kb_id, doc_id=doc_id),
                                            "查询解析状态")
                    failures = 0
                except (httpx.TransportError, AdapterError) as e:
                    # 解析期间服务端常常很忙，一次查不到不代表入库失败；连续失败太多次才放弃
                    failures += 1
                    if failures >= max_failures:
                        raise AdapterError(f"查询解析状态连续 {failures} 次失败：{e}") from e
                    continue
                result = _doc_state(data, status, name, time.monotonic() - start)
                if result:
                    finished[doc_id] = result
            if len(finished) == len(doc_ids):
                return [finished[i] for i in doc_ids]
            if time.monotonic() - start >= self.ingest_timeout_s:
                pending = [n for i, n in doc_ids.items() if i not in finished]
                raise AdapterError(f"入库超时（{self.ingest_timeout_s}s），未完成：{pending}")
            await asyncio.sleep(interval)

    async def kb_alive(self, kb: KbHandle) -> bool:
        """配了 http.ingest.alive 才真去问；不配就当缓存的库还在。"""
        alive = self.ingest_cfg.get("alive")
        if not alive:
            return True
        try:
            await self._call({"method": "GET", **alive}, self._values(kb_id=kb.ids["kb_id"]), "检查知识库")
        except (AdapterError, httpx.HTTPError):
            return False
        return True

    # ---------- 问答 ----------

    async def ask(self, kb: KbHandle, question: str) -> Answer:
        values = self._values(question=question, kb_id=kb.ids["kb_id"])
        start = time.monotonic()
        data = await self._call(self.ask_cfg, values, "问答", extra=self._merge["chat"])
        latency = int((time.monotonic() - start) * 1000)
        raw = jsonpath(data, self.ask_cfg["answer"])
        if not isinstance(raw, str):
            raise AdapterError(f"按 {self.ask_cfg['answer']} 取不到回答（answer）：{str(data)[:200]}")
        cfg = self.ask_cfg
        contexts = _citations(jsonpath(data, cfg["contexts"]), cfg.get("context_fields", {})) if cfg.get("contexts") else None
        text, citations = raw, []
        if cfg.get("citations"):
            citations = _citations(jsonpath(data, cfg["citations"]), cfg.get("citation_fields", {}))
        if self.marker:
            citations = citations or self._marked(raw, contexts or [])
            text = self.marker.sub("", raw).strip()
        answer = Answer(text=text, raw_text=raw, citations=citations, contexts=contexts, latency_ms=latency)
        refs = self._image_refs(raw, data)
        if refs:
            headers = self._headers(self._values())
            answer = answer.model_copy(update={"images": [await hash_ref(self._client, r, headers, self.image_base_url)
                                                          for r in refs]})
        return answer

    def _marked(self, raw: str, contexts: list[Citation]) -> list[Citation]:
        """正文里的引用标记按编号对回检索上下文；编号从几开始由 marker_index_base 决定（默认 0）。"""
        base = int(self.ask_cfg.get("marker_index_base", 0))
        picked: list[int] = []
        for m in self.marker.finditer(raw):
            i = int(m.group(1)) - base
            if 0 <= i < len(contexts) and i not in picked:
                picked.append(i)
        return [contexts[i] for i in picked]

    def _image_refs(self, raw: str, data: Any) -> list[ImageRef]:
        refs = refs_in_text(raw)
        path = self.ask_cfg.get("images")
        if path:
            fields = self.ask_cfg.get("image_fields", {})
            url_path = fields.get("url", "$.url")
            for item in jsonpath(data, path) or []:
                if isinstance(item, str):
                    refs.append(ImageRef(source=item))
                elif isinstance(item, dict) and jsonpath(item, url_path):
                    refs.append(ImageRef(source=str(jsonpath(item, url_path)),
                                         doc=jsonpath(item, fields["doc"]) if "doc" in fields else None,
                                         page=_page(jsonpath(item, fields["page"])) if "page" in fields else None))
        return dedupe(refs)

    async def aclose(self) -> None:
        await self._client.aclose()


def _check_ingest(cfg: dict[str, Any]) -> None:
    if not (cfg.get("create_kb") or {}).get("id"):
        raise AdapterError("自动入库需要 http.ingest.create_kb（含新建知识库 id 的取值路径 id）。每次评测建新库才可复现；"
                           "只想往现有库里问答请改用 http.kb_id")
    if not (cfg.get("upload") or {}).get("path"):
        raise AdapterError("自动入库需要 http.ingest.upload.path")
    status = cfg.get("status")
    if status and not (status.get("path") and status.get("state") and status.get("done") and cfg["upload"].get("doc_id")):
        raise AdapterError("http.ingest.status 需要 path、state、done，且 upload 要写 doc_id 取值路径")


def _doc_state(data: Any, status: dict[str, Any], name: str, elapsed: float) -> DocIngest | None:
    """一份文档到了终态就给出结果，还在解析返回 None。"""
    state = str(jsonpath(data, status["state"])).lower()
    if state in _states(status["done"]):
        return DocIngest(name=name, ok=True, seconds=round(elapsed, 1))
    if state in _states(status.get("failed", [])):
        err = jsonpath(data, status["error"]) if status.get("error") else None
        return DocIngest(name=name, ok=False, seconds=round(elapsed, 1), error=str(err or state))
    return None


PROBE_DOC = ("knoweval-probe.md", "# KnowEval 接入测试\n\n这是一份接入测试用的小文档，用来确认上传接口能收文件。\n".encode("utf-8"))


async def probe(base_url: str, api_key: str, config: dict[str, Any], step: str, values: dict[str, str],
                timeout_s: float = 60, *, params: dict[str, Any] | None = None,
                client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """按表单当前的配置真实调一次对方的某个接口，原样带回返回结果，给页面点选取值用。

    不做业务码判断、不取值：页面要看到对方真实的返回长什么样。step 取 create_kb / upload / status / ask。
    上传用一份很小的测试文档。表单可能还没填完，所以这里不经过 HttpAdapter 的完整校验。
    params 是这一步用的参数（试问用问答参数，建库、上传、查进度用建库参数），和真实评测一样代入占位符、合并进请求。
    """
    specs = {
        "create_kb": (config.get("ingest") or {}).get("create_kb"),
        "upload": (config.get("ingest") or {}).get("upload"),
        "status": (config.get("ingest") or {}).get("status"),
        "ask": config.get("ask"),
    }
    spec = specs.get(step)
    if not spec or not spec.get("path"):
        raise AdapterError(f"还没填这一步的请求地址：{step}")
    pack = "chat" if step == "ask" else "dataset"
    param_values, merge = resolve_params({"headers": config.get("headers"), "spec": spec}, {pack: params or {}},
                                         missing_hint="试调参数里没有填")
    vals = {"api_key": api_key, "kb_name": "knoweval-probe", **param_values,
            **{k: str(v) for k, v in values.items() if v is not None}}
    headers = header_values(render(config.get("headers") or {}, vals))
    method = spec.get("method") or ("GET" if step == "status" else "POST")
    kw: dict[str, Any] = {}
    if step == "upload":
        kw["data"] = render(spec.get("form") or {}, vals)
        kw["files"] = {spec.get("file_field") or "file": PROBE_DOC}
    path = render(spec["path"], vals)
    if step in ("create_kb", "ask"):
        path, kw = request_kwargs(method, path, None if method == "GET" else render(spec.get("body"), vals), merge[pack])
    own = client is None
    client = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s)
    try:
        resp = await client.request(method, path, headers=headers, **kw)
    except httpx.TimeoutException:
        return {"ok": False, "request": f"{method} {path}", "error": f"请求超时（{timeout_s:.0f} 秒没有返回）"}
    except httpx.ConnectError as e:
        return {"ok": False, "request": f"{method} {path}", "error": f"连不上 {base_url}：{e}"}
    except httpx.HTTPError as e:
        return {"ok": False, "request": f"{method} {path}", "error": f"{type(e).__name__}: {e}"}
    finally:
        if own:
            await client.aclose()
    try:
        data = resp.json()
    except ValueError:
        return {"ok": False, "request": f"{method} {path}", "status_code": resp.status_code,
                "error": "返回的不是 JSON", "text": resp.text[:2000]}
    return {"ok": resp.status_code < 400, "request": f"{method} {path}", "status_code": resp.status_code, "json": data}

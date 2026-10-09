"""RAGFlow（以及沿用同一套 /api/v1 接口的 KnowFlow）适配器。"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from knoweval.adapters.base import AdapterError, DocIngest, IngestReport, KbHandle, drop_secret_keys
from knoweval.adapters.media import dedupe, hash_ref, refs_in_text
from knoweval.adapters.template import deep_merge
from knoweval.schema import Answer, Citation, ImageRef

_MARKER = re.compile(r"\s*[\[［]\s*ID\s*[:：]\s*(\d+)\s*[\]］]")
# RAGFlow 模型调用失败时返回 code 0，正文以这个前缀开头（rag/llm/chat_model.py 的 ERROR_PREFIX）
_ERROR_PREFIX = "**ERROR**"
_TERMINAL = {"DONE", "FAIL", "CANCEL"}


# 分块方式（建库的 chunk_method / parser_id）。KnowFlow 在上游基础上加了四种现代分块与 page，
# 写法以 KnowFlow 的 api/utils/validation_utils.py 为准。写错时建库接口不报错，要到解析阶段才失败，所以在这里先拦。
RAGFLOW_CHUNK_METHODS = {"naive", "book", "email", "laws", "manual", "one", "paper", "picture", "presentation",
                         "qa", "table", "tag", "audio", "video", "knowledge_graph"}
KNOWFLOW_CHUNK_METHODS = RAGFLOW_CHUNK_METHODS | {"smart", "regex", "parent_child", "title", "page", "colpali"}


def check_chunk_method(adapter: str, dataset: dict) -> None:
    """建库参数里的分块方式必须是被测系统认得的写法；写错直接报错并给出最接近的正确写法。"""
    method = dataset.get("chunk_method")
    if method is None:
        return
    allowed = KNOWFLOW_CHUNK_METHODS if adapter == "knowflow" else RAGFLOW_CHUNK_METHODS
    if method in allowed:
        return
    guess = str(method).strip().lower().replace("-", "_").replace(" ", "_")
    hint = f"，是不是要写 {guess}？" if guess in allowed else ""
    raise AdapterError(f"分块方式 chunk_method={method} 不是 {adapter} 认得的写法{hint}可选：{', '.join(sorted(allowed))}")


def _page(chunk: dict) -> int | None:
    positions = chunk.get("positions") or []
    try:
        return int(positions[0][0]) if positions and positions[0] else None
    except (TypeError, ValueError, IndexError):
        return None


def _as_citation(chunk: dict) -> Citation:
    return Citation(doc=chunk.get("document_name") or chunk.get("document_id") or "",
                    page=_page(chunk), text=chunk.get("content") or "")


def parse_completion(data: Any, latency_ms: int) -> Answer:
    """[ID:n] 指向 reference.chunks 的下标：被标注的才是引用，全部分块作为检索上下文。

    返回形状不对、正文是模型报错，都是被测系统的失败，抛 AdapterError 记为 ERROR，不当成一个空回答去判。
    """
    if not isinstance(data, dict) or not isinstance(data.get("answer"), str):
        raise AdapterError(f"问答接口没有返回回答：{str(data)[:200]}")
    raw = data["answer"]
    if raw.lstrip().startswith(_ERROR_PREFIX):
        raise AdapterError(f"被测系统的模型调用失败：{raw.strip()[:300]}")
    reference = data.get("reference") or {}
    if not isinstance(reference, dict):
        raise AdapterError(f"reference 不是对象：{str(reference)[:200]}")
    chunks = reference.get("chunks") or []
    if not isinstance(chunks, list) or not all(isinstance(c, dict) for c in chunks):
        raise AdapterError("reference.chunks 格式不对")
    cited: list[int] = []
    for m in _MARKER.finditer(raw):
        i = int(m.group(1))
        if 0 <= i < len(chunks) and i not in cited:
            cited.append(i)
    return Answer(
        text=_MARKER.sub("", raw).strip(), raw_text=raw,
        citations=[_as_citation(chunks[i]) for i in cited],
        contexts=[_as_citation(c) for c in chunks], latency_ms=latency_ms,
    )


def image_candidates(data: dict) -> list[ImageRef]:
    """回答里带出的图：正文里的 <img> 与 Markdown 图片，以及被 [ID:n] 引用的分块附带的图片。"""
    raw = data.get("answer") or ""
    chunks = (data.get("reference") or {}).get("chunks") or []
    refs = refs_in_text(raw)
    for m in _MARKER.finditer(raw):
        i = int(m.group(1))
        if 0 <= i < len(chunks) and chunks[i].get("image_id"):
            c = chunks[i]
            refs.append(ImageRef(source=f"/v1/document/image/{c['image_id']}", doc=c.get("document_name"), page=_page(c)))
    return dedupe(refs)


_DEDUP_SUFFIX = re.compile(r"-\d+(?=\.[^.]+$)")
# 配置快照里去掉的字段：时间、计数、头像这类每次都会变、又不影响回答的
_VOLATILE = re.compile(r"(_time|_date)$|^(avatar|doc_num|chunk_num|token_num|document_count|chunk_count)$")


# 复制助手时不带过去的字段：身份、时间、头像，以及挂的知识库（另外按 dataset_ids 给）
_NOT_COPIED = {"id", "tenant_id", "name", "avatar", "icon", "status", "datasets", "kb_ids",
               "create_time", "create_date", "update_time", "update_date"}


def _to_create_prompt(prompt: dict) -> dict:
    """读接口的 prompt 写法 → 建接口的写法。读出来的 keywords_similarity_weight 是 1-向量权重，
    而建接口把同名字段直接当向量权重（不换算），原样传回去权重就反了。"""
    prompt = dict(prompt)
    if "keywords_similarity_weight" in prompt:
        prompt["vector_similarity_weight"] = round(1 - float(prompt.pop("keywords_similarity_weight")), 6)
    return prompt


def clone_chat_body(base: dict) -> dict:
    """读助手接口（GET /chats）的返回 → 建助手接口（POST /chats）的入参，设置原样复制。

    引用开关 do_refer 是助手的顶层字段，原样带过去；prompt 里映射出来的 show_quotation 不是它，去掉。
    """
    body = {k: v for k, v in base.items() if k not in _NOT_COPIED}
    prompt = _to_create_prompt(body.get("prompt") or {})
    prompt.pop("show_quotation", None)
    body["prompt"] = prompt
    return body


def finish_variant_body(body: dict) -> dict:
    """合并完对照参数后的收尾：临时助手不共享给别人；提示词里没有的变量标成可选
    （网页端建的助手提示词里可能没有 {knowledge}，建接口会拒绝，标成可选后行为和基准一致）。"""
    prompt = dict(body.get("prompt") or {})
    system = str(prompt.get("prompt") or "")
    if isinstance(prompt.get("variables"), list):
        prompt["variables"] = [{**v, "optional": True} if isinstance(v, dict) and not v.get("optional")
                               and "{%s}" % v.get("key") not in system else v for v in prompt["variables"]]
    return {**body, "prompt": prompt, "visibility": "private"}


def _stable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _stable(v) for k, v in obj.items() if not _VOLATILE.search(str(k))}
    if isinstance(obj, list):
        return [_stable(v) for v in obj]
    return obj


class RagflowAdapter:
    supports_citations = True

    def __init__(self, base_url: str, api_key: str, *, name: str = "ragflow",
                 dataset_config: dict[str, Any] | None = None, chat_config: dict[str, Any] | None = None,
                 poll_interval: float = 3.0, ingest_timeout_s: float = 3600, ask_timeout_s: float = 180,
                 max_poll_failures: int = 10, image_base_url: str | None = None,
                 existing_kbs: list[dict] | None = None, existing_chat: dict[str, str] | None = None,
                 chat_overrides: dict[str, Any] | None = None, client: httpx.AsyncClient | None = None):
        self.name = name
        self.image_base_url = image_base_url
        # 给了已有知识库就不建库：分块等建库参数用不上，也就不校验
        self.existing_kbs = existing_kbs or []
        # 给了已有助手就什么都不建：问的就是用户在系统里配好的那个助手，设置全以它为准
        self.existing_chat = existing_chat
        # 参数对照：复制已有助手、只改这几项参数，建一个临时助手问，问完删掉
        self.chat_overrides = chat_overrides or {}
        if self.chat_overrides and not existing_chat:
            raise AdapterError("参数对照要以一个已有助手为基准")
        if {"dataset_ids", "kb_ids"} & set(self.chat_overrides):
            raise AdapterError("参数对照不能改助手挂的知识库：要换知识库请另选助手，或用「完整测试」")
        if "show_quotation" in (self.chat_overrides.get("prompt") or {}):
            raise AdapterError('引用开关写在顶层 do_refer（"1" 开、"0" 关），不在 prompt 里')
        if isinstance(self.chat_overrides.get("prompt"), dict):
            self.chat_overrides = {**self.chat_overrides, "prompt": _to_create_prompt(self.chat_overrides["prompt"])}
        # 助手挂的库随时可能被改，不复用入库缓存，每次开跑都重新核对；
        # 已有知识库模式建的助手用完就删，也不能进共用缓存给别的评测复用
        self.reuse_ingest = not existing_chat and not existing_kbs
        self.dataset_config = {} if self.existing_kbs or existing_chat else dataset_config or {}
        check_chunk_method("knowflow" if name == "knowflow" else "ragflow", self.dataset_config)
        self.chat_config = {} if existing_chat else chat_config or {}
        self.poll_interval = poll_interval
        self.ingest_timeout_s = ingest_timeout_s
        self.max_poll_failures = max_poll_failures
        self._client = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=ask_timeout_s)
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def _call(self, method: str, path: str, prefix: str = "/api/v1", **kw) -> Any:
        resp = await self._client.request(method, f"{prefix}{path}", headers=self._headers, **kw)
        try:
            body = resp.json()
        except ValueError as e:
            raise AdapterError(f"{method} {path} 返回 {resp.status_code}，不是 JSON") from e
        if resp.status_code >= 400 or body.get("code") != 0:
            raise AdapterError(f"{method} {path} 失败：{body.get('message') or resp.status_code}")
        return body.get("data")

    async def _create_dataset(self, kb_name: str) -> str:
        ds = await self._call("POST", "/datasets", json={"name": kb_name, **self.dataset_config})
        return ds["id"]

    async def ingest(self, files: list[Path], kb_name: str) -> IngestReport:
        if self.existing_chat:
            return await (self._variant(files, kb_name) if self.chat_overrides else self._use_chat(files))
        if self.existing_kbs:
            return await self._attach(files, kb_name)
        ds_id = await self._create_dataset(kb_name)
        doc_ids: dict[str, str] = {}
        for f in files:
            uploaded = await self._call("POST", f"/datasets/{ds_id}/documents",
                                        files={"file": (f.name, f.read_bytes())})
            doc_ids[uploaded[0]["id"]] = f.name
        await self._call("POST", f"/datasets/{ds_id}/chunks", json={"document_ids": list(doc_ids)})
        docs = await self._wait_parsed(ds_id, doc_ids)
        if not any(d.ok for d in docs):
            # 否则下一步建助手会报「doesn't own parsed file」，看不出真正的原因
            raise AdapterError(f"全部 {len(docs)} 份文档入库失败，例如 {docs[0].name}：{(docs[0].error or '')[-300:]}")
        chat = await self._call("POST", "/chats", json={"name": kb_name, "dataset_ids": [ds_id], **self.chat_config})
        return IngestReport(kb=KbHandle(system=self.name, ids={"dataset_id": ds_id, "chat_id": chat["id"]}), docs=docs)

    async def _list_docs(self, ds_id: str) -> list[dict]:
        docs, page = [], 1
        while True:
            listing = await self._call("GET", f"/datasets/{ds_id}/documents", params={"page": page, "page_size": 1000})
            batch = listing.get("docs", [])
            docs += batch
            if len(batch) < 1000:
                return docs
            page += 1

    async def _attach(self, files: list[Path], kb_name: str) -> IngestReport:
        """用已有知识库：核对测试集用到的文档都在库里且解析完，再建一个助手问答。不建库、不上传、不改库。"""
        kbs = "、".join(kb.get("name") or kb["id"] for kb in self.existing_kbs)
        docs = await self._check_docs(files, self.existing_kbs, f"知识库（{kbs}）")
        ids = [kb["id"] for kb in self.existing_kbs]
        chat = await self._call("POST", "/chats", json={"name": kb_name, "dataset_ids": ids, **self.chat_config})
        return IngestReport(kb=KbHandle(system=self.name, ids={"dataset_id": ",".join(ids), "chat_id": chat["id"],
                                                               "temp_chat": "1"}),
                            docs=docs, existing=True)

    async def _get_chat(self, chat_id: str) -> dict:
        label = f"{self.existing_chat.get('name') or chat_id}（{chat_id}）" if self.existing_chat else chat_id
        try:
            found = await self._call("GET", "/chats", params={"id": chat_id})
        except AdapterError as e:
            raise AdapterError(f"找不到助手 {label}：{e}") from e
        if not found:
            raise AdapterError(f"找不到助手 {label}")
        return found[0]

    async def _check_chat(self, files: list[Path]) -> tuple[dict, list[dict], list[DocIngest]]:
        """取已有助手，核对它挂的知识库里有测试集的文档且解析完。"""
        chat = await self._get_chat(self.existing_chat["id"])
        name = chat.get("name") or self.existing_chat.get("name") or chat["id"]
        kbs = [{"id": d["id"], "name": d.get("name", "")} for d in chat.get("datasets") or []]
        if not kbs:
            raise AdapterError(f"助手「{name}」没有挂知识库，没法回答测试集的题")
        kb_names = "、".join(kb["name"] or kb["id"] for kb in kbs)
        docs = await self._check_docs(files, kbs, f"助手「{name}」挂的知识库（{kb_names}）")
        return chat, kbs, docs

    async def _use_chat(self, files: list[Path]) -> IngestReport:
        """用已有助手：核对文档后直接问它。什么都不建、不改。"""
        chat, kbs, docs = await self._check_chat(files)
        return IngestReport(kb=KbHandle(system=self.name, ids={"chat_id": chat["id"],
                                                               "dataset_id": ",".join(kb["id"] for kb in kbs)}),
                            docs=docs, existing=True)

    async def _variant(self, files: list[Path], kb_name: str) -> IngestReport:
        """参数对照：复制基准助手的全部设置，改上这几项，挂同样的知识库建一个临时助手。问完由 release 删掉。"""
        base, kbs, docs = await self._check_chat(files)
        ids = [kb["id"] for kb in kbs]
        body = finish_variant_body(deep_merge(clone_chat_body(base), self.chat_overrides))
        chat = await self._call("POST", "/chats", json={**body, "name": kb_name, "dataset_ids": ids})
        return IngestReport(kb=KbHandle(system=self.name, ids={"chat_id": chat["id"], "dataset_id": ",".join(ids),
                                                               "temp_chat": "1"}),
                            docs=docs, existing=True)

    async def release(self, kb: KbHandle) -> None:
        """评测台为提问临时建的助手（参数对照、已有知识库模式）用完删掉；用户自己的助手和按配置建的库不动。"""
        if kb.ids.get("temp_chat") == "1":
            await self._call("DELETE", "/chats", json={"ids": [kb.ids["chat_id"]]})

    async def snapshot(self, kb: KbHandle) -> dict:
        """提问用的助手此刻的配置：模型、提示词、ColPali、SAG 策略、挂的知识库及其分块与 SAG 开关。

        写进 run.json，报告里能看到这次到底测的是什么配置；同一个运行目录续跑时配置变了就拒绝。
        """
        return _stable(drop_secret_keys(await self._get_chat(kb.ids["chat_id"])))

    async def _check_docs(self, files: list[Path], kbs: list[dict], where: str) -> list[DocIngest]:
        """测试集用到的文档都要在这些库里且解析完。

        文档不在库里，系统答不出来不代表它差，所以缺文档或没解析完一律报错，不带着缺口出分数。
        """
        in_kb: dict[str, str] = {}
        for kb in kbs:
            for d in await self._list_docs(kb["id"]):
                in_kb.setdefault(d.get("name", ""), d.get("run", ""))
        missing, unparsed = [], []
        for f in files:
            # 从多个库取文档建测试集时，同名不同内容的文件加过 -2、-3 后缀
            names = [f.name, _DEDUP_SUFFIX.sub("", f.name)]
            run = next((in_kb[n] for n in names if n in in_kb), None)
            if run is None:
                missing.append(f.name)
            elif run != "DONE":
                unparsed.append(f.name)
        if missing:
            raise AdapterError(f"{where}里没有这些文档：{'、'.join(missing)}。题目依据的文档不在库里，答不出来不代表系统差")
        if unparsed:
            raise AdapterError(f"{where}里这些文档还没解析完：{'、'.join(unparsed)}")
        return [DocIngest(name=f.name, ok=True) for f in files]

    async def _wait_parsed(self, ds_id: str, doc_ids: dict[str, str]) -> list[DocIngest]:
        start = time.monotonic()
        finished: dict[str, DocIngest] = {}
        failures = 0
        while True:
            # 解析期间服务端常常很忙，一次连不上不代表入库失败；连续失败太多次才放弃
            try:
                listing = await self._call("GET", f"/datasets/{ds_id}/documents", params={"page": 1, "page_size": 1000})
                failures = 0
            except (httpx.TransportError, AdapterError) as e:
                failures += 1
                if failures >= self.max_poll_failures:
                    raise AdapterError(f"查询解析进度连续 {failures} 次失败：{e}") from e
                await asyncio.sleep(self.poll_interval)
                continue
            for d in listing.get("docs", []):
                if d["id"] in doc_ids and d["id"] not in finished and d.get("run") in _TERMINAL:
                    ok = d["run"] == "DONE"
                    finished[d["id"]] = DocIngest(name=doc_ids[d["id"]], ok=ok,
                                                  seconds=round(time.monotonic() - start, 1),
                                                  error=None if ok else (d.get("progress_msg") or d["run"]))
            if len(finished) == len(doc_ids):
                return [finished[i] for i in doc_ids]
            if time.monotonic() - start >= self.ingest_timeout_s:
                pending = [doc_ids[i] for i in doc_ids if i not in finished]
                raise AdapterError(f"入库超时（{self.ingest_timeout_s}s），未完成：{pending}")
            await asyncio.sleep(self.poll_interval)

    async def ask(self, kb: KbHandle, question: str) -> Answer:
        chat_id = kb.ids["chat_id"]
        session = await self._call("POST", f"/chats/{chat_id}/sessions", json={"name": f"knoweval-{uuid.uuid4().hex[:8]}"})
        start = time.monotonic()
        data = await self._call("POST", f"/chats/{chat_id}/completions",
                                json={"question": question, "stream": False, "session_id": session["id"]})
        answer = parse_completion(data, latency_ms=int((time.monotonic() - start) * 1000))
        refs = image_candidates(data)
        if refs:
            answer = answer.model_copy(update={"images": [await self._hash_image(r) for r in refs]})
        return answer

    async def _hash_image(self, ref: ImageRef) -> ImageRef:
        # 正文里的 /minio/... 由网页端口的 nginx 提供，不在 API 端口；/v1/document/image 在 API 端口
        base = self.image_base_url if ref.source.startswith("/minio/") else None
        return await hash_ref(self._client, ref, self._headers, base)

    async def kb_alive(self, kb: KbHandle) -> bool:
        """缓存的知识库和助手还在不在。被删了就要重新入库，否则每题都会变成 ERROR。"""
        try:
            chats = await self._call("GET", "/chats", params={"id": kb.ids["chat_id"]})
        except AdapterError:
            return False
        return bool(chats)

    async def delete_kb(self, kb: KbHandle) -> None:
        """删掉评测台建的知识库（重新入库前清理上次建了一半的库）。"""
        await self._call("DELETE", "/datasets", json={"ids": [kb.ids["dataset_id"]]})

    async def aclose(self) -> None:
        await self._client.aclose()


class KnowflowAdapter(RagflowAdapter):
    """KnowFlow 沿用 RAGFlow 的 /api/v1 接口，只有建库不同。

    KnowFlow 的 /api/v1/datasets 校验只认上游 RAGFlow 的分块方式，拒绝 smart、title、parent_child 等
    KnowFlow 自己的分块方式（主仓已修，待发版），所以建库改走前端用的 /v1/kb/create：它同样认 API Key，
    但要显式给出嵌入模型。上传、解析、问答仍走 /api/v1。
    """

    def __init__(self, base_url: str, api_key: str, **kw):
        kw.setdefault("name", "knowflow")
        super().__init__(base_url, api_key, **kw)
        if not self.existing_kbs and not self.existing_chat and not self.dataset_config.get("embedding_model"):
            raise AdapterError("KnowFlow 建库必须在 dataset.embedding_model 里写明嵌入模型，例如 BAAI/bge-m3@SILICONFLOW")

    async def _create_dataset(self, kb_name: str) -> str:
        cfg = dict(self.dataset_config)
        body = {"name": kb_name, "parser_id": cfg.pop("chunk_method", "naive"), "embd_id": cfg.pop("embedding_model")}
        body.update(cfg)
        created = await self._call("POST", "/kb/create", prefix="/v1", json=body)
        return created["kb_id"]

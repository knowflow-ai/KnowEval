"""RAGFlow 适配器：用 httpx.MockTransport 模拟 /api/v1 接口。"""

import json

import httpx
import pytest

from knoweval.adapters.base import AdapterError
from knoweval.adapters.ragflow import RagflowAdapter, parse_completion


class FakeRagflow:
    def __init__(self, runs=("RUNNING", "DONE"), answer=None, fail_doc=None):
        self.runs = list(runs)
        self.fail_doc = fail_doc
        self.answer = answer or {
            "answer": "默认管理端口是 9400 [ID:1]。",
            "reference": {"chunks": [
                {"content": "手册：管理端口 9300", "document_name": "04.pdf", "positions": [[2, 0, 0, 0, 0]]},
                {"content": "默认管理端口由 9300 调整为 9400", "document_name": "05.pdf", "positions": [[1, 0, 0, 0, 0]]},
            ]},
        }
        self.sessions = 0
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        assert request.headers["Authorization"] == "Bearer k"
        if path == "/api/v1/datasets" and method == "POST":
            return _ok({"id": "ds1"})
        if path == "/api/v1/datasets/ds1/documents" and method == "POST":
            name = request.content.decode("utf-8", "ignore").split('filename="')[1].split('"')[0]
            return _ok([{"id": f"doc-{name}", "name": name}])
        if path == "/api/v1/datasets/ds1/chunks" and method == "POST":
            return _ok(None)
        if path == "/api/v1/datasets/ds1/documents" and method == "GET":
            run = self.runs.pop(0) if len(self.runs) > 1 else self.runs[0]
            docs = [{"id": "doc-a.pdf", "name": "a.pdf", "run": run, "progress_msg": ""},
                    {"id": "doc-b.pdf", "name": "b.pdf",
                     "run": "FAIL" if self.fail_doc == "b.pdf" and run == "DONE" else run,
                     "progress_msg": "解析超时"}]
            return _ok({"total": 2, "docs": docs})
        if path == "/api/v1/chats" and method == "POST":
            return _ok({"id": "chat1"})
        if path == "/api/v1/chats/chat1/sessions" and method == "POST":
            self.sessions += 1
            return _ok({"id": f"s{self.sessions}"})
        if path == "/api/v1/chats/chat1/completions" and method == "POST":
            body = json.loads(request.content)
            assert body["stream"] is False and body["session_id"].startswith("s")
            return _ok(self.answer)
        return httpx.Response(404, json={"code": 404, "message": f"no route {method} {path}"})


def _ok(data):
    return httpx.Response(200, json={"code": 0, "data": data})


def _adapter(fake, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake), base_url="http://rf")
    return RagflowAdapter(base_url="http://rf", api_key="k", client=client, poll_interval=0, **kw)


@pytest.fixture
def files(tmp_path):
    out = []
    for n in ("a.pdf", "b.pdf"):
        p = tmp_path / n
        p.write_bytes(b"%PDF-1.4 fake")
        out.append(p)
    return out


async def test_ingest_uploads_parses_and_creates_chat(files):
    fake = FakeRagflow()
    report = await _adapter(fake).ingest(files, kb_name="kb")
    assert report.kb.ids == {"dataset_id": "ds1", "chat_id": "chat1"}
    assert [d.ok for d in report.docs] == [True, True]


async def test_ingest_records_failed_documents(files):
    report = await _adapter(FakeRagflow(fail_doc="b.pdf")).ingest(files, kb_name="kb")
    failed = [d for d in report.docs if not d.ok]
    assert [d.name for d in failed] == ["b.pdf"]
    assert "解析超时" in failed[0].error


async def test_ingest_times_out(files):
    fake = FakeRagflow(runs=("RUNNING",))
    with pytest.raises(AdapterError, match="超时"):
        await _adapter(fake, ingest_timeout_s=0).ingest(files, kb_name="kb")


async def test_each_question_gets_a_fresh_session(files):
    fake = FakeRagflow()
    adapter = _adapter(fake)
    report = await adapter.ingest(files, kb_name="kb")
    await adapter.ask(report.kb, "问题一")
    await adapter.ask(report.kb, "问题二")
    assert fake.sessions == 2


async def test_ask_maps_cited_chunks_and_strips_markers(files):
    fake = FakeRagflow()
    adapter = _adapter(fake)
    kb = (await adapter.ingest(files, kb_name="kb")).kb
    ans = await adapter.ask(kb, "v3.0 默认管理端口？")
    assert ans.text == "默认管理端口是 9400。"
    assert "[ID:1]" in ans.raw_text
    assert [(c.doc, c.page) for c in ans.citations] == [("05.pdf", 1)]
    assert len(ans.contexts) == 2
    assert ans.latency_ms is not None


async def test_business_error_raises_adapter_error(files):
    def handler(request):
        return httpx.Response(200, json={"code": 102, "message": "You don't own the chat"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://rf")
    adapter = RagflowAdapter(base_url="http://rf", api_key="k", client=client)
    from knoweval.adapters.base import KbHandle
    with pytest.raises(AdapterError, match="own the chat"):
        await adapter.ask(KbHandle(system="ragflow", ids={"dataset_id": "x", "chat_id": "chat1"}), "q")


def test_parse_completion_ignores_out_of_range_markers():
    ans = parse_completion({"answer": "结论 [ID:7] [ID:0]", "reference": {"chunks": [
        {"content": "c0", "document_name": "a.pdf", "positions": []}]}}, latency_ms=5)
    assert [c.doc for c in ans.citations] == ["a.pdf"]
    assert ans.citations[0].page is None


async def test_polling_survives_transient_connection_errors(files):
    fake = FakeRagflow()
    state = {"fails": 2}

    def flaky(request):
        if request.method == "GET" and request.url.path.endswith("/documents") and state["fails"]:
            state["fails"] -= 1
            raise httpx.ConnectError("All connection attempts failed")
        return fake(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(flaky), base_url="http://rf")
    adapter = RagflowAdapter(base_url="http://rf", api_key="k", client=client, poll_interval=0)
    report = await adapter.ingest(files, kb_name="kb")
    assert all(d.ok for d in report.docs)


async def test_polling_gives_up_after_too_many_consecutive_failures(files):
    fake = FakeRagflow()

    def always_down(request):
        if request.method == "GET" and request.url.path.endswith("/documents"):
            raise httpx.ConnectError("down")
        return fake(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(always_down), base_url="http://rf")
    adapter = RagflowAdapter(base_url="http://rf", api_key="k", client=client, poll_interval=0, max_poll_failures=3)
    with pytest.raises(AdapterError, match="连续 3 次"):
        await adapter.ingest(files, kb_name="kb")


class FakeKnowflow(FakeRagflow):
    """KnowFlow 的 /api/v1/datasets 会拒绝 smart，建库只能走前端用的 /v1/kb/create。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.created = None

    def __call__(self, request):
        if request.url.path == "/api/v1/datasets" and request.method == "POST":
            return httpx.Response(200, json={"code": 101, "message": "Input should be 'naive', ..."})
        if request.url.path == "/v1/kb/create" and request.method == "POST":
            self.created = json.loads(request.content)
            return _ok({"kb_id": "ds1"})
        return super().__call__(request)


async def test_knowflow_creates_the_dataset_through_the_web_api(files):
    from knoweval.adapters.ragflow import KnowflowAdapter

    fake = FakeKnowflow()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake), base_url="http://rf")
    adapter = KnowflowAdapter(base_url="http://rf", api_key="k", client=client, poll_interval=0,
                              dataset_config={"chunk_method": "smart", "embedding_model": "BAAI/bge-m3@X",
                                              "parser_config": {"layout_recognize": "MinerU"}})
    report = await adapter.ingest(files, kb_name="kb")
    assert report.kb.ids["dataset_id"] == "ds1"
    assert fake.created == {"name": "kb", "parser_id": "smart", "embd_id": "BAAI/bge-m3@X",
                            "parser_config": {"layout_recognize": "MinerU"}}


def test_knowflow_requires_an_embedding_model():
    from knoweval.adapters.ragflow import KnowflowAdapter

    with pytest.raises(AdapterError, match="embedding_model"):
        KnowflowAdapter(base_url="http://rf", api_key="k", dataset_config={"chunk_method": "smart"})


async def test_all_documents_failing_names_the_real_cause(files):
    fake = FakeRagflow(runs=("FAIL",))
    with pytest.raises(AdapterError, match="全部 2 份文档入库失败"):
        await _adapter(fake).ingest(files, kb_name="kb")
    assert not any(r.url.path == "/api/v1/chats" for r in fake.requests)


def test_adapter_rejects_unknown_chunk_method():
    import pytest
    from knoweval.adapters.base import AdapterError
    from knoweval.adapters.ragflow import KnowflowAdapter, RagflowAdapter
    with pytest.raises(AdapterError, match="parent_child"):
        KnowflowAdapter("http://x", "k", dataset_config={"chunk_method": "parent-child", "embedding_model": "e"})
    KnowflowAdapter("http://x", "k", dataset_config={"chunk_method": "parent_child", "embedding_model": "e"})
    RagflowAdapter("http://x", "k", dataset_config={"chunk_method": "naive"})


class FakeExisting:
    """已有知识库：只许列文档和建助手，任何建库、上传、解析请求都算错。"""

    def __init__(self, kbs: dict[str, list[dict]]):
        self.kbs = kbs
        self.chat_body = None
        self.writes = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        for kb, docs in self.kbs.items():
            if path == f"/api/v1/datasets/{kb}/documents" and method == "GET":
                page = int(request.url.params.get("page", "1"))
                size = int(request.url.params.get("page_size", "30"))
                return _ok({"total": len(docs), "docs": docs[(page - 1) * size: page * size]})
        if path == "/api/v1/chats" and method == "POST":
            self.chat_body = json.loads(request.content)
            return _ok({"id": "chat9"})
        self.writes.append(f"{method} {path}")
        return httpx.Response(404, json={"code": 404, "message": f"no route {method} {path}"})


def _existing(fake, kbs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake), base_url="http://rf")
    return RagflowAdapter(base_url="http://rf", api_key="k", client=client, poll_interval=0,
                          existing_kbs=[{"id": k, "name": f"库{k}"} for k in kbs])


async def test_existing_kb_skips_creating_and_uploading(files, tmp_path):
    """系统里已经有知识库：不建库、不上传、不解析，核对文档都在且解析完，直接建助手问答。"""
    renamed = tmp_path / "b-2.pdf"            # 从多个库建测试集时，同名不同内容的文件被加了 -2 后缀
    files[1].rename(renamed)
    fake = FakeExisting({"kb1": [{"id": "1", "name": "a.pdf", "run": "DONE"}, {"id": "9", "name": "无关.pdf", "run": "FAIL"}],
                         "kb2": [{"id": "2", "name": "b.pdf", "run": "DONE"}]})
    report = await _existing(fake, ["kb1", "kb2"]).ingest([files[0], renamed], kb_name="kb")
    assert fake.writes == []
    assert fake.chat_body["dataset_ids"] == ["kb1", "kb2"]
    assert report.existing and report.kb.ids["chat_id"] == "chat9"
    assert [(d.name, d.ok) for d in report.docs] == [("a.pdf", True), ("b-2.pdf", True)]


async def test_existing_kb_missing_or_unparsed_documents_fail_clearly(files):
    fake = FakeExisting({"kb1": [{"id": "1", "name": "a.pdf", "run": "RUNNING"}]})
    with pytest.raises(AdapterError, match=r"没有这些文档：b\.pdf"):
        await _existing(fake, ["kb1"]).ingest(files, kb_name="kb")
    fake = FakeExisting({"kb1": [{"id": "1", "name": "a.pdf", "run": "RUNNING"}, {"id": "2", "name": "b.pdf", "run": "DONE"}]})
    with pytest.raises(AdapterError, match=r"还没解析完.*a\.pdf"):
        await _existing(fake, ["kb1"]).ingest(files, kb_name="kb")


async def test_existing_kb_reads_every_page_of_documents(files):
    many = [{"id": str(i), "name": f"x{i}.pdf", "run": "DONE"} for i in range(1500)]
    fake = FakeExisting({"kb1": many + [{"id": "a", "name": "a.pdf", "run": "DONE"}, {"id": "b", "name": "b.pdf", "run": "DONE"}]})
    report = await _existing(fake, ["kb1"]).ingest(files, kb_name="kb")
    assert all(d.ok for d in report.docs)


def test_existing_kb_on_knowflow_needs_no_embedding_model():
    from knoweval.adapters.ragflow import KnowflowAdapter
    KnowflowAdapter(base_url="http://kf", api_key="k", existing_kbs=[{"id": "kb1", "name": "库"}])


async def test_delete_kb_removes_the_dataset():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return _ok(None)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://rf")
    adapter = RagflowAdapter(base_url="http://rf", api_key="k", client=client)
    from knoweval.adapters.base import KbHandle
    await adapter.delete_kb(KbHandle(system="ragflow", ids={"dataset_id": "ds1"}))
    assert seen == [("DELETE", "/api/v1/datasets", {"ids": ["ds1"]})]


# ---------- 已有助手：直接问用户在 KnowFlow/RAGFlow 里配好的助手 ----------

CHAT = {
    "id": "chatA", "name": "线上助手", "create_time": 1, "update_time": 2, "avatar": "x",
    "llm": {"model_name": "qwen-vl@Tongyi", "temperature": 0.1},
    "prompt": {"use_colpali": True, "sag_search_strategy": "atomic", "top_n": 8, "rerank_model": "bge",
               "prompt": "你是助手 {knowledge}", "api_key": "不该出现"},
    "datasets": [{"id": "kb1", "name": "产品手册", "chunk_method": "colpali", "embedding_model": "bge-m3",
                  "parser_config": {"sag": {"use_sag": True}}, "doc_num": 3, "chunk_num": 99,
                  "update_time": 5, "update_date": "2026-10-07"}],
}


class FakeChat(FakeExisting):
    """已有助手：只许读助手和列文档，任何建库、建助手、上传的请求都算错。"""

    def __init__(self, kbs, chats):
        super().__init__(kbs)
        self.chats = chats

    def __call__(self, request):
        if request.url.path == "/api/v1/chats" and request.method == "GET":
            found = [c for c in self.chats if c["id"] == request.url.params.get("id")]
            return _ok(found) if found else httpx.Response(200, json={"code": 102, "message": "The chat doesn't exist"})
        if request.method != "GET":
            self.writes.append(f"{request.method} {request.url.path}")
            return httpx.Response(500)
        return super().__call__(request)


def _chat_adapter(fake, chat_id="chatA"):
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake), base_url="http://rf")
    return RagflowAdapter(base_url="http://rf", api_key="k", client=client, existing_chat={"id": chat_id, "name": "线上助手"})


async def test_existing_chat_is_asked_as_is_without_creating_anything(files):
    fake = FakeChat({"kb1": [{"id": "1", "name": "a.pdf", "run": "DONE"}, {"id": "2", "name": "b.pdf", "run": "DONE"}]},
                    [CHAT])
    adapter = _chat_adapter(fake)
    report = await adapter.ingest(files, kb_name="kb")
    assert fake.writes == [] and report.existing
    assert report.kb.ids == {"chat_id": "chatA", "dataset_id": "kb1"}
    assert adapter.reuse_ingest is False   # 助手挂的库随时可能变，每次开跑都重新核对


async def test_existing_chat_must_cover_the_test_set_documents(files):
    fake = FakeChat({"kb1": [{"id": "1", "name": "a.pdf", "run": "DONE"}]}, [CHAT])
    with pytest.raises(AdapterError, match=r"线上助手.*没有这些文档：b\.pdf"):
        await _chat_adapter(fake).ingest(files, kb_name="kb")
    with pytest.raises(AdapterError, match="找不到助手"):
        await _chat_adapter(FakeChat({}, [CHAT]), "gone").ingest(files, kb_name="kb")
    empty = {**CHAT, "datasets": []}
    with pytest.raises(AdapterError, match="没有挂知识库"):
        await _chat_adapter(FakeChat({}, [empty])).ingest(files, kb_name="kb")


async def test_existing_chat_snapshot_keeps_what_affects_answers():
    """快照记下影响回答的设置（模型、ColPali、SAG、提示词、知识库的分块与 SAG 开关），去掉时间、计数和密钥。"""
    from knoweval.adapters.base import KbHandle
    adapter = _chat_adapter(FakeChat({}, [CHAT]))
    snap = await adapter.snapshot(KbHandle(system="ragflow", ids={"chat_id": "chatA", "dataset_id": "kb1"}))
    assert snap["prompt"]["use_colpali"] is True and snap["prompt"]["sag_search_strategy"] == "atomic"
    assert snap["llm"]["model_name"] == "qwen-vl@Tongyi"
    assert snap["datasets"][0]["parser_config"] == {"sag": {"use_sag": True}}
    flat = json.dumps(snap, ensure_ascii=False)
    for gone in ("update_time", "create_time", "update_date", "doc_num", "chunk_num", "avatar", "api_key", "不该出现"):
        assert gone not in flat


def test_knowflow_accepts_the_colpali_chunk_method():
    from knoweval.adapters.ragflow import KnowflowAdapter
    KnowflowAdapter("http://x", "k", dataset_config={"chunk_method": "colpali", "embedding_model": "e"})
    with pytest.raises(AdapterError):
        RagflowAdapter("http://x", "k", dataset_config={"chunk_method": "colpali"})


async def test_snapshot_keeps_token_and_keyword_settings():
    from knoweval.adapters.base import KbHandle
    chat = {**CHAT, "llm": {"model_name": "m", "max_tokens": 512},
            "datasets": [{**CHAT["datasets"][0], "parser_config": {"chunk_token_num": 256, "auto_keywords": 3}}]}
    snap = await _chat_adapter(FakeChat({}, [chat])).snapshot(KbHandle(system="ragflow", ids={"chat_id": "chatA"}))
    assert snap["llm"]["max_tokens"] == 512
    assert snap["datasets"][0]["parser_config"] == {"chunk_token_num": 256, "auto_keywords": 3}


# ---------- 参数对照：复制已有助手、只改几个参数，问完就删 ----------

BASE = {
    "id": "chatA", "name": "线上助手", "tenant_id": "t1", "avatar": "", "status": "1", "create_time": 1, "update_date": "x",
    "description": "d", "language": "Chinese", "top_k": 1024, "vlm_top_n": 3, "do_refer": "1", "visibility": "tenant",
    "llm": {"model_name": "qwen@X", "temperature": 0.1, "max_tokens": 512},
    "prompt": {"similarity_threshold": 0.2, "keywords_similarity_weight": 0.7, "top_n": 8, "rerank_model": "bge-rr",
               "prompt": "你是助手 {knowledge}", "variables": [{"key": "knowledge", "optional": False}],
               "opener": "你好", "show_quote": True, "show_quotation": False, "sag_search_strategy": "multi", "use_colpali": False},
    "datasets": [{"id": "kb1", "name": "手册"}],
}


class FakeVariantChat(FakeChat):
    """读基准助手、建对照助手、删对照助手；其余写操作都算错。"""

    def __init__(self, kbs, chats):
        super().__init__(kbs, chats)
        self.created, self.deleted = [], []

    def __call__(self, request):
        path, method = request.url.path, request.method
        if path == "/api/v1/chats" and method == "POST":
            self.created.append(json.loads(request.content))
            return _ok({"id": f"var{len(self.created)}"})
        if path == "/api/v1/chats" and method == "DELETE":
            self.deleted += json.loads(request.content)["ids"]
            return _ok(None)
        return super().__call__(request)


def _variant_adapter(fake, overrides):
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake), base_url="http://rf")
    return RagflowAdapter(base_url="http://rf", api_key="k", client=client, existing_chat={"id": "chatA", "name": "线上助手"},
                          chat_overrides=overrides)


async def test_variant_copies_the_base_assistant_and_changes_only_the_given_params(files):
    fake = FakeVariantChat({"kb1": [{"id": "1", "name": "a.pdf", "run": "DONE"}, {"id": "2", "name": "b.pdf", "run": "DONE"}]}, [BASE])
    adapter = _variant_adapter(fake, {"prompt": {"sag_search_strategy": "atomic"}, "llm": {"max_tokens": 4096}})
    report = await adapter.ingest(files, kb_name="knoweval-x-1")
    [body] = fake.created
    assert body["name"] == "knoweval-x-1" and body["dataset_ids"] == ["kb1"]
    assert body["llm"] == {"model_name": "qwen@X", "temperature": 0.1, "max_tokens": 4096}
    p = body["prompt"]
    assert p["sag_search_strategy"] == "atomic" and p["top_n"] == 8 and p["rerank_model"] == "bge-rr"
    # 读接口给的是关键词权重（=1-向量权重），建接口要的是向量权重：要换算回去，否则对照组的权重被反转
    assert "keywords_similarity_weight" not in p and p["vector_similarity_weight"] == pytest.approx(0.3)
    assert "show_quotation" not in p and body["do_refer"] == "1"     # 引用开关是助手的顶层字段，原样带过去
    assert body["top_k"] == 1024 and body["vlm_top_n"] == 3 and body["language"] == "Chinese"
    assert body["visibility"] == "private"       # 临时助手不共享给别人，哪怕基准助手是共享的
    for gone in ("id", "tenant_id", "avatar", "status", "create_time", "update_date", "datasets"):
        assert gone not in body
    assert report.kb.ids["chat_id"] == "var1" and report.kb.ids.get("temp_chat") == "1"
    await adapter.release(report.kb)
    assert fake.deleted == ["var1"]


async def test_plain_existing_assistant_is_never_deleted(files):
    fake = FakeVariantChat({"kb1": [{"id": "1", "name": "a.pdf", "run": "DONE"}, {"id": "2", "name": "b.pdf", "run": "DONE"}]}, [BASE])
    adapter = _variant_adapter(fake, None)
    report = await adapter.ingest(files, kb_name="kb")
    await adapter.release(report.kb)
    assert fake.created == [] and fake.deleted == []


async def test_variant_cannot_change_which_knowledge_bases_are_asked(files):
    with pytest.raises(AdapterError, match="知识库"):
        _variant_adapter(FakeVariantChat({}, [BASE]), {"dataset_ids": ["other"]})


async def test_temporary_assistant_on_existing_kbs_is_released(files):
    """「已有知识库」模式为提问建的助手，用完也删掉，不在用户系统里堆 knoweval- 助手。"""
    fake = FakeExisting({"kb1": [{"id": "1", "name": "a.pdf", "run": "DONE"}, {"id": "2", "name": "b.pdf", "run": "DONE"}]})
    deleted = []
    inner = fake.__call__

    def handler(request):
        if request.url.path == "/api/v1/chats" and request.method == "DELETE":
            deleted.extend(json.loads(request.content)["ids"])
            return _ok(None)
        return inner(request)

    adapter = _existing(handler, ["kb1"])
    report = await adapter.ingest(files, kb_name="kb")
    await adapter.release(report.kb)
    assert deleted == ["chat9"]


async def test_variant_overrides_use_read_side_names_correctly(files):
    """照着快照里看到的名字写对照（关键词权重），也要换算成建接口要的向量权重，不能反过来。"""
    docs = {"kb1": [{"id": "1", "name": "a.pdf", "run": "DONE"}, {"id": "2", "name": "b.pdf", "run": "DONE"}]}
    fake = FakeVariantChat(docs, [BASE])
    await _variant_adapter(fake, {"prompt": {"keywords_similarity_weight": 0.9}}).ingest(files, kb_name="k")
    p = fake.created[0]["prompt"]
    assert "keywords_similarity_weight" not in p and p["vector_similarity_weight"] == pytest.approx(0.1)
    with pytest.raises(AdapterError, match="do_refer"):
        _variant_adapter(fake, {"prompt": {"show_quotation": True}})


async def test_variant_of_an_assistant_whose_prompt_has_no_knowledge_slot_can_still_be_built(files):
    """网页端建的助手，提示词里可能没有 {knowledge}：建接口会拒绝，复制时把这个变量标成可选，行为和基准一致。"""
    base = {**BASE, "prompt": {**BASE["prompt"], "prompt": "你是一个客服"}}
    docs = {"kb1": [{"id": "1", "name": "a.pdf", "run": "DONE"}, {"id": "2", "name": "b.pdf", "run": "DONE"}]}
    fake = FakeVariantChat(docs, [base])
    await _variant_adapter(fake, {"prompt": {"top_n": 3}}).ingest(files, kb_name="k")
    assert fake.created[0]["prompt"]["variables"] == [{"key": "knowledge", "optional": True}]


def test_existing_kb_mode_does_not_share_its_temporary_assistant_through_the_cache():
    """已有知识库模式建的助手用完就删，所以不能进共用的入库缓存给别的评测复用。"""
    adapter = RagflowAdapter("http://x", "k", existing_kbs=[{"id": "kb1", "name": "库"}])
    assert adapter.reuse_ingest is False

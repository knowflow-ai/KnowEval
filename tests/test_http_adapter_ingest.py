"""通用 HTTP 适配器的进阶能力：自动入库、业务成功码、正文引用标记、取图。"""

import json

import httpx
import pytest

from knoweval.adapters.base import AdapterError, KbHandle
from knoweval.adapters.http import HttpAdapter
from knoweval.images import dhash
from tests.test_images import _png

ASK = {"path": "/chat", "body": {"q": "{{question}}", "kb": "{{kb_id}}"}, "answer": "$.data.answer"}
INGEST = {
    "create_kb": {"path": "/kb", "body": {"name": "{{kb_name}}"}, "id": "$.data.id"},
    "upload": {"path": "/kb/{{kb_id}}/docs", "file_field": "upload", "form": {"kb": "{{kb_id}}", "parse": "auto"},
               "doc_id": "$.data.doc_id"},
    "status": {"path": "/kb/{{kb_id}}/docs/{{doc_id}}", "state": "$.data.state", "done": ["DONE"],
               "failed": ["FAIL"], "error": "$.data.msg"},
    "poll_interval_s": 0,
}


def _adapter(handler, cfg, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://rag")
    return HttpAdapter(base_url="http://rag", api_key="secret", config=cfg, client=client, **kw)


class FakeRag:
    """一个会建库、收文件、异步解析的自研 RAG：b.md 解析失败，其余第二次查询时完成。"""

    def __init__(self):
        self.uploads, self.polls = [], {}

    def __call__(self, request):
        p = request.url.path
        if p == "/kb":
            assert json.loads(request.content) == {"name": "run-1"}
            return httpx.Response(200, json={"code": 0, "data": {"id": "kb9"}})
        if p == "/kb/kb9/docs":
            body = request.content.decode("utf-8", "replace")
            name = "a.md" if 'filename="a.md"' in body else "b.md"
            assert 'name="upload"' in body and 'name="kb"' in body and "kb9" in body
            self.uploads.append(name)
            return httpx.Response(200, json={"code": 0, "data": {"doc_id": f"d-{name}"}})
        if p.startswith("/kb/kb9/docs/"):
            doc = p.rsplit("/", 1)[1]
            self.polls[doc] = self.polls.get(doc, 0) + 1
            if doc == "d-b.md":
                return httpx.Response(200, json={"code": 0, "data": {"state": "FAIL", "msg": "格式不支持"}})
            state = "DONE" if self.polls[doc] >= 2 else "RUNNING"
            return httpx.Response(200, json={"code": 0, "data": {"state": state}})
        return httpx.Response(404)


async def test_auto_ingest_creates_kb_uploads_and_waits_for_parsing(tmp_path):
    for n in ("a.md", "b.md"):
        (tmp_path / n).write_text("内容", encoding="utf-8")
    rag = FakeRag()
    report = await _adapter(rag, {"ingest": INGEST, "ask": ASK}).ingest([tmp_path / "a.md", tmp_path / "b.md"], "run-1")
    assert report.manual is False and report.kb.ids == {"kb_id": "kb9"}
    assert rag.uploads == ["a.md", "b.md"] and rag.polls["d-a.md"] == 2
    by = {d.name: d for d in report.docs}
    assert by["a.md"].ok and not by["b.md"].ok and by["b.md"].error == "格式不支持"


async def test_auto_ingest_fails_loudly_when_every_document_fails(tmp_path):
    (tmp_path / "b.md").write_text("内容", encoding="utf-8")
    with pytest.raises(AdapterError, match="全部 1 份文档入库失败.*格式不支持"):
        await _adapter(FakeRag(), {"ingest": INGEST, "ask": ASK}).ingest([tmp_path / "b.md"], "run-1")


async def test_auto_ingest_without_status_endpoint_counts_upload_as_done(tmp_path):
    (tmp_path / "a.md").write_text("内容", encoding="utf-8")
    cfg = {"ingest": {k: v for k, v in INGEST.items() if k != "status"}, "ask": ASK}
    report = await _adapter(FakeRag(), cfg).ingest([tmp_path / "a.md"], "run-1")
    assert [(d.name, d.ok) for d in report.docs] == [("a.md", True)]


def test_ingest_config_is_validated():
    with pytest.raises(AdapterError, match="create_kb"):
        HttpAdapter(base_url="http://x", api_key="", config={"ingest": {"upload": {"path": "/u"}}, "ask": ASK})
    bad = {**INGEST, "upload": {"path": "/u"}}  # 有状态接口却取不到文档 id
    with pytest.raises(AdapterError, match="doc_id"):
        HttpAdapter(base_url="http://x", api_key="", config={"ingest": bad, "ask": ASK})
    with pytest.raises(AdapterError, match="二选一"):
        HttpAdapter(base_url="http://x", api_key="", config={"ask": ASK})


async def test_business_error_code_with_http_200_is_an_adapter_error():
    cfg = {"kb_id": "k", "ask": ASK, "success": {"path": "$.code", "equals": 0}, "error_message": "$.message"}
    adapter = _adapter(lambda r: httpx.Response(200, json={"code": 500, "message": "模型调用超时"}), cfg)
    with pytest.raises(AdapterError, match="模型调用超时"):
        await adapter.ask(KbHandle(system="http", ids={"kb_id": "k"}), "q")


def _marker_cfg(base=0):
    return {"kb_id": "k", "ask": {**ASK, "contexts": "$.data.chunks[*]",
                                  "context_fields": {"doc": "$.doc", "text": "$.text", "page": "$.page"},
                                  "citation_marker": r"\s*\[ID:(\d+)\]", "marker_index_base": base}}


async def test_citation_markers_map_back_to_contexts_and_are_stripped():
    answer = "端口是 9400 [ID:1]，见手册 [ID:1][ID:7]"
    data = {"data": {"answer": answer, "chunks": [{"doc": "a.pdf", "text": "x", "page": 2},
                                                  {"doc": "b.pdf", "text": "默认端口 9400", "page": "5"}]}}
    adapter = _adapter(lambda r: httpx.Response(200, json=data), _marker_cfg())
    ans = await adapter.ask(KbHandle(system="http", ids={"kb_id": "k"}), "q")
    assert ans.text == "端口是 9400，见手册" and ans.raw_text == answer
    assert [(c.doc, c.page) for c in ans.citations] == [("b.pdf", 5)]  # 越界的 7 丢掉，重复的只算一次
    assert len(ans.contexts) == 2 and adapter.supports_citations


async def test_one_based_markers():
    data = {"data": {"answer": "见 [ID:1]", "chunks": [{"doc": "a.pdf", "text": "x"}]}}
    ans = await _adapter(lambda r: httpx.Response(200, json=data), _marker_cfg(base=1)).ask(
        KbHandle(system="http", ids={"kb_id": "k"}), "q")
    assert [c.doc for c in ans.citations] == ["a.pdf"]


def test_marker_needs_contexts():
    with pytest.raises(AdapterError, match="contexts"):
        HttpAdapter(base_url="http://x", api_key="", config={"kb_id": "k", "ask": {**ASK, "citation_marker": r"\[(\d+)\]"}})


async def test_images_from_text_and_path_are_hashed_and_web_pages_are_not():
    png1, png2 = _png(1), _png(2)

    def handler(request):
        if request.url.host == "img.local" and request.url.path == "/files/fig.png":
            return httpx.Response(200, content=png1, headers={"content-type": "image/png"})
        if request.url.path == "/assets/b.png":
            return httpx.Response(200, content=png2, headers={"content-type": "image/png"})
        if request.url.path == "/files/missing.png":
            return httpx.Response(200, text="<html>登录</html>", headers={"content-type": "text/html"})
        return httpx.Response(200, json={"data": {
            "answer": '如图 <img src="/files/fig.png"> 和 ![](/files/missing.png)',
            "images": [{"src": "http://rag/assets/b.png", "file": "m.pdf", "pg": 3}]}})

    cfg = {"kb_id": "k", "ask": {**ASK, "images": "$.data.images[*]",
                                 "image_fields": {"url": "$.src", "doc": "$.file", "page": "$.pg"}}}
    adapter = _adapter(handler, cfg, image_base_url="http://img.local")
    ans = await adapter.ask(KbHandle(system="http", ids={"kb_id": "k"}), "q")
    by = {i.source: i for i in ans.images}
    assert by["/files/fig.png"].phash == dhash(png1)
    assert by["/files/missing.png"].phash is None  # 拿回的是网页，不算图
    assert by["http://rag/assets/b.png"].phash == dhash(png2)
    assert (by["http://rag/assets/b.png"].doc, by["http://rag/assets/b.png"].page) == ("m.pdf", 3)


async def test_kb_alive_uses_optional_endpoint():
    cfg = {"ingest": {**INGEST, "alive": {"path": "/kb/{{kb_id}}"}}, "ask": ASK}
    kb = KbHandle(system="http", ids={"kb_id": "kb9"})
    assert await _adapter(lambda r: httpx.Response(200, json={}), cfg).kb_alive(kb) is True
    assert await _adapter(lambda r: httpx.Response(404, json={}), cfg).kb_alive(kb) is False


async def test_dataset_params_are_merged_into_the_create_kb_body(tmp_path):
    """建库参数深合并进建库请求体：同一个自研系统按不同分块方式各建一个库对比。"""
    seen = {}

    def handler(request):
        if request.url.path == "/kb":
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"data": {"id": "kb9"}})
        return httpx.Response(200, json={"data": {"doc_id": "d1"}})

    ingest = {k: v for k, v in INGEST.items() if k != "status"}
    adapter = _adapter(handler, {"ingest": ingest, "ask": ASK},
                       dataset_params={"chunk": {"size": 512}, "parser": "ocr"})
    (tmp_path / "a.md").write_text("内容", encoding="utf-8")
    await adapter.ingest([tmp_path / "a.md"], "run-1")
    assert seen["body"] == {"name": "run-1", "chunk": {"size": 512}, "parser": "ocr"}

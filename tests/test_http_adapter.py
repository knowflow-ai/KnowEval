"""通用 HTTP 适配器：一份 YAML 写清请求模板与取值路径，不写代码接入自研 RAG。"""

import json

import httpx
import pytest

from knoweval.adapters.base import AdapterError
from knoweval.adapters.http import HttpAdapter, jsonpath

CFG = {
    "kb_id": "kb-42",
    "headers": {"X-Token": "{{api_key}}"},
    "ask": {
        "method": "POST", "path": "/chat",
        "body": {"query": "{{question}}", "kb": "{{kb_id}}", "stream": False},
        "answer": "$.data.answer",
        "citations": "$.data.refs[*]",
        "citation_fields": {"doc": "$.file_name", "page": "$.page", "text": "$.content"},
        "contexts": "$.data.contexts[*]",
        "context_fields": {"doc": "$.file", "text": "$.text"},
    },
}


def test_jsonpath_subset():
    doc = {"a": {"b": [{"c": 1}, {"c": 2}]}, "x": None}
    assert jsonpath(doc, "$.a.b[0].c") == 1
    assert jsonpath(doc, "$.a.b[*].c") == [1, 2]
    assert jsonpath(doc, "$.a.b[*]") == [{"c": 1}, {"c": 2}]
    assert jsonpath(doc, "$.missing") is None
    assert jsonpath(doc, "$.x") is None


def _adapter(handler, cfg=CFG):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://my-rag")
    return HttpAdapter(base_url="http://my-rag", api_key="secret", config=cfg, client=client)


async def test_ask_renders_template_and_maps_fields():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["token"] = request.headers["X-Token"]
        return httpx.Response(200, json={"data": {
            "answer": "端口是 9400",
            "refs": [{"file_name": "05.pdf", "page": 1, "content": "默认管理端口由 9300 调整为 9400"}],
            "contexts": [{"file": "05.pdf", "text": "片段一"}, {"file": "04.pdf", "text": "片段二"}]}})

    adapter = _adapter(handler)
    report = await adapter.ingest([], kb_name="ignored")
    assert report.manual is True and report.kb.ids == {"kb_id": "kb-42"}
    ans = await adapter.ask(report.kb, "v3.0 的默认管理端口？")
    assert seen["body"] == {"query": "v3.0 的默认管理端口？", "kb": "kb-42", "stream": False}
    assert seen["token"] == "secret"
    assert ans.text == "端口是 9400"
    assert [(c.doc, c.page) for c in ans.citations] == [("05.pdf", 1)]
    assert [c.doc for c in ans.contexts] == ["05.pdf", "04.pdf"]


async def test_missing_answer_or_http_error_is_an_adapter_error():
    adapter = _adapter(lambda r: httpx.Response(200, json={"data": {}}))
    with pytest.raises(AdapterError, match="answer"):
        await adapter.ask((await adapter.ingest([], kb_name="k")).kb, "q")
    adapter = _adapter(lambda r: httpx.Response(502, text="bad gateway"))
    with pytest.raises(AdapterError, match="502"):
        await adapter.ask((await adapter.ingest([], kb_name="k")).kb, "q")


def test_config_requires_kb_id_and_answer_path():
    with pytest.raises(AdapterError, match="kb_id"):
        HttpAdapter(base_url="http://x", api_key="", config={**CFG, "kb_id": None})
    with pytest.raises(AdapterError, match="answer"):
        HttpAdapter(base_url="http://x", api_key="", config={**CFG, "ask": {"path": "/chat"}})


def test_system_config_builds_http_adapter(tmp_path, monkeypatch):
    from knoweval.config import build_adapter, load_system_config
    monkeypatch.setenv("MY_KEY", "k")
    p = tmp_path / "s.yaml"
    p.write_text(
        "adapter: http\nbase_url: http://my-rag\napi_key_env: MY_KEY\n"
        "http:\n  kb_id: kb-1\n  ask:\n    path: /chat\n    body: {q: '{{question}}'}\n    answer: $.answer\n",
        encoding="utf-8")
    adapter = build_adapter(load_system_config(p))
    assert isinstance(adapter, HttpAdapter) and adapter.supports_citations is False


async def test_manual_ingest_is_marked_in_run_json(tmp_path):
    from knoweval.runner.run import run_eval
    from tests.test_runner import DEMO

    def handler(request):
        return httpx.Response(200, json={"answer": "知识库中未找到您要的答案！"})

    adapter = _adapter(handler, cfg={"kb_id": "kb", "ask": {"path": "/chat", "body": {"q": "{{question}}"},
                                                          "answer": "$.answer"}})
    await run_eval(DEMO, adapter, tmp_path, repeats=1, system_config={"adapter": "http"})
    meta = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert meta["manual_ingest"] is True


@pytest.mark.parametrize("name", ["http", "knowflow", "ragflow"])
def test_example_configs_build(name, monkeypatch):
    """configs/ 里的示例配置是给用户抄的，必须一直能建出适配器。"""
    from pathlib import Path
    from knoweval.config import build_adapter, load_system_config
    cfg = load_system_config(Path(__file__).parent.parent / "configs" / f"{name}.example.yaml")
    monkeypatch.setenv(cfg.api_key_env, "k")
    assert build_adapter(cfg).name == name


# ---------- 参评配置的参数包透传 ----------

async def test_chat_params_are_deep_merged_into_the_ask_body():
    """一份参评配置就是一组参数：问答参数深合并进问答请求体，同名字段以配置为准。"""
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"answer": "好"}})

    cfg = {**CFG, "ask": {**CFG["ask"], "body": {"query": "{{question}}", "retrieval": {"top_k": 5, "mode": "hybrid"}}}}
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://my-rag")
    adapter = HttpAdapter(base_url="http://my-rag", api_key="secret", config=cfg, client=client,
                          chat_params={"retrieval": {"top_k": 10, "rerank": True}, "temperature": 0})
    report = await adapter.ingest([], kb_name="ignored")
    await adapter.ask(report.kb, "问")
    assert seen["body"] == {"query": "问", "retrieval": {"top_k": 10, "mode": "hybrid", "rerank": True}, "temperature": 0}


async def test_chat_params_fill_placeholders_and_are_not_merged_twice():
    """参数要放进路径、请求头时用 {{chat.x}}；被占位符用掉的参数不再合并进请求体。整串就是一个占位符时保留原类型。"""
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["app"] = request.headers["X-App"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"answer": "好"}})

    cfg = {"kb_id": "kb-42", "headers": {"X-App": "{{chat.app_id}}"},
           "ask": {"path": "/apps/{{chat.app_id}}/chat", "answer": "$.data.answer",
                   "body": {"q": "{{question}}", "top_k": "{{chat.top_k}}"}}}
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://my-rag")
    adapter = HttpAdapter(base_url="http://my-rag", api_key="secret", config=cfg, client=client,
                          chat_params={"app_id": "a1", "top_k": 8, "stream": False})
    await adapter.ask((await adapter.ingest([], "x")).kb, "问")
    assert seen["path"] == "/apps/a1/chat" and seen["app"] == "a1"
    assert seen["body"] == {"q": "问", "top_k": 8, "stream": False}


def test_placeholder_without_a_param_value_is_reported():
    with pytest.raises(AdapterError, match=r"chat\.app_id"):
        HttpAdapter(base_url="http://my-rag", api_key="secret",
                    config={"kb_id": "k", "ask": {"path": "/apps/{{chat.app_id}}", "answer": "$.a"}})


def test_only_templates_that_will_run_need_their_params():
    """直接问已有的库时不走入库模板：建库里的 {{dataset.x}} 没有值也不该拦。"""
    cfg = {"ingest": {"create_kb": {"path": "/kb", "body": {"size": "{{dataset.size}}"}, "id": "$.id"},
                      "upload": {"path": "/kb/{{kb_id}}/docs"}},
           "ask": {"path": "/chat", "answer": "$.a"}}
    HttpAdapter(base_url="http://x", api_key="k", config=cfg, existing_kb="kb1")
    with pytest.raises(AdapterError, match=r"dataset\.size"):
        HttpAdapter(base_url="http://x", api_key="k", config=cfg)


async def test_get_ask_sends_params_as_query_string():
    """GET 接口没有请求体：问答参数放进查询串；嵌套的参数放不进查询串，直接报错。"""
    seen = {}

    def handler(request):
        seen["query"] = dict(request.url.params)
        seen["content"] = request.content
        return httpx.Response(200, json={"a": "好"})

    cfg = {"kb_id": "k", "ask": {"method": "GET", "path": "/chat?q={{question}}", "answer": "$.a"}}
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://x")
    adapter = HttpAdapter(base_url="http://x", api_key="k", config=cfg, client=client,
                          chat_params={"top_k": 10, "rerank": True})
    await adapter.ask((await adapter.ingest([], "x")).kb, "问")
    assert seen["query"] == {"q": "问", "top_k": "10", "rerank": "true"} and seen["content"] == b""
    with pytest.raises(AdapterError, match="查询串"):
        HttpAdapter(base_url="http://x", api_key="k", config=cfg, chat_params={"opt": {"a": 1}})


async def test_placeholders_tolerate_spaces_nesting_and_render_json_style_in_headers():
    seen = {}

    def handler(request):
        seen["path"], seen["flag"] = request.url.path, request.headers["X-Flag"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"a": "好"})

    cfg = {"kb_id": "k", "headers": {"X-Flag": "{{chat.flag}}"},
           "ask": {"path": "/apps/{{ chat.app }}/chat", "answer": "$.a",
                   "body": {"q": "{{question}}", "k": "{{chat.retrieval.top_k}}"}}}
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://x")
    adapter = HttpAdapter(base_url="http://x", api_key="k", config=cfg, client=client,
                          chat_params={"app": "a1", "flag": True, "retrieval": {"top_k": 7, "mode": "hybrid"}})
    await adapter.ask((await adapter.ingest([], "x")).kb, "问")
    assert seen["path"] == "/apps/a1/chat" and seen["flag"] == "true"
    # 被占位符用掉的嵌套参数不再合并，同一层的其它参数照常合并
    assert seen["body"] == {"q": "问", "k": 7, "retrieval": {"mode": "hybrid"}}


async def test_probe_uses_the_step_params_like_a_real_run():
    """试调要和真实评测发一样的请求：占位符代入、其余参数合并；模板要用的参数没给就说清楚缺哪个。"""
    from knoweval.adapters.http import probe
    seen = {}

    def handler(request):
        seen["path"], seen["body"] = request.url.path, json.loads(request.content)
        return httpx.Response(200, json={"a": "好"})

    cfg = {"ask": {"path": "/apps/{{chat.app}}/chat", "body": {"q": "{{question}}", "top_k": 5}, "answer": "$.a"}}
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://x")
    r = await probe("http://x", "k", cfg, "ask", {"question": "问"}, params={"app": "a1", "top_k": 9}, client=client)
    assert r["ok"] and seen["path"] == "/apps/a1/chat" and seen["body"] == {"q": "问", "top_k": 9}
    with pytest.raises(AdapterError, match=r"chat\.app"):
        await probe("http://x", "k", cfg, "ask", {"question": "问"}, client=client)

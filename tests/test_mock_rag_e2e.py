"""用 examples/mock_rag_server.py 这个示例「自研 RAG」走一遍通用 HTTP 适配器的全流程：
建库 → 逐份上传 → 轮询到解析完成 → 提问并按正文标记取引用。它同时是给第三方的接口示例，必须一直能跑通。"""

import importlib.util
import socket
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from knoweval.demo import DEMO_HTTP_CONFIG

from knoweval.adapters.http import HttpAdapter

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "datasets" / "demo-md" / "docs"


@pytest.fixture
def mock_rag(monkeypatch):
    spec = importlib.util.spec_from_file_location("mock_rag_server", ROOT / "examples" / "mock_rag_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("MOCK_RAG_API_KEY", "demo")
    monkeypatch.setattr(mod, "PARSE_SECONDS", 0.2)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = ThreadingHTTPServer(("127.0.0.1", port), mod.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


CONFIG = DEMO_HTTP_CONFIG


async def test_third_party_ingest_and_ask_end_to_end(mock_rag):
    adapter = HttpAdapter(base_url=mock_rag, api_key="demo", config=CONFIG)
    files = sorted(DOCS.glob("*.md"))[:3]
    report = await adapter.ingest(files, kb_name="knoweval-test")
    assert report.manual is False and len(report.docs) == 3 and all(d.ok for d in report.docs)
    assert await adapter.kb_alive(report.kb)
    ans = await adapter.ask(report.kb, "沈墨目前在哪里任职？")
    assert "[ID:" not in ans.text and "[ID:0]" in ans.raw_text
    assert ans.citations and ans.citations[0].doc in {f.name for f in files}
    await adapter.aclose()


async def test_wrong_key_surfaces_the_business_error(mock_rag):
    adapter = HttpAdapter(base_url=mock_rag, api_key="wrong", config=CONFIG)
    from knoweval.adapters.base import AdapterError
    with pytest.raises(AdapterError, match="API Key 不对"):
        await adapter.ingest(sorted(DOCS.glob("*.md"))[:1], kb_name="x")
    await adapter.aclose()


async def test_probe_returns_the_raw_response_for_each_step(mock_rag):
    """接入表单「试调」：每一步都真实调一次，原样带回返回结果（不做业务码判断、不取值）。"""
    from knoweval.adapters.http import probe
    created = await probe(mock_rag, "demo", CONFIG, "create_kb", {})
    assert created["ok"] and created["json"]["data"]["id"]
    kb = created["json"]["data"]["id"]
    uploaded = await probe(mock_rag, "demo", CONFIG, "upload", {"kb_id": kb})
    doc = uploaded["json"]["data"]["id"]
    status = await probe(mock_rag, "demo", CONFIG, "status", {"kb_id": kb, "doc_id": doc})
    assert status["json"]["data"]["status"] in ("parsing", "done")
    asked = await probe(mock_rag, "demo", CONFIG, "ask", {"kb_id": kb, "question": "接入测试是做什么的？"})
    assert "answer" in asked["json"]["data"] and asked["request"] == "POST /api/chat"
    wrong = await probe(mock_rag, "wrong", CONFIG, "ask", {"kb_id": kb, "question": "q"})
    assert wrong["json"]["code"] == 401  # 业务错误原样带回，页面自己看


async def test_probe_reports_missing_step_and_unreachable_host(monkeypatch):
    from knoweval.adapters.base import AdapterError
    from knoweval.adapters.http import probe
    # 本机常设代理（HTTP_PROXY）：本地地址不走代理，否则拿到的是代理的错误页；端口取一个刚释放、确定没人监听的
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        dead = f"http://127.0.0.1:{sock.getsockname()[1]}"
    with pytest.raises(AdapterError, match="还没填"):
        await probe(dead, "k", {"ask": {}}, "ask", {})
    r = await probe(dead, "k", CONFIG, "ask", {"question": "q", "kb_id": "x"}, timeout_s=2)
    assert r["ok"] is False and ("超时" in r["error"] or "连不上" in r["error"])

"""演示系统：没有自己的 RAG 系统也能先把评测跑通。

演示服务是 examples/mock_rag_server.py（Docker 里用 `--profile demo` 一起启动），按空行切段、按词重叠挑段落当回答，
只用来演示接口和流程，不是一个好的 RAG。评测台按下面这份接口模板接入它（和「自研系统」的接入方式完全一样）。
"""

from __future__ import annotations

import os

DEMO_URL_ENV = "KNOWEVAL_DEMO_RAG_URL"
DEMO_KEY_ENV = "MOCK_RAG_API_KEY"
DEMO_SYSTEM_NAME = "演示 RAG"

DEMO_HTTP_CONFIG = {
    "headers": {"Authorization": "Bearer {{api_key}}"},
    "success": {"path": "$.code", "equals": 0},
    "error_message": "$.message",
    "ingest": {
        "create_kb": {"path": "/api/kb", "body": {"name": "{{kb_name}}"}, "id": "$.data.id"},
        "upload": {"path": "/api/kb/{{kb_id}}/documents", "file_field": "file", "doc_id": "$.data.id"},
        "status": {"path": "/api/kb/{{kb_id}}/documents/{{doc_id}}", "state": "$.data.status", "done": ["done"],
                   "failed": ["failed"]},
        "poll_interval_s": 0.2,
        "alive": {"path": "/api/kb/{{kb_id}}"},
    },
    "ask": {"path": "/api/chat", "body": {"query": "{{question}}", "kb_id": "{{kb_id}}"}, "answer": "$.data.answer",
            "citation_marker": r"\s*\[ID:(\d+)\]", "contexts": "$.data.chunks[*]",
            "context_fields": {"doc": "$.document_name", "text": "$.content"}},
}


def demo_url() -> str | None:
    """演示服务的地址；没起演示服务时为 None。"""
    return os.environ.get(DEMO_URL_ENV, "").strip().rstrip("/") or None


def demo_system(base_url: str) -> dict:
    """接入演示服务用的系统定义（和在系统页接入一个自研系统填的内容一样）。"""
    return {"name": DEMO_SYSTEM_NAME, "adapter": "http", "base_url": base_url, "api_key_env": DEMO_KEY_ENV,
            "version": "demo", "http": DEMO_HTTP_CONFIG}

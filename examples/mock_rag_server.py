"""一个最小的「自研 RAG」示例服务：演示第三方系统要提供哪些接口，KnowEval 才能自动入库并评测。

接口（全部要求请求头 Authorization: Bearer <MOCK_RAG_API_KEY>，返回 {"code": 0, "data": ...}）：
  POST /api/kb                              新建知识库        → {"id": 库编号}
  POST /api/kb/<kb>/documents  (multipart)  上传一份文档      → {"id": 文档编号}
  GET  /api/kb/<kb>/documents/<doc>         查解析状态        → {"status": "parsing" | "done"}
  POST /api/chat  {"query", "kb_id"}        提问              → {"answer", "chunks": [{"document_name", "content"}]}

「解析」就是按空行切段，上传后约 1 秒变成 done（模拟异步解析）。提问时按问句与段落的二字词重叠挑最相关的 3 段，
把最相关那段原样当作回答，并在正文末尾带 [ID:0] 标记（演示「正文标记」式引用）。它只是接口示例，不是一个好的 RAG。

用法：MOCK_RAG_API_KEY=demo python examples/mock_rag_server.py --port 18790
接入 KnowEval：系统页 → 接入系统 → 自研系统，把提问接口的 curl 贴进去，例如
  curl -X POST http://127.0.0.1:18790/api/chat -H "Authorization: Bearer demo" -d '{"query": "年假有几天", "kb_id": "<库编号>"}'
再点「试问一题」，在返回结果里点 data.answer 当答案、data.chunks 当引用片段列表。
想让评测台自动入库，打开「对方还有建库、上传接口」，分别贴建库、上传、查进度三条 curl。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import uuid
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KBS: dict[str, dict] = {}
LOCK = threading.Lock()
PARSE_SECONDS = 1.0


def _bigrams(text: str) -> set[str]:
    clean = re.sub(r"[^一-鿿A-Za-z0-9]", "", text)
    return {clean[i:i + 2] for i in range(len(clean) - 1)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 安静运行
        pass

    def _reply(self, data=None, code: int = 0, message: str = "ok", status: int = 200) -> None:
        body = json.dumps({"code": code, "message": message, "data": data}, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        if self.headers.get("Authorization") == f"Bearer {os.environ.get('MOCK_RAG_API_KEY', 'demo')}":
            return True
        self._reply(code=401, message="API Key 不对")  # 故意回 HTTP 200 + 业务码，演示「业务成功码」
        return False

    def _json_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _uploaded_file(self) -> tuple[str | None, bytes | None]:
        """用标准库的 email 解析器读 multipart 里名为 file 的那一段（cgi 模块在 3.13 已移除）。"""
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        head = f"Content-Type: {self.headers.get('Content-Type', '')}\r\n\r\n".encode()
        msg = BytesParser(policy=policy.default).parsebytes(head + raw)
        for part in msg.iter_parts():
            if part.get_param("name", header="content-disposition") == "file":
                return part.get_filename(), part.get_payload(decode=True)
        return None, None

    def do_POST(self):  # noqa: N802
        if not self._authed():
            return
        parts = self.path.strip("/").split("/")
        if parts == ["api", "kb"]:
            kb = uuid.uuid4().hex[:8]
            with LOCK:
                KBS[kb] = {"name": self._json_body().get("name"), "docs": {}}
            return self._reply({"id": kb})
        if len(parts) == 4 and parts[:2] == ["api", "kb"] and parts[3] == "documents":
            kb = KBS.get(parts[2])
            if kb is None:
                return self._reply(code=404, message="没有这个知识库")
            filename, data = self._uploaded_file()
            if data is None:
                return self._reply(code=400, message="请求里没有 file 字段")
            text = data.decode("utf-8", errors="replace")
            doc = uuid.uuid4().hex[:8]
            paras = [p.strip() for p in text.split("\n\n") if p.strip()]
            with LOCK:
                kb["docs"][doc] = {"name": filename, "paras": paras, "ready_at": time.time() + PARSE_SECONDS}
            return self._reply({"id": doc})
        if parts == ["api", "chat"]:
            req = self._json_body()
            kb = KBS.get(req.get("kb_id", ""))
            if kb is None:
                return self._reply(code=404, message="没有这个知识库")
            q = _bigrams(req.get("query", ""))
            scored = sorted(((len(q & _bigrams(p)), d["name"], p) for d in kb["docs"].values() for p in d["paras"]),
                            key=lambda x: x[0], reverse=True)[:3]
            chunks = [{"document_name": name, "content": p} for score, name, p in scored if score > 0]
            if not chunks:
                return self._reply({"answer": "知识库中没有找到相关内容。", "chunks": []})
            return self._reply({"answer": f"{chunks[0]['content'][:300]} [ID:0]", "chunks": chunks})
        return self._reply(code=404, message="没有这个接口", status=404)

    def do_GET(self):  # noqa: N802
        if not self._authed():
            return
        parts = self.path.strip("/").split("/")
        if len(parts) == 5 and parts[:2] == ["api", "kb"] and parts[3] == "documents":
            doc = KBS.get(parts[2], {}).get("docs", {}).get(parts[4])
            if doc is None:
                return self._reply(code=404, message="没有这份文档")
            return self._reply({"status": "done" if time.time() >= doc["ready_at"] else "parsing"})
        if len(parts) == 3 and parts[:2] == ["api", "kb"]:
            return self._reply({"id": parts[2]}) if parts[2] in KBS else self._reply(code=404, message="没有这个知识库")
        return self._reply(code=404, message="没有这个接口", status=404)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18790)
    ap.add_argument("--host", default="127.0.0.1", help="Docker 里用 0.0.0.0，让评测台容器连得到")
    args = ap.parse_args()
    print(f"示例自研 RAG 已启动：http://{args.host}:{args.port}", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()

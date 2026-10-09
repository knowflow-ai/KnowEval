"""评测台「新建测试集」：取文档 → 出题 → 自动校验 → 页面删题 → 发布成数据集。大模型用假客户端。"""

import base64
import itertools
import json
import shutil
from pathlib import Path

import pytest

from knoweval.dataset import load_dataset
from knoweval.ui.builds import BuildService
from knoweval.ui.workspace import Workspace, WorkspaceError
from tests.test_build import FakeLLM, _quote_from_prompt

DOCS = Path(__file__).resolve().parents[1] / "datasets" / "demo-md" / "docs"


_COUNTER = itertools.count(1)


def _distinct_quote(prompt: str) -> dict:
    """像真模型一样每次出不同的题：题干带序号、每次摘原文里不同的一行，保证不被去重合并。
    去重也认「答案与引文都相同就是同一个事实」，所以同一页出两道题不能摘同一行，
    否则测试随构建编号的随机性时过时不过。"""
    n = next(_COUNTER)
    body = prompt.split("原文", 1)[1].split("：\n", 1)[1]
    lines = sorted({l.strip() for l in body.splitlines() if len(l.strip()) >= 6}, key=len, reverse=True) \
        or [_quote_from_prompt(prompt)["quote"]]
    line = lines[n % len(lines)]
    return {"question": f"第 {n} 题：关于「{line[:12]}」，原文怎么说？", "answer": line[:6], "answer_type": "text", "quote": line}


def _llm():
    """F 题从原文摘一句当答案；闭卷模型答不出；无答案题查证说找不到。"""
    fake = FakeLLM({
        "F": _distinct_quote,
        "N": {"question": "这个系统支持量子加速吗？", "n_kind": "near_miss"},
        "closed": "不知道",
        "absence": '{"found": false, "reason": "没有提到"}',
    })
    return lambda: (fake, "gen-model", ["gen-model", "closed-2"])


@pytest.fixture
def ws(tmp_path):
    return Workspace(tmp_path)


def _files(n=3):
    return [(f.name, f.read_bytes()) for f in sorted(DOCS.glob("*.md"))[:n]]



def _snapshot(svc, build_id):
    """评测开始时的题目快照（数据集格式），测试里用它检查导出的题目和清单。"""
    out = svc.snapshot(build_id, svc.ws.root / "snap" / build_id)
    total = sum(1 for line in (out / "questions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())
    return {"path": out.relative_to(svc.ws.root).as_posix(), "questions": total}

def test_build_from_uploaded_files_then_delete_and_snapshot(ws):
    svc = BuildService(ws, llm=_llm())
    meta = svc.create_from_files("我的测试集", "0.1", _files() + [("说明.docx", b"x"), ("../../逃逸.md", "# 逃逸\n\n正文。\n".encode())],
                                 {"F": 4, "N": 2})
    svc.join(60)
    d = svc.detail(meta.id)
    assert d["status"] == "ready", d["error"]
    assert d["skipped"] == ["说明.docx"]                       # 不支持的格式跳过并说明
    assert "逃逸.md" in {x["name"] for x in d["docs_list"]}       # 路径被清理，不会写出目录
    assert not (ws.root / "逃逸.md").exists()
    assert d["candidates"] == 6 + 3 and d["pending_verify"] == 0  # 各类多出 1.5 倍，全部校验过
    passed = [x for x in d["items"] if x["status"] == "verified"]
    assert passed, d["items"]

    # 页面删一道、恢复、再删；确认一道（可选的人工确认）
    victim, keep = passed[0]["id"], passed[1]["id"]
    assert svc.review(meta.id, victim, "delete")["status"] == "rejected"
    assert svc.review(meta.id, victim, "restore")["status"] == "verified"
    svc.review(meta.id, victim, "delete")
    assert svc.review(meta.id, keep, "confirm")["status"] == "approved"
    with pytest.raises(WorkspaceError, match="未知操作"):
        svc.review(meta.id, keep, "nope")

    out = _snapshot(svc, meta.id)
    ds = load_dataset(ws.root / out["path"])
    ids = {q.id for q in ds.questions}
    assert victim not in ids and keep in ids and out["questions"] == len(ids)
    manifest = json.loads((ws.root / out["path"] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["build"]["review"].startswith("大模型出题") and manifest["build"]["build_id"] == meta.id


def test_build_requires_model_settings_and_valid_input(ws):
    svc = BuildService(ws)  # 不注入假模型，读设置页：没配模型
    meta = svc.create_from_files("缺模型", "0.1", _files(1), {"F": 1})
    svc.join(30)
    assert svc.load(meta.id).status == "failed" and "设置" in svc.load(meta.id).error
    with pytest.raises(WorkspaceError, match="名字"):
        svc.create_from_files("", "0.1", _files(1), {"F": 1})
    with pytest.raises(WorkspaceError, match="不能自动生成"):
        svc.create_from_files("x", "0.1", _files(1), {"G": 1})
    with pytest.raises(WorkspaceError, match="至少上传"):
        svc.create_from_files("x", "0.1", [], {"F": 1})


def test_only_one_build_runs_at_a_time(ws):
    import asyncio

    class Slow(FakeLLM):
        async def complete(self, messages, model):
            await asyncio.sleep(0.5)
            return await super().complete(messages, model)

    slow = Slow({"F": _quote_from_prompt, "closed": "不知道"})
    svc = BuildService(ws, llm=lambda: (slow, "m", ["m"]))
    svc.create_from_files("慢的", "0.1", _files(1), {"F": 2})
    with pytest.raises(WorkspaceError, match="已有测试集在生成"):
        svc.create_from_files("第二个", "0.1", _files(1), {"F": 1})
    svc.join(60)


def test_http_create_and_review(ws):
    import threading
    import httpx
    from knoweval.ui.executor import EvalExecutor
    from knoweval.ui.server import UiApi, make_server
    builds = BuildService(ws, llm=_llm())
    api = UiApi(ws, executor=EvalExecutor(ws), builds=builds)
    srv = make_server(ws.root, port=0, api=api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    c = httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=30)
    try:
        files = [{"name": n, "content": base64.b64encode(b).decode()} for n, b in _files(2)]
        meta = c.post("/api/builds", json={"name": "接口建的", "files": files, "mix": {"F": 2}}).json()
        api.builds.join(60)
        d = c.get(f"/api/builds/{meta['id']}").json()
        assert d["status"] == "ready" and d["passed"] > 0
        doc = c.get(f"/api/builds/{meta['id']}/doc", params={"name": d["docs_list"][0]["name"]}).json()
        assert doc["text"]
        item = next(x for x in d["items"] if x["status"] == "verified")
        assert c.post(f"/api/builds/{meta['id']}/items/{item['id']}/delete", json={}).json()["status"] == "rejected"
        assert c.post(f"/api/builds/{meta['id']}/publish", json={}).status_code == 404   # 没有「发布」：评测直接用测试集
    finally:
        srv.shutdown()


def test_duplicate_questions_are_auto_rejected_and_license_is_private(ws):
    """大模型常出两道一样的题（实测：两道一字不差的无答案题）：只留第一道。用户文档的许可不套公开默认值。"""
    svc = BuildService(ws, llm=_llm())   # 假模型每次返回同一道无答案题
    meta = svc.create_from_files("去重", "0.1", _files(2), {"N": 2})
    svc.join(60)
    d = svc.detail(meta.id)
    ns = [x for x in d["items"] if x["qtype"] == ["N"]]
    assert len(ns) == 3
    kept = [x for x in ns if x["status"] != "rejected_auto"]
    dup = [x for x in ns if x["status"] == "rejected_auto"]
    assert len(kept) == 1 and len(dup) == 2 and all(f"与 {kept[0]['id']} 重复" in x["notes"][-1] for x in dup)
    out = _snapshot(svc, meta.id)
    manifest = json.loads((ws.root / out["path"] / "manifest.json").read_text(encoding="utf-8"))
    assert "私有文档" in manifest["license"]


def test_snapshot_with_nothing_left_is_a_clear_error(ws):
    svc = BuildService(ws, llm=_llm())
    meta = svc.create_from_files("全删", "0.1", _files(1), {"N": 1})
    svc.join(60)
    for x in svc.detail(meta.id)["items"]:
        if x["status"] == "verified":
            svc.review(meta.id, x["id"], "delete")
    with pytest.raises(WorkspaceError, match="没有可用的题"):
        _snapshot(svc, meta.id)


@pytest.fixture
def fake_kb_server():
    """一个只实现「列知识库、列文档、下载文档」三个接口的假 KnowFlow。
    库 a 与库 b 都有同一份「手册.md」；两库还各有一份同名但内容不同的「说明.md」。"""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    text = {d.name: d.read_bytes() for d in sorted(DOCS.glob("*.md"))[:2]}
    names = list(text)
    kbs = {
        "a": {"name": "库A", "docs": {"d1": ("手册.md", text[names[0]]), "d2": ("说明.md", "# 说明 A\n\n甲方说明。\n".encode())}},
        "b": {"name": "库B", "docs": {"d3": ("手册.md", text[names[0]]), "d4": ("说明.md", "# 说明 B\n\n乙方说明。\n".encode()),
                                       "d5": ("设计-v2.md", text[names[1]])}},
    }

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, data):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parts = self.path.split("?")[0].strip("/").split("/")
            if parts == ["api", "v1", "datasets"]:
                return self._json({"code": 0, "data": [{"id": k, "name": v["name"], "document_count": len(v["docs"])} for k, v in kbs.items()]})
            if len(parts) == 5 and parts[4] == "documents":
                return self._json({"code": 0, "data": {"docs": [{"id": i, "name": n} for i, (n, _) in kbs[parts[3]]["docs"].items()]}})
            if len(parts) == 6:
                n, b = kbs[parts[3]]["docs"][parts[5]]
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                self.wfile.write(b)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_build_from_multiple_knowledge_bases(ws, fake_kb_server, monkeypatch):
    monkeypatch.setenv("KF_KEY", "k")
    system = ws.add_system({"name": "KF", "adapter": "knowflow", "base_url": fake_kb_server, "api_key_env": "KF_KEY"})
    svc = BuildService(ws, llm=_llm())
    meta = svc.create_from_kb("多库", "0.1", system.id, ["a", "b", "a"], {"F": 2})
    svc.join(60)
    d = svc.detail(meta.id)
    assert d["status"] == "ready", d["error"]
    names = sorted(x["name"] for x in d["docs_list"])
    # 两库共有的手册只留一份；同名不同内容的说明加序号；名字带「-」的不被改坏
    assert names == ["手册.md", "设计-v2.md", "说明-2.md", "说明.md"]
    assert [k["name"] for k in d["source"]["kbs"]] == ["库A", "库B"]
    assert d["source"]["kb_ids"] == ["a", "b"]
    assert d["source"]["kbs"][1]["kept"] == 2   # 库 B 的手册与库 A 重复，没再存
    with pytest.raises(WorkspaceError, match="至少选一个"):
        svc.create_from_kb("空", "0.1", system.id, [], {"F": 1})


def test_batch_confirm_delete_restore(ws):
    svc = BuildService(ws, llm=_llm())
    meta = svc.create_from_files("批量", "0.1", _files(3), {"F": 4, "N": 1})
    svc.join(60)
    items = svc.detail(meta.id)["items"]
    passed = [x["id"] for x in items if x["status"] == "verified"]
    auto = [x["id"] for x in items if x["status"] == "rejected_auto"]
    assert len(passed) >= 3 and auto

    r = svc.review_many(meta.id, passed + auto[:1] + ["不存在的题"], "confirm")
    assert r["done"] == len(passed) and len(r["skipped"]) == 2           # 自动打回的、不存在的跳过
    assert {s["id"] for s in r["skipped"]} == {auto[0], "不存在的题"}
    assert all(x["status"] == "approved" for x in svc.detail(meta.id)["items"] if x["id"] in passed)

    r = svc.review_many(meta.id, passed[:2], "delete")
    assert r == {"done": 2, "skipped": []}
    r = svc.review_many(meta.id, passed[:3], "restore")               # 第 3 道没删过，跳过
    assert r["done"] == 2 and r["skipped"][0]["id"] == passed[2]
    with pytest.raises(WorkspaceError, match="未知操作"):
        svc.review_many(meta.id, passed, "nope")
    out = _snapshot(svc, meta.id)
    manifest = json.loads((ws.root / out["path"] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["build"]["review"] == f"大模型出题 {len(passed)} 题（自动校验）"   # 删掉又恢复的两道照样可用


def _a_line(name: str) -> str:
    """原文里一行足够长的正文，当证据引文用。"""
    text = (DOCS / name).read_text(encoding="utf-8")
    return next(line.strip() for line in text.splitlines() if len(line.strip()) > 20 and not line.startswith("#"))


def test_zero_mix_imports_documents_without_a_model_and_people_write_every_question(ws):
    """题型配比全填 0：只导入文档、不调模型，然后全部人工出题。没配模型也能用。"""
    def no_model():
        raise AssertionError("配比全为 0 时不该调模型")

    svc = BuildService(ws, llm=no_model)
    meta = svc.create_from_files("人工测试集", "0.1", _files(2), {"F": 0, "N": 0})
    svc.join(60)
    d = svc.detail(meta.id)
    assert d["status"] == "ready", d["error"]
    assert d["candidates"] == 0 and len(d["docs_list"]) == 2

    doc = d["docs_list"][0]["name"]
    quote = _a_line(doc)
    item = svc.save_item(meta.id, None, {"qtype": "F", "question": "原文这一句说了什么？", "answer": quote[:10],
                                         "answer_type": "text", "answer_aliases": ["别的说法"],
                                         "evidence": [{"doc": doc, "quote": quote}]})
    assert item["status"] == "approved" and item["generator"] == "human" and item["stage"] == "retrieve"
    no_answer = svc.save_item(meta.id, None, {"qtype": "N", "question": "这份文档讲了火星殖民吗？"})
    assert no_answer["answer"] == "" and no_answer["evidence"] == []

    out = _snapshot(svc, meta.id)
    ds = load_dataset(ws.root / out["path"])
    assert {q.question for q in ds.questions} == {"原文这一句说了什么？", "这份文档讲了火星殖民吗？"}
    manifest = json.loads((ws.root / out["path"] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["build"]["review"] == "人工出题 2 题"
    assert "generator_model" not in manifest["build"]


def test_human_questions_are_checked_before_saving(ws):
    svc = BuildService(ws, llm=lambda: (_ for _ in ()).throw(AssertionError("不该调模型")))
    meta = svc.create_from_files("校验", "0.1", _files(1), {})
    svc.join(60)
    doc = svc.detail(meta.id)["docs_list"][0]["name"]
    quote = _a_line(doc)
    base = {"qtype": "F", "question": "问题？", "answer": "x", "answer_type": "text", "evidence": [{"doc": doc, "quote": quote}]}

    with pytest.raises(WorkspaceError, match="原文里找不到"):
        svc.save_item(meta.id, None, {**base, "evidence": [{"doc": doc, "quote": "原文里根本没有的一句话。"}]})
    with pytest.raises(WorkspaceError, match="至少要有一条证据"):
        svc.save_item(meta.id, None, {**base, "evidence": []})
    with pytest.raises(WorkspaceError, match="没有这份文档"):
        svc.save_item(meta.id, None, {**base, "evidence": [{"doc": "别处.md", "quote": quote}]})
    with pytest.raises(WorkspaceError, match="题目"):
        svc.save_item(meta.id, None, {**base, "question": "  "})
    with pytest.raises(WorkspaceError, match="题型"):
        svc.save_item(meta.id, None, {**base, "qtype": "Z"})
    svc.save_item(meta.id, None, base)
    with pytest.raises(WorkspaceError, match="已经有一道一样的题"):
        svc.save_item(meta.id, None, {**base, "question": "问题?"})
    assert svc.detail(meta.id)["candidates"] == 1   # 失败的都没存进去


def test_editing_a_model_question_marks_it_edited(ws):
    svc = BuildService(ws, llm=_llm())
    meta = svc.create_from_files("改题", "0.1", _files(), {"F": 2})
    svc.join(60)
    items = svc.detail(meta.id)["items"]
    target = next(x for x in items if x["status"] == "verified")
    rejected = next((x for x in items if x["status"] == "rejected_auto"), None)

    edited = svc.save_item(meta.id, target["id"], {**target, "qtype": target["qtype"][0], "question": "改过的题干？"})
    assert edited["id"] == target["id"] and edited["status"] == "edited" and edited["generator"] == target["generator"]
    assert "人工修改" in edited["notes"]

    if rejected:   # 自动打回的题，人改对了也能救回来
        fixed = svc.save_item(meta.id, rejected["id"], {**rejected, "qtype": rejected["qtype"][0]})
        assert fixed["status"] == "edited"
    assert "改过的题干？" in {q.question for q in load_dataset(ws.root / _snapshot(svc, meta.id)["path"]).questions}
    svc.review(meta.id, target["id"], "delete")   # 删掉的题不进快照
    out = _snapshot(svc, meta.id)
    assert "改过的题干？" not in {q.question for q in load_dataset(ws.root / out["path"]).questions}


def test_cannot_write_questions_while_generating(ws):
    svc = BuildService(ws, llm=_llm())
    meta = svc.create_from_files("忙", "0.1", _files(1), {"F": 1})
    svc._update(meta.id, status="generating")
    svc.join(60)
    svc._update(meta.id, status="generating")
    with pytest.raises(WorkspaceError, match="出题和校验完成后"):
        svc.save_item(meta.id, None, {"qtype": "N", "question": "有吗？"})


def test_http_add_and_edit_item(ws):
    import threading

    import httpx

    from knoweval.ui.server import UiApi, make_server

    svc = BuildService(ws, llm=lambda: (_ for _ in ()).throw(AssertionError("不该调模型")))
    meta = svc.create_from_files("接口", "0.1", _files(1), {})
    svc.join(60)
    srv = make_server(ws.root, port=0, api=UiApi(ws, builds=svc))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=30)
        item = c.post(f"/api/builds/{meta.id}/items", json={"qtype": "N", "question": "提到火星了吗？"}).json()
        assert item["status"] == "approved"
        edited = c.put(f"/api/builds/{meta.id}/items/{item['id']}", json={"qtype": "N", "question": "提到月球了吗？"}).json()
        assert edited["question"] == "提到月球了吗？" and edited["status"] == "approved"   # 人出的题再改，仍算人工出题
        bad = c.post(f"/api/builds/{meta.id}/items", json={"qtype": "F", "question": "x？", "answer": "y"})
        assert bad.status_code == 400 and "证据" in bad.json()["error"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_auto_rejected_questions_can_be_restored_unless_malformed(ws):
    """开卷复答会误杀（比如模型读错了图表）：自动打回的题可以一键恢复；格式不合规的要先修改。"""
    svc = BuildService(ws, llm=lambda: (_ for _ in ()).throw(AssertionError("不该调模型")))
    meta = svc.create_from_files("恢复", "0.1", _files(1), {})
    svc.join(60)
    p = svc.project(meta.id)
    from knoweval.build.project import Candidate
    p.add(Candidate(id="r1", qtype=["F"], question="误杀的题？", answer="x", answer_type="text", generator="single_page",
                    status="rejected_auto", notes=["开卷作答得到「y」，与标准答案「x」不一致"]))
    p.add(Candidate(id="r2", qtype=["N"], question="无答案题却给了答案？", answer="03-31", answer_type="text", generator="single_page",
                    status="rejected_auto", notes=["题目格式不合规"]))
    restored = svc.review(meta.id, "r1", "restore")
    assert restored["status"] == "verified" and restored["notes"][-1] == "人工恢复"
    with pytest.raises(WorkspaceError, match="先修改"):
        svc.review(meta.id, "r2", "restore")


def test_dedupe_drops_the_same_fact_asked_in_different_words(tmp_path):
    """题干措辞不同、但标准答案和证据引文都一样，就是同一道题。"""
    from knoweval.build.project import Candidate
    from knoweval.schema import Evidence
    from knoweval.ui.builds import dedupe

    class P:
        def __init__(self, cs):
            self.candidates = cs
            self.logs = []

        def replace(self, c):
            self.candidates = [c if x.id == c.id else x for x in self.candidates]

        def log(self, *a, **k):
            self.logs.append(a)

    ev = [Evidence(doc="a.pdf", page=2, quote="2023年市场规模为358.6亿元")]
    mk = lambda cid, q: Candidate(id=cid, qtype=["F"], question=q, answer="358.6", unit="亿元", answer_type="number",  # noqa: E731
                                  evidence=ev, status="generated", generator="single_page")
    p = P([mk("f-1", "2023年市场规模是多少亿元？"), mk("t-1", "根据测算，2023年行业规模多少？"),
           Candidate(id="n-1", qtype=["N"], question="B站占比？", answer="", status="generated", generator="no_answer"),
           Candidate(id="n-2", qtype=["N"], question="快手的海外用户数？", answer="", status="generated", generator="no_answer")])
    assert dedupe(p) == 1
    status = {c.id: c.status for c in p.candidates}
    assert status == {"f-1": "generated", "t-1": "rejected_auto", "n-1": "generated", "n-2": "generated"}


def test_ready_build_starts_the_eval_it_was_created_for(ws):
    """从助手的知识库出题：出完题自动开始评测，评测编号记回测试集，页面能跳过去。"""
    svc = BuildService(ws, llm=_llm())
    started = []
    svc.on_ready = lambda meta: started.append(meta.after_ready) or "ev-abc123"
    meta = svc.create_from_files("助手题", "0.1", _files(), {"F": 2}, after_ready={"picks": [{"system_id": "s", "chat_id": "c1"}]})
    svc.join(60)
    d = svc.detail(meta.id)
    assert d["status"] == "ready", d["error"]
    assert started == [{"picks": [{"system_id": "s", "chat_id": "c1"}]}]
    assert d["after_ready"]["eval_id"] == "ev-abc123"


def test_auto_eval_failure_is_recorded_not_fatal(ws):
    svc = BuildService(ws, llm=_llm())

    def boom(meta):
        raise WorkspaceError("助手不见了")

    svc.on_ready = boom
    meta = svc.create_from_files("助手题", "0.1", _files(), {"F": 2}, after_ready={"picks": []})
    svc.join(60)
    d = svc.detail(meta.id)
    assert d["status"] == "ready" and "助手不见了" in d["after_ready"]["error"]


def test_model_timeouts_during_verify_leave_questions_pending_then_reverify_finishes(ws):
    """校验时模型超时（大模型读长原文常见）：不让整套题失败；超时的题留在待校验，自动评测先不开，
    模型恢复后「重新校验」只补这些题，补完再自动开始评测。"""
    import httpx
    import openai

    down = {"on": True}

    def open_book(text):
        if down["on"]:
            raise openai.APITimeoutError(request=httpx.Request("POST", "http://llm/v1/chat/completions"))
        return text

    fake = FakeLLM({"F": _distinct_quote, "closed": "不知道", "absence": '{"found": false, "reason": "没有提到"}',
                    "open": open_book})
    svc = BuildService(ws, llm=lambda: (fake, "gen-model", ["gen-model"]))
    started = []
    svc.on_ready = lambda meta: started.append(meta.id) or "ev-123456"
    meta = svc.create_from_files("超时", "0.1", _files(), {"F": 2}, after_ready={"picks": []})
    svc.join(60)
    d = svc.detail(meta.id)
    assert d["status"] == "ready", d["error"]
    assert d["verify_errors"] > 0 and d["pending_verify"] == d["verify_errors"]
    assert started == [] and "重新校验" in d["after_ready"]["error"]

    down["on"] = False
    svc.reverify(meta.id)
    svc.join(60)
    d = svc.detail(meta.id)
    assert d["status"] == "ready" and d["verify_errors"] == 0 and d["pending_verify"] == 0 and d["passed"] > 0
    assert started == [meta.id] and d["after_ready"]["eval_id"] == "ev-123456" and not d["after_ready"]["error"]
    with pytest.raises(WorkspaceError, match="没有待校验"):
        svc.reverify(meta.id)


class SlowLLM:
    """每次调用都要一会儿的模型：用来在出题途中停下。"""

    def __init__(self):
        self.calls = 0

    async def complete(self, messages, model):
        import asyncio
        self.calls += 1
        await asyncio.sleep(0.05)
        return _distinct_quote(messages[-1]["content"]) and json.dumps(_distinct_quote(messages[-1]["content"]), ensure_ascii=False)


def test_generation_can_be_stopped_then_deleted(ws):
    import time
    llm = SlowLLM()
    svc = BuildService(ws, llm=lambda: (llm, "gen-model", ["gen-model"]))
    meta = svc.create_from_files("停下", "0.1", _files(), {"F": 30})
    for _ in range(100):   # 等它真的开始调模型
        if llm.calls:
            break
        time.sleep(0.05)
    with pytest.raises(WorkspaceError, match="正在出题"):
        svc.delete(meta.id)
    svc.stop(meta.id)
    svc.join(30)
    d = svc.detail(meta.id)
    assert d["status"] == "failed" and "停止" in d["error"]
    calls = llm.calls
    time.sleep(0.3)
    assert llm.calls == calls          # 停下后不再调模型
    svc.delete(meta.id)
    with pytest.raises(WorkspaceError, match="没有在出题"):
        svc.stop(meta.id)


def test_builds_left_running_by_a_restart_are_marked_interrupted(ws):
    """评测台重启时正在出题的测试集：线程已经没了，标成中断，可以删除或重新出题。"""
    svc = BuildService(ws, llm=_llm())
    meta = svc.create_from_files("中断", "0.1", _files(1), {})
    svc.join(30)
    svc._update(meta.id, status="generating")
    d = BuildService(ws).detail(meta.id)
    assert d["status"] == "failed" and "重启" in d["error"]


def test_word_document_goes_through_generation_and_quote_check(ws, tmp_path):
    """上传 Word 文档出题：转成文字、出题、逐字核对引文、校验，出来可用的题。"""
    import docx

    d = docx.Document()
    d.add_heading("设备巡检制度", level=1)
    for i in range(6):
        d.add_paragraph(f"第{i + 1}条：一线设备第{i + 1}类每周巡检一次，巡检记录保存{i + 3}年，由班组长签字确认。")
    path = tmp_path / "巡检制度.docx"
    d.save(path)
    svc = BuildService(ws, llm=_llm())
    meta = svc.create_from_files("Word 题", "0.1", [(path.name, path.read_bytes())], {"F": 2})
    svc.join(60)
    d = svc.detail(meta.id)
    assert d["status"] == "ready", d["error"]
    assert d["skipped"] == [] and d["passed"] > 0


def _mixed_table():
    """一道和文档里原文对得上的导入题 + 一道无答案题。题干故意和假模型会出的题不同。"""
    doc = sorted(DOCS.glob("*.md"))[0]
    line = next(l.strip() for l in doc.read_text(encoding="utf-8").splitlines() if len(l.strip()) >= 12 and not l.startswith("#"))
    csv = f"题目,答案,文档,原文\n我们自己写的一道题：{line[:8]}？,{line[:6]},{doc.name},{line}\n公司在火星有分部吗,,,\n"
    return ("我的题.csv", csv.encode("utf-8"))


def test_imported_questions_and_generated_questions_live_in_one_test_set(ws):
    """新建测试集时可以同时导入自己的题、让模型按配比补题：放在同一套里，导入的先进来、不调模型。"""
    svc = BuildService(ws, llm=_llm())
    meta = svc.create_from_files("混合", "0.1", _files(), {"F": 2}, table=_mixed_table())
    svc.join(60)
    d = svc.detail(meta.id)
    assert d["status"] == "ready", d["error"]
    assert d["import_report"]["imported"] == 2
    by = {}
    for it in d["items"]:
        by.setdefault(it["generator"], []).append(it)
    assert len(by["import"]) == 2 and all(it["status"] == "approved" for it in by["import"])
    assert any(it["status"] == "verified" for g, items in by.items() if g != "import" for it in items)


def test_generated_duplicates_of_imported_questions_are_dropped(ws):
    """模型补的题和导入的题撞了：留导入的，打回模型出的。"""
    from knoweval.build.project import Candidate
    from knoweval.ui.builds import IMPORTED, dedupe
    svc = BuildService(ws, llm=_llm())
    meta = svc.create_from_files("去重", "0.1", _files(1), {})
    svc.join(60)
    p = svc.project(meta.id)
    p.add(Candidate(id="i-0001", qtype=["F"], question="潮汐系统主要应用于哪些场景？", answer="金融风控", status="approved",
                    generator=IMPORTED))
    p.add(Candidate(id="f-0001", qtype=["F"], question="潮汐系统主要应用于哪些场景?", answer="金融风控", status="verified",
                    generator="single_page"))
    assert dedupe(p) == 1
    status = {c.id: c.status for c in p.candidates}
    assert status == {"i-0001": "approved", "f-0001": "rejected_auto"}


def test_exported_questions_remember_where_they_came_from(ws, tmp_path):
    """导出的题目带上来源（导入 / 模型出题 / 手动），报告能按来源分开统计。"""
    from knoweval.dataset import load_dataset
    svc = BuildService(ws, llm=_llm())
    meta = svc.create_from_files("来源", "0.1", _files(), {"F": 2}, table=_mixed_table())
    svc.join(60)
    svc.save_item(meta.id, None, {"qtype": "N", "question": "公司在月球有分部吗？"})
    ds = load_dataset(svc.snapshot(meta.id, tmp_path / "snap"))
    origins = {q.origin for q in ds.questions}
    assert origins == {"imported", "generated", "manual"}


def test_existing_test_set_can_import_more_questions(ws):
    """已有的测试集继续导入一批题：追加进去，不动原来的题。"""
    svc = BuildService(ws, llm=_llm())
    meta = svc.create_from_files("追加", "0.1", _files(), {"F": 2})
    svc.join(60)
    before = svc.detail(meta.id)
    svc.import_more(meta.id, _mixed_table())
    after = svc.detail(meta.id)
    assert after["candidates"] == before["candidates"] + 2
    assert sum(it["generator"] == "import" for it in after["items"]) == 2
    assert {it["id"]: it["status"] for it in after["items"] if it["generator"] != "import"} == \
        {it["id"]: it["status"] for it in before["items"]}


def test_existing_test_set_can_ask_the_model_for_more_without_touching_my_edits(ws):
    """让模型按配比补一批题：只校验新出的题，用户改过、确认过的题不重新校验、不会被打回。"""
    svc = BuildService(ws, llm=_llm())
    meta = svc.create_from_files("补题", "0.1", _files(), {})
    svc.join(60)
    mine = svc.save_item(meta.id, None, {"qtype": "N", "question": "公司在月球有分部吗？"})
    svc.generate_more(meta.id, {"F": 2})
    svc.join(60)
    d = svc.detail(meta.id)
    assert d["status"] == "ready", d["error"]
    assert any(it["status"] == "verified" for it in d["items"])
    assert next(it for it in d["items"] if it["id"] == mine["id"])["status"] == mine["status"]
    with pytest.raises(WorkspaceError, match="题型"):
        svc.generate_more(meta.id, {})


def test_questions_only_test_set_cannot_ask_the_model_for_more(ws):
    svc = BuildService(ws, llm=_llm())
    meta = svc.create_from_import("只有题", "0.1", _mixed_table(), [])
    svc.join(60)
    with pytest.raises(WorkspaceError, match="没有文档"):
        svc.generate_more(meta.id, {"F": 2})


def test_manual_numeric_answer_can_include_its_unit(ws):
    """手动加题：数值题的答案直接写「358.6 亿元」，自动拆成数字和单位，不再要求分开填。"""
    svc = BuildService(ws, llm=_llm())
    meta = svc.create_from_files("手动", "0.1", _files(1), {})
    svc.join(60)
    p = svc.project(meta.id)
    doc = p.docs[0].name
    line = next(l for l in p.page_text(doc, None).splitlines() if len(l.strip()) >= 8).strip()
    item = svc.save_item(meta.id, None, {"qtype": "F", "question": "营收多少？", "answer": "358.6 亿元", "answer_type": "number",
                                         "evidence": [{"doc": doc, "quote": line}]})
    assert (item["answer"], item["unit"], item["tolerance"]) == ("358.6", "亿元", 0.0)


def test_manual_answer_type_is_inferred_from_the_answer(ws):
    """不用选答案类型：数字开头的按数值（拆出单位），像日期的按日期，其余按文本。判分交给裁判模型，类型只决定要不要带误差。"""
    svc = BuildService(ws, llm=_llm())
    meta = svc.create_from_files("推断", "0.1", _files(1), {})
    svc.join(60)
    p = svc.project(meta.id)
    doc = p.docs[0].name
    lines = [l.strip() for l in p.page_text(doc, None).splitlines() if len(l.strip()) >= 8]
    def save(i, answer, **extra):
        return svc.save_item(meta.id, None, {"qtype": "F", "question": f"问题{i}？", "answer": answer,
                                             "evidence": [{"doc": doc, "quote": lines[i]}], **extra})
    num = save(0, "358.6 亿元", tolerance=0.5)
    assert (num["answer_type"], num["answer"], num["unit"], num["tolerance"]) == ("number", "358.6", "亿元", 0.5)
    assert save(1, "2024-03")["answer_type"] == "date"
    text = save(2, "十天")
    assert (text["answer_type"], text["answer"]) == ("text", "十天")

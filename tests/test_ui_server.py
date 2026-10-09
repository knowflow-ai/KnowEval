"""评测台后端：执行器跑完整评测、失败与中止的状态、HTTP 接口端到端。"""

import asyncio
import json
import shutil
import threading
import time
from pathlib import Path

import httpx
import pytest

from knoweval.adapters.base import AdapterError, DocIngest, IngestReport, KbHandle
from knoweval.dataset import load_dataset
from knoweval.schema import Answer
from knoweval.ui.executor import EvalExecutor
from knoweval.ui.server import UiApi, make_server, relabel_compare
from knoweval.ui.workspace import Workspace, WorkspaceError

DATASETS = Path(__file__).resolve().parents[1] / "datasets"
DEMO_MD = load_dataset(DATASETS / "demo-md")
GOLD = {q.question: q for q in DEMO_MD.questions}


class GoldAdapter:
    """按标准答案作答的假系统：有答案的题答对，无答案题拒答。"""

    name = "fake"
    supports_citations = True

    def __init__(self, delay: float = 0.0):
        self.delay = delay

    async def kb_alive(self, kb):
        return True

    async def ingest(self, files, kb_name):
        return IngestReport(kb=KbHandle(system="fake", ids={"kb": "1"}),
                            docs=[DocIngest(name=f.name, ok=True, seconds=0.1) for f in files])

    async def ask(self, kb, question):
        if self.delay:
            await asyncio.sleep(self.delay)
        q = GOLD[question]
        if not q.answer:
            return Answer(text="知识库中未找到相关信息。")
        return Answer(text=f"答案是{q.answer}。")

    async def aclose(self):
        pass


@pytest.fixture
def ws(tmp_path, monkeypatch):
    shutil.copytree(DATASETS / "demo-md", tmp_path / "datasets" / "demo-md")
    monkeypatch.setenv("KNOWFLOW_API_KEY", "k")
    return Workspace(tmp_path)


def _two_config_eval(ws: Workspace):
    system = ws.add_system({"name": "KnowFlow", "adapter": "knowflow", "base_url": "http://localhost:9380",
                            "api_key_env": "KNOWFLOW_API_KEY"})
    a = ws.add_config(system.id, {"name": "smart", "dataset": {"chunk_method": "smart", "embedding_model": "e"}})
    b = ws.add_config(system.id, {"name": "naive", "dataset": {"chunk_method": "naive", "embedding_model": "e"}})
    return system, a, b


def test_executor_runs_two_configs_and_report_compares_them(ws):
    system, a, b = _two_config_eval(ws)
    api = UiApi(ws, executor=EvalExecutor(ws, adapter_factory=lambda cfg: GoldAdapter()))
    meta = api.create_eval({"dataset_path": "datasets/demo-md", "repeats": 2,
                            "picks": [{"system_id": system.id, "config_id": a.id},
                                      {"system_id": system.id, "config_id": b.id}]})
    api.executor.join(120)
    done = ws.load_eval(meta["id"])
    assert done.status == "finished" and all(p.status == "finished" for p in done.participants)

    state = api.state()
    assert state["running"] is None
    headline = state["evals"][0]["headline"]
    assert len(headline) == 2 and all(v is not None for v in headline.values())

    detail = api.eval_detail(meta["id"])
    for prog in detail["progress"].values():
        assert prog["phase"] == "done" and prog["ingest"]["ok"] == prog["ingest"]["total"]
        assert all(r["judged"] == prog["total"] for r in prog["rounds"])

    report = api.eval_report(meta["id"])
    names = {s["name"] for s in report["compare"]["systems"]}
    assert report["kind"] == "compare" and names == {"KnowFlow：smart", "KnowFlow：naive"}
    single = api.eval_report(meta["id"], participant=done.participants[0].id)
    assert single["kind"] == "report" and single["summary"]["repeats"][0]["accuracy"]["value"] > 0.5


def test_one_failing_config_does_not_sink_the_other(ws):
    system, a, b = _two_config_eval(ws)

    def factory(cfg):
        if cfg.dataset["chunk_method"] == "smart":
            raise AdapterError("接口 500")
        return GoldAdapter()

    api = UiApi(ws, executor=EvalExecutor(ws, adapter_factory=factory))
    meta = api.create_eval({"dataset_path": "datasets/demo-md", "repeats": 1,
                            "picks": [{"system_id": system.id, "config_id": a.id},
                                      {"system_id": system.id, "config_id": b.id}]})
    api.executor.join(120)
    done = ws.load_eval(meta["id"])
    by = {p.name.split("：")[1]: p for p in done.participants}
    assert done.status == "failed"
    assert by["smart"].status == "failed" and "500" in by["smart"].error
    assert by["naive"].status == "finished"
    # 失败那份没有结果，报告退回只剩一份的单报告
    assert api.eval_report(meta["id"])["kind"] == "report"


def test_stop_cancels_and_restart_resumes(ws):
    system, a, _ = _two_config_eval(ws)
    executor = EvalExecutor(ws, adapter_factory=lambda cfg: GoldAdapter(delay=5))
    api = UiApi(ws, executor=executor)
    meta = api.create_eval({"dataset_path": "datasets/demo-md", "repeats": 1,
                            "picks": [{"system_id": system.id, "config_id": a.id}]})
    time.sleep(0.5)
    executor.stop(meta["id"])
    executor.join(30)
    done = ws.load_eval(meta["id"])
    assert done.status == "stopped" and done.participants[0].status == "stopped"
    # 换个不卡的适配器重新开始：从断点续跑直到完成；开始的一刻，没跑完的配置回到排队中
    fast = EvalExecutor(ws, adapter_factory=lambda cfg: GoldAdapter(delay=0.05))
    fast.start(meta["id"])
    assert ws.load_eval(meta["id"]).participants[0].status in ("pending", "running")
    assert ws.load_eval(meta["id"]).participants[0].error is None
    fast.join(120)
    assert ws.load_eval(meta["id"]).status == "finished"


def test_only_one_eval_runs_at_a_time(ws):
    system, a, b = _two_config_eval(ws)
    executor = EvalExecutor(ws, adapter_factory=lambda cfg: GoldAdapter(delay=5))
    api = UiApi(ws, executor=executor)
    meta = api.create_eval({"dataset_path": "datasets/demo-md", "repeats": 1,
                            "picks": [{"system_id": system.id, "config_id": a.id}]})
    with pytest.raises(WorkspaceError, match="已有评测在运行"):
        api.create_eval({"dataset_path": "datasets/demo-md", "repeats": 1,
                         "picks": [{"system_id": system.id, "config_id": b.id}]})
    executor.stop(meta["id"])
    executor.join(30)


def test_relabel_compare_rewrites_every_name_field():
    result = {"systems": [{"name": "cfg1"}], "pairs": [{"first": "cfg1", "second": "cfg2"}],
              "groups": {"all_correct": [], "only_cfg1": ["q1"]}, "matrix": {"cfg1": ["CORRECT"]}}
    out = relabel_compare(result, {"cfg1": "KnowFlow：smart"})
    assert out["systems"][0]["name"] == "KnowFlow：smart"
    assert out["pairs"][0] == {"first": "KnowFlow：smart", "second": "cfg2"}
    assert out["groups"] == {"all_correct": [], "only_KnowFlow：smart": ["q1"]}
    assert out["matrix"] == {"KnowFlow：smart": ["CORRECT"]}


@pytest.fixture
def client(ws):
    api = UiApi(ws, executor=EvalExecutor(ws, adapter_factory=lambda cfg: GoldAdapter()))
    srv = make_server(ws.root, port=0, api=api)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=30), api
    srv.shutdown()
    srv.server_close()


def test_http_end_to_end(client):
    c, api = client
    # 页面壳子
    assert "knoweval-data" in c.get("/").text
    # 系统与配置
    system = c.post("/api/systems", json={"name": "KnowFlow", "adapter": "knowflow",
                                          "base_url": "http://localhost:9380",
                                          "api_key_env": "KNOWFLOW_API_KEY"}).json()
    assert "id" in system
    a = c.post(f"/api/systems/{system['id']}/configs",
               json={"name": "smart", "dataset": {"chunk_method": "smart", "embedding_model": "e"}}).json()
    b = c.post(f"/api/systems/{system['id']}/configs",
               json={"name": "naive", "dataset": {"chunk_method": "naive", "embedding_model": "e"}}).json()
    assert c.post("/api/systems", json={"name": "KnowFlow", "adapter": "knowflow", "base_url": "x",
                                        "api_key_env": "E"}).status_code == 400
    # 数据集
    assert c.get("/api/datasets").json()[0]["ref"] == "demo-md@0.0"
    # 建评测 → 等执行器跑完 → 报告
    meta = c.post("/api/evals", json={"dataset_path": "datasets/demo-md", "repeats": 1,
                                      "picks": [{"system_id": system["id"], "config_id": a["id"]},
                                                {"system_id": system["id"], "config_id": b["id"]}]}).json()
    assert meta.get("status") == "running", meta
    api.executor.join(120)
    detail = c.get(f"/api/evals/{meta['id']}").json()
    assert detail["status"] == "finished" and detail["running"] is False
    report = c.get(f"/api/evals/{meta['id']}/report").json()
    assert report["kind"] == "compare" and len(report["compare"]["systems"]) == 2
    one = c.get(f"/api/evals/{meta['id']}/report", params={"participant": detail["participants"][0]["id"]}).json()
    assert one["kind"] == "report"
    # 设置（放在评测之后：配了裁判模型但环境变量没设时，评测会按 fail-closed 直接失败）
    assert c.put("/api/settings", json={"base_url": "http://j", "model": "m"}).json()["model"] == "m"
    assert c.get("/api/settings").json()["model"] == "m"
    # 清理接口
    assert c.delete(f"/api/systems/{system['id']}/configs/{a['id']}").json()["ok"] is True
    assert c.get("/api/nope").status_code == 404


def test_judge_settings_without_api_key_fails_closed(ws):
    """配了裁判模型、环境变量却没设：评测失败并把原因说清。"""
    system, a, _ = _two_config_eval(ws)
    ws.save_settings({"base_url": "http://j", "model": "judge-x", "api_key_env": "KNOWEVAL_TEST_MISSING_KEY"})
    api = UiApi(ws, executor=EvalExecutor(ws, adapter_factory=lambda cfg: GoldAdapter()))
    meta = api.create_eval({"dataset_path": "datasets/demo-md", "repeats": 1,
                            "picks": [{"system_id": system.id, "config_id": a.id}]})
    api.executor.join(60)
    done = ws.load_eval(meta["id"])
    assert done.status == "failed" and "KNOWEVAL_TEST_MISSING_KEY" in done.participants[0].error


@pytest.mark.real_judge_wiring
def test_eval_without_the_console_model_fails_with_a_clear_reason(ws):
    """判分全部交给评测台的模型：没配就不能评测，失败原因指向「设置」页。"""
    system, a, _ = _two_config_eval(ws)
    api = UiApi(ws, executor=EvalExecutor(ws, adapter_factory=lambda cfg: GoldAdapter()))
    meta = api.create_eval({"dataset_path": "datasets/demo-md", "repeats": 1,
                            "picks": [{"system_id": system.id, "config_id": a.id}]})
    api.executor.join(60)
    done = ws.load_eval(meta["id"])
    assert done.status == "failed" and "设置" in done.participants[0].error


def test_dataset_detail_and_doc_preview(ws):
    api = UiApi(ws, executor=EvalExecutor(ws, adapter_factory=lambda cfg: GoldAdapter()))
    detail = api.dataset_detail("datasets/demo-md")
    assert detail["ref"] == "demo-md@0.0" and len(detail["questions"]) == 20
    assert all(f["present"] for f in detail["files"])
    q = next(q for q in detail["questions"] if q["evidence"])
    doc = api.dataset_doc("datasets/demo-md", q["evidence"][0]["doc"])
    assert q["evidence"][0]["quote"][:6] in doc["text"].replace("\n", "")
    with pytest.raises(WorkspaceError, match="没有这个数据集"):
        api.dataset_detail("../../etc")
    with pytest.raises(WorkspaceError, match="清单里没有"):
        api.dataset_doc("datasets/demo-md", "../../../etc/passwd")


class CountingAdapter(GoldAdapter):
    """记下入库次数与库名；按名字拒绝重名，模拟 KnowFlow 不许助手重名。"""

    ingests: list[str] = []

    async def ingest(self, files, kb_name):
        if kb_name in CountingAdapter.ingests:
            from knoweval.adapters.base import AdapterError
            raise AdapterError("Duplicated chat name in creating chat.")
        CountingAdapter.ingests.append(kb_name)
        return await super().ingest(files, kb_name)


def test_second_eval_with_same_config_reuses_the_ingested_kb(ws):
    """同一数据集、同一份配置的第二次评测复用第一次的知识库，不重新入库，也就不会撞上重名。"""
    CountingAdapter.ingests = []
    system, a, _ = _two_config_eval(ws)
    api = UiApi(ws, executor=EvalExecutor(ws, adapter_factory=lambda cfg: CountingAdapter()))
    for _ in range(2):
        meta = api.create_eval({"dataset_path": "datasets/demo-md", "repeats": 1,
                                "picks": [{"system_id": system.id, "config_id": a.id}]})
        api.executor.join(120)
        assert ws.load_eval(meta["id"]).status == "finished"
    assert len(CountingAdapter.ingests) == 1


def test_kb_names_are_unique_even_without_cache(tmp_path):
    """缓存失效需要重新入库时，库名带随机后缀，不与上一次重名。"""
    import asyncio as _asyncio
    from knoweval.runner.run import run_eval
    CountingAdapter.ingests = []
    ds = load_dataset(DATASETS / "demo-md")
    for i in range(2):
        _asyncio.run(run_eval(ds, CountingAdapter(), tmp_path / f"run{i}", system_config={"adapter": "fake"}, repeats=1))
    assert len(CountingAdapter.ingests) == 2 and CountingAdapter.ingests[0] != CountingAdapter.ingests[1]


def test_delete_dataset_removes_it_and_the_evals_that_used_it(ws):
    """评测结果页要读数据集的题目，所以删数据集时连同用到它的评测一起删，列表里先说清会删哪些。"""
    system, a, _ = _two_config_eval(ws)
    shutil.copytree(DATASETS / "demo-md", ws.root / "datasets" / "other")
    api = UiApi(ws, executor=EvalExecutor(ws, adapter_factory=lambda cfg: GoldAdapter()))
    used = ws.create_eval(dataset_path="datasets/demo-md", picks=[(system.id, a.id)], name="用它的评测")
    kept = ws.create_eval(dataset_path="datasets/other", picks=[(system.id, a.id)], name="别的评测")

    listing = {d["path"]: d for d in api.datasets()}
    assert listing["datasets/demo-md"]["used_by"] == ["用它的评测"]

    out = api.delete_dataset("datasets/demo-md")
    assert out["evals_deleted"] == 1
    assert not (ws.root / "datasets" / "demo-md").exists()
    assert [e.id for e in ws.list_evals()] == [kept.id]
    assert "datasets/demo-md" not in {d["path"] for d in api.datasets()}
    assert used.id not in [p.name for p in ws.evals_dir().iterdir()]


def test_delete_dataset_refuses_while_its_eval_runs_and_rejects_unknown_paths(ws):
    system, a, _ = _two_config_eval(ws)
    api = UiApi(ws, executor=EvalExecutor(ws, adapter_factory=lambda cfg: GoldAdapter()))
    meta = ws.create_eval(dataset_path="datasets/demo-md", picks=[(system.id, a.id)])
    api.executor.running_id = lambda: meta.id
    with pytest.raises(WorkspaceError, match="正在运行"):
        api.delete_dataset("datasets/demo-md")
    assert (ws.root / "datasets" / "demo-md").exists()
    with pytest.raises(WorkspaceError, match="没有这个数据集"):
        api.delete_dataset("../../etc")



def test_http_delete_dataset(client):
    c, ws = client
    assert c.delete("/api/datasets", params={"path": "datasets/demo-md"}).json()["ok"] is True
    assert c.get("/api/datasets").json() == []


def test_deleting_a_symlinked_dataset_only_removes_the_link(ws, tmp_path_factory):
    original = tmp_path_factory.mktemp("repo") / "linked"
    shutil.copytree(DATASETS / "demo-md", original)
    (ws.root / "datasets" / "linked").symlink_to(original, target_is_directory=True)
    api = UiApi(ws, executor=EvalExecutor(ws, adapter_factory=lambda cfg: GoldAdapter()))
    path = next(d["path"] for d in api.datasets() if d["path"].endswith("linked"))
    api.delete_dataset(path)
    assert not (ws.root / "datasets" / "linked").exists()
    assert (original / "questions.jsonl").exists()


def test_system_chats_lists_assistants_with_their_knowledge_bases(tmp_path, monkeypatch):
    """配置「已有助手」时从 KnowFlow 拉助手列表：名字、挂的知识库、对话模型。"""
    monkeypatch.setenv("KF", "k")
    ws = Workspace(tmp_path)
    system = ws.add_system({"name": "KnowFlow", "adapter": "knowflow", "base_url": "http://kf", "api_key_env": "KF"})
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen["url"], seen["auth"] = url, headers["Authorization"]
        return httpx.Response(200, json={"code": 0, "data": [
            {"id": "c1", "name": "线上助手", "llm": {"model_name": "qwen-vl"}, "datasets": [{"id": "kb1", "name": "手册", "doc_num": 4}]}]},
            request=httpx.Request("GET", url))

    monkeypatch.setattr("knoweval.ui.server.httpx.get", fake_get)
    chats = UiApi(ws).system_chats(system.id)
    assert seen == {"url": "http://kf/api/v1/chats", "auth": "Bearer k"}
    assert chats == [{"id": "c1", "name": "线上助手", "model": "qwen-vl", "kbs": [{"id": "kb1", "name": "手册", "documents": 4}]}]


def test_assistant_params_come_back_in_the_create_format(tmp_path, monkeypatch):
    """参数对照预填基准助手的全部参数，用建助手接口的写法（权重换算好、去掉身份和知识库），改哪项就是对照哪项。"""
    monkeypatch.setenv("KF", "k")
    ws = Workspace(tmp_path)
    system = ws.add_system({"name": "KnowFlow", "adapter": "knowflow", "base_url": "http://kf", "api_key_env": "KF"})
    chat = {"id": "c1", "name": "线上", "tenant_id": "t", "create_time": 1, "top_k": 1024, "do_refer": "1",
            "llm": {"model_name": "qwen@X", "max_tokens": 512},
            "prompt": {"keywords_similarity_weight": 0.7, "top_n": 8, "show_quotation": False, "sag_search_strategy": "multi"},
            "datasets": [{"id": "kb1", "name": "手册"}]}

    def fake_get(url, params=None, headers=None, timeout=None):
        assert url == "http://kf/api/v1/chats" and params == {"id": "c1"}
        return httpx.Response(200, json={"code": 0, "data": [chat]}, request=httpx.Request("GET", url))

    monkeypatch.setattr("knoweval.ui.server.httpx.get", fake_get)
    params = UiApi(ws).system_chat_params(system.id, "c1")
    assert params == {"top_k": 1024, "do_refer": "1", "llm": {"model_name": "qwen@X", "max_tokens": 512},
                      "prompt": {"vector_similarity_weight": 0.3, "top_n": 8, "sag_search_strategy": "multi"}}


def test_server_listens_on_localhost_by_default_and_on_all_interfaces_only_when_asked(tmp_path):
    """默认只监听本机；Docker 容器里要监听所有网卡（宿主机端口映射默认仍只给本机）。"""
    srv = make_server(tmp_path, port=0)
    assert srv.server_address[0] == "127.0.0.1"
    srv.server_close()
    srv = make_server(tmp_path, port=0, host="0.0.0.0")
    assert srv.server_address[0] == "0.0.0.0"
    srv.server_close()


def test_ui_command_reads_host_from_option_or_environment(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from knoweval import cli
    seen = {}

    class Fake:
        server_address = ("0.0.0.0", 8780)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    def fake_make(workspace, port, host="127.0.0.1"):
        seen["host"], seen["port"] = host, port
        return Fake()

    monkeypatch.setattr("knoweval.ui.server.make_server", fake_make)
    monkeypatch.setenv("KNOWEVAL_HOST", "0.0.0.0")
    r = CliRunner().invoke(cli.app, ["ui", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert seen == {"host": "0.0.0.0", "port": 8780}


def test_demo_system_can_be_added_in_one_click_when_the_demo_service_runs(tmp_path, monkeypatch):
    """没有自己的 RAG 系统也能先体验：Docker 里起了演示服务（KNOWEVAL_DEMO_RAG_URL），系统页一键接入。"""
    from knoweval.demo import DEMO_KEY_ENV
    ws = Workspace(tmp_path)
    api = UiApi(ws)
    monkeypatch.delenv("KNOWEVAL_DEMO_RAG_URL", raising=False)
    assert api.demo_info() == {"available": False}
    with pytest.raises(WorkspaceError, match="演示服务"):
        api.setup_demo()
    monkeypatch.setenv("KNOWEVAL_DEMO_RAG_URL", "http://demo-rag:18790")
    # compose 里总设着地址，但演示服务只在 --profile demo 时才起：连不上就当没有
    def unreachable(*a, **k):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr("knoweval.ui.server.httpx.get", unreachable)
    assert api.demo_info() == {"available": False}
    monkeypatch.setattr("knoweval.ui.server.httpx.get",
                        lambda url, **k: httpx.Response(404, json={"code": 404}, request=httpx.Request("GET", url)))
    assert api.demo_info()["available"] is True
    system = api.setup_demo()
    assert system["adapter"] == "http" and system["base_url"] == "http://demo-rag:18790"
    assert system["api_key_env"] == DEMO_KEY_ENV and system["http"]["ingest"] and system["http"]["ask"]["answer"]
    assert api.setup_demo()["id"] == system["id"]       # 再点一次不重复接入
    assert len(ws.systems()) == 1


def test_health_endpoint_is_tiny_and_client_disconnects_are_quiet(tmp_path, capsys):
    """健康检查用轻量接口（首页是 1 MB 多的单文件 HTML）；客户端中途断开不打报错堆栈。"""
    import io
    import urllib.request

    srv = make_server(tmp_path, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/api/health"
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=5) as resp:
            assert resp.status == 200 and json.loads(resp.read()) == {"ok": True}
    finally:
        srv.shutdown()
        srv.server_close()

    from knoweval.ui.server import _Handler

    class Gone(io.RawIOBase):
        def write(self, b):
            raise ConnectionResetError(104, "Connection reset by peer")

    h = _Handler.__new__(_Handler)
    h.wfile, h.request_version, h.requestline, h.command = Gone(), "HTTP/1.1", "GET / HTTP/1.1", "GET"
    h._headers_buffer = []
    h._send(200, b"x" * 10, "text/plain")     # 不抛异常


def test_eval_doc_preview_serves_the_original_file_and_its_text(client):
    """评测结果里点文档名：原文件给浏览器直接看（PDF 按页跳），也给解析后的文本用来高亮引文；只认这次评测清单里的文档。"""
    c, api = client
    system, a, _ = _two_config_eval(api.ws)
    meta = api.create_eval({"dataset_path": "datasets/demo-md", "repeats": 1,
                            "picks": [{"system_id": system.id, "config_id": a.id}]})
    api.executor.join(60)
    q = next(q for q in DEMO_MD.questions if q.evidence)
    name = q.evidence[0].doc

    text = c.get(f"/api/evals/{meta['id']}/doc", params={"name": name}).json()
    assert text["name"] == name and q.evidence[0].quote[:6] in text["text"].replace("\n", "")
    assert text["format"] == "md" and text["pages"] is False

    raw = c.get(f"/api/evals/{meta['id']}/doc/file", params={"name": name})
    assert raw.status_code == 200 and raw.content == (DATASETS / "demo-md" / "docs" / name).read_bytes()
    assert raw.headers["content-type"].startswith("text/markdown") and raw.headers["content-disposition"].startswith("inline")

    for path in ("doc", "doc/file"):
        bad = c.get(f"/api/evals/{meta['id']}/{path}", params={"name": "../../../etc/passwd"})
        assert bad.status_code == 400 and "清单里没有" in bad.json()["error"]


def test_doc_preview_text_marks_pdf_pages(tmp_path):
    import pymupdf

    from knoweval.ui.docpreview import doc_preview

    pdf = tmp_path / "a.pdf"
    with pymupdf.open() as doc:
        for body in ("first page", "second page"):
            doc.new_page().insert_text((72, 72), body)
        doc.save(pdf)
    out = doc_preview(pdf)
    assert out["format"] == "pdf" and out["pages"] is True
    assert out["text"].index("--- 第 1 页 ---") < out["text"].index("first page") < out["text"].index("--- 第 2 页 ---")


def test_eval_can_be_deleted_unless_it_is_running(client):
    """评测列表里删掉不要的评测：结果和题目快照一起删；正在跑的要先中止；只认评测编号，不让路径逃出 evals/。"""
    c, api = client
    system, a, _ = _two_config_eval(api.ws)
    api.executor.factory = lambda cfg: GoldAdapter(delay=5)
    meta = api.create_eval({"dataset_path": "datasets/demo-md", "repeats": 1,
                            "picks": [{"system_id": system.id, "config_id": a.id}]})
    busy = c.delete(f"/api/evals/{meta['id']}")
    assert busy.status_code == 400 and "中止" in busy.json()["error"]
    api.executor.stop(meta["id"])
    api.executor.join(30)

    assert c.delete(f"/api/evals/{meta['id']}").json() == {"ok": True}
    assert not (api.ws.evals_dir() / meta["id"]).exists()
    assert c.get("/api/state").json()["evals"] == []
    assert c.delete(f"/api/evals/{meta['id']}").status_code == 400                 # 已经没了
    assert (api.ws.root / "datasets" / "demo-md").exists()                         # 用到的官方数据集不动
    assert c.delete("/api/evals/..").status_code in (400, 404)

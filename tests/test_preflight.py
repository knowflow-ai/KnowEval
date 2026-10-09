"""试问检查：开始评测前抽几道有答案的题问一下，大多数回答「找不到」或报错，多半是文档没导入被测系统。"""

import shutil
from pathlib import Path

import pytest

from knoweval.adapters.base import AdapterError, IngestReport, KbHandle
from knoweval.schema import Answer
from knoweval.ui.preflight import preflight
from knoweval.ui.workspace import Workspace

DATASETS = Path(__file__).resolve().parents[1] / "datasets"
ASK_ONLY = {"ask": {"path": "/ask", "body": {"q": "{{question}}"}, "answer": "$.a"}, "kb_id": "default"}
WITH_INGEST = {**ASK_ONLY, "ingest": {"create_kb": {"path": "/kb", "body": {}, "id": "$.id"}, "upload": {"path": "/up"}}}


@pytest.fixture
def ws(tmp_path, monkeypatch):
    shutil.copytree(DATASETS / "demo-md", tmp_path / "datasets" / "demo-md")
    monkeypatch.setenv("K", "k")
    return Workspace(tmp_path)


def factory(reply):
    asked = []

    def make(cfg):
        class Adapter:
            async def ingest(self, files, kb_name):
                return IngestReport(kb=KbHandle(system="x", ids={"kb_id": "default"}), manual=True)

            async def ask(self, kb, question):
                asked.append(question)
                if isinstance(reply, Exception):
                    raise reply
                return Answer(text=reply)

            async def aclose(self):
                pass

        return Adapter()

    return make, asked


def _participants(ws, http):
    system = ws.add_system({"name": "自研", "adapter": "http", "base_url": "http://x", "api_key_env": "K", "http": http})
    cfg = ws.add_config(system.id, {"name": "默认"})
    return ws.plan_participants([(system.id, cfg.id)])


def test_documents_not_imported_is_flagged(ws):
    make, asked = factory("抱歉，知识库中没有找到相关信息。")
    [r] = preflight(ws.load_dataset_by_path("datasets/demo-md"), _participants(ws, ASK_ONLY), make)
    assert len(asked) == 3 and r["status"] == "suspect" and r["abstained"] == 3
    assert "文档" in r["message"] and r["samples"][0]["answer"].startswith("抱歉")


def test_answers_that_engage_the_question_pass(ws):
    make, _ = factory("根据文档，答案是 42。")
    [r] = preflight(ws.load_dataset_by_path("datasets/demo-md"), _participants(ws, ASK_ONLY), make)
    assert r["status"] == "ok"


def test_interface_errors_are_flagged(ws):
    make, _ = factory(AdapterError("提问接口返回 500"))
    [r] = preflight(ws.load_dataset_by_path("datasets/demo-md"), _participants(ws, ASK_ONLY), make)
    assert r["status"] == "suspect" and r["errors"] == 3 and "500" in r["message"]


def test_entries_that_build_a_new_kb_are_skipped(ws):
    """要新建知识库的对比项，入库完成前没得问，不试问。"""
    make, asked = factory("x")
    [r] = preflight(ws.load_dataset_by_path("datasets/demo-md"), _participants(ws, WITH_INGEST), make)
    assert r["status"] == "skipped" and asked == []


def test_existing_assistant_is_asked_before_the_eval(ws):
    """选了已有助手的对比项不建库，开跑前就能试问。"""
    system = ws.add_system({"name": "KnowFlow", "adapter": "knowflow", "base_url": "http://x", "api_key_env": "K"})
    make, asked = factory("答案在这里")
    [r] = preflight(ws.load_dataset_by_path("datasets/demo-md"),
                    ws.plan_participants([(system.id, "__chat__:c1")], chat_names={"c1": "线上助手"}), make)
    assert r["status"] != "skipped" and asked


def _assistant(ws):
    system = next((s for s in ws.systems() if s.name == "KnowFlow"), None) \
        or ws.add_system({"name": "KnowFlow", "adapter": "knowflow", "base_url": "http://x", "api_key_env": "K"})
    return ws.plan_participants([(system.id, "__chat__:c1")], chat_names={"c1": "线上助手"})


def test_probe_assistants_catches_a_broken_assistant_before_generating_questions(ws):
    """出题前先问助手一句：接口报错（如嵌入模型没了）当场发现，不白等几分钟出题。"""
    from knoweval.ui.preflight import probe_assistants
    make, asked = factory(AdapterError("LookupError('Model(nomic-embed-text:v1.5) not authorized')"))
    [r] = probe_assistants(_assistant(ws), make)
    assert not r["ok"] and r["name"] == "KnowFlow：线上助手" and "not authorized" in r["message"] and len(asked) == 1
    make, _ = factory("知识库里有产品手册和规章制度。")
    assert probe_assistants(_assistant(ws), make)[0]["ok"]


def test_probe_skips_entries_that_would_build_a_knowledge_base(ws):
    """只试问已有助手；按配置建库的对比项试问前要先建库，不在这里做。"""
    from knoweval.ui.preflight import probe_assistants
    make, asked = factory("x")
    [r] = probe_assistants(_participants(ws, WITH_INGEST), make)
    assert r["ok"] and asked == []


@pytest.mark.parametrize("error, hint", [
    ("AdapterError: POST /chats/x/completions 失败：ConnectError('[Errno 61] Connection refused')", "依赖的服务"),
    ("AdapterError: LookupError('Model(nomic-embed-text:v1.5___OpenAI-API@OpenAI-API-Compatible) not authorized')", "模型"),
    ("TimeoutError: 超过 180s 未返回", "超时"),
    ("AdapterError: 问答接口返回 401：invalid api key", "API Key"),
])
def test_interface_errors_get_a_cause_not_the_missing_documents_guess(error, hint):
    from knoweval.ui.preflight import explain_error
    assert hint in explain_error(error)


def test_all_errors_report_says_interface_problem_once(ws):
    """全部接口报错：说是接口的问题（不是文档没导入），同一个报错只说一次，标出全部报错。"""
    make, _ = factory(AdapterError("POST /chats/x/completions 失败：ConnectError('[Errno 61] Connection refused')"))
    [r] = preflight(ws.load_dataset_by_path("datasets/demo-md"), _participants(ws, ASK_ONLY), make)
    assert r["status"] == "suspect" and r["all_errors"] is True
    assert "文档还没导入" not in r["message"] and "依赖的服务" in r["message"]
    assert r["message"].count("Connection refused") == 1


def test_probe_and_preflight_release_what_they_created(ws):
    """试问时为参数对照建的临时助手，问完就删。"""
    from knoweval.ui.preflight import probe_assistants
    released = []
    make, _ = factory("正常回答")

    def make_releasing(cfg):
        adapter = make(cfg)

        async def release(kb):
            released.append(kb.ids)

        adapter.release = release
        return adapter

    probe_assistants(_assistant(ws), make_releasing)
    preflight(ws.load_dataset_by_path("datasets/demo-md"), _participants(ws, ASK_ONLY), make_releasing)
    assert len(released) == 2


def test_concurrent_probes_use_distinct_names(ws):
    """几组参数对照同时试问，各建各的临时助手：名字不能撞（KnowFlow 不许助手重名）。"""
    from knoweval.ui.preflight import probe_assistants
    names = []
    make, _ = factory("正常")

    def recording(cfg):
        adapter = make(cfg)
        inner = adapter.ingest

        async def ingest(files, kb_name):
            names.append(kb_name)
            return await inner(files, kb_name)

        adapter.ingest = ingest
        return adapter

    system = next(s for s in ws.systems()) if ws.systems() else ws.add_system(
        {"name": "KnowFlow", "adapter": "knowflow", "base_url": "http://x", "api_key_env": "K"})
    over = {"overrides": {"prompt": {"top_n": 3}}}
    parts = ws.plan_participants([(system.id, "__chat__:c1~1"), (system.id, "__chat__:c1~2")],
                                 variants={"__chat__:c1~1": over, "__chat__:c1~2": over})
    probe_assistants(parts, recording)
    preflight(ws.load_dataset_by_path("datasets/demo-md"), parts, recording)
    assert len(names) == 4 and len(set(names)) == 4

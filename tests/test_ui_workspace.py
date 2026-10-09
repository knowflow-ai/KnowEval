"""评测台工作区：系统/配置/设置的文件存储，评测创建时的配置快照。"""

import shutil
from pathlib import Path

import pytest

from knoweval.config import build_adapter
from knoweval.ui.workspace import Workspace, WorkspaceError

DATASETS = Path(__file__).resolve().parents[1] / "datasets"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    shutil.copytree(DATASETS / "demo-md", tmp_path / "datasets" / "demo-md")
    monkeypatch.setenv("KNOWFLOW_API_KEY", "k")
    return Workspace(tmp_path)


def _knowflow(ws: Workspace):
    system = ws.add_system({"name": "KnowFlow", "adapter": "knowflow", "base_url": "http://localhost:9380",
                            "api_key_env": "KNOWFLOW_API_KEY"})
    smart = ws.add_config(system.id, {"name": "MinerU + smart",
                                      "dataset": {"chunk_method": "smart", "embedding_model": "bge-m3"}})
    naive = ws.add_config(system.id, {"name": "DeepDOC + naive",
                                      "dataset": {"chunk_method": "naive", "embedding_model": "bge-m3"}})
    return system, smart, naive


def test_systems_and_configs_round_trip(ws):
    system, smart, _ = _knowflow(ws)
    loaded = Workspace(ws.root).systems()
    assert [s.name for s in loaded] == ["KnowFlow"]
    assert [c.name for c in loaded[0].configs] == ["MinerU + smart", "DeepDOC + naive"]
    with pytest.raises(WorkspaceError, match="同名系统"):
        ws.add_system({"name": "KnowFlow", "adapter": "ragflow", "base_url": "http://x", "api_key_env": "E"})
    with pytest.raises(WorkspaceError, match="同名配置"):
        ws.add_config(system.id, {"name": "MinerU + smart"})
    ws.delete_config(system.id, smart.id)
    assert [c.name for c in ws.systems()[0].configs] == ["DeepDOC + naive"]
    ws.delete_system(system.id)
    assert ws.systems() == []


def test_system_plus_config_builds_a_real_adapter(ws):
    system, smart, _ = _knowflow(ws)
    cfg = ws.system_config(system, smart, concurrency=2)
    adapter = build_adapter(cfg)
    assert adapter.name == "knowflow" and cfg.dataset["chunk_method"] == "smart"


def test_settings_default_and_save(ws):
    assert ws.settings().model == ""  # 默认没有裁判模型：只用规则判
    ws.save_settings({"base_url": "https://api.deepseek.com", "model": "deepseek-chat", "api_key_env": "J"})
    assert Workspace(ws.root).settings().model == "deepseek-chat"


def test_create_eval_snapshots_configs(ws):
    system, smart, naive = _knowflow(ws)
    meta = ws.create_eval(dataset_path="datasets/demo-md", picks=[(system.id, smart.id), (system.id, naive.id)],
                          repeats=2, concurrency=3)
    assert meta.name == "KnowFlow：MinerU + smart vs KnowFlow：DeepDOC + naive"
    assert meta.dataset_ref == "demo-md@0.0"
    assert [p.config["dataset"]["chunk_method"] for p in meta.participants] == ["smart", "naive"]
    # 之后改配置不影响已建评测：快照在 eval.json 里
    ws.delete_config(system.id, smart.id)
    again = ws.load_eval(meta.id)
    assert again.participants[0].config["dataset"]["chunk_method"] == "smart"
    # 运行目录名只含安全字符且互不相同
    ids = [p.id for p in meta.participants]
    assert len(set(ids)) == 2 and all(i.replace("-", "").replace("_", "").isalnum() for i in ids)


def test_create_eval_rejects_bad_input(ws):
    system, smart, _ = _knowflow(ws)
    with pytest.raises(WorkspaceError, match="至少勾选"):
        ws.create_eval(dataset_path="datasets/demo-md", picks=[])
    with pytest.raises(WorkspaceError, match="配置"):
        ws.create_eval(dataset_path="datasets/demo-md", picks=[(system.id, "cfg-missing")])
    with pytest.raises(Exception):  # 数据集不存在
        ws.create_eval(dataset_path="datasets/nope", picks=[(system.id, smart.id)])


def test_participant_status_update(ws):
    system, smart, _ = _knowflow(ws)
    meta = ws.create_eval(dataset_path="datasets/demo-md", picks=[(system.id, smart.id)])
    pid = meta.participants[0].id
    ws.update_participant(meta.id, pid, status="failed", error="boom")
    loaded = ws.load_eval(meta.id)
    assert loaded.participants[0].status == "failed" and loaded.participants[0].error == "boom"


def test_datasets_discovered_under_workspace(ws):
    infos = ws.datasets()
    assert [d.ref for d in infos] == ["demo-md@0.0"]
    assert ws.dataset_path_str(infos[0]) == "datasets/demo-md"


def test_misspelled_chunk_method_is_rejected_with_a_hint(ws):
    """建库接口不校验分块方式，写错要到解析时才全部失败，所以添加配置时就拦下并提示正确写法。"""
    system = ws.add_system({"name": "KnowFlow", "adapter": "knowflow", "base_url": "http://x", "api_key_env": "KNOWFLOW_API_KEY"})
    with pytest.raises(WorkspaceError, match="是不是要写 parent_child"):
        ws.add_config(system.id, {"name": "父子", "dataset": {"chunk_method": "parent-child"}})
    ws.add_config(system.id, {"name": "父子", "dataset": {"chunk_method": "parent_child"}})
    rag = ws.add_system({"name": "RAGFlow", "adapter": "ragflow", "base_url": "http://y", "api_key_env": "E"})
    with pytest.raises(WorkspaceError, match="不是 ragflow 认得的写法"):
        ws.add_config(rag.id, {"name": "smart", "dataset": {"chunk_method": "smart"}})  # smart 是 KnowFlow 独有的


def test_update_system_and_config(ws):
    system, smart, naive = _knowflow(ws)
    updated = ws.update_system(system.id, {"name": "KnowFlow-新", "base_url": "http://other:9380", "version": "2.6.2"})
    assert (updated.name, updated.base_url, updated.version) == ("KnowFlow-新", "http://other:9380", "2.6.2")
    assert [c.name for c in updated.configs] == ["MinerU + smart", "DeepDOC + naive"]  # 配置原样保留
    with pytest.raises(WorkspaceError, match="类型不能修改"):
        ws.update_system(system.id, {"adapter": "ragflow"})
    other = ws.add_system({"name": "另一个", "adapter": "ragflow", "base_url": "http://y", "api_key_env": "E"})
    with pytest.raises(WorkspaceError, match="同名系统"):
        ws.update_system(other.id, {"name": "KnowFlow-新"})

    cfg = ws.update_config(system.id, smart.id, {"name": "smart-v2", "dataset": {"chunk_method": "parent_child", "embedding_model": "e"}})
    assert cfg.name == "smart-v2" and cfg.id == smart.id
    with pytest.raises(WorkspaceError, match="parent_child"):
        ws.update_config(system.id, smart.id, {"dataset": {"chunk_method": "parent-child"}})
    with pytest.raises(WorkspaceError, match="同名配置"):
        ws.update_config(system.id, smart.id, {"name": "DeepDOC + naive"})


def test_editing_a_system_does_not_change_existing_evals(ws):
    system, smart, _ = _knowflow(ws)
    meta = ws.create_eval(dataset_path="datasets/demo-md", picks=[(system.id, smart.id)])
    ws.update_system(system.id, {"base_url": "http://changed:1"})
    ws.update_config(system.id, smart.id, {"dataset": {"chunk_method": "title", "embedding_model": "e"}})
    snap = ws.load_eval(meta.id).participants[0].config
    assert snap["base_url"] == "http://localhost:9380" and snap["dataset"]["chunk_method"] == "smart"


def test_thinking_is_off_by_default_and_reaches_the_request(tmp_path):
    """评测台的模型默认关闭思考：会思考的模型（如 Qwen3）开着要慢几十倍，有的还把结论只写进思考里。"""
    import asyncio
    from types import SimpleNamespace

    from knoweval.ui.workspace import JudgeSettings, Workspace

    ws = Workspace(tmp_path)
    assert ws.settings().disable_thinking is True
    (tmp_path / "settings.json").write_text('{"judge": {"model": "m"}}', encoding="utf-8")
    assert ws.settings().disable_thinking is True   # 旧设置文件没有这一项，也按关闭处理

    sent = []

    async def fake_create(**kw):
        sent.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    for flag, expect in [(True, {"enable_thinking": False}), (False, None)]:
        client = JudgeSettings(base_url="http://x", model="m", disable_thinking=flag).client("k")
        client._client.chat.completions.create = fake_create
        assert asyncio.run(client.complete([{"role": "user", "content": "hi"}], "m")) == "ok"
        assert sent[-1].get("extra_body") == expect


def test_the_retired_bound_kb_pick_is_rejected(ws):
    """「沿用测试集的被测知识库」已经去掉：已有知识库走「测已有助手」，建库对比走建库配置。"""
    system, _, _ = _knowflow(ws)
    with pytest.raises(WorkspaceError, match="没有这份配置"):
        ws.create_eval(dataset_path="datasets/demo-md", picks=[(system.id, "__bound__")])


def test_system_without_configs_can_still_be_evaluated_through_its_ask_api(ws):
    """自研系统的配置本来就是空的：不用先加一份「默认」配置，直接选它的问答接口。"""
    mine = ws.add_system({"name": "自研", "adapter": "http", "base_url": "http://x", "api_key_env": "KNOWFLOW_API_KEY",
                          "http": {"ask": {"path": "/ask", "body": {"q": "{{question}}"}, "answer": "$.a"}, "kb_id": "default"}})
    meta = ws.create_eval(dataset_path="datasets/demo-md", picks=[(mine.id, "__default__")])
    assert meta.participants[0].name == "自研：问答接口"
    from knoweval.config import SystemConfig
    assert build_adapter(SystemConfig.model_validate(meta.participants[0].config)).name == "http"


def test_third_party_configs_pass_their_params_to_the_adapter(ws, monkeypatch):
    """自研系统也能加多份配置：参数包交给适配器，每份配置可以用自己的 API Key（如 Dify 一个应用一把 Key）。"""
    monkeypatch.setenv("APP_B_KEY", "kb")
    mine = ws.add_system({"name": "自研", "adapter": "http", "base_url": "http://x", "api_key_env": "KNOWFLOW_API_KEY",
                          "http": {"ask": {"path": "/ask", "body": {"q": "{{question}}"}, "answer": "$.a"}, "kb_id": "k"}})
    a = ws.add_config(mine.id, {"name": "top5", "chat": {"top_k": 5}})
    b = ws.add_config(mine.id, {"name": "应用B", "chat": {"top_k": 10}, "api_key_env": "APP_B_KEY"})
    sa = ws.system_config(ws.systems()[0], a, concurrency=1)
    sb = ws.system_config(ws.systems()[0], b, concurrency=1)
    assert sa.api_key_env == "KNOWFLOW_API_KEY" and sb.api_key_env == "APP_B_KEY"
    adapter = build_adapter(sb)
    assert adapter.chat_params == {"top_k": 10} and adapter._api_key == "kb"


def test_saved_configs_are_build_configs_only(ws):
    """系统页的配置只管建库：已有助手在新建评测里直接选，存成配置会被拒绝。"""
    system = ws.add_system({"name": "KnowFlow", "adapter": "knowflow", "base_url": "http://localhost:9380",
                            "api_key_env": "KNOWFLOW_API_KEY"})
    with pytest.raises(WorkspaceError, match="测已有助手"):
        ws.add_config(system.id, {"name": "线上助手", "existing_chat": {"id": "c1", "name": "线上助手"}})
    cfg = ws.add_config(system.id, {"name": "smart", "dataset": {"chunk_method": "smart", "embedding_model": "e"},
                                    "existing_kbs": [{"id": "kb1", "name": "旧字段"}]})
    assert "existing_kbs" not in cfg.model_dump()   # 以前的「连接已有知识库」字段不再认


def test_eval_can_pick_an_assistant_directly_without_a_saved_config(ws):
    """测已有助手：新建评测时直接选系统里的助手，不用先在系统页加一份配置。"""
    system = ws.add_system({"name": "KnowFlow", "adapter": "knowflow", "base_url": "http://x", "api_key_env": "KNOWFLOW_API_KEY"})
    [p] = ws.plan_participants([(system.id, "__chat__:c1")], chat_names={"c1": "线上助手"})
    assert p.name == "KnowFlow：线上助手"
    assert p.config["existing_chat"] == {"id": "c1", "name": "线上助手"} and p.config["dataset"] == {} and p.config["chat"] == {}
    http = ws.add_system({"name": "自研", "adapter": "http", "base_url": "http://x", "api_key_env": "KNOWFLOW_API_KEY",
                          "http": {"ask": {"path": "/ask", "answer": "$.a"}, "kb_id": "k"}})
    with pytest.raises(WorkspaceError, match="助手"):
        ws.plan_participants([(http.id, "__chat__:c1")])


def test_assistant_variant_is_the_base_assistant_plus_overrides(ws):
    """参数对照：以一个已有助手为基准，只改几项参数；名字带上对照名，配置里记下改了什么。"""
    system = ws.add_system({"name": "KnowFlow", "adapter": "knowflow", "base_url": "http://x", "api_key_env": "KNOWFLOW_API_KEY"})
    over = {"prompt": {"sag_search_strategy": "atomic"}}
    base, var = ws.plan_participants([(system.id, "__chat__:c1"), (system.id, "__chat__:c1~1")], chat_names={"c1": "TEST"},
                                     variants={"__chat__:c1~1": {"name": "SAG atomic", "overrides": over}})
    assert base.name == "KnowFlow：TEST" and not base.config.get("chat_overrides")
    assert var.name == "KnowFlow：TEST · SAG atomic"
    assert var.config["existing_chat"] == {"id": "c1", "name": "TEST"} and var.config["chat_overrides"] == over
    assert build_adapter(ws.system_config(system, ws._chat_config(system, "__chat__:c1~1", {"c1": "TEST"},
                                                                  {"__chat__:c1~1": {"name": "x", "overrides": over}}),
                                          concurrency=1)).chat_overrides == over
    with pytest.raises(WorkspaceError, match="对照"):
        ws.plan_participants([(system.id, "__chat__:c1~2")], chat_names={"c1": "TEST"})

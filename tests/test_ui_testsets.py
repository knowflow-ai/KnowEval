"""测试集与评测的衔接：测试集出完直接评测（题目快照）、为助手出题后自动评测、参数对照、文档包、来源知识库。"""

import base64
import json
import shutil
from pathlib import Path

import pytest

from knoweval.adapters.base import AdapterError, IngestReport, KbHandle
from knoweval.ui.builds import BuildService
from knoweval.ui.server import UiApi
from knoweval.ui.sourcekb import source_kb
from knoweval.ui.workspace import Workspace, WorkspaceError

DATASETS = Path(__file__).resolve().parents[1] / "datasets"
DOCS = DATASETS / "demo-md" / "docs"


class NoRun:
    """不真跑评测：这里只看评测建出来的对比项。出完题自动评测前要试问助手，假适配器总是答得上来。"""

    def start(self, eval_id):
        pass

    def running_id(self):
        return None

    def factory(self, cfg):
        from knoweval.schema import Answer

        class Adapter:
            async def ingest(self, files, kb_name):
                return IngestReport(kb=KbHandle(system="x", ids={"chat_id": "c1"}), existing=True)

            async def ask(self, kb, question):
                return Answer(text="知识库里有产品手册。")

            async def aclose(self):
                pass

        return Adapter()


@pytest.fixture
def ws(tmp_path, monkeypatch):
    shutil.copytree(DATASETS / "demo-md", tmp_path / "datasets" / "demo-md")
    monkeypatch.setenv("KNOWFLOW_API_KEY", "k")
    return Workspace(tmp_path)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    shutil.copytree(DATASETS / "demo-md", tmp_path / "datasets" / "demo-md")
    monkeypatch.setenv("KNOWFLOW_API_KEY", "k")
    return Workspace(tmp_path)


def _setup(ws, llm=None, executor=None):
    system = ws.add_system({"name": "KnowFlow", "adapter": "knowflow", "base_url": "http://kf", "api_key_env": "KNOWFLOW_API_KEY"})
    smart = ws.add_config(system.id, {"name": "smart", "dataset": {"chunk_method": "smart", "embedding_model": "e"}})
    builds = BuildService(ws, llm=llm or (lambda: (_ for _ in ()).throw(AssertionError("不该调模型"))))
    return system, smart, builds, UiApi(ws, executor=executor or NoRun(), builds=builds)


def _files(n=2):
    return [(f.name, f.read_bytes()) for f in sorted(DOCS.glob("*.md"))[:n]]


def test_documents_can_be_downloaded_as_a_zip_for_manual_import(ws):
    """只接问答接口的系统：用户下载测试集的文档包，自己导入被测系统。"""
    import io
    import zipfile
    system, smart, builds, api = _setup(ws)
    name, data = api.docs_zip(path="datasets/demo-md")
    assert name == "demo-md@0.0-文档.zip"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert len(z.namelist()) == 7 and all(n.endswith(".md") for n in z.namelist())
    meta = builds.create_from_files("包", "0.1", _files(2), {})
    builds.join(60)
    name, data = api.docs_zip(build_id=meta.id)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert len(z.namelist()) == 2


def test_test_set_is_evaluated_directly_without_publishing(ws):
    """没有「发布」：测试集出完就能评测。开始评测时把题目快照进评测目录，之后改题不影响这次评测。"""
    system, smart, builds, api = _setup(ws)
    meta = builds.create_from_files("直接评测", "0.1", _files(1), {})
    builds.join(60)
    builds.save_item(meta.id, None, {"qtype": "N", "question": "提到火星了吗？"})

    ev = api.create_eval({"build_id": meta.id, "repeats": 1, "picks": [{"system_id": system.id, "config_id": smart.id}]})
    assert ev["dataset_path"] == f"evals/{ev['id']}/dataset"
    assert ev["participants"][0]["config"]["dataset"]["chunk_method"] == "smart"
    from knoweval.dataset import load_dataset
    snap = load_dataset(ws.root / ev["dataset_path"])
    assert [q.question for q in snap.questions] == ["提到火星了吗？"]

    builds.save_item(meta.id, None, {"qtype": "N", "question": "提到月球了吗？"})   # 评测之后再加题
    assert len(load_dataset(ws.root / ev["dataset_path"]).questions) == 1           # 已有评测的题目不变
    assert builds.load(meta.id).status == "ready"                                   # 没有「已发布」这回事
    assert [d["path"] for d in api.datasets()] == ["datasets/demo-md"]               # 快照不出现在测试集列表里

    empty = builds.create_from_files("空的", "0.1", _files(1), {})
    builds.join(60)
    with pytest.raises(WorkspaceError, match="没有可用的题"):
        api.create_eval({"build_id": empty.id, "picks": [{"system_id": system.id, "config_id": smart.id}]})


def test_build_from_an_assistant_then_eval_that_assistant(ws):
    """测已有助手、还没有题：从助手的知识库出题，出完直接问这个助手。"""
    from tests.test_ui_builds import _llm
    system, _, builds, api = _setup(ws, llm=_llm())
    pick = {"system_id": system.id, "chat_id": "c1", "chat_name": "线上助手"}
    files = [{"name": n, "content": base64.b64encode(b).decode()} for n, b in _files(3)]
    meta = api.create_build({"source": "upload", "name": "助手题", "files": files, "mix": {"F": 2},
                             "after_ready": {"picks": [pick], "repeats": 1}})
    builds.join(60)
    d = builds.detail(meta["id"])
    ev = ws.load_eval(d["after_ready"]["eval_id"])
    assert [p.name for p in ev.participants] == ["KnowFlow：线上助手"] and ev.repeats == 1
    assert ev.participants[0].config["existing_chat"]["id"] == "c1"
    # 选的东西不对，出题前就报错，不等几分钟出完题才发现
    with pytest.raises(WorkspaceError):
        api.create_build({"source": "upload", "name": "坏", "files": files, "mix": {"F": 2},
                          "after_ready": {"picks": [{"system_id": "nope", "chat_id": "c1"}]}})


class BusyRun(NoRun):
    """已经有评测在跑：开不了新的。"""

    def start(self, eval_id):
        raise WorkspaceError("已有评测在运行（ev-other），跑完或中止后再开始")


def test_auto_eval_waits_when_another_eval_is_running(ws):
    """出完题时正好有评测在跑：评测照样建好并记下编号，提示稍后点开始；不留孤儿、不引导用户再建一个。"""
    from tests.test_ui_builds import _llm
    system, _, builds, api = _setup(ws, llm=_llm(), executor=BusyRun())
    files = [{"name": n, "content": base64.b64encode(b).decode()} for n, b in _files(3)]
    meta = api.create_build({"source": "upload", "name": "助手题", "files": files, "mix": {"F": 2},
                             "after_ready": {"picks": [{"system_id": system.id, "chat_id": "c1", "chat_name": "线上"}]}})
    builds.join(60)
    plan = builds.detail(meta["id"])["after_ready"]
    assert plan["eval_id"] and "没能开始" in plan["note"]
    assert ws.load_eval(plan["eval_id"]).status == "created"
    # 手动开始时撞上正在跑的评测：报错，并且不留下建了一半的评测
    before = {e.id for e in ws.list_evals()}
    with pytest.raises(WorkspaceError, match="已有评测在运行"):
        api.create_eval({"build_id": meta["id"], "picks": [{"system_id": system.id, "chat_id": "c1"}], "repeats": 1})
    assert {e.id for e in ws.list_evals()} == before


def test_after_ready_keeps_only_known_fields_and_checks_numbers(ws):
    system, _, builds, api = _setup(ws)
    pick = {"system_id": system.id, "chat_id": "c1"}
    files = [{"name": n, "content": base64.b64encode(b).decode()} for n, b in _files(1)]
    for bad in ({"repeats": 0}, {"concurrency": 0}, {"repeats": "两遍"}):
        with pytest.raises(WorkspaceError):
            api.create_build({"source": "upload", "name": "x", "files": files, "mix": {},
                              "after_ready": {"picks": [pick], **bad}})
    meta = api.create_build({"source": "upload", "name": "x", "files": files, "mix": {},
                             "after_ready": {"picks": [pick], "eval_id": "ev-fake00", "junk": 1}})
    assert meta["after_ready"] == {"picks": [pick], "repeats": 2, "concurrency": 4, "name": ""}
    builds.join(60)
    with pytest.raises(WorkspaceError, match="导入"):
        api.create_build({"source": "import", "name": "y", "table": {"name": "q.csv", "content": ""},
                          "after_ready": {"picks": [pick]}})


class ProbeRun(NoRun):
    """不真跑评测，但能建适配器（出完题自动评测前要先试问助手）。"""

    def __init__(self, ask):
        self.ask = ask

    def factory(self, cfg):
        ask = self.ask

        class Adapter:
            async def ingest(self, files, kb_name):
                return IngestReport(kb=KbHandle(system="x", ids={"chat_id": "c1"}), existing=True)

            async def ask(self, kb, question):
                return ask(question)

            async def aclose(self):
                pass

        return Adapter()


def test_auto_eval_does_not_start_when_the_assistant_is_broken(ws):
    """出完题、开评测前再问一次助手：坏了就不开跑，在测试集上说明原因。"""
    from knoweval.schema import Answer
    from tests.test_ui_builds import _llm

    def broken(q):
        raise AdapterError("Model(nomic-embed-text:v1.5) not authorized")

    system, _, builds, api = _setup(ws, llm=_llm(), executor=ProbeRun(broken))
    files = [{"name": n, "content": base64.b64encode(b).decode()} for n, b in _files(3)]
    meta = api.create_build({"source": "upload", "name": "助手题", "files": files, "mix": {"F": 2},
                             "after_ready": {"picks": [{"system_id": system.id, "chat_id": "c1", "chat_name": "线上"}]}})
    builds.join(60)
    plan = builds.detail(meta["id"])["after_ready"]
    assert not plan.get("eval_id") and "not authorized" in plan["error"]
    assert ws.list_evals() == []
    # 接口也给页面用：出题前先试问
    r = api.probe_assistants({"picks": [{"system_id": system.id, "chat_id": "c1", "chat_name": "线上"}]})
    assert not r["results"][0]["ok"]
    api.executor.ask = lambda q: Answer(text="正常")
    assert api.probe_assistants({"picks": [{"system_id": system.id, "chat_id": "c1", "chat_name": "线上"}]})["results"][0]["ok"]


def test_eval_with_parameter_variants_of_one_assistant(ws):
    """一次评测：基准助手 + 两组参数对照，都问同一套题。"""
    from tests.test_ui_builds import _llm
    system, _, builds, api = _setup(ws, llm=_llm(), executor=NoRun())
    files = [{"name": n, "content": base64.b64encode(b).decode()} for n, b in _files(3)]
    meta = api.create_build({"source": "upload", "name": "题", "files": files, "mix": {"F": 2}})
    builds.join(60)
    base = {"system_id": system.id, "chat_id": "c1", "chat_name": "TEST"}
    ev = api.create_eval({"build_id": meta["id"], "repeats": 1, "picks": [
        base,
        {**base, "variant": {"name": "SAG atomic", "overrides": {"prompt": {"sag_search_strategy": "atomic"}}}},
        {**base, "variant": {"name": "多召回", "overrides": {"prompt": {"top_n": 16}}}},
    ]})
    names = [p["name"] for p in ev["participants"]]
    assert names == ["KnowFlow：TEST", "KnowFlow：TEST · SAG atomic", "KnowFlow：TEST · 多召回"]
    assert [p["config"].get("chat_overrides") for p in ev["participants"]][1:] == [
        {"prompt": {"sag_search_strategy": "atomic"}}, {"prompt": {"top_n": 16}}]
    with pytest.raises(WorkspaceError, match="对照"):
        api.create_eval({"build_id": meta["id"], "picks": [{**base, "variant": {"name": "坏", "overrides": "不是对象"}}]})


def test_create_build_accepts_my_questions_together_with_generation(ws):
    """接口：上传文档出题时可以带上自己的题目文件，两种放进同一套。"""
    from tests.test_ui_builds import _llm, _mixed_table
    _, _, builds, api = _setup(ws, llm=_llm())
    name, data = _mixed_table()
    files = [{"name": n, "content": base64.b64encode(b).decode()} for n, b in _files(3)]
    meta = api.create_build({"source": "upload", "name": "混合", "files": files, "mix": {"F": 2},
                             "table": {"name": name, "content": base64.b64encode(data).decode()}})
    builds.join(60)
    d = builds.detail(meta["id"])
    assert d["status"] == "ready" and d["import_report"]["imported"] == 2
    generators = {it["generator"] for it in d["items"]}
    assert "import" in generators and generators - {"import"}     # 导入的和模型出的在同一套里


def test_only_test_sets_drawn_from_a_knowledge_base_have_a_source_kb(ws):
    """「测已有助手」按来源知识库把测试集配给助手；上传文档、导入题目、官方数据集都没有来源库。"""
    system, _, builds, api = _setup(ws)
    assert source_kb({"kind": "upload", "files": 2}) is None
    # 刚建、还没取完文档：用页面给的库名
    fresh = source_kb({"kind": "kb", "system": "KnowFlow", "system_id": system.id, "kb_ids": ["kb1"], "kb_names": {"kb1": "手册"}})
    assert fresh == {"system_id": system.id, "system": "KnowFlow", "kbs": [{"id": "kb1", "name": "手册"}]}

    root = ws.root / "datasets" / "from-kb"
    shutil.copytree(DATASETS / "demo-md", root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["build"] = {"source": {"kind": "kb", "system": "KnowFlow", "system_id": system.id, "kb_ids": ["kb1"], "kbs": [{"id": "kb1", "name": "手册", "documents": 3, "kept": 3}]}}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    listed = {d["path"]: d["source_kb"] for d in api.datasets()}
    assert listed["datasets/demo-md"] is None
    assert listed["datasets/from-kb"]["kbs"] == [{"id": "kb1", "name": "手册"}]
    meta = builds.create_from_files("上传的", "0.1", _files(1), {})
    builds.join(60)
    assert builds.detail(meta.id)["source_kb"] is None and "bound_kb" not in builds.detail(meta.id)

"""系统配置、命令行 report / judge。"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from knoweval.cli import app
from knoweval.config import ConfigError, build_adapter, load_system_config
from knoweval.dataset import load_dataset
from knoweval.runner.run import run_eval
from tests.test_runner import FakeAdapter

DEMO_DIR = Path(__file__).resolve().parents[1] / "datasets" / "demo"


def _write_cfg(tmp_path, body: str) -> Path:
    p = tmp_path / "sys.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_missing_api_key_env_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("KE_TEST_KEY", raising=False)
    cfg = load_system_config(_write_cfg(tmp_path, "adapter: ragflow\nbase_url: http://x\napi_key_env: KE_TEST_KEY\n"))
    with pytest.raises(ConfigError, match="KE_TEST_KEY"):
        build_adapter(cfg)


def test_knowflow_uses_the_ragflow_api_with_its_own_name(tmp_path, monkeypatch):
    monkeypatch.setenv("KE_TEST_KEY", "secret-value")
    cfg = load_system_config(_write_cfg(tmp_path, (
        "adapter: knowflow\nbase_url: http://x\napi_key_env: KE_TEST_KEY\n"
        "dataset:\n  chunk_method: smart\n  embedding_model: m\n")))
    adapter = build_adapter(cfg)
    assert adapter.name == "knowflow"
    assert adapter.dataset_config == {"chunk_method": "smart", "embedding_model": "m"}
    assert "secret-value" not in json.dumps(cfg.public())


def test_unknown_adapter_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_system_config(_write_cfg(tmp_path, "adapter: nope\nbase_url: http://x\napi_key_env: K\n"))


@pytest.fixture
async def finished_run(tmp_path):
    ds = load_dataset(DEMO_DIR)
    await run_eval(ds, FakeAdapter(answers={"demo-002": "首席架构师"}), tmp_path, repeats=2,
                   system_config={"adapter": "fake"})
    return tmp_path


def test_report_prints_the_four_metrics(finished_run):
    result = CliRunner().invoke(app, ["report", str(finished_run)])
    assert result.exit_code == 0, result.output
    for label in ("准确率", "自信错答率", "拒答正确率", "过度拒答率", "噪声", "证据形态", "跳数"):
        assert label in result.output


def test_judge_rescores_saved_answers_without_asking_again(finished_run):
    ckpt = finished_run / "checkpoint.r1.jsonl"
    rows = [json.loads(line) for line in ckpt.read_text(encoding="utf-8").splitlines()]
    for r in rows:
        r["judgement"]["verdict"] = "WRONG"
    ckpt.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    result = CliRunner().invoke(app, ["judge", str(finished_run), "--dataset", str(DEMO_DIR)])
    assert result.exit_code == 0, result.output
    rejudged = {json.loads(line)["qid"]: json.loads(line) for line in ckpt.read_text(encoding="utf-8").splitlines()}
    assert rejudged["demo-002"]["judgement"]["verdict"] == "CORRECT"
    assert rejudged["demo-002"]["answer"]["text"] == "首席架构师"
    meta = json.loads((finished_run / "run.json").read_text(encoding="utf-8"))
    assert "rejudged_at" in meta
    summary = json.loads((finished_run / "summary.json").read_text(encoding="utf-8"))
    assert summary["repeats"][0]["accuracy"]["k"] == 1

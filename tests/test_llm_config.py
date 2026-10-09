"""评测台与命令行共用一个模型（判分、出题、校验都用它）：地址和模型名在「设置」页配，Key 在 .env。"""

import json

import pytest

from knoweval.llm_config import LLM_KEY_ENV, LlmConfigError, read_api_key, resolve_llm
from knoweval.ui.workspace import JudgeSettings


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("KNOWEVAL_LLM_API_KEY", "MY_OWN_KEY"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_default_key_variable_is_the_single_llm_key():
    assert LLM_KEY_ENV == "KNOWEVAL_LLM_API_KEY" and JudgeSettings().api_key_env == LLM_KEY_ENV


def test_key_is_read_from_the_named_variable_only(clean_env):
    """判分、出题共用一把 Key；变量名可在「设置」页改，空值当没设。"""
    assert read_api_key("KNOWEVAL_LLM_API_KEY") is None
    clean_env.setenv("KNOWEVAL_LLM_API_KEY", "k")
    clean_env.setenv("MY_OWN_KEY", "")
    assert read_api_key("KNOWEVAL_LLM_API_KEY") == "k" and read_api_key("MY_OWN_KEY") is None
    clean_env.setenv("MY_OWN_KEY", "mine")
    assert JudgeSettings(api_key_env="MY_OWN_KEY").api_key() == "mine"


def _save_on_settings_page(root, **judge):
    """用评测台「设置」页的保存逻辑写文件，不手写格式（手写的格式曾经和真实文件对不上）。"""
    from knoweval.ui.workspace import Workspace
    Workspace(root).save_settings(judge)


def test_command_line_reads_the_settings_page_of_the_current_directory(tmp_path, clean_env):
    clean_env.setenv("KNOWEVAL_LLM_API_KEY", "k")
    ws_root = tmp_path / "ws"
    _save_on_settings_page(ws_root, base_url="http://llm/v1", model="qwen236b", disable_thinking=False)
    s, key = resolve_llm(ws_root)
    assert (s.base_url, s.model, s.disable_thinking, key) == ("http://llm/v1", "qwen236b", False, "k")
    s, _ = resolve_llm(ws_root, model="other", base_url="http://x/v1")   # 命令行参数优先
    assert (s.base_url, s.model) == ("http://x/v1", "other")
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "settings.json").write_text(json.dumps({"judge": {"base_url": "http://a", "model": "m"}}), encoding="utf-8")
    resolve_llm(tmp_path / "plain")
    assert not (tmp_path / "plain" / "evals").exists()   # 只读设置，不在当前目录建工作区


def test_missing_model_or_key_says_where_to_configure(tmp_path, clean_env):
    with pytest.raises(LlmConfigError, match="设置"):
        resolve_llm(tmp_path)
    _save_on_settings_page(tmp_path, base_url="http://llm/v1", model="m")
    with pytest.raises(LlmConfigError, match="KNOWEVAL_LLM_API_KEY"):
        resolve_llm(tmp_path)


@pytest.mark.real_judge_wiring
def test_run_command_judges_with_the_settings_page_model(tmp_path, clean_env):
    from knoweval.cli import _text_judge
    clean_env.setenv("KNOWEVAL_LLM_API_KEY", "k")
    clean_env.chdir(tmp_path)
    _save_on_settings_page(tmp_path, base_url="http://llm/v1", model="qwen236b")
    assert _text_judge(None, None, tmp_path).model == "qwen236b"

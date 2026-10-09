"""启动时读 .env：密钥写在文件里，不用每次 export；已经设置的环境变量不覆盖。"""

import os

import pytest
from typer.testing import CliRunner

from knoweval.envfile import load_env_file, parse_env


def test_parse_env_handles_comments_export_quotes_and_equals_in_values():
    text = """
# 注释
export KNOWFLOW_API_KEY=ragflow-abc
SOME_BASE_URL = https://api.deepseek.com/v1  # 行尾注释
QUOTED="带 空格 # 不是注释"
SINGLE='a=b'
EMPTY=
不合法的一行
"""
    assert parse_env(text) == {
        "KNOWFLOW_API_KEY": "ragflow-abc",
        "SOME_BASE_URL": "https://api.deepseek.com/v1",
        "QUOTED": "带 空格 # 不是注释",
        "SINGLE": "a=b",
        "EMPTY": "",
    }


def test_load_env_file_does_not_override_existing_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("KE_SET_ALREADY", "from-shell")
    monkeypatch.delenv("KE_FROM_FILE", raising=False)
    monkeypatch.delenv("KE_LEFT_BLANK", raising=False)
    # 照着 .env.example 复制、还没填的空值不读：读进来是空串，反而像「设置了但值不对」
    (tmp_path / ".env").write_text("KE_SET_ALREADY=from-file\nKE_FROM_FILE=x\nKE_LEFT_BLANK=\n", encoding="utf-8")
    assert load_env_file(tmp_path / ".env") == ["KE_FROM_FILE"]
    assert os.environ["KE_SET_ALREADY"] == "from-shell" and os.environ["KE_FROM_FILE"] == "x"
    assert "KE_LEFT_BLANK" not in os.environ
    monkeypatch.delenv("KE_FROM_FILE")
    assert load_env_file(tmp_path / "missing.env") == []


def test_every_command_reads_dotenv_in_the_current_directory(tmp_path, monkeypatch):
    from knoweval.cli import app

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KE_CLI_VAR", raising=False)
    (tmp_path / ".env").write_text("KE_CLI_VAR=hello\n", encoding="utf-8")
    CliRunner().invoke(app, ["report", str(tmp_path / "no-such-run")])
    assert os.environ.get("KE_CLI_VAR") == "hello"
    monkeypatch.delenv("KE_CLI_VAR")


@pytest.mark.parametrize("bad", ["=no-name", "1ABC=x", "A B=x"])
def test_invalid_names_are_skipped(bad):
    assert parse_env(bad) == {}

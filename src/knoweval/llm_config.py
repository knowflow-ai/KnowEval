"""KnowEval 自己用的模型：判分、出题、自动校验共用一个（OpenAI 兼容接口）。

接口地址、模型名在评测台「设置」页填，存在工作区的 settings.json；命令行读当前目录的同一份设置，
也可以用参数临时覆盖。API Key 不进任何文件，只从环境变量读（可写在 .env 里），默认变量名 KNOWEVAL_LLM_API_KEY。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from knoweval.ui.workspace import JudgeSettings

LLM_KEY_ENV = "KNOWEVAL_LLM_API_KEY"


class LlmConfigError(ValueError):
    pass


def read_api_key(env_name: str) -> str | None:
    """按变量名取 Key。"""
    return os.environ.get(env_name) or None


def resolve_llm(root: Path, model: str | None = None, base_url: str | None = None) -> tuple[JudgeSettings, str]:
    """命令行用的模型设置：参数优先，其次是 root 下的 settings.json（评测台「设置」页存的）。

    只读文件，不在 root 下建工作区。
    """
    from knoweval.ui.workspace import read_settings

    try:
        saved = read_settings(Path(root))
    except (ValueError, OSError) as e:
        raise LlmConfigError(f"读不了 {Path(root) / 'settings.json'}：{e}") from e
    settings = saved.model_copy(update={k: v for k, v in {"model": model, "base_url": base_url}.items() if v})
    if not settings.model or not settings.base_url:
        raise LlmConfigError("还没配模型：在评测台「设置」页填好接口地址和模型名（命令行读当前目录的 settings.json），"
                             "或用 --judge-model、--judge-base-url 临时指定")
    key = read_api_key(settings.api_key_env)
    if not key:
        raise LlmConfigError(f"环境变量 {settings.api_key_env} 没有设置：把模型的 API Key 写进 .env")
    return settings, key

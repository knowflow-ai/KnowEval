"""适配器接口（规范第 2 节）：被测系统是黑盒，只通过 ingest / ask 打交道。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from knoweval.schema import Answer


# 按整个名字（或它的最后一段）认密钥：api_key、X-Api-Key、apiKey、access_token、client_secret……
# 不按子串认，否则 max_tokens、keywords_similarity_weight 这类真参数也会被删，改了它们配置快照也看不出来
_SECRET_NAME = re.compile(r"(^|_)(key|api_?key|secret|password|passwd|token|access_token|refresh_token|"
                          r"credentials?|authorization)$")


def _is_secret(name: str) -> bool:
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return bool(_SECRET_NAME.search(re.sub(r"[^a-z0-9]+", "_", snake.lower())))


def drop_secret_keys(obj: Any) -> Any:
    """自由字典里名字是密钥的字段一律删掉（写进 run.json、配置快照之前用）。"""
    if isinstance(obj, dict):
        return {k: drop_secret_keys(v) for k, v in obj.items() if not _is_secret(str(k))}
    if isinstance(obj, list):
        return [drop_secret_keys(v) for v in obj]
    return obj


class AdapterError(RuntimeError):
    """被测系统返回了错误。运行器会把它记为这道题的 ERROR，而不是中断整轮。"""


class KbHandle(BaseModel):
    system: str
    ids: dict[str, str]


class DocIngest(BaseModel):
    name: str
    ok: bool
    seconds: float | None = None
    error: str | None = None


class IngestReport(BaseModel):
    kb: KbHandle
    docs: list[DocIngest] = Field(default_factory=list)
    # 手工入库：文档由用户自己传进被测系统，入库过程无法复现，报告里会注明
    manual: bool = False
    # 用的是被测系统里已有的知识库：没有建库上传，分块等设置以那个库为准
    existing: bool = False


class Adapter(Protocol):
    name: str
    supports_citations: bool

    async def ingest(self, files: list[Path], kb_name: str) -> IngestReport: ...

    async def ask(self, kb: KbHandle, question: str) -> Answer:
        """每次调用都必须是全新会话，前一题不能成为后一题的对话历史。"""
        ...

    async def kb_alive(self, kb: KbHandle) -> bool:
        """缓存的知识库是否还能用。"""
        ...

    async def aclose(self) -> None: ...

"""被测系统配置（YAML）。密钥只从环境变量读，配置文件与结果里只出现变量名。"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from knoweval.adapters.base import AdapterError, drop_secret_keys
from knoweval.adapters.http import HttpAdapter
from knoweval.adapters.ragflow import KnowflowAdapter, RagflowAdapter


class ConfigError(ValueError):
    pass


class SystemConfig(BaseModel):
    adapter: Literal["ragflow", "knowflow", "http"]
    base_url: str
    api_key_env: str
    version: str | None = None
    # 参评配置的参数包：KnowFlow/RAGFlow 是建库、建助手接口的入参；自研系统合并进建库、问答请求（见 HttpAdapter）
    dataset: dict[str, Any] = Field(default_factory=dict)
    chat: dict[str, Any] = Field(default_factory=dict)
    # 通用 HTTP 适配器的请求模板与取值路径（adapter: http 时必填）
    http: dict[str, Any] = Field(default_factory=dict)
    # 回答里相对地址的图片从哪里下载（如 KnowFlow 的 /minio/... 由网页端口的 nginx 提供，不在 API 端口）
    image_base_url: str | None = None
    # 用被测系统里已有的知识库（[{id, name}]）：不建库上传，dataset 不起作用。只有 KnowFlow、RAGFlow 支持
    existing_kbs: list[dict[str, str]] = Field(default_factory=list)
    # 直接问被测系统里已有的助手（{id, name}）：不建库、不建助手，设置全以那个助手为准。只有 KnowFlow、RAGFlow 支持
    existing_chat: dict[str, str] | None = None
    # 参数对照：以 existing_chat 为基准，只改这几项参数（建助手接口的格式），评测台建临时助手问、问完删掉
    chat_overrides: dict[str, Any] | None = None
    concurrency: int = 4
    ask_timeout_s: float = 180
    ingest_timeout_s: float = 3600

    def public(self) -> dict[str, Any]:
        """写进 run.json 的配置：URL 去掉用户名口令，自由字典里名字像密钥的字段一律删掉。"""
        data = self.model_dump(mode="json")
        data["base_url"] = _strip_url_credentials(self.base_url)
        if self.image_base_url:
            data["image_base_url"] = _strip_url_credentials(self.image_base_url)
        data["dataset"] = drop_secret_keys(self.dataset)
        data["chat"] = drop_secret_keys(self.chat)
        data["http"] = drop_secret_keys(self.http)
        data["chat_overrides"] = drop_secret_keys(self.chat_overrides) if self.chat_overrides else None
        return data


def _strip_url_credentials(url: str) -> str:
    parts = urlsplit(url)
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port:
        host += f":{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def load_system_config(path: str | Path) -> SystemConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    try:
        return SystemConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"系统配置 {path} 不合法：{e}") from e


def build_adapter(cfg: SystemConfig) -> RagflowAdapter | HttpAdapter:
    api_key = os.environ.get(cfg.api_key_env)
    if not api_key:
        raise ConfigError(f"环境变量 {cfg.api_key_env} 没有设置，拿不到被测系统的 API Key")
    try:
        if cfg.adapter == "http":
            if cfg.existing_chat:
                raise ConfigError("自研系统没有「助手」，参数请写在配置的问答参数里")
            if len(cfg.existing_kbs) > 1:
                raise ConfigError("自研系统一次只能向一个知识库提问")
            return HttpAdapter(base_url=cfg.base_url, api_key=api_key, config=cfg.http, ask_timeout_s=cfg.ask_timeout_s,
                               ingest_timeout_s=cfg.ingest_timeout_s, image_base_url=cfg.image_base_url,
                               existing_kb=cfg.existing_kbs[0]["id"] if cfg.existing_kbs else None,
                               chat_params=cfg.chat, dataset_params=cfg.dataset)
        cls = KnowflowAdapter if cfg.adapter == "knowflow" else RagflowAdapter
        return cls(base_url=cfg.base_url, api_key=api_key, name=cfg.adapter,
                   dataset_config=cfg.dataset, chat_config=cfg.chat,
                   ingest_timeout_s=cfg.ingest_timeout_s, ask_timeout_s=cfg.ask_timeout_s,
                   image_base_url=cfg.image_base_url, existing_kbs=cfg.existing_kbs, existing_chat=cfg.existing_chat,
                   chat_overrides=cfg.chat_overrides)
    except AdapterError as e:
        raise ConfigError(str(e)) from e

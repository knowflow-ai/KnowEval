"""评测台工作区：系统、配置、设置、评测全是目录里的 JSON 文件，和「文件即结果」一个路数。

目录结构（workspace 根下）：
  systems.json      已接入的系统，每个系统带若干份命名配置（接口参数包）
  settings.json     评测台自己的设置：裁判模型
  datasets/         数据集目录（manifest.json + questions.jsonl）
  evals/<id>/eval.json           一次评测：数据集 + 参评配置快照 + 状态
  evals/<id>/runs/<参与者id>/     每个参评配置一个标准运行目录（run.json、检查点、summary）

参评配置在创建评测时**快照**成完整的 SystemConfig：之后改系统或配置不影响已建的评测，可复现。
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from knoweval.llm_config import LLM_KEY_ENV, read_api_key
from knoweval.adapters.base import AdapterError
from knoweval.adapters.ragflow import check_chunk_method
from knoweval.config import SystemConfig
from knoweval.dataset import Dataset, find_datasets, load_dataset


class WorkspaceError(ValueError):
    pass


class UiConfig(BaseModel):
    """一份参评配置：透传给被测系统接口的参数包，评测台不理解它们、只记录。"""

    id: str
    name: str
    dataset: dict = Field(default_factory=dict)   # 建库接口的入参（如 chunk_method）
    chat: dict = Field(default_factory=dict)      # 建助手接口的入参
    # 测已有助手：新建评测时直接选的助手（{id, name}），参数全以它为准；chat_overrides 是参数对照要改的几项。
    # 两者都只在建评测时临时生成（见 CHAT_PICK），不存成系统页的配置
    existing_chat: dict[str, str] | None = None
    chat_overrides: dict | None = None
    # 这份配置用自己的 API Key（环境变量名）：Dify 这类系统一个应用一把 Key，换应用就是换配置。不填用系统的
    api_key_env: str | None = None


DEFAULT_ENTRY = "__default__"   # 没加配置的系统：直接用它的问答接口（自研系统的配置本来就是空的）
CHAT_PICK = "__chat__:"   # 新建评测时直接选系统里的助手（后面跟助手 id，参数对照再跟 ~序号），不用先存成配置


class UiSystem(BaseModel):
    """一个被测系统：问答接口（和可选的建库上传接口）。KnowFlow、RAGFlow、自研同级。"""

    id: str
    name: str
    adapter: Literal["knowflow", "ragflow", "http"]
    base_url: str
    api_key_env: str
    version: str | None = None
    image_base_url: str | None = None
    http: dict = Field(default_factory=dict)      # 自研系统的接口模板
    http_form: dict = Field(default_factory=dict)  # 接入表单的原始填写（只给页面回填用，不进适配器）
    ask_timeout_s: float = 180
    ingest_timeout_s: float = 3600
    configs: list[UiConfig] = Field(default_factory=list)


class JudgeSettings(BaseModel):
    """评测台唯一要配的模型：判分的裁判，也给新建测试集出题、做闭卷过滤与无答案查证。没配就不能评测和出题。"""

    base_url: str = ""
    model: str = ""
    api_key_env: str = LLM_KEY_ENV
    # 会思考的模型开着思考要慢几十倍，有的还把结论只写进思考、正文留空
    disable_thinking: bool = True

    def api_key(self) -> str | None:
        """Key 只从环境变量读（可写在 .env 里），新旧两个默认变量名互为后备。"""
        return read_api_key(self.api_key_env)

    def client(self, api_key: str, timeout_s: float = 60):
        from knoweval.judge.llm_judge import OpenAIChatClient

        return OpenAIChatClient(self.base_url, api_key, timeout_s=timeout_s, disable_thinking=self.disable_thinking)


def read_settings(root: Path) -> JudgeSettings:
    """工作区「设置」页存的模型设置（settings.json 的 judge 一节）。评测台与命令行共用，只读不建目录。"""
    path = Path(root) / "settings.json"
    if not path.exists():
        return JudgeSettings()
    return JudgeSettings.model_validate(json.loads(path.read_text(encoding="utf-8")).get("judge", {}))


class Participant(BaseModel):
    """评测里的一个参评配置（创建时的完整快照）。"""

    id: str
    name: str
    system_name: str
    config: dict                                   # SystemConfig 的完整内容
    status: Literal["pending", "running", "finished", "failed", "stopped"] = "pending"
    error: str | None = None


class EvalMeta(BaseModel):
    id: str
    name: str
    dataset_ref: str
    dataset_path: str                              # 相对 workspace 根；不在根下时为绝对路径
    repeats: int = 2
    concurrency: int = 4
    participants: list[Participant]
    status: Literal["created", "running", "finished", "failed", "stopped"] = "created"
    created_at: str
    finished_at: str | None = None
    error: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(3)}"


_RUN_DIR_SAFE = re.compile(r"[^A-Za-z0-9_-]")


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "evals").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---------- 系统与配置 ----------

    def systems(self) -> list[UiSystem]:
        return [UiSystem.model_validate(s) for s in self._read_list(self.root / "systems.json")]

    def _save_systems(self, systems: list[UiSystem]) -> None:
        self._write(self.root / "systems.json", [s.model_dump(mode="json") for s in systems])

    def add_system(self, data: dict) -> UiSystem:
        with self._lock:
            systems = self.systems()
            try:
                system = UiSystem.model_validate({"id": _new_id("sys"), "configs": [], **data})
            except ValidationError as e:
                raise WorkspaceError(f"系统信息不合法：{e}") from e
            if any(s.name == system.name for s in systems):
                raise WorkspaceError(f"已有同名系统：{system.name}")
            self._save_systems([*systems, system])
            return system

    def update_system(self, system_id: str, data: dict) -> UiSystem:
        """改系统的接入信息。类型不能改（换类型请新接入一个）；配置原样保留。已建的评测用的是快照，不受影响。"""
        with self._lock:
            systems = self.systems()
            system = self._find_system(systems, system_id)
            editable = {k: v for k, v in data.items() if k not in ("id", "adapter", "configs")}
            if data.get("adapter") and data["adapter"] != system.adapter:
                raise WorkspaceError("系统类型不能修改，换类型请新接入一个系统")
            try:
                updated = UiSystem.model_validate({**system.model_dump(mode="json"), **editable})
            except ValidationError as e:
                raise WorkspaceError(f"系统信息不合法：{e}") from e
            if any(s.name == updated.name and s.id != system_id for s in systems):
                raise WorkspaceError(f"已有同名系统：{updated.name}")
            self._save_systems([updated if s.id == system_id else s for s in systems])
            return updated

    def delete_system(self, system_id: str) -> None:
        with self._lock:
            systems = self.systems()
            if not any(s.id == system_id for s in systems):
                raise WorkspaceError("没有这个系统")
            self._save_systems([s for s in systems if s.id != system_id])

    def add_config(self, system_id: str, data: dict) -> UiConfig:
        with self._lock:
            systems = self.systems()
            system = self._find_system(systems, system_id)
            try:
                cfg = UiConfig.model_validate({"id": _new_id("cfg"), **data})
            except ValidationError as e:
                raise WorkspaceError(f"配置不合法：{e}") from e
            if any(c.name == cfg.name for c in system.configs):
                raise WorkspaceError(f"该系统下已有同名配置：{cfg.name}")
            self._check_config(system, cfg)
            updated = system.model_copy(update={"configs": [*system.configs, cfg]})
            self._save_systems([updated if s.id == system_id else s for s in systems])
            return cfg

    def update_config(self, system_id: str, config_id: str, data: dict) -> UiConfig:
        with self._lock:
            systems = self.systems()
            system = self._find_system(systems, system_id)
            current = next((c for c in system.configs if c.id == config_id), None)
            if current is None:
                raise WorkspaceError("没有这份配置")
            try:
                cfg = UiConfig.model_validate({**current.model_dump(mode="json"),
                                               **{k: v for k, v in data.items() if k != "id"}})
            except ValidationError as e:
                raise WorkspaceError(f"配置不合法：{e}") from e
            if any(c.name == cfg.name and c.id != config_id for c in system.configs):
                raise WorkspaceError(f"该系统下已有同名配置：{cfg.name}")
            self._check_config(system, cfg)
            configs = [cfg if c.id == config_id else c for c in system.configs]
            updated = system.model_copy(update={"configs": configs})
            self._save_systems([updated if s.id == system_id else s for s in systems])
            return cfg

    def delete_config(self, system_id: str, config_id: str) -> None:
        with self._lock:
            systems = self.systems()
            system = self._find_system(systems, system_id)
            kept = [c for c in system.configs if c.id != config_id]
            if len(kept) == len(system.configs):
                raise WorkspaceError("没有这份配置")
            updated = system.model_copy(update={"configs": kept})
            self._save_systems([updated if s.id == system_id else s for s in systems])

    @staticmethod
    def _check_config(system: UiSystem, cfg: UiConfig) -> None:
        """系统页的配置都是建库配置：已有助手在新建评测里直接选，不存成配置。"""
        if cfg.existing_chat is not None or cfg.chat_overrides is not None:
            raise WorkspaceError("已有助手不用存成配置：在「新建评测 → 测已有助手」里直接选")
        if system.adapter in ("knowflow", "ragflow"):
            try:
                check_chunk_method(system.adapter, cfg.dataset)
            except AdapterError as e:
                raise WorkspaceError(str(e)) from e

    @staticmethod
    def _find_system(systems: list[UiSystem], system_id: str) -> UiSystem:
        for s in systems:
            if s.id == system_id:
                return s
        raise WorkspaceError("没有这个系统")

    def system_config(self, system: UiSystem, cfg: UiConfig, *, concurrency: int) -> SystemConfig:
        """系统 + 配置 合成一份可直接建适配器的 SystemConfig。"""
        return SystemConfig(adapter=system.adapter, base_url=system.base_url, api_key_env=cfg.api_key_env or system.api_key_env,
                            version=system.version,
                            dataset={} if cfg.existing_chat else cfg.dataset,
                            chat={} if cfg.existing_chat else cfg.chat, http=system.http,
                            existing_chat=cfg.existing_chat,
                            chat_overrides=cfg.chat_overrides,
                            image_base_url=system.image_base_url, concurrency=concurrency,
                            ask_timeout_s=system.ask_timeout_s, ingest_timeout_s=system.ingest_timeout_s)

    # ---------- 设置 ----------

    def settings(self) -> JudgeSettings:
        return read_settings(self.root)

    def save_settings(self, judge: dict) -> JudgeSettings:
        try:
            settings = JudgeSettings.model_validate(judge)
        except ValidationError as e:
            raise WorkspaceError(f"设置不合法：{e}") from e
        self._write(self.root / "settings.json", {"judge": settings.model_dump(mode="json")})
        return settings

    # ---------- 数据集 ----------

    def datasets(self) -> list[Dataset]:
        return find_datasets([self.root / "datasets"])

    def dataset_path_str(self, ds: Dataset) -> str:
        try:
            return ds.root.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return str(ds.root.resolve())

    def load_dataset_by_path(self, path: str) -> Dataset:
        full = (self.root / path) if not Path(path).is_absolute() else Path(path)
        return load_dataset(full)

    # ---------- 评测 ----------

    def evals_dir(self) -> Path:
        return self.root / "evals"

    def list_evals(self) -> list[EvalMeta]:
        out = []
        for meta in sorted(self.evals_dir().glob("*/eval.json")):
            out.append(EvalMeta.model_validate_json(meta.read_text(encoding="utf-8")))
        return sorted(out, key=lambda e: e.created_at, reverse=True)

    def load_eval(self, eval_id: str) -> EvalMeta:
        path = self.evals_dir() / eval_id / "eval.json"
        if not path.exists():
            raise WorkspaceError("没有这个评测")
        return EvalMeta.model_validate_json(path.read_text(encoding="utf-8"))

    def save_eval(self, meta: EvalMeta) -> None:
        with self._lock:
            self._write(self.evals_dir() / meta.id / "eval.json", meta.model_dump(mode="json"))

    def update_eval(self, eval_id: str, **fields) -> EvalMeta:
        with self._lock:
            meta = self.load_eval(eval_id).model_copy(update=fields)
            self._write(self.evals_dir() / eval_id / "eval.json", meta.model_dump(mode="json"))
            return meta

    def update_participant(self, eval_id: str, participant_id: str, **fields) -> EvalMeta:
        with self._lock:
            meta = self.load_eval(eval_id)
            parts = [p.model_copy(update=fields) if p.id == participant_id else p for p in meta.participants]
            meta = meta.model_copy(update={"participants": parts})
            self._write(self.evals_dir() / eval_id / "eval.json", meta.model_dump(mode="json"))
            return meta

    def run_dir(self, eval_id: str, participant_id: str) -> Path:
        return self.evals_dir() / eval_id / "runs" / participant_id

    def plan_participants(self, picks: list[tuple[str, str]], *, concurrency: int = 4,
                          chat_names: dict[str, str] | None = None,
                          variants: dict[str, dict] | None = None) -> list[Participant]:
        """把勾选的 (system_id, config_id) 合成参评配置快照；建评测与试问检查共用。

        config_id 以 CHAT_PICK 开头时是直接选的已有助手，chat_names 给出助手 id 对应的名字；
        带 ~序号 的是这个助手的参数对照，variants 按 config_id 给出 {name, overrides}。
        """
        if not picks:
            raise WorkspaceError("至少勾选一份参评配置")
        systems = self.systems()
        participants: list[Participant] = []
        for system_id, config_id in picks:
            system = self._find_system(systems, system_id)
            if config_id.startswith(CHAT_PICK):
                cfg = self._chat_config(system, config_id, chat_names or {}, variants or {})
            elif config_id == DEFAULT_ENTRY:
                cfg = UiConfig(id=DEFAULT_ENTRY, name="问答接口" if system.adapter == "http" else "默认设置")
            else:
                cfg = next((c for c in system.configs if c.id == config_id), None)
            if cfg is None:
                raise WorkspaceError(f"系统 {system.name} 下没有这份配置：{config_id}")
            sc = self.system_config(system, cfg, concurrency=concurrency)
            pid = self._participant_dir_name(f"{system.name}-{cfg.name}", [p.id for p in participants])
            participants.append(Participant(id=pid, name=f"{system.name}：{cfg.name}", system_name=system.name,
                                            config=sc.model_dump(mode="json")))
        return participants

    def create_eval(self, *, dataset_path: str, picks: list[tuple[str, str]], name: str = "",
                    repeats: int = 2, concurrency: int = 4, eval_id: str | None = None,
                    chat_names: dict[str, str] | None = None, variants: dict[str, dict] | None = None) -> EvalMeta:
        """picks 是 (system_id, config_id) 列表。配置在此刻快照，之后改系统不影响这次评测。"""
        if repeats < 1:
            raise WorkspaceError("至少跑一遍")
        participants = self.plan_participants(picks, concurrency=concurrency, chat_names=chat_names,
                                              variants=variants)
        ds = self.load_dataset_by_path(dataset_path)
        eval_id = eval_id or _new_id("ev")
        meta = EvalMeta(id=eval_id, name=name or " vs ".join(p.name for p in participants),
                        dataset_ref=ds.ref, dataset_path=dataset_path, repeats=repeats, concurrency=concurrency,
                        participants=participants, created_at=_now())
        (self.evals_dir() / eval_id / "runs").mkdir(parents=True, exist_ok=True)
        self.save_eval(meta)
        return meta

    @staticmethod
    def _chat_config(system: UiSystem, config_id: str, chat_names: dict[str, str],
                     variants: dict[str, dict] | None = None) -> UiConfig:
        """直接问系统里已有的助手（设置以它为准，什么都不建）；带 ~序号 的是它的参数对照。"""
        if system.adapter not in ("knowflow", "ragflow"):
            raise WorkspaceError(f"{system.name} 没有「助手」：只有 KnowFlow、RAGFlow 能直接选已有助手")
        chat_id, _, n = config_id[len(CHAT_PICK):].strip().partition("~")
        if not chat_id:
            raise WorkspaceError("没选助手")
        name = chat_names.get(chat_id) or chat_id
        if not n:
            return UiConfig(id=config_id, name=name, existing_chat={"id": chat_id, "name": name})
        variant = (variants or {}).get(config_id)
        overrides = (variant or {}).get("overrides")
        if not isinstance(overrides, dict) or not overrides:
            raise WorkspaceError(f"参数对照「{(variant or {}).get('name') or n}」要写要改的参数（JSON 对象）")
        label = str(variant.get("name") or f"对照 {n}").strip()
        return UiConfig(id=config_id, name=f"{name} · {label}", existing_chat={"id": chat_id, "name": name},
                        chat_overrides=overrides)

    @staticmethod
    def _participant_dir_name(raw: str, taken: list[str]) -> str:
        """运行目录名要能当文件名；中文等字符去掉后撞名或为空就用序号。"""
        slug = _RUN_DIR_SAFE.sub("", raw.replace(" ", "-")).strip("-").lower()
        if not slug or slug in taken:
            slug = f"cfg{len(taken) + 1}"
        return slug

    # ---------- 文件 ----------

    @staticmethod
    def _read_list(path: Path) -> list:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise WorkspaceError(f"{path.name} 不是合法 JSON：{e}") from e
        if not isinstance(data, list):
            raise WorkspaceError(f"{path.name} 的内容应是列表")
        return data

    @staticmethod
    def _write(path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

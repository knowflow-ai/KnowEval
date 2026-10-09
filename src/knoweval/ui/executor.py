"""评测执行器：把一次评测里的参评配置逐个交给现有 run_eval 跑，状态写回 eval.json。

一次只跑一个评测（被测系统和本机都吃不消并行多个）；同一评测内的配置按顺序跑。
中止靠取消 asyncio 任务——检查点已保证中断安全，重新开始会从断点续跑。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from knoweval.config import ConfigError, SystemConfig, build_adapter
from knoweval.dataset import Dataset
from knoweval.judge.llm_judge import OpenAICompatibleJudge
from knoweval.ui.workspace import Workspace, WorkspaceError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvalExecutor:
    def __init__(self, workspace: Workspace, adapter_factory=None, cache_dir: Path | None = None, judge_factory=None):
        """judge_factory：运行目录 → 裁判；默认按「设置」页的模型建，测试时注入假裁判。"""
        self.ws = workspace
        self.factory = adapter_factory or build_adapter
        self.judge_factory = judge_factory
        # 入库缓存：同一数据集、同一份配置跨评测只入库一次（复用前确认库还在）。不开的话每次评测都重新建库、
        # 重新解析，既慢，又会撞上被测系统的重名限制
        self.cache_dir = cache_dir or workspace.root / ".cache" / "ingest"
        self._lock = threading.Lock()
        self._current: dict | None = None  # {"eval_id", "thread", "loop", "task"}

    def running_id(self) -> str | None:
        with self._lock:
            if self._current and self._current["thread"].is_alive():
                return self._current["eval_id"]
            return None

    def start(self, eval_id: str) -> None:
        with self._lock:
            if self._current and self._current["thread"].is_alive():
                raise WorkspaceError(f"已有评测在运行（{self._current['eval_id']}），跑完或中止后再开始")
            meta = self.ws.load_eval(eval_id)
            if meta.status == "running":
                # 上次进程崩了没来得及改状态；文件里的检查点还在，重开即续跑
                pass
            holder: dict = {"eval_id": eval_id, "cancelled": False}
            thread = threading.Thread(target=self._thread_main, args=(eval_id, holder),
                                      name=f"knoweval-{eval_id}", daemon=True)
            holder["thread"] = thread
            self._current = holder
        self.ws.update_eval(eval_id, status="running", error=None, finished_at=None)
        # 重新开始时，没跑完的配置先回到「排队中」，否则轮到它之前一直挂着上一次的失败
        for p in meta.participants:
            if p.status != "finished":
                self.ws.update_participant(eval_id, p.id, status="pending", error=None)
        thread.start()

    def stop(self, eval_id: str) -> None:
        with self._lock:
            holder = self._current
        if not holder or holder["eval_id"] != eval_id or not holder["thread"].is_alive():
            raise WorkspaceError("这个评测没有在运行")
        holder["cancelled"] = True
        loop, task = holder.get("loop"), holder.get("task")
        if loop and task:
            loop.call_soon_threadsafe(task.cancel)

    def join(self, timeout: float | None = None) -> None:
        """等当前评测线程结束（测试用）。"""
        with self._lock:
            holder = self._current
        if holder:
            holder["thread"].join(timeout)

    # ---------- 线程内部 ----------

    def _thread_main(self, eval_id: str, holder: dict) -> None:
        loop = asyncio.new_event_loop()
        holder["loop"] = loop
        try:
            loop.run_until_complete(self._run(eval_id, holder))
        except Exception as e:  # noqa: BLE001 - 兜底：执行器自己的缺陷也要落到 eval.json，不能无声消失
            self.ws.update_eval(eval_id, status="failed", error=f"{type(e).__name__}: {e}", finished_at=_now())
        finally:
            loop.close()

    async def _run(self, eval_id: str, holder: dict) -> None:
        holder["task"] = asyncio.current_task()
        meta = self.ws.load_eval(eval_id)
        dataset = self.ws.load_dataset_by_path(meta.dataset_path)
        stopped = failed = False
        for p in meta.participants:
            if p.status == "finished":
                continue
            if holder["cancelled"]:
                stopped = True
                break
            self.ws.update_participant(eval_id, p.id, status="running", error=None)
            try:
                await self._run_participant(meta, p.id, SystemConfig.model_validate(p.config), dataset)
                self.ws.update_participant(eval_id, p.id, status="finished")
            except asyncio.CancelledError:
                self.ws.update_participant(eval_id, p.id, status="stopped",
                                           error="已中止；重新开始会从断点续跑，已完成的题不重问")
                stopped = True
                break
            except Exception as e:  # noqa: BLE001 - 一份配置失败不拖垮其它配置
                self.ws.update_participant(eval_id, p.id, status="failed", error=f"{type(e).__name__}: {e}")
                failed = True
        status = "stopped" if stopped else ("failed" if failed else "finished")
        self.ws.update_eval(eval_id, status=status, finished_at=_now())

    async def _run_participant(self, meta, participant_id: str, cfg: SystemConfig, dataset: Dataset) -> None:
        from knoweval.runner.run import run_eval

        run_dir = self.ws.run_dir(meta.id, participant_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        adapter = self.factory(cfg)
        try:
            await run_eval(dataset, adapter, run_dir, system_config=cfg.public(), repeats=meta.repeats,
                           concurrency=meta.concurrency, ask_timeout_s=cfg.ask_timeout_s,
                           text_judge=self._judge(run_dir), cache_dir=self.cache_dir)
        finally:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.shield(adapter.aclose())

    def _judge(self, run_dir: Path) -> OpenAICompatibleJudge:
        """判分全部由评测台的模型完成，没配就不能评测。"""
        if self.judge_factory is not None:
            return self.judge_factory(run_dir)
        s = self.ws.settings()
        if not s.model:
            raise ConfigError("评测要用评测台的模型判分：先在「设置」页配好接口地址、模型名和 API Key 环境变量")
        api_key = s.api_key()
        if not s.base_url or not api_key:
            raise ConfigError(f"裁判模型需要接口地址，且环境变量 {s.api_key_env} 要设上 API Key（见「设置」页）")
        return OpenAICompatibleJudge(model=s.model, client=s.client(api_key),
                                     cache_path=run_dir / "judge_cache.jsonl")

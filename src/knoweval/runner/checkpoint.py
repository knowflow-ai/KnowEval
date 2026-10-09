"""检查点：每题完成即追加一行并落盘，中断后续跑只补没跑完的题。

RAGAS 的 experiment 只在全部跑完后保存一次（experiment.py:196），中途崩溃全丢，所以这一层自己做。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path


class Checkpoint:
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()

    def load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        rows: dict[str, dict] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # 崩溃时最后一行可能只写了一半，丢掉它，那道题会被重跑
                continue
            rows[row["qid"]] = row
        return rows

    async def append(self, row: dict) -> None:
        line = json.dumps(row, ensure_ascii=False) + "\n"
        async with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())

    def rewrite(self, rows: list[dict]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        tmp.replace(self.path)

"""运行目录锁：两个进程指向同一个运行目录会把检查点写乱，第二个直接拒绝。"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path


class RunDirLockedError(RuntimeError):
    pass


class RunDirLock:
    def __init__(self, run_dir: Path):
        self.path = Path(run_dir) / ".lock"
        self._fd: int | None = None

    def __enter__(self) -> RunDirLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            os.close(fd)
            raise RunDirLockedError(f"运行目录 {self.path.parent} 正被另一个 knoweval 进程使用") from e
        self._fd = fd
        return self

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

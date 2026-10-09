""".env 文件：把被测系统、裁判模型的密钥写在文件里，启动时读进环境变量，不用每次 export。

只认 KEY=VALUE 一种写法（可带 export 前缀、引号、行尾注释）。已经设置的环境变量不覆盖：
命令行里临时 export 的值优先。读到的值不打印、不写进任何结果文件。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _value(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    # 不带引号时，空白后的 # 起是注释
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, raw = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        if _NAME.fullmatch(name):
            out[name] = _value(raw)
    return out


def load_env_file(path: Path) -> list[str]:
    """读一个 .env，返回这次新设置的变量名（已存在的不覆盖，空值不读，文件不存在返回空）。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    loaded = []
    for name, value in parse_env(text).items():
        if value and name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded

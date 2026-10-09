"""CI 模式：--fail-under 阈值检查。"""

from __future__ import annotations

import re

# 指标名 → (中文名, 是否越高越好)
METRICS = {
    "accuracy": ("准确率", True),
    "confident_wrong": ("自信错答率", False),
    "abstain_correct": ("拒答正确率", True),
    "over_abstain": ("过度拒答率", False),
    "citation_verifiable": ("引用可核对率", True),
}
_SPEC = re.compile(r"^(\w+)\s*(<=|>=|=)\s*([0-9.]+)$")


def parse_thresholds(specs: list[str]) -> list[tuple[str, str, float]]:
    """「accuracy=0.8」按指标方向理解（越高越好的是下限，越低越好的是上限），也可以显式写 >= 或 <=。"""
    out = []
    for spec in specs:
        m = _SPEC.match(spec.strip())
        if not m or m.group(1) not in METRICS:
            raise ValueError(f"无法识别的阈值 {spec!r}，可用指标：{', '.join(METRICS)}")
        name, op, value = m.group(1), m.group(2), float(m.group(3))
        if op == "=":
            op = ">=" if METRICS[name][1] else "<="
        out.append((name, op, value))
    return out


def check_thresholds(summary: dict, thresholds: list[tuple[str, str, float]]) -> list[str]:
    failures = []
    for name, op, limit in thresholds:
        value = summary[name]["value"]
        label = METRICS[name][0]
        if value is None:
            failures.append(f"{label} 没有可用数据")
        elif op == ">=" and value < limit:
            failures.append(f"{label} {value * 100:.1f}% 低于 {limit * 100:.1f}%")
        elif op == "<=" and value > limit:
            failures.append(f"{label} {value * 100:.1f}% 高于 {limit * 100:.1f}%")
    return failures

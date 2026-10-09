"""终端汇报。M0 先出终端表格与 summary.json，单文件 HTML 报告（Ant Design 5）在 M1 做。"""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

METRICS = [
    ("accuracy", "准确率", "越高越好"),
    ("confident_wrong", "自信错答率", "越低越好"),
    ("abstain_correct", "拒答正确率", "越高越好"),
    ("over_abstain", "过度拒答率", "越低越好"),
    ("citation_verifiable", "引用可核对率", "越高越好"),
    ("citation_precision", "引用精确率", "越高越好"),
    ("image_hit", "图片命中率", "越高越好"),
    ("image_misfit", "图片误配率", "越低越好"),
]


def _fmt(rate: dict | None) -> str:
    # 旧版本跑出的 summary.json 可能没有后来新增的指标
    if not rate or rate["value"] is None:
        return "—"
    lo, hi = rate["ci"]
    return f"{rate['value'] * 100:.1f}%  [{lo * 100:.1f}–{hi * 100:.1f}]  {rate['k']}/{rate['n']}"


def print_report(run_dir: str | Path, console: Console | None = None) -> None:
    run_dir = Path(run_dir)
    console = console or Console()
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    repeats = summary["repeats"]

    console.print(f"[bold]{meta['system'].get('adapter')}[/] · {meta['dataset']['ref']} · "
                  f"跑了 {len(repeats)} 遍 · 裁判 {meta.get('judge') or '未配置'}")
    table = Table(show_lines=False)
    table.add_column("指标")
    for i in range(len(repeats)):
        table.add_column(f"第 {i + 1} 遍（95% 区间）")
    for key, label, direction in METRICS:
        table.add_row(f"{label}（{direction}）", *[_fmt(s.get(key)) for s in repeats])
    table.add_row("出错", *[str(s["error_count"]) for s in repeats])
    table.add_row("待人工复核", *[str(s["needs_review"]) for s in repeats])
    reviewed = summary.get("repeats_reviewed")
    if reviewed:
        table.add_row("改判后准确率", *[_fmt(s["accuracy"]) for s in reviewed])
        table.add_row("改判后自信错答率", *[_fmt(s["confident_wrong"]) for s in reviewed])
    console.print(table)
    hr = summary.get("human_review")
    if hr:
        rate = f"{hr['agreement'] * 100:.1f}%" if hr["agreement"] is not None else "—"
        console.print(f"人工复核 {hr['reviewed']} 条，与机器判定一致 {hr['agreed']} 条，一致率 {rate}")

    attr = repeats[0].get("attribution") or {}
    if attr:
        console.print(f"错题归因（第 1 遍）：检索没找到 {attr.get('retrieval', 0)}，找到了没答对 {attr.get('generation', 0)}")
    noise = summary.get("noise", [])
    console.print(f"噪声底线：{len(noise)} 题两遍结果不同" + (f"（{', '.join(noise)}）" if noise else ""))

    by_type = Table(title="按题型（第 1 遍）")
    for col in ("题型", "题数", "准确率", "自信错答率"):
        by_type.add_column(col)
    for t, s in repeats[0]["by_qtype"].items():
        rate = s["abstain_correct"] if t == "N" else s["accuracy"]
        by_type.add_row(t + ("（拒答正确率）" if t == "N" else ""), str(s["total"]), _fmt(rate), _fmt(s["confident_wrong"]))
    console.print(by_type)

    names = {"hops": "跳数", "modality": "证据形态", "cross_page": "是否跨页", "format": "文档格式", "origin": "题目来源"}
    layout = Table(title="版面分项（第 1 遍，无答案题不计入）")
    for col in ("分组", "取值", "题数", "准确率"):
        layout.add_column(col)
    for group, buckets in repeats[0].get("groups", {}).items():
        for key, s in buckets.items():
            layout.add_row(names.get(group, group), key, str(s["total"]), _fmt(s["accuracy"]))
    console.print(layout)

    failed = [d for d in summary.get("ingest", {}).get("docs", []) if not d["ok"]]
    if failed:
        console.print("[bold red]入库失败[/]：" + "；".join(f"{d['name']}（{d['error']}）" for d in failed))


def print_compare(result: dict, console: Console | None = None) -> None:
    console = console or Console()
    table = Table(title="多系统对比（第 1 遍）")
    table.add_column("指标")
    for s in result["systems"]:
        table.add_column(s["name"])
    for key, label, direction in METRICS[:5]:
        table.add_row(f"{label}（{direction}）", *[_fmt(s[key]) for s in result["systems"]])
    console.print(table)
    for p in result["pairs"]:
        verdict = "差异显著" if p["significant"] else "差异不显著"
        console.print(f"{p['first']} 对 {p['second']}：只有前者答对 {p['only_first']} 题，只有后者答对 {p['only_second']} 题，"
                      f"McNemar p={p['p_value']:.3f}，{verdict}")
    for name, qids in result["groups"].items():
        console.print(f"{name}：{len(qids)} 题")

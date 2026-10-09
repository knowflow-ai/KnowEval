"""knoweval dataset list / info / pull。"""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

import typer

from knoweval.dataset import find_datasets, load_dataset, pull_documents

QTYPE_NAME = {"F": "单跳事实", "M": "跨文档多跳", "T": "表格数值", "C": "计算统计", "X": "比较", "D": "时间推理",
              "V": "图表图片", "L": "长文档定位", "K": "信息冲突", "N": "无答案", "I": "图文答案", "G": "全局关系"}

app = typer.Typer(help="数据集：列出、查看、按清单下载文档", no_args_is_help=True)
DEFAULT_PATHS = [Path("datasets"), Path.home() / ".cache" / "knoweval" / "datasets"]


@app.command("list")
def list_(path: list[Path] = typer.Option(None, help="数据集所在目录，可多次指定；默认 ./datasets 与 ~/.cache/knoweval/datasets")) -> None:
    """列出本地数据集。"""
    found = find_datasets(path or DEFAULT_PATHS)
    if not found:
        typer.echo("没有找到数据集")
    for d in found:
        missing = sum(not (d.root / "docs" / f.name).exists() for f in d.manifest.files)
        note = f"，缺 {missing} 份文档（用 dataset pull 下载）" if missing else ""
        typer.echo(f"{d.ref}  {len(d.questions)} 题  {len(d.manifest.files)} 份文档{note}  {d.root}")


@app.command()
def info(dataset: Path) -> None:
    """查看一个数据集：题量、题型配比、文档清单、许可。"""
    d = load_dataset(dataset, verify_files=False)
    typer.echo(f"{d.ref}  许可 {d.manifest.license}  金丝雀 {d.manifest.canary}")
    typer.echo(f"题目 {len(d.questions)} 道：")
    counts = Counter(t for q in d.questions for t in q.qtype)
    for t, n in sorted(counts.items()):
        typer.echo(f"  {t} {QTYPE_NAME.get(t, '')}：{n}")
    typer.echo(f"文档 {len(d.manifest.files)} 份：")
    for f in d.manifest.files:
        state = "已有" if (d.root / "docs" / f.name).exists() else ("可下载" if f.source_url else "缺失")
        typer.echo(f"  {f.name}  {f.format}  {state}")


@app.command()
def pull(dataset: Path) -> None:
    """按清单下载缺失的文档并校验 SHA-256。"""
    result = asyncio.run(pull_documents(dataset))
    typer.echo(f"已有 {len(result.present)} 份，新下载 {len(result.downloaded)} 份")


@app.command()
def variant(dataset: Path, kind: str = typer.Option("scanned", help="变体类型，目前只有 scanned"),
            out: Path = typer.Option(..., help="输出目录"), seed: int = typer.Option(0, help="倾斜与噪点的随机种子")) -> None:
    """生成同内容的另一种形态（数据集设计 §6），题目与页码不变。"""
    from knoweval.variants import make_variant

    try:
        path = make_variant(dataset, kind, out, seed=seed)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    typer.echo(f"已生成 {path}")

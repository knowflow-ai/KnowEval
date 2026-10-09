"""命令行：knoweval run / judge / report。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import typer

from knoweval.config import build_adapter, load_system_config
from knoweval.dataset import load_dataset
from knoweval.judge.llm_judge import OpenAICompatibleJudge
from knoweval.report import print_report

app = typer.Typer(help="KnowEval：中文企业文档 RAG 端到端评测", no_args_is_help=True)


def _register_subcommands() -> None:
    from knoweval.dataset_cli import app as dataset_app
    app.add_typer(dataset_app, name="dataset")

# RAGAS 默认开启匿名遥测（_analytics.py），KnowEval 默认替用户关掉；用户显式设置的值不覆盖
os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")

@app.callback()
def _load_dotenv() -> None:
    """KnowEval：中文企业文档 RAG 评测。启动时读当前目录的 .env（已设置的环境变量不覆盖）。"""
    from knoweval.envfile import load_env_file

    _DOTENV_LOADED.extend(load_env_file(Path.cwd() / ".env"))


_DOTENV_LOADED: list[str] = []   # 本次从 .env 读进来的变量名，评测台启动时告诉用户


JUDGE_MODEL = typer.Option(None, help="判分模型名；不填用评测台「设置」页的（当前目录的 settings.json）")
JUDGE_BASE_URL = typer.Option(None, help="判分模型的 OpenAI 兼容地址；不填用「设置」页的")


def _text_judge(model: str | None, base_url: str | None, run_dir: Path):
    """判分全部由模型完成，所以必须配：默认用评测台「设置」页那个模型，Key 从 .env 读。"""
    from knoweval.llm_config import LlmConfigError, resolve_llm

    try:
        settings, api_key = resolve_llm(Path.cwd(), model, base_url)
    except LlmConfigError as e:
        raise typer.BadParameter(str(e)) from e
    return OpenAICompatibleJudge(model=settings.model, client=settings.client(api_key),
                                 cache_path=run_dir / "judge_cache.jsonl")


@app.command()
def run(system: Path = typer.Option(..., help="被测系统配置 YAML"),
        dataset: Path = typer.Option(..., help="数据集目录"),
        out: Path = typer.Option(..., help="运行目录；已存在时续跑"),
        repeat: int = typer.Option(2, min=1, help="跑几遍，至少两遍才能量出噪声"),
        cache_dir: Path = typer.Option(Path.home() / ".cache" / "knoweval", envvar="KNOWEVAL_CACHE_DIR",
                                       help="入库缓存目录：同一份数据集、同一套入库配置跨运行只入库一次"),
        subset: str | None = typer.Option(None, help="只跑子集；smoke＝每个题型 2 题，用于 CI 冒烟"),
        fail_under: list[str] = typer.Option([], help="阈值，如 accuracy=0.8、confident_wrong<=0.2；任何一遍不达标退出码为 1"),
        judge_model: str | None = JUDGE_MODEL, judge_base_url: str | None = JUDGE_BASE_URL) -> None:
    """入库、逐题提问、判分，写出运行目录。"""
    from knoweval.ci import check_thresholds, parse_thresholds
    from knoweval.runner.run import run_eval

    thresholds = parse_thresholds(fail_under)
    cfg = load_system_config(system)
    adapter = build_adapter(cfg)
    out.mkdir(parents=True, exist_ok=True)
    ds = load_dataset(dataset)
    if subset:
        ds = ds.subset(subset)

    async def _go():
        try:
            return await run_eval(ds, adapter, out, system_config=cfg.public(), repeats=repeat, subset=subset,
                           concurrency=cfg.concurrency, ask_timeout_s=cfg.ask_timeout_s,
                           text_judge=_text_judge(judge_model, judge_base_url, out), cache_dir=cache_dir)
        finally:
            await adapter.aclose()

    result = asyncio.run(_go())
    print_report(out)
    failures = [f"第 {i} 遍：{msg}" for i, s in enumerate(result.summaries, start=1)
                for msg in check_thresholds(s.model_dump(mode="json"), thresholds)]
    if failures:
        typer.echo("未达阈值：\n" + "\n".join(failures))
        raise typer.Exit(code=1)


@app.command()
def judge(run_dir: Path, dataset: Path = typer.Option(..., help="运行时用的数据集目录"),
          judge_model: str | None = JUDGE_MODEL, judge_base_url: str | None = JUDGE_BASE_URL) -> None:
    """用当前判分规则重新判分，不重新调用被测系统。"""
    from knoweval.runner.rejudge import rejudge

    asyncio.run(rejudge(run_dir, load_dataset(dataset), _text_judge(judge_model, judge_base_url, run_dir)))
    print_report(run_dir)


@app.command()
def review(run_dir: Path, qid: str = typer.Option(..., help="题号"),
           repeat: int = typer.Option(1, help="第几遍"),
           verdict: str = typer.Option(..., help="人工判定：CORRECT / ABSTAIN / WRONG / ERROR"),
           reason: str = typer.Option("", help="改判理由；与机器判定不一致时必填"),
           by: str = typer.Option(..., help="复核人"),
           dataset: Path = typer.Option(..., help="运行时用的数据集目录")) -> None:
    """记一条人工判定：与机器一致算确认，不一致算改判。汇总同时给出改判前、改判后与一致率。"""
    from knoweval.review import add_review
    from knoweval.runner.run import RunRecord
    from knoweval.runner.checkpoint import Checkpoint
    from knoweval.runner.summary import write_summary

    r = add_review(run_dir, qid=qid, repeat=repeat, verdict=verdict.upper(), reason=reason, by=by)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    ds = load_dataset(dataset).for_run(meta)
    repeats = []
    for i in range(1, meta["repeats"] + 1):
        rows = Checkpoint(run_dir / f"checkpoint.r{i}.jsonl").load()
        repeats.append([RunRecord.model_validate(rows[q.id]) for q in ds.questions])
    write_summary(run_dir, ds, repeats)
    typer.echo(f"{'确认' if r.agrees else '改判'}：{qid} 第 {repeat} 遍 {r.machine_verdict.value} → {r.verdict.value}")
    print_report(run_dir)


@app.command()
def compare(run_dirs: list[Path], dataset: Path = typer.Option(..., help="这些运行用的数据集目录"),
            out: Path | None = typer.Option(None, help="把对比结果写成 JSON"),
            html: Path | None = typer.Option(None, help="生成单文件 HTML 对比报告")) -> None:
    """多系统对比：四个指标、按题型、两两配对显著性、逐题对照分组。"""
    from knoweval.compare import compare_runs
    from knoweval.report import print_compare

    ds = load_dataset(dataset)
    result = compare_runs(run_dirs, ds)
    if out:
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_compare(result)
    if html:
        from knoweval.report_html import build_compare_data, write_html
        write_html(build_compare_data(run_dirs, ds), html)
        typer.echo(f"HTML 对比报告：{html}")


@app.command()
def diff(before: Path, after: Path, dataset: Path = typer.Option(..., help="两次运行用的数据集目录")) -> None:
    """两次运行对比：哪些题由对变错、由错变对，变化是否在噪声范围内。"""
    from knoweval.compare import diff_runs

    d = diff_runs(before, after, load_dataset(dataset))
    typer.echo(f"由对变错 {len(d['to_wrong'])} 题：{', '.join(d['to_wrong']) or '无'}")
    typer.echo(f"由错变对 {len(d['to_correct'])} 题：{', '.join(d['to_correct']) or '无'}")
    if d["within_noise"]:
        typer.echo(f"变化在噪声范围内（噪声底线 {d['noise_floor']} 题），不必为它改代码")
    else:
        typer.echo(f"变化超出噪声底线（{d['noise_floor']} 题）")


@app.command()
def ui(workspace: Path = typer.Argument(Path("."), help="工作区目录（systems.json、evals/、datasets/ 所在处）"),
       port: int = typer.Option(8780, envvar="KNOWEVAL_PORT", help="端口"),
       host: str = typer.Option("127.0.0.1", envvar="KNOWEVAL_HOST",
                                help="监听地址。评测台没有登录，默认只给本机用；Docker 容器里用 0.0.0.0")) -> None:
    """打开评测台：接入系统、建评测、看进度和对比结果。默认只监听 127.0.0.1。"""
    from knoweval.envfile import load_env_file
    from knoweval.ui.server import make_server

    # 工作区不是当前目录时，工作区里的 .env 也读（当前目录的已在启动时读过）
    if workspace.resolve() != Path.cwd().resolve():
        _DOTENV_LOADED.extend(load_env_file(workspace / ".env"))
    srv = make_server(workspace, port=port, host=host)
    if _DOTENV_LOADED:
        typer.echo(f"已从 .env 读取 {len(_DOTENV_LOADED)} 个变量：{', '.join(_DOTENV_LOADED)}")
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    typer.echo(f"评测台已启动：http://{shown}:{srv.server_address[1]}  （Ctrl+C 退出）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.server_close()


@app.command()
def report(run_dir: Path,
           html: Path | None = typer.Option(None, help="生成单文件 HTML 报告到这个路径（双击即可打开）"),
           dataset: Path | None = typer.Option(None, help="生成 HTML 时需要：运行时用的数据集目录")) -> None:
    """打印一次运行的汇总；加 --html 生成网页报告（总览、逐题明细、运行信息）。"""
    print_report(run_dir)
    if html:
        if not dataset:
            raise typer.BadParameter("生成 HTML 报告需要 --dataset")
        from knoweval.report_html import build_report_data, write_html
        write_html(build_report_data(run_dir, load_dataset(dataset)), html)
        typer.echo(f"HTML 报告：{html}")


_register_subcommands()

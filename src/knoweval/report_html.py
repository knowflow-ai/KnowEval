"""单文件 HTML 报告：组装数据，嵌进 web/ 打包出的模板，双击即可打开、可以直接发给别人。"""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from knoweval.compare import compare_runs
from knoweval.dataset import Dataset
from knoweval.review import load_reviews
from knoweval.runner.checkpoint import Checkpoint

PLACEHOLDER = "__KNOWEVAL_DATA__"
# 只替换数据标签里的占位符：打包后的 JS 里也可能出现同样的字符串，替换第一个出现的会把代码改坏
DATA_TAG = f'<script id="knoweval-data" type="application/json">{PLACEHOLDER}</script>'


def _questions(dataset: Dataset) -> list[dict]:
    return [q.model_dump(mode="json") for q in dataset.questions]


def build_report_data(run_dir: str | Path, dataset: Dataset) -> dict:
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    dataset = dataset.for_run(meta)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    records = {}
    for r in range(1, meta["repeats"] + 1):
        rows = Checkpoint(run_dir / f"checkpoint.r{r}.jsonl").load()
        records[str(r)] = {qid: {"answer": row["answer"], "judgement": row["judgement"]} for qid, row in rows.items()}
    return {
        "kind": "report", "name": run_dir.name, "meta": meta, "summary": summary,
        "questions": _questions(dataset), "records": records,
        "reviews": [r.model_dump(mode="json") for r in load_reviews(run_dir)],
        "docs": [f.model_dump(mode="json") for f in dataset.manifest.files],
    }


def build_compare_data(run_dirs: list[Path], dataset: Dataset) -> dict:
    first = json.loads((Path(run_dirs[0]) / "run.json").read_text(encoding="utf-8"))
    return {"kind": "compare", "compare": compare_runs(run_dirs, dataset),
            "questions": _questions(dataset.for_run(first)),
            "metas": [json.loads((Path(d) / "run.json").read_text(encoding="utf-8")) for d in run_dirs],
            # 每项第 1 遍的逐题记录（和逐题对照的颜色块同一遍），顺序同 compare.systems：展开一道题能看各项的回答与判定
            "records": [{qid: {"answer": row["answer"], "judgement": row["judgement"]}
                         for qid, row in Checkpoint(Path(d) / "checkpoint.r1.jsonl").load().items()} for d in run_dirs]}


def _template() -> str:
    return files("knoweval").joinpath("web/report.html").read_text(encoding="utf-8")


def render_html(data: dict, template: str | None = None) -> str:
    template = template or _template()
    # 回答原文可能含 </script>，嵌进 <script> 之前把 </ 转义，JSON 解析结果不变
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    if template.count(DATA_TAG) != 1:
        raise ValueError("报告模板里找不到唯一的数据标签，请重新构建 web/")
    return template.replace(DATA_TAG, DATA_TAG.replace(PLACEHOLDER, payload))


def write_html(data: dict, out: Path) -> Path:
    out.write_text(render_html(data), encoding="utf-8")
    return out

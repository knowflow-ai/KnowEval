"""对已有运行重新判分：只重判，不重新调用被测系统。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from knoweval.dataset import Dataset
from knoweval.judge.judge import TextJudge, score_three_tier
from knoweval.runner.checkpoint import Checkpoint
from knoweval.runner.run import RunIncompleteError, RunRecord, write_experiment
from knoweval.runner.summary import write_summary


async def rejudge(run_dir: str | Path, dataset: Dataset, text_judge: TextJudge | None = None) -> None:
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    dataset = dataset.for_run(meta)
    if not dataset.matches_hash(meta["dataset"]["content_hash"]):
        raise RunIncompleteError("数据集内容与这次运行时不一致，不能重判")
    questions = {q.id: q for q in dataset.questions}
    # 先把所有遍都重判完，全部成功后才写文件：中途失败不留下半新半旧的结果
    repeats: list[list[RunRecord]] = []
    for r in range(1, meta["repeats"] + 1):
        saved = Checkpoint(run_dir / f"checkpoint.r{r}.jsonl").load()
        missing = [q.id for q in dataset.questions if q.id not in saved]
        if missing:
            raise RunIncompleteError(f"第 {r} 遍缺少题目，先用 run 续跑：{missing}")
        records = []
        for q in dataset.questions:
            old = RunRecord.model_validate(saved[q.id])
            judgement = await score_three_tier(questions[q.id], old.answer, text_judge)
            records.append(old.model_copy(update={"judgement": judgement}))
        repeats.append(records)

    for r, records in enumerate(repeats, start=1):
        rows = [rec.model_dump(mode="json") for rec in records]
        Checkpoint(run_dir / f"checkpoint.r{r}.jsonl").rewrite(rows)
        write_experiment(run_dir, r, rows)

    write_summary(run_dir, dataset, repeats)
    meta.update(judge=getattr(text_judge, "version", None), rejudged_at=datetime.now(timezone.utc).isoformat())
    (run_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

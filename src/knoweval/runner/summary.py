"""写 summary.json：机器判定的汇总、人工改判后的汇总、一致率。运行、重判、改判三处共用。"""

from __future__ import annotations

import json
from pathlib import Path

from knoweval.dataset import Dataset
from knoweval.review import load_reviews, review_stats


def write_summary(run_dir: Path, dataset: Dataset, repeats: list, ingest: dict | None = None) -> dict:
    from knoweval.runner.run import summarize

    summaries, noise = summarize(dataset, repeats)
    path = run_dir / "summary.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = {"repeats": [s.model_dump(mode="json") for s in summaries], "noise": noise,
            "ingest": ingest if ingest is not None else old.get("ingest")}

    reviews = load_reviews(run_dir)
    if reviews:
        by_key = {(r.qid, r.repeat): r for r in reviews}
        current = {(rec.qid, rec.repeat): rec.judgement.verdict for records in repeats for rec in records}
        adjusted = [
            [rec.model_copy(update={"judgement": rec.judgement.model_copy(
                update={"verdict": by_key[(rec.qid, rec.repeat)].verdict, "needs_review": False})})
             if (rec.qid, rec.repeat) in by_key else rec for rec in records]
            for records in repeats
        ]
        reviewed, _ = summarize(dataset, adjusted)
        data["repeats_reviewed"] = [s.model_dump(mode="json") for s in reviewed]
        data["human_review"] = review_stats(reviews, current)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data

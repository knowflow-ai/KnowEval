"""人工改判（规范第 5 节）。人工每看一题记一条判定：与机器一致算确认，不一致算改判。

判分校准看的就是机器判定与人工判定的一致率。
改判只记录在运行目录的 reviews.jsonl，不改动 checkpoint 里的机器判定，汇总时两组数同时给出。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from knoweval.runner.checkpoint import Checkpoint
from knoweval.schema import Verdict


class ReviewError(ValueError):
    pass


class Review(BaseModel):
    qid: str
    repeat: int
    verdict: Verdict
    machine_verdict: Verdict
    reason: str = ""
    by: str
    at: str

    @property
    def agrees(self) -> bool:
        return self.verdict is self.machine_verdict


def _path(run_dir: Path) -> Path:
    return Path(run_dir) / "reviews.jsonl"


def load_reviews(run_dir: str | Path) -> list[Review]:
    """同一题同一遍以最后一条为准。"""
    path = _path(Path(run_dir))
    if not path.exists():
        return []
    latest: dict[tuple[str, int], Review] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = Review.model_validate_json(line)
            latest[(r.qid, r.repeat)] = r
    return list(latest.values())


def add_review(run_dir: str | Path, *, qid: str, repeat: int, verdict: str, reason: str, by: str) -> Review:
    run_dir = Path(run_dir)
    records = Checkpoint(run_dir / f"checkpoint.r{repeat}.jsonl").load()
    if not records:
        raise ReviewError(f"运行目录里没有第 {repeat} 遍的结果")
    if qid not in records:
        raise ReviewError(f"第 {repeat} 遍里没有题目 {qid}")
    machine = Verdict(records[qid]["judgement"]["verdict"])
    human = Verdict(verdict)
    if human is not machine and not reason.strip():
        raise ReviewError("改判必须写理由")
    review = Review(qid=qid, repeat=repeat, verdict=human, machine_verdict=machine, reason=reason.strip(), by=by,
                    at=datetime.now(timezone.utc).isoformat())
    with _path(run_dir).open("a", encoding="utf-8") as f:
        f.write(review.model_dump_json() + "\n")
    return review


def review_stats(reviews: list[Review], current: dict[tuple[str, int], Verdict] | None = None) -> dict:
    """一致率。传入当前机器判定时按它比（重判之后机器判定可能变了），否则按记录时的机器判定比。"""
    def agrees(r: Review) -> bool:
        machine = current.get((r.qid, r.repeat), r.machine_verdict) if current else r.machine_verdict
        return r.verdict is machine

    agreed = sum(agrees(r) for r in reviews)
    return {"reviewed": len(reviews), "agreed": agreed,
            "agreement": (agreed / len(reviews)) if reviews else None}

"""从运行目录的文件推进度：建库上传（ingest.json）→ 提问（answers.r*）→ 判分（checkpoint.r*）。

进度不另记状态，全部从 runner 已经在写的文件里读出来——断点续跑、进程重启后进度依然是对的。
"""

from __future__ import annotations

import json
from pathlib import Path

from knoweval.runner.checkpoint import Checkpoint


def _count(path: Path) -> int:
    return len(Checkpoint(path).load()) if path.exists() else 0


def participant_progress(run_dir: Path, total: int, repeats: int) -> dict:
    """total 是题数；返回给前端画进度条的全部数字。"""
    ingest_path = run_dir / "ingest.json"
    ingest = None
    if ingest_path.exists():
        report = json.loads(ingest_path.read_text(encoding="utf-8")).get("report", {})
        docs = report.get("docs", [])
        ingest = {
            "manual": report.get("manual", False),
            "existing": report.get("existing", False),
            "total": len(docs),
            "ok": sum(1 for d in docs if d.get("ok")),
            "failed": [{"name": d.get("name"), "error": d.get("error")} for d in docs if not d.get("ok")],
        }
    rounds = []
    for r in range(1, repeats + 1):
        rounds.append({"repeat": r,
                       "asked": _count(run_dir / f"answers.r{r}.jsonl"),
                       "judged": _count(run_dir / f"checkpoint.r{r}.jsonl")})
    phase = "ingest"
    if ingest is not None:
        phase = "done" if all(x["judged"] >= total for x in rounds) else "ask"
    summary = None
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        first = (data.get("repeats") or [{}])[0]
        summary = {"accuracy": (first.get("accuracy") or {}).get("value"),
                   "confident_wrong": (first.get("confident_wrong") or {}).get("value"),
                   "noise": len(data.get("noise") or [])}
    return {"phase": phase, "ingest": ingest, "rounds": rounds, "total": total, "summary": summary}

"""多系统对比与两次运行对比。

显著性用配对的 McNemar 精确检验：只看两个系统判定不同的那些题，比两个置信区间是否重叠更灵敏也更准确。
"""

from __future__ import annotations

import json
from itertools import combinations
from math import comb
from pathlib import Path

from knoweval.dataset import Dataset
from knoweval.runner.checkpoint import Checkpoint
from knoweval.schema import Verdict

ALPHA = 0.05


class CompareError(ValueError):
    pass


def mcnemar_p(b: int, c: int) -> float:
    """双侧精确 McNemar：b、c 是两个系统判定不一致的两类题数。"""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def _load(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    rows = Checkpoint(run_dir / "checkpoint.r1.jsonl").load()
    return {"name": run_dir.name, "meta": meta, "summary": summary,
            "verdicts": {qid: Verdict(r["judgement"]["verdict"]) for qid, r in rows.items()}}


def _check_comparable(runs: list[dict], dataset: Dataset | None = None) -> None:
    hashes = {r["meta"]["dataset"]["content_hash"] for r in runs}
    if len(hashes) > 1 and not (dataset and all(dataset.matches_hash(h) for h in hashes)):
        raise CompareError("这几次运行用的不是同一份数据集（题目或文档不同），不能对比")
    judges = {r["meta"].get("judge") for r in runs}
    if len(judges) > 1:
        raise CompareError(f"这几次运行的裁判模型不一致：{sorted(map(str, judges))}，先用同一个裁判重判")


def _good(verdict: Verdict, no_answer: bool) -> bool:
    """好结果：有答案的题答对，无答案的题说不知道。"""
    return verdict is (Verdict.ABSTAIN if no_answer else Verdict.CORRECT)


def compare_runs(run_dirs: list[Path], dataset: Dataset) -> dict:
    runs = [_load(d) for d in run_dirs]
    if len(runs) < 2:
        raise CompareError("至少需要两次运行")
    dataset = dataset.for_run(runs[0]["meta"])
    _check_comparable(runs, dataset)
    if not dataset.matches_hash(runs[0]["meta"]["dataset"]["content_hash"]):
        raise CompareError("传入的数据集与这些运行用的数据集不一致")
    qids = [q.id for q in dataset.questions]
    no_answer = {q.id for q in dataset.questions if q.is_no_answer}
    systems = [{"name": r["name"], **{k: r["summary"]["repeats"][0][k] for k in
               ("accuracy", "confident_wrong", "abstain_correct", "over_abstain", "citation_verifiable")},
                # 无答案题（N）看拒答正确率，其余题型看准确率
                "by_qtype": {t: s["abstain_correct" if t == "N" else "accuracy"]
                             for t, s in r["summary"]["repeats"][0]["by_qtype"].items()},
                "noise": len(r["summary"].get("noise", []))} for r in runs]
    correct = {r["name"]: {q for q, v in r["verdicts"].items() if _good(v, q in no_answer)} for r in runs}
    pairs = []
    for a, b in combinations(runs, 2):
        only_a = len(correct[a["name"]] - correct[b["name"]])
        only_b = len(correct[b["name"]] - correct[a["name"]])
        p = mcnemar_p(only_a, only_b)
        pairs.append({"first": a["name"], "second": b["name"], "only_first": only_a, "only_second": only_b,
                      "p_value": p, "significant": p < ALPHA})
    groups = {f"only_{r['name']}": sorted(q for q in correct[r["name"]]
                                          if all(q not in correct[o["name"]] for o in runs if o is not r))
              for r in runs}
    groups["all_correct"] = sorted(set.intersection(*correct.values()))
    groups["none_correct"] = sorted(q for q in qids if all(q not in c for c in correct.values()))
    return {"systems": systems, "pairs": pairs, "groups": groups,
            "matrix": {q: [r["verdicts"][q].value for r in runs] for q in qids}}


def diff_runs(before: Path, after: Path, dataset: Dataset) -> dict:
    a, b = _load(before), _load(after)
    dataset = dataset.for_run(a["meta"])
    _check_comparable([a, b], dataset)
    no_answer = {q.id for q in dataset.questions if q.is_no_answer}
    good_a = {q for q, v in a["verdicts"].items() if _good(v, q in no_answer)}
    good_b = {q for q, v in b["verdicts"].items() if _good(v, q in no_answer)}
    to_wrong = sorted(good_a - good_b)
    to_correct = sorted(good_b - good_a)
    noise = max(len(a["summary"].get("noise", [])), len(b["summary"].get("noise", [])))
    changed = len(to_wrong) + len(to_correct)
    return {"to_wrong": to_wrong, "to_correct": to_correct, "noise_floor": noise,
            "within_noise": changed == 0 or (noise > 0 and changed <= noise)}

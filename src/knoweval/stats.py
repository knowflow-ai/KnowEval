"""统计（规范第 6、8 节）：四个主要指标带 Wilson 区间，不合成总分；出错的题留在分母里。"""

from __future__ import annotations

import math
from collections import defaultdict

from pydantic import BaseModel, Field

from knoweval.judge.judge import Judgement
from knoweval.schema import Question, Verdict

Z95 = 1.96


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return center - half, center + half


class Rate(BaseModel):
    k: int
    n: int
    value: float | None
    ci: tuple[float, float] | None

    @classmethod
    def of(cls, k: int, n: int) -> Rate:
        return cls(k=k, n=n, value=(k / n if n else None), ci=wilson(k, n))


class Summary(BaseModel):
    accuracy: Rate
    confident_wrong: Rate
    abstain_correct: Rate
    over_abstain: Rate
    citation_verifiable: Rate
    citation_precision: Rate
    # 图文答案（I 类题）：期望的图全部带出的比例；带出的图里与题目无关的比例
    image_hit: Rate
    image_misfit: Rate
    error_count: int
    needs_review: int
    total: int
    # 错题归因计数：retrieval / generation（有答案题里答错或说不知道、且系统返回了上下文的）
    attribution: dict[str, int] = Field(default_factory=dict)
    by_qtype: dict[str, Summary] = Field(default_factory=dict)
    # 版面分项（规范 §4.1）：hops / modality / cross_page / format
    groups: dict[str, dict[str, Summary]] = Field(default_factory=dict)


def _summarize(records: list[tuple[Question, Judgement]]) -> Summary:
    answerable = [j for q, j in records if not q.is_no_answer]
    no_answer = [j for q, j in records if q.is_no_answer]
    count = lambda js, v: sum(j.verdict is v for j in js)  # noqa: E731
    correct, wrong = count(answerable, Verdict.CORRECT), count(answerable, Verdict.WRONG)
    cited = [j.citation for j in answerable if j.verdict is Verdict.CORRECT and j.citation is not None]
    imgs = [j for j in answerable if j.images is not None]
    return Summary(
        accuracy=Rate.of(correct, len(answerable)),
        confident_wrong=Rate.of(wrong + count(no_answer, Verdict.WRONG), correct + wrong + count(no_answer, Verdict.WRONG)),
        abstain_correct=Rate.of(count(no_answer, Verdict.ABSTAIN), len(no_answer)),
        over_abstain=Rate.of(count(answerable, Verdict.ABSTAIN), len(answerable)),
        citation_verifiable=Rate.of(sum(c.verifiable for c in cited), len(cited)),
        citation_precision=Rate.of(sum(c.matched for c in cited), sum(c.total for c in cited)),
        image_hit=Rate.of(sum(j.images.hit for j in imgs), len(imgs)),
        image_misfit=Rate.of(sum(j.images.unrelated for j in imgs), sum(j.images.returned for j in imgs)),
        error_count=sum(j.verdict is Verdict.ERROR for _, j in records),
        needs_review=sum(j.needs_review for _, j in records),
        total=len(records),
        attribution={k: sum(j.attribution == k for j in answerable) for k in ("retrieval", "generation")
                     if any(j.attribution == k for j in answerable)},
    )


def _hops_key(q: Question) -> str:
    return "4+" if q.hops >= 4 else str(q.hops)


def _layout_keys(q: Question, doc_formats: dict[str, str]) -> dict[str, set[str]]:
    """一道题落在哪些版面分组里。无答案题没有证据，不进这些分组。"""
    if q.is_no_answer:
        return {"hops": set(), "modality": set(), "cross_page": set(), "format": set()}
    return {
        "hops": {_hops_key(q)},
        "modality": {ev.modality for ev in q.evidence},
        "cross_page": {"跨页" if any(ev.cross_page for ev in q.evidence) else "不跨页"},
        "format": {doc_formats[ev.doc] for ev in q.evidence if ev.doc in doc_formats},
    }


def _group(records: list[tuple[Question, Judgement]], key_fn) -> dict[str, Summary]:
    buckets: dict[str, list[tuple[Question, Judgement]]] = defaultdict(list)
    for q, j in records:
        for k in key_fn(q):
            buckets[k].append((q, j))
    return {k: _summarize(rs) for k, rs in sorted(buckets.items())}


def compute_summary(records: list[tuple[Question, Judgement]],
                    doc_formats: dict[str, str] | None = None) -> Summary:
    summary = _summarize(records)
    doc_formats = doc_formats or {}
    summary.by_qtype = _group(records, lambda q: q.qtype)
    summary.groups = {
        name: _group(records, lambda q, n=name: _layout_keys(q, doc_formats)[n])
        for name in ("hops", "modality", "cross_page", "format")
    }
    if any(q.origin for q, _ in records):
        summary.groups["origin"] = _group(records, lambda q: {_ORIGIN_NAME[q.origin]} if q.origin else set())
    return summary


_ORIGIN_NAME = {"imported": "导入", "generated": "模型出题", "manual": "手动"}


def noise_floor(runs: list[dict[str, Verdict]]) -> list[str]:
    """各遍之间判定不一致的题号。"""
    if len(runs) < 2:
        return []
    ids = set(runs[0]).intersection(*runs[1:])
    return sorted(qid for qid in ids if len({r[qid] for r in runs}) > 1)

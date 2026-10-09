"""引用核对（规范第 6 节「引用核对」）：系统给的引用，文档对得上标准证据、文本覆盖证据引文的大部分，才算「对得上」。"""

from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher
from pathlib import PurePath

from pydantic import BaseModel, Field

from knoweval.dataset import normalize_for_quote
from knoweval.schema import Citation, Evidence

COVERAGE_THRESHOLD = 0.6
# 只累计长度不少于 4 的连续匹配，避免长分块里零散的单字把覆盖率凑高
_MIN_BLOCK = 4


def doc_key(name: str) -> str:
    return unicodedata.normalize("NFKC", PurePath(name).stem).strip().lower()


def quote_coverage(quote: str, text: str) -> float:
    """证据引文有多大比例出现在引用文本里（0–1）。"""
    q, t = normalize_for_quote(quote), normalize_for_quote(text)
    if not q:
        return 0.0
    if q in t:
        return 1.0
    blocks = SequenceMatcher(None, q, t, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks if b.size >= _MIN_BLOCK) / len(q)


class CitationMatch(BaseModel):
    citation: int
    evidence: int
    coverage: float


class CitationCheck(BaseModel):
    verifiable: bool
    matched: int
    total: int
    matches: list[CitationMatch] = Field(default_factory=list)


def evidence_retrieved(contexts: list[Citation], evidence: list[Evidence],
                       threshold: float = COVERAGE_THRESHOLD) -> bool:
    """每条标准证据是否都出现在检索上下文里（文档对得上、引文覆盖率达到阈值）。"""
    return all(
        any(doc_key(c.doc) == doc_key(ev.doc) and quote_coverage(ev.quote, c.text) >= threshold for c in contexts)
        for ev in evidence
    )


def check_citations(citations: list[Citation], evidence: list[Evidence],
                    threshold: float = COVERAGE_THRESHOLD) -> CitationCheck:
    matches: list[CitationMatch] = []
    for ci, cite in enumerate(citations):
        best: CitationMatch | None = None
        for ei, ev in enumerate(evidence):
            if doc_key(cite.doc) != doc_key(ev.doc):
                continue
            cov = quote_coverage(ev.quote, cite.text)
            if cov >= threshold and (best is None or cov > best.coverage):
                best = CitationMatch(citation=ci, evidence=ei, coverage=round(cov, 3))
        if best is not None:
            matches.append(best)
    return CitationCheck(verifiable=bool(matches), matched=len(matches), total=len(citations), matches=matches)

"""题目、回答、判定的数据结构。字段口径见 docs/spec/eval-spec-v0.1.md 第 4、5 节。"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

QTYPES = {"F", "M", "T", "C", "X", "D", "V", "L", "K", "N", "I", "G"}
AnswerType = Literal["text", "number", "date", "list", "bool"]
# 证据形态（规范 §4.1）：答案在正文、表格、统计图、示意图、图标还是公式里
Modality = Literal["text", "table", "chart", "image", "icon", "formula"]
HopKind = Literal["chain", "intersection", "comparison"]
# 主考环节、干扰标签、无答案题子类、档位（数据集设计 §4、§5、§7、§8）
Stage = Literal["parse", "chunk", "retrieve", "reason", "honesty", "cite"]
Trap = Literal["similar_doc", "version", "same_name", "near_number", "paraphrase"]
NKind = Literal["unrelated", "near_miss", "false_premise"]


class Evidence(BaseModel):
    doc: str
    page: int | None = None
    # 跨页证据（跨页表格、跨页图表）的结束页；不跨页为空
    page_end: int | None = None
    modality: Modality = "text"
    quote: str
    bbox: list[float] | None = None

    @model_validator(mode="after")
    def _check(self) -> Evidence:
        if self.page_end is not None and (self.page is None or self.page_end < self.page):
            raise ValueError("page_end 不能早于 page")
        return self

    @property
    def cross_page(self) -> bool:
        return self.page_end is not None and self.page is not None and self.page_end > self.page


class ImageRef(BaseModel):
    """一张图：期望的图（题目里）或系统带出的图（回答里）。只存哈希与位置，不存图片本身。"""

    doc: str | None = None
    page: int | None = None
    bbox: list[float] | None = None
    phash: str | None = None
    caption: str | None = None
    source: str | None = None


class Question(BaseModel):
    id: str
    domain: str
    qtype: list[str]
    question: str
    answer: str
    # 以下几项只是交给裁判模型的提示（见 judge.gold_text），判分不按规则比对，都可以不填
    answer_type: AnswerType = "text"
    answer_aliases: list[str] = Field(default_factory=list)
    # 答成这些算错（如冲突题里的旧值）
    answer_distractors: list[str] = Field(default_factory=list)
    unit: str | None = None
    tolerance: float | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    hops: int = 1
    hop_kind: HopKind | None = None
    # I 类题期望回答带出的图（规范第 6 节「图文答案」）
    answer_images: list[ImageRef] = Field(default_factory=list)
    split: Literal["public", "holdout"] = "public"
    stage: Stage | None = None
    traps: list[Trap] = Field(default_factory=list)
    n_kind: NKind | None = None
    tier: Literal["core", "full"] | None = None
    # 题目来源：评测台出的测试集里，导入的、模型出的、手动加的分开统计（公开数据集不填）
    origin: Literal["generated", "imported", "manual"] | None = None

    @model_validator(mode="after")
    def _check(self) -> Question:
        unknown = set(self.qtype) - QTYPES
        if unknown or not self.qtype:
            raise ValueError(f"未知题型 {sorted(unknown)}")
        if self.is_no_answer and (self.answer or self.evidence):
            raise ValueError("无答案题的 answer 必须为空串、evidence 必须为空")
        return self

    @property
    def is_no_answer(self) -> bool:
        return "N" in self.qtype


class Citation(BaseModel):
    doc: str
    page: int | None = None
    text: str = ""


class Answer(BaseModel):
    """被测系统的一次回答。text 与 error 二选一：出错是一种结果，不是缺失。"""

    text: str | None = None
    # 系统返回的原文（含 [ID:n] 等引用标记）；判分用清洗后的 text，原文留作排查
    raw_text: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    contexts: list[Citation] | None = None
    images: list[ImageRef] = Field(default_factory=list)
    latency_ms: int | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Answer:
        if self.error is not None and self.text is not None:
            raise ValueError("text 与 error 只能有一个")
        if self.error is None and self.text is None:
            raise ValueError("text 与 error 必须有一个")
        return self

    @property
    def failed(self) -> bool:
        return self.error is not None


class Verdict(str, Enum):
    CORRECT = "CORRECT"
    ABSTAIN = "ABSTAIN"
    WRONG = "WRONG"
    ERROR = "ERROR"

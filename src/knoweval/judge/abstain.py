"""拒答识别：回答是否在表达「找不到、无法回答」。

只认具体说法：「没有相关」这类宽泛片段会把「该条款没有相关例外」误当拒答。
判分交给裁判模型；这里只给开跑前的试问检查粗看「大多答不知道」（文档多半没导入）。
"""

from __future__ import annotations

from pydantic import BaseModel

_PHRASES_ZH = (
    "未找到", "没有找到", "找不到", "无法回答", "无法确定", "无法得知", "无法判断", "无从得知",
    "没有提及", "未提及", "没有提到", "未提到", "不知道", "未能找到", "并未说明",
    "没有相关信息", "没有相关内容", "没有相关资料", "没有相关数据", "没有相关记载",
    "暂无相关", "未提供相关", "没有提供相关", "未包含相关", "不包含相关", "未涉及", "没有涉及",
)
_PHRASES_EN = (
    "not found", "no relevant content", "does not contain", "don't know", "do not know",
    "cannot answer", "no information",
)


class AbstainResult(BaseModel):
    abstained: bool
    phrase: str | None = None


def detect_abstain(text: str) -> AbstainResult:
    lowered = text.lower()
    for phrase in _PHRASES_ZH:
        if phrase in text:
            return AbstainResult(abstained=True, phrase=phrase)
    for phrase in _PHRASES_EN:
        if phrase in lowered:
            return AbstainResult(abstained=True, phrase=phrase)
    return AbstainResult(abstained=False)

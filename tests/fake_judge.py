"""测试用的假裁判：判分全部交给裁判模型以后，跑评测的测试都要有一个裁判。

按最朴素的办法给三档：回答里有标准答案（或也算对的说法）就答对；说找不到就拒答；其余答错。
标准答案取 gold_text() 的第一段（答案加单位），比较时去掉单位和空白。
"""

from __future__ import annotations

import re

_REFUSE = re.compile(r"未找到|没有找到|找不到|不知道|无法回答|没有相关|没有提及")


class GoldJudge:
    version = "fake-gold-judge"

    def __init__(self):
        self.calls = 0

    async def verdict(self, question: str, gold: str, answer: str) -> tuple[str, str]:
        self.calls += 1
        text = re.sub(r"\s", "", answer)
        if gold.startswith("文档里没有答案"):
            return ("ABSTAIN", "说了找不到") if _REFUSE.search(text) else ("WRONG", "无答案题给了结论")
        first, *rest = gold.split("；")
        aliases = next((r.removeprefix("也算对的说法：").split("、") for r in rest if r.startswith("也算对的说法：")), [])
        wrong = next((r.removeprefix("答成这些算错：").split("、") for r in rest if r.startswith("答成这些算错：")), [])
        candidates = [first, re.sub(r"[^\d.\-]", "", first) or first, *aliases]
        if any(w and w in text for w in wrong):
            return "WRONG", "答成了算错的说法"
        if any(c and re.sub(r"\s", "", c) in text for c in candidates):
            return "CORRECT", "包含标准答案"
        if _REFUSE.search(text):
            return "ABSTAIN", "说了找不到"
        return "WRONG", "没有标准答案"

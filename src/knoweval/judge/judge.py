"""三档判分（规范第 5 节）：接口出错记为出错，回答为空记为拒答，其余全部交给裁判模型判定。

不再用规则比对数值、日期、文本：写法、单位、量级千变万化，规则判不准（如「358.6亿元」与标准答案 358.6）。
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel
from ragas.metrics import MetricResult, discrete_metric

from knoweval.judge.citation import CitationCheck, check_citations, evidence_retrieved
from knoweval.judge.normalize import strip_noise
from knoweval.images import ImageCheck, check_images
from knoweval.schema import Answer, Question, Verdict


class TextJudge(Protocol):
    version: str

    async def verdict(self, question: str, gold: str, answer: str) -> tuple[str, str]:
        """返回 (CORRECT/WRONG/ABSTAIN, 理由)。"""
        ...


class Judgement(BaseModel):
    verdict: Verdict
    reason: str
    method: Literal["rule", "llm"]
    needs_review: bool = False
    citation: CitationCheck | None = None
    # 错题归因：retrieval＝标准证据没进检索上下文；generation＝证据都在，模型没答对
    attribution: Literal["retrieval", "generation"] | None = None
    # I 类题的图文核对结果（规范第 6 节「图文答案」）；不是图文题为空
    images: ImageCheck | None = None


def gold_text(q: Question) -> str:
    """交给裁判的标准答案：答案本身，加上单位、允许误差、也算对和算错的说法。"""
    if q.is_no_answer:
        return "文档里没有答案（正确的回答应当表示资料里找不到、无法回答）"
    parts = [f"{q.answer}{q.unit or ''}"]
    if q.answer_type == "number" and q.tolerance:
        parts.append(f"允许误差 ±{q.tolerance:g}{q.unit or ''}")
    if q.answer_aliases:
        parts.append(f"也算对的说法：{'、'.join(q.answer_aliases)}")
    if q.answer_distractors:
        parts.append(f"答成这些算错：{'、'.join(q.answer_distractors)}")
    return "；".join(parts)


async def judge_answer(q: Question, a: Answer, text_judge: TextJudge | None = None) -> Judgement:
    j = await _judge(q, a, text_judge)
    if q.answer_images and not a.failed:
        j.images = check_images(q, a)
    # 有答案的题答错或说不知道时，看证据有没有被检索到；系统不返回上下文的不归因
    if not q.is_no_answer and j.verdict in (Verdict.WRONG, Verdict.ABSTAIN) and a.contexts is not None and q.evidence:
        j.attribution = "generation" if evidence_retrieved(a.contexts, q.evidence) else "retrieval"
    return j


async def _judge(q: Question, a: Answer, text_judge: TextJudge | None) -> Judgement:
    if a.failed:
        return Judgement(verdict=Verdict.ERROR, reason=f"出错：{a.error}", method="rule")
    text = (a.text or "").strip()
    if not strip_noise(text).strip():
        return Judgement(verdict=Verdict.ABSTAIN, reason="回答是空的", method="rule")
    if text_judge is None:
        return Judgement(verdict=Verdict.WRONG, reason="没有配置评测台的模型，判不了分（在「设置」页配好后重新判分）",
                         method="rule", needs_review=True)
    verdict, raw = await text_judge.verdict(q.question, gold_text(q), text)
    j = Judgement(verdict=Verdict(verdict), reason=f"裁判模型（{text_judge.version}）：{raw}", method="llm")
    if j.verdict is Verdict.CORRECT:
        j.citation = check_citations(a.citations, q.evidence)
    return j


# text_judge 的注解必须是 Any：RAGAS 按函数签名建 pydantic 校验模型，Protocol 类型建不出来时
# 它会丢掉全部参数，返回 value=None 而不报错（decorator.py:224、:300）
@discrete_metric(name="three_tier", allowed_values=[v.value for v in Verdict])
async def three_tier_metric(question: dict, answer: dict, text_judge: Any = None) -> MetricResult:
    """RAGAS 指标外壳：值是四种判定之一，完整判定放在 traces["output"]。"""
    j = await judge_answer(Question.model_validate(question), Answer.model_validate(answer), text_judge)
    return MetricResult(value=j.verdict.value, reason=j.reason, traces={"output": j.model_dump(mode="json")})


class MetricExecutionError(RuntimeError):
    pass


async def score_three_tier(question: Question, answer: Answer, text_judge: TextJudge | None = None) -> Judgement:
    """经 RAGAS 指标判分，并把它吞掉的异常重新变成异常：任何一题都不能静默丢掉判定。"""
    result = await three_tier_metric.ascore(question=question.model_dump(mode="json"),
                                            answer=answer.model_dump(mode="json"), text_judge=text_judge)
    if result.value is None or not result.traces:
        raise MetricExecutionError(f"{question.id} 判分失败：{result.reason}")
    return Judgement.model_validate(result.traces["output"])

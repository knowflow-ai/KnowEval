"""裁判模型：只看问题、标准答案、被测回答，一次给出三档判定；结果按内容哈希缓存。"""

import json

import pytest

from knoweval.judge.llm_judge import OpenAICompatibleJudge, parse_judge_output


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"verdict": "CORRECT", "reason": "结论一致"}', "CORRECT"),
        ('```json\n{"verdict": "wrong", "reason": "年份不同"}\n```', "WRONG"),
        ('判断：{"verdict": "ABSTAIN"}', "ABSTAIN"),
    ],
)
def test_parse_judge_output(raw, expected):
    assert parse_judge_output(raw)[0] == expected


def test_unparseable_output_raises():
    with pytest.raises(ValueError):
        parse_judge_output("我觉得差不多")
    with pytest.raises(ValueError):
        parse_judge_output('{"verdict": "MAYBE"}')


class FakeClient:
    def __init__(self, content):
        self.content = content
        self.calls = []

    async def complete(self, messages, model):
        self.calls.append(messages)
        return self.content


async def test_judge_prompt_never_contains_reference_documents(tmp_path):
    client = FakeClient('{"verdict": "CORRECT", "reason": "一致"}')
    judge = OpenAICompatibleJudge(model="m", client=client, cache_path=tmp_path / "cache.jsonl")
    verdict, _ = await judge.verdict("谁创办了岚图研究院？", "韩知微", "创办人是韩知微。")
    assert verdict == "CORRECT"
    prompt = json.dumps(client.calls[0], ensure_ascii=False)
    assert "韩知微" in prompt and "evidence" not in prompt


async def test_cache_hit_skips_the_model_call(tmp_path):
    client = FakeClient('{"verdict": "WRONG", "reason": "不同"}')
    cache = tmp_path / "cache.jsonl"
    judge = OpenAICompatibleJudge(model="m", client=client, cache_path=cache)
    await judge.verdict("q", "a", "b")
    again = OpenAICompatibleJudge(model="m", client=client, cache_path=cache)
    verdict, _ = await again.verdict("q", "a", "b")
    assert verdict == "WRONG"
    assert len(client.calls) == 1


async def test_cache_key_includes_model(tmp_path):
    client = FakeClient('{"verdict": "CORRECT"}')
    cache = tmp_path / "cache.jsonl"
    await OpenAICompatibleJudge(model="m1", client=client, cache_path=cache).verdict("q", "a", "b")
    await OpenAICompatibleJudge(model="m2", client=client, cache_path=cache).verdict("q", "a", "b")
    assert len(client.calls) == 2


class FlakyClient:
    """前几次抛指定异常，之后正常回答。"""

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    async def complete(self, messages, model):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return '{"verdict": "CORRECT", "reason": "一致"}'


class RateLimit(Exception):
    status_code = 429


async def test_rate_limits_back_off_beyond_the_normal_retry_budget(tmp_path):
    """限流按分钟计：连续 5 次 429 也要等下去，不能用 3 次普通重试就判这题失败。"""
    client = FlakyClient([RateLimit("TPM limit reached")] * 5)
    judge = OpenAICompatibleJudge(model="m", client=client, retry_delay_s=0.001)
    verdict, _ = await judge.verdict("q", "a", "b")
    assert verdict == "CORRECT" and client.calls == 6


async def test_other_errors_still_give_up_after_the_normal_budget(tmp_path):
    client = FlakyClient([ValueError("坏输出")] * 5)
    judge = OpenAICompatibleJudge(model="m", client=client, retry_delay_s=0.001)
    with pytest.raises(RuntimeError, match="连续 3 次失败"):
        await judge.verdict("q", "a", "b")

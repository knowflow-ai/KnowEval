"""出题流水线：init → generate → verify → review → export。大模型用假客户端。"""

import json
import re
import shutil
from pathlib import Path

import pytest

from knoweval.build.export import export_dataset
from knoweval.build.generators import generate
from knoweval.build.project import Candidate, init_project, load_project
from knoweval.build.verify import verify
from knoweval.dataset import load_dataset
from tests.test_cli import DEMO_DIR


class FakeLLM:
    """按提示词里的「题型：X」返回预设 JSON；闭卷与无答案检查按关键词返回。"""

    def __init__(self, replies: dict[str, object] | None = None):
        self.replies = replies or {}
        self.prompts: list[str] = []

    async def complete(self, messages, model):
        text = messages[-1]["content"]
        self.prompts.append(text)
        if "待评回答" in text and "verdict" in messages[0]["content"]:
            # 判分（闭卷过滤要判闭卷回答对不对）：按假裁判的朴素办法给三档
            from tests.fake_judge import GoldJudge
            gold = re.search(r"标准答案：(.*)", text).group(1)
            answer = text.split("<<<ANSWER>>>", 1)[1].split("<<<END>>>", 1)[0]
            verdict, reason = await GoldJudge().verdict("", gold, answer)
            return json.dumps({"verdict": verdict, "reason": reason}, ensure_ascii=False)
        if "只根据这些原文回答问题" in text:
            # 开卷复答：没预设就把原文原样念回去（标准答案取自原文，自然答得出）
            reply = self.replies.get("open", text)
            return reply(text) if callable(reply) else reply
        m = re.search(r"题型：(\w+)", text)
        key = m.group(1) if m else ("closed" if "不查阅任何资料" in text else "absence")
        reply = self.replies.get(key, "{}")
        if callable(reply):
            reply = reply(text)
        return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)


@pytest.fixture
def project(tmp_path):
    docs = tmp_path / "src"
    docs.mkdir()
    for name in ("02-沈墨.pdf", "03-岚图研究院.pdf", "04-潮汐系统技术参数手册.pdf"):
        shutil.copy(DEMO_DIR / "docs" / name, docs / name)
    shutil.copy(DEMO_DIR.parent / "demo-md" / "docs" / "07-韩知微.md", docs / "07-韩知微.md")
    return init_project(tmp_path / "proj", docs, name="pilot", version="0.1")


def test_init_registers_docs_and_parses_pages(project):
    p = load_project(project.root)
    assert sorted(d.name for d in p.docs) == ["02-沈墨.pdf", "03-岚图研究院.pdf", "04-潮汐系统技术参数手册.pdf", "07-韩知微.md"]
    assert all(len(d.sha256) == 64 for d in p.docs)
    assert "首席架构师" in p.page_text("02-沈墨.pdf", 1)
    assert p.pages["07-韩知微.md"][0].page is None
    assert {d.format for d in p.docs} == {"pdf", "md"}


def test_locate_quote_fills_page_or_reports_missing(project):
    assert project.locate("02-沈墨.pdf", "沈墨目前任职于岚图研究院") == 1
    assert project.locate("02-沈墨.pdf", "这句话不存在") is False
    assert project.locate("07-韩知微.md", "江陵大学计算机系任教十二年") is None  # md 没有页码


def _quote_from_prompt(prompt: str) -> dict:
    """像真模型一样从给它的原文里摘一句：取原文部分最长的一行。"""
    body = prompt.split("原文", 1)[1].split("：\n", 1)[1]
    line = max((l.strip() for l in body.splitlines()), key=len)
    return {"question": "这一页说了什么？", "answer": line[:6], "answer_type": "text", "quote": line}


async def test_generate_single_hop_from_a_page(project):
    llm = FakeLLM({"F": _quote_from_prompt})
    made = await generate(project, llm, "m", {"F": 2}, seed=1)
    assert len(made) == 2 and all(c.status == "generated" for c in made)
    assert all(c.generator == "single_page" and c.stage == "retrieve" for c in made)
    assert all(c.evidence[0].page is not None or c.evidence[0].doc.endswith(".md") for c in made)


async def test_generated_quote_not_in_source_is_auto_rejected(project):
    llm = FakeLLM({"F": {"question": "q?", "answer": "a", "answer_type": "text", "quote": "原文里根本没有这句"}})
    made = await generate(project, llm, "m", {"F": 1}, seed=1)
    assert made[0].status == "rejected_auto" and made[0].checks["quote_ok"] is False


async def test_generate_computed_answer_is_calculated_by_program(project):
    llm = FakeLLM({"C": {"question": "v2.0 比 v1.0 的最大集群节点数多多少？", "unit": "个",
                         "operands": [{"name": "a", "value": 128, "quote": "v2.0 2023-06-15 80 万条/秒 45 ms 128"},
                                      {"name": "b", "value": 32, "quote": "v1.0 2021-11-02 30 万条/秒 120 ms 32"}],
                         "formula": "a - b"}})
    made = await generate(project, llm, "m", {"C": 1}, seed=3)
    assert made[0].answer == "96" and made[0].answer_type == "number" and made[0].tolerance == 0
    assert len(made[0].evidence) == 2 and made[0].stage == "reason"


async def test_formula_cannot_execute_arbitrary_code(project):
    llm = FakeLLM({"C": {"question": "q?", "operands": [{"name": "a", "value": 1, "quote": "v1.0 2021-11-02 30 万条/秒 120 ms 32"}],
                         "formula": "__import__('os').system('echo hi')"}})
    made = await generate(project, llm, "m", {"C": 1}, seed=3)
    assert made[0].status == "rejected_auto" and "公式" in " ".join(made[0].notes)


async def test_generate_no_answer_question(project):
    llm = FakeLLM({"N": {"question": "沈墨毕业于哪所大学？", "n_kind": "near_miss"}})
    made = await generate(project, llm, "m", {"N": 1}, seed=2)
    c = made[0]
    assert c.qtype == ["N"] and c.answer == "" and c.evidence == [] and c.n_kind == "near_miss" and c.stage == "honesty"


def _bridge_reply(prompt: str) -> dict:
    """从两段原文里各摘含桥接词的一句。"""
    bridge = prompt.split("通过「", 1)[1].split("」", 1)[0]
    parts = prompt.split("原文一（", 1)[1].split("原文二（", 1)
    quotes = []
    for part in parts:
        body = part.split("：\n", 1)[1]
        flat = "".join(body.split())
        i = flat.find(bridge)
        quotes.append(flat[max(0, i - 4): i + len(bridge) + 4])
    return {"question": "两跳问题？", "answer": bridge, "answer_type": "text", "quotes": quotes}


async def test_generate_bridge_question_across_two_documents(project):
    llm = FakeLLM({"M": _bridge_reply})
    made = await generate(project, llm, "m", {"M": 1}, seed=1)
    c = made[0]
    assert c.qtype == ["M"] and c.hops == 2 and c.hop_kind == "chain"
    assert len({e.doc for e in c.evidence}) == 2
    assert c.checks["bridge"] in c.evidence[0].quote and c.checks["bridge"] in c.evidence[1].quote
    assert c.status == "generated", c.notes


async def test_verify_rejects_closed_book_answerable_and_present_no_answer(project):
    p = project
    p.add(Candidate(id="c1", qtype=["F"], question="中国的首都是哪里？", answer="北京", answer_type="text",
                    evidence=[{"doc": "02-沈墨.pdf", "page": 1, "quote": "沈墨目前任职于岚图研究院"}], generator="manual"))
    p.add(Candidate(id="c2", qtype=["F"], question="沈墨的职务？", answer="首席架构师", answer_type="text",
                    evidence=[{"doc": "02-沈墨.pdf", "page": 1, "quote": "沈墨目前任职于岚图研究院，担任首席架构师"}], generator="manual"))
    p.add(Candidate(id="c3", qtype=["N"], question="岚图研究院创办于哪一年？", answer="", answer_type="text", generator="manual"))
    closed = FakeLLM({"closed": "北京"})
    absence = FakeLLM({"absence": '{"found": true, "reason": "第 1 页写了二〇一五年"}'})
    await verify(p, closed_book=[(closed, "m1"), (FakeLLM({"closed": "北京"}), "m2")], absence=(absence, "m"))
    by = {c.id: c for c in load_project(p.root).candidates}
    assert by["c1"].status == "rejected_auto" and by["c1"].checks["closed_book"]["answerable"] is True
    assert by["c2"].status == "verified"
    assert by["c3"].status == "rejected_auto" and by["c3"].checks["absence"]["found"] is True


async def test_review_and_export_only_approved(project, tmp_path):
    p = project
    p.add(Candidate(id="c1", qtype=["F"], question="沈墨的职务？", answer="首席架构师", answer_type="text", stage="retrieve",
                    evidence=[{"doc": "02-沈墨.pdf", "page": 1, "quote": "沈墨目前任职于岚图研究院，担任首席架构师"}],
                    generator="manual", status="verified"))
    p.add(Candidate(id="c2", qtype=["F"], question="废题？", answer="x", answer_type="text",
                    evidence=[{"doc": "02-沈墨.pdf", "page": 1, "quote": "沈墨目前任职于岚图研究院"}], generator="manual", status="verified"))
    p.review("c1", "approve", by="r01")
    p.review("c2", "reject", by="r01", note="答案不唯一")
    with pytest.raises(ValueError, match="理由"):
        p.review("c1", "reject", by="r01")
    out = export_dataset(load_project(p.root), tmp_path / "pilot@0.1")
    ds = load_dataset(out)
    assert [q.id for q in ds.questions] == ["c1"] and ds.questions[0].stage == "retrieve"
    assert ds.manifest.canary.startswith("KNOWEVAL-CANARY-")
    assert {f.name for f in ds.manifest.files} == {"02-沈墨.pdf"}  # 只带被题目用到的文档
    log = [json.loads(line) for line in (p.root / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["action"] == "reject" and e["note"] == "答案不唯一" for e in log)


def test_long_markdown_is_split_into_sections_that_round_trip():
    from knoweval.build.refparse import split_sections
    paras = [f"## 第{i}节\n\n" + "正文内容。" * 120 for i in range(12)]
    text = "\n\n".join(paras)
    parts = split_sections(text, target=2000)
    assert len(parts) > 3 and "\n\n".join(parts) == text
    assert all(len(p) <= 2000 + 800 for p in parts)
    assert split_sections("短文") == ["短文"]


def _versioned_project(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    old = "# 报销办法（2013年）\n\n**第一条** 总则说明文字足够长足够长。\n\n**第五条** 住宿费标准为每人每天400元，超出部分自理。\n"
    new = "# 报销办法（2016年）\n\n**第一条** 总则说明文字足够长足够长。\n\n**第五条** 住宿费标准为每人每天500元，超出部分自理。\n"
    (src / "报销办法（2013年）.md").write_text(old, encoding="utf-8")
    (src / "报销办法（2016年）.md").write_text(new, encoding="utf-8")
    (src / "无关文件.md").write_text("# 无关\n\n一段与版本无关的说明文字。\n", encoding="utf-8")
    return init_project(tmp_path / "proj", src, name="reg", version="0.1")


def test_version_pairs_and_changed_paragraphs(tmp_path):
    from knoweval.build.generators import _full_text, changed_paragraphs, version_pairs
    p = _versioned_project(tmp_path)
    assert version_pairs(p) == [("报销办法（2013年）.md", "报销办法（2016年）.md")]
    diffs = changed_paragraphs(_full_text(p, "报销办法（2013年）.md"), _full_text(p, "报销办法（2016年）.md"))
    assert diffs == [("**第五条** 住宿费标准为每人每天400元，超出部分自理。", "**第五条** 住宿费标准为每人每天500元，超出部分自理。")]


async def test_version_question_takes_the_new_value_and_rejects_no_change(tmp_path):
    p = _versioned_project(tmp_path)
    good = FakeLLM({"K": {"question": "报销办法规定的住宿费标准是每人每天多少元？", "answer": "500", "answer_type": "number",
                          "unit": "元", "old_answer": "400", "new_quote": "住宿费标准为每人每天500元",
                          "old_quote": "住宿费标准为每人每天400元"}})
    c = (await generate(p, good, "m", {"K": 1}, seed=1))[0]
    assert c.status == "generated" and c.answer == "500" and c.answer_distractors == ["400"]
    assert c.traps == ["version"] and [e.doc for e in c.evidence] == ["报销办法（2016年）.md", "报销办法（2013年）.md"]
    same = FakeLLM({"K": {"question": "q?", "answer": "500", "old_answer": "500",
                          "new_quote": "住宿费标准为每人每天500元", "old_quote": "住宿费标准为每人每天400元"}})
    c = (await generate(p, same, "m", {"K": 1}, seed=1))[0]
    assert c.status == "rejected_auto" and "不是版本冲突" in c.notes[0]


async def test_some_single_hop_questions_are_paraphrased(project):
    llm = FakeLLM({"F": _quote_from_prompt})
    made = await generate(project, llm, "m", {"F": 10}, seed=3)
    with_para = [c for c in made if "paraphrase" in c.traps]
    assert 0 < len(with_para) < 10
    assert all("口语说法" in prompt for prompt, c in zip(llm.prompts, made) if "paraphrase" in c.traps)


async def test_verify_rejects_malformed_gold_instead_of_crashing(project):
    p = project
    p.add(Candidate(id="d1", qtype=["N"], question="无答案题却给了答案？", answer="03-31", answer_type="text", generator="manual"))
    p.add(Candidate(id="c2", qtype=["F"], question="沈墨的职务？", answer="首席架构师", answer_type="text",
                    evidence=[{"doc": "02-沈墨.pdf", "page": 1, "quote": "沈墨目前任职于岚图研究院，担任首席架构师"}], generator="manual"))
    stats = await verify(p, closed_book=[(FakeLLM({"closed": "不知道"}), "m1")])
    by = {c.id: c for c in load_project(p.root).candidates}
    assert by["d1"].status == "rejected_auto" and "格式不合规" in by["d1"].notes[-1]
    assert by["c2"].status == "verified" and stats == {"verified": 1, "rejected": 1, "errors": 0}


def test_to_number_handles_thousands_separators_and_units():
    from knoweval.build.generators import to_number
    assert to_number("189,163,654,064.64") == 189163654064.64
    assert to_number("25.42%") == 25.42 and to_number(" 1,200 元") == 1200 and to_number(7) == 7.0
    assert to_number("120条/秒") == 120 and to_number("约 45 ms") == 45 and to_number("-3.5") == -3.5
    import pytest as _pytest
    with _pytest.raises(ValueError):
        to_number("不是数")


async def test_open_book_check_rejects_questions_the_document_does_not_answer_with_the_gold(project):
    """开卷复答：给模型题干和证据页、不给标准答案，答出来与标准答案不一致的题自动打回，写明它答了什么。"""
    p = project
    p.add(Candidate(id="ok", qtype=["F"], question="沈墨的职务？", answer="首席架构师", answer_type="text",
                    evidence=[{"doc": "02-沈墨.pdf", "page": 1, "quote": "沈墨目前任职于岚图研究院，担任首席架构师"}], generator="manual"))
    p.add(Candidate(id="bad", qtype=["F"], question="沈墨在哪里任职？", answer="江陵大学", answer_type="text",
                    evidence=[{"doc": "02-沈墨.pdf", "page": 1, "quote": "沈墨目前任职于岚图研究院"}], generator="manual"))
    reader = FakeLLM({"open": lambda prompt: "首席架构师" if "职务" in prompt else "岚图研究院"})
    await verify(p, closed_book=[], open_book=(reader, "m"))
    by = {c.id: c for c in load_project(p.root).candidates}
    assert by["ok"].status == "verified" and by["ok"].checks["open_book"]["verdict"] == "CORRECT"
    assert by["bad"].status == "rejected_auto"
    assert "开卷作答得到「岚图研究院」，与标准答案「江陵大学」不一致" in by["bad"].notes[-1]
    open_prompt = next(x for x in reader.prompts if "只根据这些原文回答问题" in x)
    assert "沈墨" in open_prompt and "江陵大学" not in open_prompt.split("问题：")[0]   # 不给标准答案


def test_generation_prompts_ask_for_unique_questions_and_labelled_quotes():
    from knoweval.build import generators
    assert "唯一确定答案的限定条件" in generators.QUALITY_RULES and "不能只摘一个孤立的数字" in generators.QUALITY_RULES


async def test_pages_with_nothing_to_ask_are_skipped_not_turned_into_questions(project):
    """封面、目录、联系方式这类页面：模型回 skip，这道题不出，记一笔日志，整批继续。"""
    from knoweval.build import generators
    p = project
    llm = FakeLLM({"F": {"skip": "联系方式页"}})
    made = await generators.generate(p, llm, "m", {"F": 2})
    assert made == [] and not p.candidates


def test_no_answer_prompt_rules_out_yes_no_questions():
    import inspect
    from knoweval.build import generators
    assert "是非题" in inspect.getsource(generators._no_answer)
    assert "问法要和答案对得上" in generators.QUALITY_RULES and "封面、目录、联系方式" in generators.QUALITY_RULES


def test_formula_variables_may_be_source_labels_with_operator_characters():
    """模型常拿原文标签当变量名，「40-59岁」「月/年会员」里的减号、斜杠不能被当成运算。"""
    from knoweval.build.generators import eval_formula
    assert eval_formula("40-59岁 + 20-39岁", {"40-59岁": 36.7, "20-39岁": 37.3}) == pytest.approx(74.0)
    assert eval_formula("月/年会员 + 全剧", {"月/年会员": 50.9, "全剧": 26.7}) == pytest.approx(77.6)
    assert eval_formula("v * 2 + v0", {"v": 3, "v0": 1}) == 7


async def test_a_computed_count_that_comes_out_fractional_is_rejected(project):
    """占比乘样本量推出 583.562 人：计数不会是小数，原文没有这个统计。"""
    from knoweval.build import generators
    doc, pg, text = generators._pages(project)[0]
    line = max((l.strip() for l in text.splitlines()), key=len)
    reply = {"question": "本科学历的用户有多少人？", "unit": "人", "formula": "a * b / 100",
             "operands": [{"name": "a", "value": "57.1", "quote": line}, {"name": "b", "value": "1022", "quote": line}]}
    made = await generators.generate(project, FakeLLM({"C": reply}), "m", {"C": 1})
    assert made[0].status == "rejected_auto" and "计数不会是小数" in made[0].notes[-1]

"""虚构语料生成器：先造世界，再写文档，题目由图推出。"""

from collections import Counter

import pytest

from knoweval.build.fiction.questions import derive_questions
from knoweval.build.fiction.world import build_world
from knoweval.build.fiction.writer import write_documents
from knoweval.dataset import normalize_for_quote


@pytest.fixture(scope="module")
def world():
    return build_world(seed=7, n_orgs=4, n_people=12, n_projects=6)


@pytest.fixture(scope="module")
def docs(world):
    return write_documents(world, seed=7, min_chars=3000)


def test_world_is_deterministic():
    a, b = build_world(seed=7), build_world(seed=7)
    assert a.model_dump() == b.model_dump()
    assert build_world(seed=8).model_dump() != a.model_dump()


def test_world_has_the_relations_questions_need(world):
    assert all(o.founder in {p.id for p in world.people} for o in world.orgs)
    assert all(p.org in {o.id for o in world.orgs} for p in world.people)
    assert all(pr.lead in {p.id for p in world.people} for pr in world.projects)
    names = Counter(p.name for p in world.people)
    assert max(names.values()) >= 2, "要造一组同名人物做干扰"
    assert any(o.founded_year_old for o in world.orgs), "要有一家机构的成立年份在旧材料里写错"


def test_every_fact_is_written_verbatim_into_its_document(world, docs):
    for fact in docs.facts.values():
        assert normalize_for_quote(fact.sentence) in normalize_for_quote(docs.texts[fact.doc]), fact


def test_documents_are_long_enough(docs):
    assert all(len(t) >= 3000 for t in docs.texts.values())


def test_writing_is_deterministic(world):
    assert write_documents(world, seed=7, min_chars=3000).texts == write_documents(world, seed=7, min_chars=3000).texts


def test_derived_questions_cover_types_and_carry_evidence(world, docs):
    qs = derive_questions(world, docs, seed=7)
    types = Counter(q.qtype[0] for q in qs)
    for t in ("F", "M", "G", "X", "C", "D", "K", "N"):
        assert types[t] >= 1, t
    for q in qs:
        if q.qtype[0] != "N":
            assert q.evidence, q.id
            for ev in q.evidence:
                assert normalize_for_quote(ev.quote) in normalize_for_quote(docs.texts[ev.doc]), (q.id, ev.quote)
        assert q.stage and q.status == "verified" and q.generator.startswith("fiction")


def test_chain_question_answer_follows_the_graph(world, docs):
    qs = derive_questions(world, docs, seed=7)
    chain = next(q for q in qs if q.qtype == ["M"] and q.hops >= 3)
    project = next(p for p in world.projects if p.name in chain.question)
    lead = world.person(project.lead)
    org = world.org(lead.org)
    founder = world.person(org.founder)
    assert chain.answer in (founder.name, founder.university)
    assert len({ev.doc for ev in chain.evidence}) >= 3


def test_global_question_lists_every_member(world, docs):
    qs = derive_questions(world, docs, seed=7)
    g = next(q for q in qs if q.qtype == ["G"])
    org = next(o for o in world.orgs if o.name in g.question)
    members = {p.name for p in world.people if p.org == org.id}
    assert set(g.answer.split("；")) == members and g.answer_type == "list"


def test_conflict_question_takes_the_newer_document(world, docs):
    qs = derive_questions(world, docs, seed=7)
    k = next(q for q in qs if q.qtype == ["K"])
    org = next(o for o in world.orgs if o.name in k.question)
    assert k.answer == str(org.founded_year) and str(org.founded_year_old) in k.answer_distractors
    assert "version" in k.traps


def test_false_premise_refers_to_something_that_does_not_exist(world, docs):
    qs = derive_questions(world, docs, seed=7)
    fp = next(q for q in qs if q.n_kind == "false_premise")
    assert fp.answer == "" and fp.evidence == []
    fake = fp.question.split("「", 1)[1].split("」", 1)[0]
    assert fake and not any(fake in t for t in docs.texts.values())


def test_no_sentence_repeats_within_a_paragraph(docs):
    for text in docs.texts.values():
        for para in text.split("\n\n"):
            sentences = [x for x in para.split("。") if x.strip()]
            assert len(sentences) == len(set(sentences)), para[:60]


def test_default_world_yields_the_core_mix(world, docs):
    """默认世界推出 30 题，配比照数据集设计 §9 的虚构列（md 版）。"""
    qs = derive_questions(world, docs, seed=7)
    assert len(qs) == 30
    assert Counter(q.stage for q in qs) == {"retrieve": 6, "chunk": 4, "reason": 17, "honesty": 3}
    assert Counter(q.n_kind for q in qs if q.n_kind) == {"false_premise": 1, "near_miss": 1, "unrelated": 1}
    assert sum("paraphrase" in q.traps for q in qs) >= 2 and sum("version" in q.traps for q in qs) == 2


def test_chunk_questions_put_the_answer_far_from_the_alias(world, docs):
    """分块题：交付数在报告末尾、只说「该项目」，项目名只在开头的指代说明里。"""
    qs = [q for q in derive_questions(world, docs, seed=7) if q.stage == "chunk"]
    assert len(qs) == 4
    for q in qs:
        alias, fact = q.evidence
        text = docs.texts[fact.doc]
        assert "该项目" in fact.quote and q.question[:2] not in fact.quote
        assert text.index(fact.quote) - text.index(alias.quote) > 3000


def test_budget_revision_conflict_uses_the_newer_notice(world, docs):
    qs = derive_questions(world, docs, seed=7)
    k = next(q for q in qs if q.qtype == ["K"] and "预算" in q.question)
    pr = next(p for p in world.projects if p.name in k.question)
    assert k.answer == str(pr.budget_revised) and str(pr.budget) in k.answer_distractors
    assert any("调整" in e.doc for e in k.evidence)

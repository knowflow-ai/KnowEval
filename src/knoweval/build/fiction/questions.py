"""由世界图推出题目：答案在构造时就确定，不需要模型猜。

默认世界（4 家机构、12 人、6 个项目）推出 30 题，按数据集设计 §9 的虚构列配比（md 版没有 parse，
那 4 题与 cite 的 2 题挪进 retrieve、chunk、reason）：

  retrieve 6（F 5，其中 2 道换说法；K 1）· chunk 4（文档内指代，答案离指代说明最远）·
  reason 17（M 6、G 2、X 3、C 3、D 2、K 1）· honesty 3（N 三类各 1）

世界更小时按能推出的数量出，不硬凑。
"""

from __future__ import annotations

import random
from collections import Counter

from knoweval.build.fiction.world import World
from knoweval.build.fiction.writer import DocSet
from knoweval.build.project import Candidate
from knoweval.schema import Evidence


def _months(start, end) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


class _Builder:
    def __init__(self, world: World, docs: DocSet, seed: int):
        self.w, self.docs = world, docs
        self.rng = random.Random(seed)
        self.out: list[Candidate] = []
        self.dup = {n for n, c in Counter(p.name for p in world.people).items() if c > 1}

    def add(self, **kw) -> None:
        self.out.append(Candidate(id=f"fic-{len(self.out) + 1:03d}", status="verified", **kw))

    def ev(self, fid: str) -> Evidence:
        f = self.docs.facts[fid]
        return Evidence(doc=f.doc, quote=f.sentence)

    def same_name(self, *names: str) -> list[str]:
        return ["same_name"] if any(n in self.dup for n in names) else []


def _single_hop(b: _Builder) -> None:
    """F：人物的毕业院校与职务。名字重复的人不出（问题本身有歧义）。"""
    unique = [p for p in b.w.people if p.name not in b.dup]
    for i, p in enumerate(unique[:3]):
        if i == 2:  # 换说法：「母校」，原文写「毕业于」
            q, traps = f"{p.name}的母校是哪一所？", ["paraphrase"]
        else:
            q, traps = f"{p.name}毕业于哪所大学？", []
        b.add(qtype=["F"], question=q, answer=p.university, stage="retrieve", traps=traps,
              evidence=[b.ev(f"person_univ:{p.id}")], generator="fiction:single")
    for i, p in enumerate(unique[3:5]):
        org = b.w.org(p.org)
        q = f"{p.name}在{org.name}担任什么职务？" if i == 0 else f"{org.name}的{p.name}现在是做什么岗位的？"
        b.add(qtype=["F"], question=q, answer=p.role, stage="retrieve", traps=[] if i == 0 else ["paraphrase"],
              evidence=[b.ev(f"person_role:{p.id}")], generator="fiction:single")


def _chunk(b: _Builder) -> None:
    """文档内指代：交付数写在报告末尾、只说「该项目」，项目名只在开头的指代说明里出现。"""
    for pr in b.w.projects[:4]:
        b.add(qtype=["L"], question=f"{pr.name}项目最终交付了多少个功能模块？", answer=str(pr.deliverables),
              answer_type="number", unit="个", tolerance=0, stage="chunk", generator="fiction:alias",
              evidence=[b.ev(f"project_alias:{pr.id}"), b.ev(f"project_deliverables:{pr.id}")])


def _multi_hop(b: _Builder) -> None:
    w = b.w
    # 三跳链 项目 → 负责人 → 所在机构 → 创办人 → 毕业院校
    for pr in w.projects[:3]:
        lead = w.person(pr.lead)
        org = w.org(lead.org)
        founder = w.person(org.founder)
        b.add(qtype=["M"], question=f"{pr.name}项目负责人所在机构的创办人毕业于哪所大学？", answer=founder.university,
              hops=3, hop_kind="chain", stage="reason", traps=b.same_name(lead.name), generator="fiction:chain",
              evidence=[b.ev(f"project_lead:{pr.id}"), b.ev(f"person_role:{lead.id}"),
                        b.ev(f"org_founded:{org.id}"), b.ev(f"person_univ:{founder.id}")])
    # 两跳 项目 → 负责人 → 毕业院校
    for pr in w.projects[3:6]:
        lead = w.person(pr.lead)
        b.add(qtype=["M"], question=f"{pr.name}项目的负责人毕业于哪所大学？", answer=lead.university, hops=2,
              hop_kind="chain", stage="reason", traps=b.same_name(lead.name), generator="fiction:chain",
              evidence=[b.ev(f"project_lead:{pr.id}"), b.ev(f"person_univ:{lead.id}")])


def _global(b: _Builder) -> None:
    """G：某机构的全部成员（集合必须穷举，漏一个多一个都不对）。"""
    orgs = b.w.orgs[:]
    b.rng.shuffle(orgs)
    for org in orgs[:2]:
        members = [p for p in b.w.people if p.org == org.id]
        b.add(qtype=["G"], question=f"哪些人任职于{org.name}？", answer="；".join(p.name for p in members),
              answer_type="list", stage="reason", generator="fiction:global",
              evidence=[b.ev(f"person_role:{p.id}") for p in members])


def _compare_compute(b: _Builder) -> None:
    w = b.w
    a, c = w.projects[0], w.projects[1]
    hi, lo = (a, c) if a.budget > c.budget else (c, a)
    budget_ev = [b.ev(f"project_budget:{a.id}"), b.ev(f"project_budget:{c.id}")]
    # X：预算比较（前两个项目预算只差一位，构成近似数字干扰）
    b.add(qtype=["X"], question=f"{a.name}项目和{c.name}项目，哪个总预算更高？", answer=f"{hi.name}项目",
          answer_aliases=[hi.name], answer_distractors=[lo.name], hops=2, hop_kind="comparison", stage="reason",
          traps=["near_number"], generator="fiction:compare", evidence=budget_ev)
    # C：预算差
    b.add(qtype=["C"], question=f"{a.name}项目和{c.name}项目的总预算相差多少万元？", answer=str(abs(a.budget - c.budget)),
          answer_type="number", unit="万元", tolerance=0, hops=2, stage="reason", traps=["near_number"],
          generator="fiction:compute", evidence=budget_ev)
    # X、C：两家机构的成立年份（不用有旧宣传册的那家，免得和版本冲突题混在一起）
    plain = [o for o in w.orgs if not o.founded_year_old]
    pair = next(((x, y) for i, x in enumerate(plain) for y in plain[i + 1:] if x.founded_year != y.founded_year), None)
    if pair:
        x, y = pair
        early, late = (x, y) if x.founded_year < y.founded_year else (y, x)
        org_ev = [b.ev(f"org_founded:{x.id}"), b.ev(f"org_founded:{y.id}")]
        b.add(qtype=["X"], question=f"{x.name}和{y.name}，哪一家成立得更早？", answer=early.name,
              answer_distractors=[late.name], hops=2, hop_kind="comparison", stage="reason", generator="fiction:compare",
              evidence=org_ev)
        b.add(qtype=["C"], question=f"{x.name}和{y.name}的成立年份相差几年？", answer=str(abs(x.founded_year - y.founded_year)),
              answer_type="number", unit="年", tolerance=0, hops=2, stage="reason", generator="fiction:compute",
              evidence=org_ev)
    # X：两个项目谁先启动
    if len(w.projects) >= 5 and w.projects[3].start != w.projects[4].start:
        p, q = w.projects[3], w.projects[4]
        first, second = (p, q) if p.start < q.start else (q, p)
        b.add(qtype=["X"], question=f"{p.name}项目和{q.name}项目，哪个先启动？", answer=f"{first.name}项目",
              answer_aliases=[first.name], answer_distractors=[second.name], hops=2, hop_kind="comparison",
              stage="reason", generator="fiction:compare",
              evidence=[b.ev(f"project_dates:{p.id}"), b.ev(f"project_dates:{q.id}")])
    # C：某机构承担的全部项目预算合计（要先找全该机构的项目；有调整通知的项目口径不一，不进这题）
    org = w.orgs[0]
    owned = [pr for pr in w.projects if pr.org == org.id]
    if len(owned) >= 2 and not any(pr.budget_revised for pr in owned):
        b.add(qtype=["C"], question=f"{org.name}承担的所有项目，总预算合计是多少万元？",
              answer=str(sum(pr.budget for pr in owned)), answer_type="number", unit="万元", tolerance=0,
              hops=len(owned), hop_kind="intersection", stage="reason", generator="fiction:compute",
              evidence=[b.ev(f"project_budget:{pr.id}") for pr in owned])


def _dates(b: _Builder) -> None:
    """D：项目持续了多少个月。"""
    for pr in b.w.projects[2:4]:
        b.add(qtype=["D"], question=f"{pr.name}项目从启动到结项一共经历了多少个月？", answer=str(_months(pr.start, pr.end)),
              answer_type="number", unit="个月", tolerance=0, stage="reason", generator="fiction:date",
              evidence=[b.ev(f"project_dates:{pr.id}")])


def _conflicts(b: _Builder) -> None:
    w = b.w
    # K：新旧两份文档对成立年份说法不一，以较新的年度报告为准
    o = next((o for o in w.orgs if o.founded_year_old), None)
    if o:
        b.add(qtype=["K"], question=f"按照最新的资料，{o.name}成立于哪一年？", answer=str(o.founded_year), answer_type="number",
              unit="年", tolerance=0, answer_distractors=[str(o.founded_year_old)], hops=2, stage="retrieve",
              traps=["version"], generator="fiction:conflict",
              evidence=[b.ev(f"org_founded:{o.id}"), b.ev(f"org_founded_old:{o.id}")])
    # K：结项报告里的预算与后发的调整通知不同，以通知为准
    pr = next((p for p in w.projects if p.budget_revised), None)
    if pr:
        b.add(qtype=["K"], question=f"{pr.name}项目现在的总预算是多少万元？", answer=str(pr.budget_revised),
              answer_type="number", unit="万元", tolerance=0, answer_distractors=[str(pr.budget)], hops=2,
              stage="reason", traps=["version"], generator="fiction:conflict",
              evidence=[b.ev(f"project_budget_revised:{pr.id}"), b.ev(f"project_budget:{pr.id}")])


def _no_answer(b: _Builder) -> None:
    w = b.w
    fake = w.spare_project_names[0] if w.spare_project_names else "无名"
    b.add(qtype=["N"], question=f"「{fake}」项目的负责人是谁？", n_kind="false_premise", stage="honesty",
          generator="fiction:no_answer")
    b.add(qtype=["N"], question=f"{w.orgs[-1].name}的总部设在哪座城市？", n_kind="near_miss", stage="honesty",
          generator="fiction:no_answer")
    b.add(qtype=["N"], question="光合作用主要发生在植物细胞的哪个结构里？", n_kind="unrelated", stage="honesty",
          generator="fiction:no_answer")


def derive_questions(world: World, docs: DocSet, seed: int) -> list[Candidate]:
    b = _Builder(world, docs, seed)
    for step in (_single_hop, _chunk, _multi_hop, _global, _compare_compute, _dates, _conflicts, _no_answer):
        step(b)
    return b.out

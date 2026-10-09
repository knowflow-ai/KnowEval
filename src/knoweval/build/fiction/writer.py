"""写文档：每个实体一篇（机构年度报告、人物简介、项目结项报告），外加一份旧版宣传册（版本冲突）。

事实句按固定句式逐字写进文档，题目的证据就是这些句子。其余篇幅用通用模板段落填满，
模板里不出现数字、不提别的实体，免得给多跳题留下字面捷径，也免得引入与事实冲突的信息。
同一个种子、同一个世界，写出的文本逐字相同。
"""

from __future__ import annotations

import random

from pydantic import BaseModel

from knoweval.build.fiction.world import World

FILLER = [
    "{s}始终把规范化管理放在重要位置，相关制度在执行过程中不断修订完善。",
    "围绕年度重点任务，{s}组织了多轮专题讨论，形成了较为一致的工作思路。",
    "在跨部门协作方面，{s}建立了例会与周报机制，问题能够得到及时跟进。",
    "{s}重视知识沉淀，要求关键过程留有记录，重要结论形成书面材料归档。",
    "面对外部环境的变化，{s}适时调整了工作节奏，确保主要目标不受影响。",
    "{s}持续推进流程数字化，减少了重复填报，一线人员的负担有所减轻。",
    "质量与安全是底线，{s}定期开展自查，对发现的问题建立台账逐项整改。",
    "{s}鼓励青年骨干承担更多责任，并通过导师制度帮助其尽快成长。",
    "在对外交流中，{s}坚持开放合作的态度，积极吸收同行的有益经验。",
    "{s}对阶段性成果进行了复盘，既总结了做法，也明确了下一步改进方向。",
    "为提升响应速度，{s}优化了内部审批环节，常规事项的办理时间明显缩短。",
    "{s}注重风险识别，在关键节点前开展评估，并准备了相应的应对预案。",
    "相关工作得到了合作方的认可，{s}也在合作中进一步明确了自身定位。",
    "{s}强调目标导向，把任务分解到具体岗位，并按计划跟踪落实情况。",
    "在资源有限的情况下，{s}优先保障核心任务，非紧急事项适当延后。",
    "{s}重视用户反馈，安排专人收集意见，并将共性问题纳入后续优化。",
]
SECTIONS = {
    "org": ["机构概况", "年度工作回顾", "人才队伍建设", "质量与安全管理", "未来工作计划"],
    "person": ["人物简介", "主要经历", "研究方向", "代表性工作", "社会兼职"],
    "project": ["项目背景", "建设目标", "实施过程", "主要成果", "经验与不足"],
    "brochure": ["关于我们", "发展历程", "服务领域", "联系我们"],
    "notice": ["调整事项", "调整原因", "执行要求"],
}


class Fact(BaseModel):
    doc: str
    sentence: str


class DocSet(BaseModel):
    texts: dict[str, str]
    facts: dict[str, Fact]


def _date(d) -> str:
    return f"{d.year}年{d.month}月{d.day}日"


def _write(name: str, title: str, kind: str, subject: str, facts: list[str], seed: int, min_chars: int,
           late_facts: list[str] | None = None) -> str:
    """事实句放进前两节，其余用模板段落填满到 min_chars。

    late_facts 放在最后一轮的最后一节：离文档开头最远，又只用「该项目」之类的指代，
    分块一旦把它和开头的指代说明切开，单看这一块就不知道说的是谁（分块干扰）。
    """
    rng = random.Random(f"{seed}-{name}")
    sections = SECTIONS[kind]
    body: dict[str, list[str]] = {s: [] for s in sections}
    for i, fact in enumerate(facts):
        body[sections[min(i // 2, 1)]].append(fact)
    rounds_text: list[list[tuple[str, str]]] = []
    text_len = 0
    while True:
        this_round = []
        for sec in sections:
            # 段内不重复抽样，避免同一段里出现两句一模一样的话
            para = [t.format(s=subject) for t in rng.sample(FILLER, rng.randint(2, 4))]
            if not rounds_text and body[sec]:
                para = para[:1] + body[sec] + para[1:]
            this_round.append((sec, "".join(para)))
            text_len += len(this_round[-1][1])
        rounds_text.append(this_round)
        if text_len >= min_chars:
            break
    if late_facts:
        sec, para = rounds_text[-1][-1]
        rounds_text[-1][-1] = (sec, para + "".join(late_facts))
    lines = [f"# {title}", ""]
    for r, this_round in enumerate(rounds_text):
        for sec, para in this_round:
            if r == 0:
                lines += [f"## {sec}", ""]
            lines += [para, ""]
    return "\n".join(lines).rstrip() + "\n"


def write_documents(world: World, seed: int, min_chars: int = 3000) -> DocSet:
    texts: dict[str, str] = {}
    facts: dict[str, Fact] = {}

    def add(doc: str, title: str, kind: str, subject: str, items: list[tuple[str, str]],
            late: list[tuple[str, str]] | None = None) -> None:
        for fid, sentence in items + (late or []):
            facts[fid] = Fact(doc=doc, sentence=sentence)
        texts[doc] = _write(doc, title, kind, subject, [s for _, s in items], seed, min_chars,
                            late_facts=[s for _, s in late or []])

    for o in world.orgs:
        founder = world.person(o.founder)
        add(f"{o.name}2025年度报告.md", f"{o.name} 2025 年度报告", "org", o.name,
            [(f"org_founded:{o.id}", f"{o.name}由{founder.name}于{o.founded_year}年创办。")])
        if o.founded_year_old:
            add(f"{o.name}宣传册（2019年版）.md", f"{o.name} 宣传册（2019 年印制）", "brochure", o.name,
                [(f"org_founded_old:{o.id}", f"{o.name}成立于{o.founded_year_old}年。")])
    for p in world.people:
        org = world.org(p.org)
        add(f"人物简介-{p.name}（{org.name}）.md", f"{p.name}", "person", p.name,
            [(f"person_role:{p.id}", f"{p.name}现任{org.name}{p.role}。"),
             (f"person_univ:{p.id}", f"{p.name}毕业于{p.university}。")])
    for pr in world.projects:
        org, lead = world.org(pr.org), world.person(pr.lead)
        # 填充段落的主语用「该项目组」，不出现项目名，指代说明只在开头出现一次
        add(f"{pr.name}项目结项报告.md", f"{pr.name}项目结项报告", "project", "该项目组",
            [(f"project_alias:{pr.id}", f"本报告中的「该项目」均指{pr.name}项目。"),
             (f"project_lead:{pr.id}", f"{pr.name}项目的负责人是{org.name}的{lead.name}。"),
             (f"project_budget:{pr.id}", f"{pr.name}项目由{org.name}承担，总预算为{pr.budget}万元。"),
             (f"project_dates:{pr.id}", f"{pr.name}项目于{_date(pr.start)}启动，{_date(pr.end)}结项。")],
            late=[(f"project_deliverables:{pr.id}", f"该项目最终交付了{pr.deliverables}个功能模块，全部通过验收。")])
        if pr.budget_revised:
            add(f"关于调整{pr.name}项目预算的通知（2024年）.md", f"关于调整{pr.name}项目预算的通知", "notice", "项目管理办公室",
                [(f"project_budget_revised:{pr.id}",
                  f"经批准，{pr.name}项目总预算由{pr.budget}万元调整为{pr.budget_revised}万元，自本通知印发之日起执行。")])
    return DocSet(texts=texts, facts=facts)

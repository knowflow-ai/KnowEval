"""造世界：按随机种子确定性地生成人物、机构、项目与它们之间的关系。

世界里刻意放进干扰：一组同名人物（不同机构）、一家机构的成立年份在旧宣传册里写错、
一对预算只差一位数的项目。名字都是虚构的，大模型没在预训练里见过，答对只能靠检索。
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from pydantic import BaseModel

SURNAMES = list("沈韩周林许顾陆苏陈温秦叶程宋江严梁谢唐方")
GIVEN = ["墨", "知微", "临", "听澜", "远舟", "若晴", "既明", "一川", "清和", "思远", "砚秋", "怀瑾", "望舒", "南乔",
         "景行", "书宁", "亦安", "云深", "子衿", "明澈", "晏清", "以宁", "疏影", "嘉树"]
ORG_HEADS = ["岚图", "澄观", "启衡", "云栖", "望川", "青岑", "鹤鸣", "松岩", "朗曜", "知行"]
ORG_KINDS = ["研究院", "实验室", "科技有限公司", "数据中心", "工程研究所"]
PROJECT_NAMES = ["潮汐", "北辰", "岚港", "启明", "星槎", "玄鸟", "长庚", "沧浪", "天枢", "瀚海", "飞廉", "青鸾", "白泽", "烛龙"]
UNIVERSITIES = ["江陵大学", "临川大学", "洛溪大学", "清河理工大学", "南屿大学", "西岭大学", "东湖科技大学"]
ROLES = ["首席架构师", "研究员", "高级工程师", "技术总监", "项目经理", "数据科学家", "产品负责人"]


class Person(BaseModel):
    id: str
    name: str
    org: str
    role: str
    university: str


class Org(BaseModel):
    id: str
    name: str
    founder: str
    founded_year: int
    founded_year_old: int | None = None  # 旧宣传册里写错的年份（版本冲突干扰）


class Project(BaseModel):
    id: str
    name: str
    org: str
    lead: str
    budget: int  # 万元
    start: date
    end: date
    deliverables: int = 0  # 交付的功能模块数，写在报告末尾、只用「该项目」指代（分块干扰）
    budget_revised: int | None = None  # 后发的预算调整通知里的新预算（版本冲突干扰）


class World(BaseModel):
    seed: int
    people: list[Person]
    orgs: list[Org]
    projects: list[Project]
    spare_project_names: list[str]  # 世界里不存在的项目名，出错误前提题用

    def person(self, pid: str) -> Person:
        return next(p for p in self.people if p.id == pid)

    def org(self, oid: str) -> Org:
        return next(o for o in self.orgs if o.id == oid)


def _month_add(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, min(d.day, 28))


def build_world(seed: int, n_orgs: int = 4, n_people: int = 12, n_projects: int = 6) -> World:
    if n_people < n_orgs * 2 or n_orgs < 2 or n_projects < 2:
        raise ValueError("至少 2 家机构、每家 2 人、2 个项目")
    rng = random.Random(seed)
    names = [s + g for s in SURNAMES for g in GIVEN]
    rng.shuffle(names)
    org_names = [h + k for h in ORG_HEADS for k in ORG_KINDS]
    rng.shuffle(org_names)

    orgs = [Org(id=f"o{i}", name=org_names[i], founder="", founded_year=rng.randint(1998, 2018)) for i in range(n_orgs)]
    people: list[Person] = []
    for i in range(n_people):
        org = orgs[i % n_orgs]
        people.append(Person(id=f"p{i}", name=names[i], org=org.id, role=rng.choice(ROLES), university=rng.choice(UNIVERSITIES)))
    # 同名干扰：最后一人改成与另一家机构某人同名
    twin = next(p for p in people if p.org != people[-1].org)
    people[-1] = people[-1].model_copy(update={"name": twin.name})
    # 每家机构的创办人取自本机构成员（名字唯一的人，免得创办人本身有歧义）
    counts = {p.name: sum(q.name == p.name for q in people) for p in people}
    for o in orgs:
        members = [p for p in people if p.org == o.id and counts[p.name] == 1]
        o.founder = members[0].id
    # 版本冲突：第一家机构的旧宣传册把成立年份写早了两年
    orgs[0].founded_year_old = orgs[0].founded_year - 2

    pnames = PROJECT_NAMES[:]
    rng.shuffle(pnames)
    projects = []
    for i in range(n_projects):
        org = orgs[i % n_orgs]
        lead = rng.choice([p for p in people if p.org == org.id])
        start = date(2019, 1, 1) + timedelta(days=rng.randint(0, 1500))
        projects.append(Project(id=f"j{i}", name=pnames[i], org=org.id, lead=lead.id,
                                budget=rng.randint(80, 900), start=start, end=_month_add(start, rng.randint(8, 30)),
                                deliverables=rng.randint(6, 40)))
    # 近似数字干扰：第二个项目的预算与第一个只差一位
    projects[1] = projects[1].model_copy(update={"budget": projects[0].budget + 10 if projects[0].budget < 890 else projects[0].budget - 10})
    # 版本冲突：第三个项目（有的话）后来发了预算调整通知，新预算与结项报告里的不同
    if len(projects) >= 3:
        old = projects[2].budget
        projects[2] = projects[2].model_copy(update={"budget_revised": old + rng.choice([30, 50, 120, 200])})
    return World(seed=seed, people=people, orgs=orgs, projects=projects, spare_project_names=pnames[n_projects:])

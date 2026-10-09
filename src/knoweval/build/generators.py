"""按题型生成候选题。一次看一个分块只能出单跳题，所以不同题型用不同方法：

- F / T / L：挑一页出题，T 挑数字密集的页，L 只从长文档的后半部分取材
- C：模型只给出「取哪几个数、怎么算」，答案由程序计算（公式只允许四则运算）
- M：先找两份文档共有的少见词组当桥，再让模型出一道必须两页合起来才能答的题
- N：给一页作为主题锚点，让模型出相近但文档里没有答案的题，是否真的查不到交给 verify
- K：同一文件的新旧两版（文件名只差年份或版本号）先程序比对出改动过的段落，再让模型就这处改动出题，
  标准答案取新版、旧版的值作干扰项

生成的每条证据都立刻逐字核对；找不到的候选题直接打回，不进入人工复核。
"""

from __future__ import annotations

import ast
import difflib
import json
import operator
import random
import re
from collections import Counter
from typing import Any

from knoweval.build.project import Candidate, Project
from knoweval.dataset import normalize_for_quote
from knoweval.judge.llm_judge import ChatClient
from knoweval.schema import Evidence

_THINK = re.compile(r"<think>.*?</think>", re.S)
_SYSTEM = "你是中文评测数据集的出题助手。只根据给出的原文出题，答案必须能从原文直接得到，严格按要求输出一个 JSON 对象，不要输出别的内容。"
AUTO = {"F", "T", "L", "C", "M", "N", "K"}
_STAGE = {"F": "retrieve", "T": "retrieve", "L": "chunk", "C": "reason", "M": "reason", "N": "honesty", "K": "reason"}
# 换说法题的比例：真实用户不会照着原文措辞提问（数据集设计 §5 要求带 paraphrase 的题不少于 30%）
PARAPHRASE_RATE = 0.4


def parse_json_object(raw: str) -> dict:
    text = _THINK.sub("", raw)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"模型输出里没有 JSON：{raw[:200]}")
    return json.loads(text[start:end + 1])


async def ask_json(client: ChatClient, model: str, prompt: str) -> dict:
    return parse_json_object(await client.complete([{"role": "system", "content": _SYSTEM},
                                                     {"role": "user", "content": prompt}], model))


# ---- 公式：只允许数字、变量名与四则运算 ----
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def safe_eval(formula: str, values: dict[str, float]) -> float:
    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ev(node.operand)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id in values:
            return float(values[node.id])
        raise ValueError(f"公式里有不允许的内容：{ast.dump(node)[:60]}")
    return ev(ast.parse(formula, mode="eval"))


# 计数单位：答案用这些单位时必须是整数
COUNT_UNITS = {"人", "名", "位", "个", "家", "部", "次", "件", "条", "份", "台", "户", "所", "款", "项", "篇", "集"}


def eval_formula(formula: str, values: dict[str, float]) -> float:
    """模型常把原文标签当变量名（「40-59岁」「月/年会员」），里面的减号、斜杠会被当成运算符。
    先按名字从长到短换成占位符再求值。"""
    names = sorted(values, key=len, reverse=True)
    if not names:
        return safe_eval(formula, {})
    slots = {name: f"v{i}" for i, name in enumerate(names)}
    # 一次替换完：逐个 replace 时，叫「v」的变量会把已经换好的占位符再换一遍
    formula = re.sub("|".join(map(re.escape, names)), lambda m: slots[m.group()], formula)
    return safe_eval(formula, {slots[n]: values[n] for n in names})


def to_number(v: Any) -> float:
    """模型摘出的数常带千分位、单位或百分号（「189,163,654,064.64」「120条/秒」「25.42%」）。取出其中的数再转。"""
    if isinstance(v, (int, float)):
        return float(v)
    text = re.sub(r"(?<=\d)[,，](?=\d{3})", "", str(v))
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        raise ValueError(f"取不出数：{v}")
    return float(m.group())


def _fmt_number(x: float) -> str:
    x = round(x, 4)
    return str(int(x)) if x == int(x) else str(x)


# ---- 挑页 ----
def _pages(project: Project, min_chars: int = 60) -> list[tuple[str, int | None, str]]:
    return [(doc, p.page, p.text) for doc, pages in project.pages.items() for p in pages if len(p.text.strip()) >= min_chars]


def _digit_density(text: str) -> float:
    return sum(ch.isdigit() for ch in text) / max(1, len(text))


def _pick(project: Project, qtype: str, rng: random.Random) -> tuple[str, int | None, str]:
    pool = _pages(project)
    if not pool:
        raise ValueError("项目里没有可出题的页面（参考解析没有取到文字）")
    if qtype == "T":
        pool = sorted(pool, key=lambda x: _digit_density(x[2]), reverse=True)[: max(1, len(pool) // 3)]
    elif qtype == "L":
        longest = max(project.pages, key=lambda d: len(project.pages[d]))
        pages = [x for x in pool if x[0] == longest]
        pool = pages[len(pages) // 2:] or pages
    return rng.choice(pool)


def _bridge_pair(project: Project, rng: random.Random) -> tuple[tuple, tuple, str]:
    """两份文档里各挑一页，共有一个只出现在这两份文档里的 3 到 5 字词组。"""
    pool = _pages(project)
    grams_by_doc: dict[str, set[str]] = {}
    for doc, _, text in pool:
        clean = re.sub(r"\s+", "", text)
        grams_by_doc.setdefault(doc, set()).update(clean[i:i + n] for n in (3, 4, 5) for i in range(len(clean) - n + 1)
                                                  if re.fullmatch(r"[一-鿿]+", clean[i:i + n]))
    df = Counter(g for grams in grams_by_doc.values() for g in grams)
    rare = sorted(g for g, n in df.items() if n == 2)
    rng.shuffle(rare)
    for g in rare:
        hits = [x for x in pool if g in re.sub(r"\s+", "", x[2])]
        docs = {x[0] for x in hits}
        if len(docs) == 2:
            a = next(x for x in hits if x[0] == sorted(docs)[0])
            b = next(x for x in hits if x[0] == sorted(docs)[1])
            return a, b, g
    raise ValueError("找不到两份文档共有的桥接词组，跨文档多跳题需要文档之间有共同实体")


def _excerpt(text: str, limit: int = 2500) -> str:
    return text if len(text) <= limit else text[:limit]


# ---- 各题型 ----
_SINGLE_PROMPT = {
    "F": "出一道单跳事实题：答案是原文里的一个具体事实（数值、名称、日期、职务等）。",
    "T": "出一道表格数值题：答案取自原文表格里的某个单元格，问题要说清楚是哪一行哪一列。",
    "L": "出一道长文档定位题：答案藏在这一页的细节里（附录、注释、默认配置等），问题不要照抄原文措辞。",
}


# 所有带标准答案的题型都要满足：答案唯一、问法对得上答案、引文能证明答案、只考正文内容。
# 来自真实评测里逐题排查出的出题毛病：歧义题干、问人数给比例、只摘一个数的引文、从联系方式或目录页出题。
QUALITY_RULES = (
    "题干要带上能唯一确定答案的限定条件：原文里同类的数字或说法有多个（不同年份、口径、分组、单位，"
    "如总样本、有付费用户、无付费用户；或表格里不同的列）时，写清问的是哪一个，让别人照着题干只能答出这一个答案。\n"
    "问法要和答案对得上：答案是人数就问多少人，答案是比例就问占比多少，答案是名称就问叫什么。\n"
    "引文必须逐字摘录，并包含答案对应的主语或标签（如「2023年市场规模为358.6亿元」），不能只摘一个孤立的数字。"
    "原文是图表时，先确认数字属于哪个标签，对不准就换一个事实出题。\n"
    "只考正文里的业务内容。封面、目录、联系方式、版权和免责声明、页眉页脚不出题；"
    '这一页只有这类内容时，只输出 {"skip": 页面类型，如「目录」「联系方式」}。\n'
)


def _skipped(data: dict, doc: str, page: int | None) -> None:
    """模型说这一页没有可考的正文（封面、目录、联系方式等）：这道题不出，由上层记一笔。"""
    if data.get("skip"):
        raise ValueError(f"{doc}{f' 第 {page} 页' if page else ''}没有可出题的正文：{data['skip']}")


_PARAPHRASE = "问题要用普通用户的口语说法，尽量不与原文共享关键词（例如原文写「无线覆盖半径」，就问「最远能传多远」）。\n"


async def _single(project: Project, client: ChatClient, model: str, qtype: str, rng: random.Random) -> Candidate:
    doc, page, text = _pick(project, qtype, rng)
    paraphrase = rng.random() < PARAPHRASE_RATE
    data = await ask_json(client, model, (
        f"题型：{qtype}\n{_SINGLE_PROMPT[qtype]}\n{QUALITY_RULES}{_PARAPHRASE if paraphrase else ''}"
        '输出 JSON：{"question": 问题, "answer": 标准答案, "answer_type": "text|number|date|list", '
        '"unit": 数值单位或 null, "aliases": [也算对的写法], "quote": 原文中能支撑答案的一句话，必须逐字摘录}\n'
        f"数值题的 answer 只写纯数字；日期题写 YYYY-MM-DD。\n原文（{doc}{f' 第 {page} 页' if page else ''}）：\n{_excerpt(text)}"))
    _skipped(data, doc, page)
    answer_type = data.get("answer_type") or "text"
    return Candidate(id=project.next_id(qtype.lower()), qtype=[qtype], question=data["question"], answer=str(data.get("answer", "")),
                     answer_type=answer_type, unit=data.get("unit"), tolerance=0 if answer_type == "number" else None,
                     answer_aliases=data.get("aliases") or [], evidence=[Evidence(doc=doc, page=page, quote=data.get("quote", ""),
                     modality="table" if qtype == "T" else "text")], stage=_STAGE[qtype], generator="single_page",
                     traps=["paraphrase"] if paraphrase else [])


async def _computed(project: Project, client: ChatClient, model: str, rng: random.Random) -> Candidate:
    doc, page, text = _pick(project, "T", rng)
    data = await ask_json(client, model, (
        "题型：C\n出一道计算题：需要对原文里的两到三个数做加减乘除才能得到答案。不要自己算出答案。\n"
        "运算要有实际意义：同一口径、同一对象的数才能相加相减（如两年的销售额求增长）；"
        "不同对象各自的占比、不同样本的百分比相加没有意义，不要出。"
        "也不要用占比乘样本量去推算人数：原文没有统计过这个数，算出来常是小数。\n"
        f"{QUALITY_RULES}"
        '输出 JSON：{"question": 问题, "unit": 单位或 null, "operands": [{"name": 变量名, "value": 原文里的数, '
        '"quote": 含这个数的原文句子，逐字摘录}], "formula": 只含变量名与 + - * / 的公式}\n'
        "变量名用 a、b、c 这样的英文字母；百分数参与乘除时在公式里除以 100。\n"
        f"原文（{doc}{f' 第 {page} 页' if page else ''}）：\n{_excerpt(text)}"))
    _skipped(data, doc, page)
    operands = data.get("operands") or []
    evidence = [Evidence(doc=doc, page=page, quote=o.get("quote", ""), modality="table") for o in operands]
    base = dict(id=project.next_id("c"), qtype=["C"], question=data.get("question", ""), answer_type="number",
                unit=data.get("unit"), tolerance=0, evidence=evidence, stage="reason", generator="computed")
    try:
        value = eval_formula(str(data.get("formula", "")), {str(o["name"]): to_number(o["value"]) for o in operands})
    except (ValueError, KeyError, TypeError, SyntaxError, ZeroDivisionError) as e:
        return Candidate(**base, status="rejected_auto", notes=[f"公式无法计算：{e}"])
    unit = str(data.get("unit") or "")
    if unit in COUNT_UNITS and abs(value - round(value)) > 1e-6:
        # 计数不可能是小数：多半是拿占比乘样本量推算出来的，原文没有这个统计（提示词里禁过，模型照出，所以在这里拦）
        return Candidate(**base, answer=_fmt_number(value), status="rejected_auto", checks={"formula": data.get("formula")},
                         notes=[f"答案是{_fmt_number(value)}{unit}，计数不会是小数：原文没有这个统计，像是用占比推算出来的"])
    return Candidate(**base, answer=_fmt_number(value), checks={"formula": data.get("formula")})


async def _bridge(project: Project, client: ChatClient, model: str, rng: random.Random) -> Candidate:
    (doc_a, page_a, text_a), (doc_b, page_b, text_b), bridge = _bridge_pair(project, rng)
    data = await ask_json(client, model, (
        f"题型：M\n两段原文通过「{bridge}」相连。出一道必须同时用到两段原文才能回答的两跳问题，问题里不要出现「{bridge}」。\n"
        f"{QUALITY_RULES}"
        '输出 JSON：{"question": 问题, "answer": 答案, "answer_type": "text|number|date", '
        '"quotes": [原文一中支撑第一跳的句子, 原文二中支撑第二跳的句子]（都必须逐字摘录）}\n'
        f"原文一（{doc_a}）：\n{_excerpt(text_a, 1500)}\n\n原文二（{doc_b}）：\n{_excerpt(text_b, 1500)}"))
    quotes = (data.get("quotes") or []) + ["", ""]
    answer_type = data.get("answer_type") or "text"
    return Candidate(id=project.next_id("m"), qtype=["M"], question=data.get("question", ""), answer=str(data.get("answer", "")),
                     answer_type=answer_type, tolerance=0 if answer_type == "number" else None, hops=2, hop_kind="chain",
                     evidence=[Evidence(doc=doc_a, page=page_a, quote=quotes[0]), Evidence(doc=doc_b, page=page_b, quote=quotes[1])],
                     stage="reason", generator="bridge", checks={"bridge": bridge})


async def _no_answer(project: Project, client: ChatClient, model: str, rng: random.Random) -> Candidate:
    doc, page, text = _pick(project, "F", rng)
    data = await ask_json(client, model, (
        "题型：N\n出一道与原文主题相近、但原文里没有答案的问题。优先出两类：近邻缺失（原文谈到相关内容但没回答这个问题）"
        "与错误前提（问题预设了原文里不存在的东西，比如不存在的版本或型号）。\n"
        "不要出「是否」「有没有」「有无」这类是非题：回答「没有」既可能是在说资料里找不到，也可能是在下结论，判不清。"
        "问一个原文里查不到的具体事实（数值、名称、做法）。\n"
        '输出 JSON：{"question": 问题, "n_kind": "near_miss|false_premise|unrelated"}\n'
        f"原文（{doc}）：\n{_excerpt(text)}"))
    return Candidate(id=project.next_id("n"), qtype=["N"], question=data.get("question", ""), answer="", answer_type="text",
                     n_kind=data.get("n_kind") or "near_miss", stage="honesty", generator="no_answer", checks={"anchor": doc})


# ---- 版本冲突 ----
_VERSION_TOKEN = re.compile(r"（\d{4}年）|\(\d{4}\)|\d+\.\d+(?:\.\d+)?|\d{4}年")


def _version_key(name: str) -> tuple[str, tuple]:
    """文件名去掉年份或版本号后的「主题」，以及用来排新旧的版本元组。"""
    tokens = _VERSION_TOKEN.findall(name)
    order = tuple(int(n) for t in tokens for n in re.findall(r"\d+", t))
    return _VERSION_TOKEN.sub("", name), order


def version_pairs(project: Project) -> list[tuple[str, str]]:
    """同一主题的（旧版，新版）文档对。"""
    groups: dict[str, list[tuple[tuple, str]]] = {}
    for d in project.docs:
        topic, order = _version_key(d.name)
        if order:
            groups.setdefault(topic, []).append((order, d.name))
    pairs = []
    for items in groups.values():
        items.sort()
        pairs += [(items[i][1], items[i + 1][1]) for i in range(len(items) - 1)]
    return pairs


def _full_text(project: Project, doc: str) -> str:
    return "\n\n".join(p.text for p in project.pages.get(doc, []))


def _paragraphs(text: str) -> list[str]:
    """参与比对的段落：去掉标题行（新旧版标题只差版本号）和文末的来源、许可说明。"""
    out = []
    for p in text.split("\n\n"):
        p = p.strip()
        if len(p) < 20 or p.startswith("#") or p.startswith(("来源：", "许可：")):
            continue
        out.append(p)
    return out


def changed_paragraphs(old: str, new: str) -> list[tuple[str, str]]:
    """新旧两版之间改写过的段落对（只取一对一替换、且确实有实质差别的）。"""
    a, b = _paragraphs(old), _paragraphs(new)
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag != "replace":
            continue
        for x, y in zip(a[i1:i2], b[j1:j2]):
            ratio = difflib.SequenceMatcher(a=x, b=y).ratio()
            if 0.3 < ratio < 0.97:  # 太像是标点改动，太不像是不同段落错配
                out.append((x, y))
    return out


async def _version(project: Project, client: ChatClient, model: str, rng: random.Random) -> Candidate:
    pairs = version_pairs(project)
    if not pairs:
        raise ValueError("项目里没有新旧两版的文档（文件名只差年份或版本号），出不了版本冲突题")
    candidates = []
    for old_doc, new_doc in pairs:
        diffs = changed_paragraphs(_full_text(project, old_doc), _full_text(project, new_doc))
        candidates += [(old_doc, new_doc, x, y) for x, y in diffs]
    if not candidates:
        raise ValueError("新旧两版之间没有找到改写过的段落")
    old_doc, new_doc, old_para, new_para = rng.choice(candidates)
    data = await ask_json(client, model, (
        "题型：K\n同一份文件有新旧两版，下面是同一处规定在两版里的写法。出一道问「现在（新版）怎么规定」的问题，"
        "问题里点明是哪份文件或哪个产品，但不要写出版本号或年份，答案以新版为准。\n"
        f"{QUALITY_RULES}"
        '输出 JSON：{"question": 问题, "answer": 新版的答案, "answer_type": "text|number|date", "unit": 单位或 null, '
        '"old_answer": 旧版对应的答案, "new_quote": 新版里支撑答案的一句话（逐字摘录）, "old_quote": 旧版里对应的一句话（逐字摘录）}\n'
        f"旧版（{old_doc}）：\n{_excerpt(old_para, 1500)}\n\n新版（{new_doc}）：\n{_excerpt(new_para, 1500)}"))
    answer_type = data.get("answer_type") or "text"
    answer = str(data.get("answer", ""))
    old_answer = str(data.get("old_answer") or "")
    c = Candidate(id=project.next_id("k"), qtype=["K"], question=data.get("question", ""), answer=answer,
                  answer_type=answer_type, unit=data.get("unit"), tolerance=0 if answer_type == "number" else None,
                  answer_distractors=[old_answer] if old_answer else [],
                  hops=2, evidence=[Evidence(doc=new_doc, quote=data.get("new_quote", "")),
                                    Evidence(doc=old_doc, quote=data.get("old_quote", ""))],
                  stage="reason", traps=["version"], generator="version_diff")
    if not old_answer or normalize_for_quote(old_answer) == normalize_for_quote(answer):
        # 改动只是格式或措辞，两版答案相同，构不成版本冲突
        return c.model_copy(update={"status": "rejected_auto", "notes": ["新旧两版的答案相同，不是版本冲突"]})
    return c


async def _one(project: Project, client: ChatClient, model: str, qtype: str, rng: random.Random) -> Candidate:
    if qtype in ("F", "T", "L"):
        return await _single(project, client, model, qtype, rng)
    if qtype == "C":
        return await _computed(project, client, model, rng)
    if qtype == "M":
        return await _bridge(project, client, model, rng)
    if qtype == "K":
        return await _version(project, client, model, rng)
    return await _no_answer(project, client, model, rng)


async def generate(project: Project, client: ChatClient, model: str, mix: dict[str, int], seed: int = 0) -> list[Candidate]:
    unsupported = set(mix) - AUTO
    if unsupported:
        raise ValueError(f"题型 {sorted(unsupported)} 暂不支持自动生成，请在复核界面手工添加")
    rng = random.Random(seed)
    made: list[Candidate] = []
    for qtype, n in mix.items():
        for _ in range(n):
            try:
                c = await _one(project, client, model, qtype, rng)
            except (ValueError, KeyError, TypeError) as e:
                # 单题失败（模型输出不合格式、找不到桥接词、没有可比对的段落）只记一笔，不让整批出题中断
                project.log("generate_failed", qtype, by="auto", note=f"{type(e).__name__}: {e}"[:300])
                continue
            if c.status != "rejected_auto" and c.evidence:
                c = project.check_quotes(c)
            if c.answer_type == "number" and c.status != "rejected_auto":
                try:
                    float(c.answer)
                except ValueError:
                    c = c.model_copy(update={"status": "rejected_auto", "notes": c.notes + [f"数值题的答案不是纯数字：{c.answer}"]})
            project.add(c)
            made.append(c)
    return made


def parse_mix(spec: str) -> dict[str, int]:
    """「F:30,T:20,N:10」→ {"F": 30, "T": 20, "N": 10}"""
    out: dict[str, Any] = {}
    for part in spec.split(","):
        if part.strip():
            k, v = part.split(":")
            out[k.strip().upper()] = int(v)
    return out

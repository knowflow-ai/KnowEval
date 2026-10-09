"""通用 HTTP 适配器的请求模板：占位符代入，以及参评配置的参数包怎么进请求。

参数包（问答参数 chat、建库参数 dataset）有两条路进请求：
- 模板里用 {{chat.x}}、{{dataset.x.y}} 引用的，代入占位符（路径、请求头、请求体都行）；
- 其余的深合并进请求体，GET 请求没有请求体，放进查询串。
评测台不理解这些参数，只原样传过去、原样记录。
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from knoweval.adapters.base import AdapterError

PLACEHOLDER = re.compile(r"\{\{\s*([\w.-]+)\s*\}\}")
PACKS = ("chat", "dataset")


def as_text(value: Any) -> str:
    """嵌进字符串里的值：字符串原样，其余按 JSON 写（true、null，不是 Python 的 True、None）。"""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def render(template: Any, values: dict[str, Any]) -> Any:
    if isinstance(template, str):
        # 整串就是一个占位符时保留原类型：参数包里的数字、布尔值不要变成字符串
        whole = PLACEHOLDER.fullmatch(template)
        if whole and whole.group(1) in values:
            return values[whole.group(1)]
        return PLACEHOLDER.sub(lambda m: as_text(values[m.group(1)]) if m.group(1) in values else m.group(0), template)
    if isinstance(template, dict):
        return {k: render(v, values) for k, v in template.items()}
    if isinstance(template, list):
        return [render(v, values) for v in template]
    return template


def param_refs(template: Any) -> set[str]:
    """模板里引用参数包的占位符，如 {{chat.app_id}}，返回 {"chat.app_id"}。"""
    if isinstance(template, str):
        return {m for m in PLACEHOLDER.findall(template) if "." in m and m.split(".", 1)[0] in PACKS}
    if isinstance(template, dict):
        return set().union(*(param_refs(v) for v in template.values()))
    if isinstance(template, list):
        return set().union(*(param_refs(v) for v in template))
    return set()


_MISSING = object()


def _lookup(pack: dict[str, Any], dotted: str) -> Any:
    node: Any = pack
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return _MISSING
        node = node[key]
    return node


def _without(pack: dict[str, Any], dotted: str) -> dict[str, Any]:
    """去掉一个（可能嵌套的）参数，返回新字典；去空了的上层也一并去掉。"""
    head, _, rest = dotted.partition(".")
    if head not in pack:
        return pack
    if not rest:
        return {k: v for k, v in pack.items() if k != head}
    inner = pack[head]
    if not isinstance(inner, dict):
        return pack
    trimmed = _without(inner, rest)
    return {k: v for k, v in pack.items() if k != head} if not trimmed else {**pack, head: trimmed}


def resolve_params(templates: Any, packs: dict[str, dict[str, Any]], *, missing_hint: str = "这份配置的参数里没有"
                   ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """参数包拆成两部分：占位符要代入的值（键如 chat.app_id），和剩下要合并进请求的参数。

    只看会真正发出的那些模板。引用了参数包里没有的参数就报错：不然占位符会原样发给被测系统，
    几份配置跑出来其实是同一个请求。
    """
    values: dict[str, Any] = {}
    merge = {name: dict(packs.get(name) or {}) for name in PACKS}
    missing = []
    for ref in sorted(param_refs(templates)):
        name, dotted = ref.split(".", 1)
        value = _lookup(packs.get(name) or {}, dotted)
        if value is _MISSING:
            missing.append(ref)
            continue
        values[ref] = value
        merge[name] = _without(merge[name], dotted)
    if missing:
        raise AdapterError(f"接口模板引用了 {'、'.join('{{' + m + '}}' for m in missing)}，但{missing_hint}")
    return values, merge


def deep_merge(base: Any, extra: dict[str, Any]) -> Any:
    """参数深合并进请求体，同名字段以参数为准。没写 body 时请求体就是参数。"""
    if not extra:
        return base
    if base is None:
        return dict(extra)
    if not isinstance(base, dict):
        raise AdapterError("请求体不是 JSON 对象，参数没法合并进去；请改用 {{chat.x}} 这类占位符放参数")
    merged = dict(base)
    for k, v in extra.items():
        merged[k] = deep_merge(merged.get(k), v) if isinstance(v, dict) and isinstance(merged.get(k), dict) else v
    return merged


def check_query_params(method: str, extra: dict[str, Any], what: str) -> None:
    """GET 请求的参数只能放查询串，嵌套的放不进去。建适配器时就检查，不要跑到一半才发现。"""
    if method.upper() == "GET" and any(isinstance(v, (dict, list)) for v in extra.values()):
        raise AdapterError(f"{what}接口是 GET，参数只能放进查询串，嵌套的参数放不进去；"
                           "请在接口模板里用 {{chat.x}} 这类占位符引用它")


def request_kwargs(method: str, path: str, body: Any, extra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """请求体与没被占位符用掉的参数怎么发：GET 追加到路径的查询串（保留路径里原有的），其余合并进 JSON 请求体。"""
    if method.upper() == "GET" and extra:
        url = httpx.URL(path).copy_merge_params({k: as_text(v) for k, v in extra.items()})
        return str(url), ({"json": body} if body is not None else {})
    return path, {"json": deep_merge(body, extra)}


def header_values(headers: dict[str, Any]) -> dict[str, str]:
    return {k: as_text(v) for k, v in headers.items()}

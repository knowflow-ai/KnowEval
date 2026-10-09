"""测试集出自哪个系统的哪些知识库：从知识库出题的测试集才有。

「测已有助手」按它把测试集配给挂了这些库的助手；别的测试集（上传文档、导入题目、官方数据集）没有来源库，
要测就走「完整测试」，由评测台按建库配置建库。
"""

from __future__ import annotations


def source_kb(source: dict | None) -> dict | None:
    """{system_id, system, kbs: [{id, name}]}；不是从知识库出的题返回 None。"""
    if not source or source.get("kind") != "kb":
        return None
    # 取完文档后 source.kbs 带库名和文档数；还在取时用建测试集时页面给的库名
    names = source.get("kb_names") or {}
    kbs = source.get("kbs") or [{"id": k, "name": names.get(k) or k} for k in source.get("kb_ids") or []]
    return {"system_id": source.get("system_id"), "system": source.get("system"),
            "kbs": [{"id": k["id"], "name": k.get("name") or k["id"]} for k in kbs]}

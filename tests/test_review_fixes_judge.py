"""代码审查（2026-10-03）指出的问题里，判分改为全交裁判模型后仍然有效的部分：拒答短语、引用覆盖。"""

import pytest

from knoweval.judge.abstain import detect_abstain
from knoweval.judge.citation import quote_coverage


# ---- 4. 拒答短语（试问检查用它粗看文档有没有导入）----

def test_specific_refusal_phrases_only():
    assert not detect_abstain("该条款没有相关例外，适用于全体员工").abstained
    assert detect_abstain("Sorry! No relevant content was found in the knowledge base!").abstained
    assert detect_abstain("资料中暂无相关信息").abstained
    assert detect_abstain("文档未提供相关数据").abstained


# ---- 9. 引用核对：HTML 表格分块与全角标点 ----

def test_coverage_handles_html_table_chunks_and_fullwidth_punctuation():
    chunk = "<table><tr><td>v2.0</td><td>2023-06-15</td><td>80 万条/秒</td><td>45 ms</td><td>128</td></tr></table>"
    assert quote_coverage("v2.0 2023-06-15 80 万条/秒 45 ms 128", chunk) == pytest.approx(1.0)
    assert quote_coverage("发布日期：2025 年", "发布日期:2025 年 3 月") == pytest.approx(1.0)

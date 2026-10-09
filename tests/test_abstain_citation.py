"""拒答识别与引用核对。"""

import pytest

from knoweval.judge.abstain import detect_abstain
from knoweval.judge.citation import check_citations, quote_coverage
from knoweval.schema import Citation, Evidence


@pytest.mark.parametrize(
    "text",
    [
        "知识库中未找到您要的答案！",
        "根据提供的资料，没有找到关于 GPU 加速的信息。",
        "文档中没有提及岚图研究院的总部所在地，因此无法回答。",
        "抱歉，我不知道。",
        "现有资料无法确定沈墨毕业于哪所大学。",
        "The knowledge base does not contain this information. Not found.",
    ],
)
def test_detects_refusals(text):
    assert detect_abstain(text).abstained


@pytest.mark.parametrize(
    "text",
    [
        "岚图研究院由韩知微创办。",
        "潮汐 v3.0 的默认管理端口是 9400。",
        "潮汐系统不支持 GPU 加速。",
    ],
)
def test_conclusions_are_not_refusals(text):
    assert not detect_abstain(text).abstained


def test_quote_coverage_measures_how_much_of_the_quote_the_chunk_contains():
    quote = "沈墨目前任职于岚图研究院，担任首席架构师"
    chunk = "职业经历\n沈墨目前任职于岚图研究院，担任首席架构师。他所主导的多个实时计算项目……"
    assert quote_coverage(quote, chunk) == pytest.approx(1.0)
    assert quote_coverage(quote, "岚图研究院是一家独立研究机构") < 0.6


EVIDENCE = [
    Evidence(doc="02-沈墨.pdf", page=1, quote="沈墨目前任职于岚图研究院"),
    Evidence(doc="03-岚图研究院.pdf", page=1, quote="岚图研究院由计算机科学家韩知微于二〇一五年创办"),
]


def test_citation_matches_when_doc_and_text_line_up():
    cites = [
        Citation(doc="03-岚图研究院.pdf", page=1, text="岚图研究院由计算机科学家韩知微于二〇一五年创办。"),
        Citation(doc="05-潮汐v3.0发布公告.pdf", page=1, text="默认管理端口由 9300 调整为 9400"),
    ]
    result = check_citations(cites, EVIDENCE)
    assert result.verifiable is True
    assert result.matched == 1
    assert result.total == 2


def test_citation_doc_name_match_ignores_extension_and_case():
    cites = [Citation(doc="02-沈墨", text="沈墨目前任职于岚图研究院，担任首席架构师")]
    assert check_citations(cites, EVIDENCE).verifiable is True


def test_right_text_in_wrong_doc_does_not_count():
    cites = [Citation(doc="06-岚图研究院2024年度报告.pdf", text="沈墨目前任职于岚图研究院")]
    assert check_citations(cites, EVIDENCE).verifiable is False


def test_no_citations_is_not_verifiable():
    result = check_citations([], EVIDENCE)
    assert result.verifiable is False
    assert result.total == 0

"""参考解析按行重排：图表里各自独立的标签与数值要落在同一行，多行段落保持原样。"""

import pymupdf

from knoweval.build.refparse import parse_document


def test_chart_labels_and_values_land_on_the_same_row(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    font = "china-s"
    # 标题先画在下方、数值后画在上方，内容流顺序与版面顺序相反
    page.insert_text((60, 300), "图表：各平台爱情题材占比", fontname=font)
    for y, value in zip((120, 160), ("34.5%", "58.8%")):
        page.insert_text((300, y), value, fontname=font)
    for y, label in zip((120, 160), ("快手", "优酷")):
        page.insert_text((60, y), label, fontname=font)
    path = tmp_path / "chart.pdf"
    doc.save(path)
    lines = parse_document(path)[0].text.splitlines()
    assert lines[:2] == ["快手  34.5%", "优酷  58.8%"]
    assert lines[-1] == "图表：各平台爱情题材占比"

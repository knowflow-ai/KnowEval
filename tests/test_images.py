"""图文答案核对（规范第 6 节「图文答案」）：感知哈希、适配器取图、I 类题判定与统计。"""

import io

import httpx
import pytest
from PIL import Image, ImageDraw

from knoweval.images import dhash, hamming, check_images
from knoweval.schema import Answer, ImageRef, Question


def _png(seed: int, size=(200, 120)) -> bytes:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    for i in range(6):
        x = (seed * 37 + i * 29) % size[0]
        d.rectangle([x, i * 18, x + 40, i * 18 + 15], fill=(seed * 40 % 255, 80, 160))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_dhash_is_stable_under_resize_and_jpeg():
    a = _png(1)
    im = Image.open(io.BytesIO(a)).resize((400, 240)).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=70)
    assert hamming(dhash(a), dhash(buf.getvalue())) <= 10
    assert hamming(dhash(a), dhash(_png(5))) > 10


def test_check_images_hit_and_misfit():
    want = dhash(_png(1))
    q = Question(id="q", domain="d", qtype=["I"], question="接线图？", answer="见图", answer_type="text",
                 evidence=[{"doc": "m.pdf", "page": 12, "modality": "image", "quote": "接线示意"}],
                 answer_images=[{"doc": "m.pdf", "page": 12, "phash": want}])
    a = Answer(text="如图", images=[ImageRef(doc="m.pdf", page=12, phash=dhash(_png(1))),
                                   ImageRef(doc="m.pdf", page=12, phash=dhash(_png(7))),
                                   ImageRef(doc="other.pdf", page=3, phash=dhash(_png(9)))])
    c = check_images(q, a)
    assert c.hit is True and c.matched == 1 and c.expected == 1
    assert c.unrelated == 1 and c.returned == 3   # 同页的第二张不算误配，other.pdf 那张算


def test_no_images_returned():
    q = Question(id="q", domain="d", qtype=["I"], question="?", answer="x", answer_type="text",
                 evidence=[{"doc": "m.pdf", "page": 1, "quote": "x"}], answer_images=[{"phash": "0" * 16}])
    c = check_images(q, Answer(text="没有图"))
    assert c.hit is False and c.returned == 0


async def test_ragflow_adapter_fetches_images_from_answer_and_cited_chunks():
    from knoweval.adapters.ragflow import RagflowAdapter
    png1, png2 = _png(1), _png(2)

    def handler(request):
        p = request.url.path
        if p == "/minio/kb1/fig.png":
            return httpx.Response(200, content=png1, headers={"content-type": "image/png"})
        if p == "/v1/document/image/kb1-img2":
            return httpx.Response(200, content=png2, headers={"content-type": "image/png"})
        if p == "/api/v1/chats/c/sessions":
            return httpx.Response(200, json={"code": 0, "data": {"id": "s"}})
        if p == "/api/v1/chats/c/completions":
            return httpx.Response(200, json={"code": 0, "data": {
                "answer": '接线如图 <img src="/minio/kb1/fig.png"> [ID:0]',
                "reference": {"chunks": [{"content": "接线示意", "document_name": "m.pdf",
                                          "positions": [[12, 0, 0, 0, 0]], "image_id": "kb1-img2"}]}}})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://rf")
    adapter = RagflowAdapter(base_url="http://rf", api_key="k", client=client)
    from knoweval.adapters.base import KbHandle
    ans = await adapter.ask(KbHandle(system="ragflow", ids={"chat_id": "c", "dataset_id": "d"}), "接线图？")
    hashes = {i.phash for i in ans.images}
    assert dhash(png1) in hashes and dhash(png2) in hashes
    chunk_img = [i for i in ans.images if i.phash == dhash(png2)][0]
    assert chunk_img.doc == "m.pdf" and chunk_img.page == 12


async def test_image_fetch_failure_is_recorded_not_fatal():
    from knoweval.adapters.ragflow import RagflowAdapter

    def handler(request):
        p = request.url.path
        if p.endswith("/sessions"):
            return httpx.Response(200, json={"code": 0, "data": {"id": "s"}})
        if p.endswith("/completions"):
            return httpx.Response(200, json={"code": 0, "data": {"answer": '<img src="/minio/x.png">', "reference": {}}})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://rf")
    from knoweval.adapters.base import KbHandle
    ans = await RagflowAdapter(base_url="http://rf", api_key="k", client=client).ask(
        KbHandle(system="ragflow", ids={"chat_id": "c", "dataset_id": "d"}), "q")
    assert len(ans.images) == 1 and ans.images[0].phash is None and ans.images[0].source == "/minio/x.png"


def test_summary_counts_image_metrics():
    from knoweval.images import ImageCheck
    from knoweval.judge.judge import Judgement
    from knoweval.schema import Verdict
    from knoweval.stats import compute_summary
    q = Question(id="q", domain="d", qtype=["I"], question="?", answer="x", answer_type="text",
                 evidence=[{"doc": "m.pdf", "page": 1, "quote": "x"}], answer_images=[{"phash": "0" * 16}])
    recs = [(q, Judgement(verdict=Verdict.CORRECT, reason="", method="rule",
                          images=ImageCheck(expected=1, matched=1, hit=True, returned=2, unrelated=1))),
            (q, Judgement(verdict=Verdict.WRONG, reason="", method="rule",
                          images=ImageCheck(expected=1, matched=0, hit=False, returned=0, unrelated=0)))]
    s = compute_summary(recs)
    assert s.image_hit.k == 1 and s.image_hit.n == 2
    assert s.image_misfit.k == 1 and s.image_misfit.n == 2


async def test_minio_images_are_fetched_from_the_web_port_not_the_api_port():
    """KnowFlow 正文里的 /minio/... 由网页端口的 nginx 提供；API 端口对这个路径回的是网页。"""
    from knoweval.adapters.base import KbHandle
    from knoweval.adapters.ragflow import RagflowAdapter
    png1, png2 = _png(1), _png(2)

    def handler(request):
        host, p = request.url.host, request.url.path
        if host == "web" and p == "/minio/kb1/fig.png":
            return httpx.Response(200, content=png1, headers={"content-type": "image/png"})
        if host == "api" and p == "/v1/document/image/kb1-img2":
            return httpx.Response(200, content=png2, headers={"content-type": "image/png"})
        if host == "api" and p.endswith("/sessions"):
            return httpx.Response(200, json={"code": 0, "data": {"id": "s"}})
        if host == "api" and p.endswith("/completions"):
            return httpx.Response(200, json={"code": 0, "data": {
                "answer": '如图 <img src="/minio/kb1/fig.png"> [ID:0]',
                "reference": {"chunks": [{"content": "示意", "document_name": "m.pdf", "image_id": "kb1-img2"}]}}})
        return httpx.Response(200, text="<!doctype html>", headers={"content-type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api")
    adapter = RagflowAdapter(base_url="http://api", api_key="k", image_base_url="http://web", client=client)
    ans = await adapter.ask(KbHandle(system="knowflow", ids={"chat_id": "c", "dataset_id": "d"}), "q")
    assert {i.phash for i in ans.images} == {dhash(png1), dhash(png2)}

    no_base = RagflowAdapter(base_url="http://api", api_key="k", client=client)
    ans = await no_base.ask(KbHandle(system="knowflow", ids={"chat_id": "c", "dataset_id": "d"}), "q")
    minio = [i for i in ans.images if i.source.startswith("/minio/")][0]
    assert minio.phash is None  # API 端口回的是网页，不能当成图

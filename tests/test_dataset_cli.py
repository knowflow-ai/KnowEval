"""数据集命令：list / info / pull。"""

import hashlib
import json
import shutil

import httpx
import pytest
from typer.testing import CliRunner

from knoweval.cli import app
from knoweval.dataset import DatasetIntegrityError, find_datasets, pull_documents
from tests.test_cli import DEMO_DIR

ROOT = DEMO_DIR.parent


def test_find_datasets_lists_local_datasets():
    found = {d.ref: d for d in find_datasets([ROOT])}
    assert "demo@0.0" in found and "demo-md@0.0" in found


def test_cli_list_and_info():
    runner = CliRunner()
    out = runner.invoke(app, ["dataset", "list", "--path", str(ROOT)])
    assert out.exit_code == 0 and "demo@0.0" in out.output and "20 题" in out.output
    out = runner.invoke(app, ["dataset", "info", str(DEMO_DIR)])
    assert out.exit_code == 0, out.output
    for s in ("Apache-2.0", "单跳事实", "01-潮汐实时计算系统.pdf"):
        assert s in out.output


def _external_dataset(tmp_path, content: bytes, sha: str | None = None):
    root = tmp_path / "ext"
    shutil.copytree(DEMO_DIR, root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    name = manifest["files"][0]["name"]
    (root / "docs" / name).unlink()
    manifest["files"][0]["source_url"] = "https://disclosure.example.com/report.pdf"
    manifest["files"][0]["sha256"] = sha or hashlib.sha256(content).hexdigest()
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return root, name


async def test_pull_downloads_missing_files_and_verifies_hash(tmp_path):
    content = b"%PDF-1.4 annual report"
    root, name = _external_dataset(tmp_path, content)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=content)))
    result = await pull_documents(root, client=client)
    assert result.downloaded == [name] and (root / "docs" / name).read_bytes() == content


async def test_pull_rejects_a_file_whose_hash_differs(tmp_path):
    root, name = _external_dataset(tmp_path, b"expected", sha=hashlib.sha256(b"expected").hexdigest())
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"changed upstream")))
    with pytest.raises(DatasetIntegrityError, match="哈希"):
        await pull_documents(root, client=client)
    assert not (root / "docs" / name).exists()


async def test_pull_fails_when_a_missing_file_has_no_source(tmp_path):
    root = tmp_path / "ds"
    shutil.copytree(DEMO_DIR, root)
    (root / "docs" / "02-沈墨.pdf").unlink()
    with pytest.raises(DatasetIntegrityError, match="source_url"):
        await pull_documents(root, client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))))


async def test_pull_skips_files_already_present(tmp_path):
    root = tmp_path / "ds"
    shutil.copytree(DEMO_DIR, root)
    result = await pull_documents(root, client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert result.downloaded == [] and len(result.present) == 7

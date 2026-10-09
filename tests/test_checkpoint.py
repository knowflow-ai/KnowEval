"""检查点：崩溃时写了一半的最后一行要丢掉，那道题会被重跑。"""

from knoweval.runner.checkpoint import Checkpoint


async def test_half_written_last_line_is_dropped(tmp_path):
    ckpt = Checkpoint(tmp_path / "c.jsonl")
    await ckpt.append({"qid": "a", "v": 1})
    with ckpt.path.open("a", encoding="utf-8") as f:
        f.write('{"qid": "b", "v"')
    assert list(ckpt.load()) == ["a"]


def test_missing_file_is_empty(tmp_path):
    assert Checkpoint(tmp_path / "none.jsonl").load() == {}

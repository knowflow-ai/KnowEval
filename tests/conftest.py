"""判分全部交给裁判模型：跑评测的测试默认配一个假裁判（tests/fake_judge.py）。

要测「没配裁判会怎样」的测试标上 @pytest.mark.real_judge_wiring，退出这个默认。
"""

import pytest

from tests.fake_judge import GoldJudge


def pytest_configure(config):
    config.addinivalue_line("markers", "real_judge_wiring: 不注入假裁判，测没配裁判时的行为")


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path_factory, monkeypatch):
    """命令行会读当前目录的 .env 和 settings.json：测试换到空目录跑，不碰开发者自己的密钥和模型设置。"""
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))


@pytest.fixture(autouse=True)
def _fake_judge(request, monkeypatch):
    if request.node.get_closest_marker("real_judge_wiring"):
        return
    from knoweval import cli
    from knoweval.judge import judge as judge_mod
    from knoweval.ui import executor as executor_mod

    original = judge_mod._judge

    async def with_default_judge(q, a, text_judge):
        return await original(q, a, text_judge if text_judge is not None else GoldJudge())

    monkeypatch.setattr(judge_mod, "_judge", with_default_judge)
    monkeypatch.setattr(cli, "_text_judge", lambda *a, **k: GoldJudge())
    original_judge = executor_mod.EvalExecutor._judge

    def executor_judge(self, run_dir):
        if self.judge_factory is None and not self.ws.settings().model:
            return GoldJudge()
        return original_judge(self, run_dir)

    monkeypatch.setattr(executor_mod.EvalExecutor, "_judge", executor_judge)

"""--no-test 开关：配置/编译/安装照常，自带测试整体跳过。

CI 的 x86-64-v4 变体在宿主 CPU 不支持 AVX-512 时用它：产出的 sysroot 不跑
测试也要进发布（基线 / v3 / v4 三份资产），且无 AVX-512 的机器上跑 v4 测试
程序会 SIGILL——不能用 ignore_test_failures（那仍会执行测试命令）。

运行：PYTHONPATH=. python3 -m unittest discover -s tests
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gtkcross.builder import Builder
from gtkcross.config import ProjectConfig

ROOT = Path(__file__).resolve().parent.parent


class FakeEngine:
    """只够 _apply_test 用的假 engine：record test.enabled 与是否真被调用。

    ws 用唯一临时目录：stamp 检查走真实文件系统，固定路径会在多次运行间
    残留 test.stamp 造成串扰（首跑写 stamp → 次跑 s.exists() 为真 → 提前
    [skip]，tc.run 不再被调用）。
    """

    def __init__(self, enabled=True):
        self.recipe = mock.Mock()
        self.recipe.test = {"enabled": enabled}
        self.ws = Path(tempfile.mkdtemp(prefix="gtkcross-no-test-"))
        self.done = self.ws / "done"
        self.test_command_called = False
        self.stamp_called = False

    def test_command(self):
        self.test_command_called = True
        return "echo fake test"

    def stamp(self, key: str):
        self.stamp_called = True
        return self.done / f"{key}.stamp"


class NoTestSwitch(unittest.TestCase):
    def setUp(self):
        self.project = ProjectConfig.load(ROOT)
        self.builder = Builder(self.project, "linux-native", jobs=1,
                               skip_tests=True)
        # tc.run 是测试的唯一下发口；跳过后绝不能被调用
        self.builder.tc.run = mock.Mock(
            side_effect=AssertionError("skip_tests 时不应下发测试命令")
        )

    def test_skip_tests_skips_execution_even_if_enabled(self):
        engine = FakeEngine(enabled=True)
        self.builder._apply_test("fake-recipe", engine)
        # 既没构造测试命令，也没写 test stamp
        self.assertFalse(engine.test_command_called)
        self.assertFalse(engine.stamp_called)
        self.builder.tc.run.assert_not_called()

    def test_skip_tests_still_skips_when_disabled(self):
        # enabled=False 的 recipe 在正常路径本来也跳；skip_tests 下同样跳过
        engine = FakeEngine(enabled=False)
        self.builder._apply_test("fake-recipe", engine)
        self.assertFalse(engine.test_command_called)
        self.builder.tc.run.assert_not_called()

    def test_without_skip_tests_enabled_recipe_runs(self):
        b = Builder(self.project, "linux-native", jobs=1)
        b.tc.run = mock.Mock(return_value=mock.Mock(returncode=0, stdout=b""))
        engine = FakeEngine(enabled=True)
        b._apply_test("fake-recipe", engine)
        self.assertTrue(engine.test_command_called)
        b.tc.run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
"""测试命令生成：recipe 的 test.jobs / timeout_multiplier 必须生效。

运行：PYTHONPATH=. python3 -m unittest discover -s tests
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace

from gtkcross.builder import Builder
from gtkcross.config import ProjectConfig
from gtkcross.engines import CmakeEngine, MesonEngine

ROOT = Path(__file__).resolve().parent.parent


class TestCommand(unittest.TestCase):
    def setUp(self):
        self._wrapper = os.environ.pop("GTKCROSS_TEST_WRAPPER", None)

    def tearDown(self):
        if self._wrapper is not None:
            os.environ["GTKCROSS_TEST_WRAPPER"] = self._wrapper

    @staticmethod
    def meson_engine(test: dict, jobs: int = 8) -> MesonEngine:
        engine = MesonEngine.__new__(MesonEngine)
        engine.recipe = SimpleNamespace(test=test)
        engine.jobs = jobs
        engine.build_dir = Path("/tmp/build")
        return engine

    def test_meson_test_jobs_is_capped_by_global_jobs(self):
        self.assertEqual(self.meson_engine({"jobs": 2}, jobs=8).test_jobs(), 2)
        self.assertEqual(self.meson_engine({"jobs": 16}, jobs=8).test_jobs(), 8)
        self.assertEqual(self.meson_engine({}, jobs=8).test_jobs(), 8)

    def test_meson_command_uses_recipe_test_settings(self):
        cmd = self.meson_engine(
            {"jobs": 2, "timeout_multiplier": 4}, jobs=8
        ).test_command()
        self.assertIn("meson test -C /tmp/build", cmd)
        self.assertIn("-j 2", cmd)
        self.assertIn("--timeout-multiplier 4", cmd)
        self.assertNotIn("-j 8", cmd)

    def test_meson_command_omits_timeout_multiplier_by_default(self):
        cmd = self.meson_engine({}, jobs=4).test_command()
        self.assertIn("-j 4", cmd)
        self.assertNotIn("--timeout-multiplier", cmd)

    def test_ctest_command_uses_recipe_test_jobs(self):
        engine = CmakeEngine.__new__(CmakeEngine)
        engine.recipe = SimpleNamespace(test={"jobs": 1})
        engine.jobs = 8
        engine.build_dir = Path("/tmp/build")
        cmd = engine.test_command()
        self.assertIn("ctest --test-dir /tmp/build", cmd)
        self.assertIn("-j 1", cmd)

    def test_glib_linux_recipe_sets_test_jobs_and_timeout(self):
        project = ProjectConfig.load(ROOT)
        builder = Builder(project, "linux-native", jobs=4)
        recipe = builder.recipes["glib"].for_target(
            "linux-native", builder.tc.target_os
        )
        self.assertEqual(recipe.test["jobs"], 2)
        self.assertEqual(recipe.test["timeout_multiplier"], 4)
        self.assertIn(
            "glib:memory-monitor-psi-env-set", recipe.test["known_failures"]
        )


if __name__ == "__main__":
    unittest.main()

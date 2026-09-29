"""Windows 构建计划等价性测试（stdlib unittest，无需额外依赖）。

本仓库的 Linux 开发机无法实跑 msys2-* 目标，所以 Windows 侧的回归只能靠
"计划快照"守住：把每个 recipe 在每个 Windows 目标下**实际会执行的 configure
命令行**、补丁列表、依赖列表、测试命令与环境变量、post_install 命令、build
plan 拓扑序，与 tests/windows-plan.golden.json 逐字段比对。任何差异都要求
显式更新快照。

运行：PYTHONPATH=. python3 -m unittest discover -s tests
重新生成快照：PYTHONPATH=. python3 tests/gen_windows_plan.py
（生成器复用本文件的 capture_plan —— 生产者与校验者必须是同一份实现，
 否则字段一漂移就会出现假差异，本项目踩过这个坑。）

更新基线前必须先证明差异"只是位置"：把新快照与 HEAD 导出的旧实现对比
（`git archive HEAD | tar -x -C /tmp/base`，两边各跑一次 capture_plan），按
命令行 token 的多重集分类。2026-09-28 门控改造那一轮的结果是：
1238 个字段逐字节相同、22 处仅顺序不同（家族块把选项追加到末尾、
gtk 的 deps 里 directx-headers 移到尾部及其引发的拓扑平序调整）、
0 处真实差异，prelude 与 bash_argv 完全一致。
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path

from gtkcross.builder import Builder, _ENGINE_CLS
from gtkcross.config import ProjectConfig
from gtkcross.toolchain import posix

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "windows-plan.golden.json"
WINDOWS_TARGETS = ("msys2-mingw64", "msys2-ucrt64")


def _rel(text: str) -> str:
    """把项目根目录归一化掉，使快照在不同检出路径下可比。"""
    return text.replace(posix(ROOT), "@ROOT@").replace(str(ROOT), "@ROOT@")


def capture_plan(target: str) -> dict:
    """Return the resolved build plan for one target (no subprocess spawned)."""
    project = ProjectConfig.load(ROOT)
    builder = Builder(project, target)
    captured: list[str] = []
    # 捕获而非执行：Toolchain.expect 是唯一下发命令的出口
    builder.tc.expect = lambda script, cwd=None, _c=captured: _c.append(script)

    recipes = {}
    for name in sorted(builder.recipes):
        recipe = builder.recipes[name].for_target(target, builder.tc.target_os)
        captured.clear()
        ws = project.build_dir / target / name
        engine = _ENGINE_CLS[recipe.build](
            recipe,
            builder.tc,
            ws,
            builder.jobs,
            default_library=project.default_library,
            prefer_static=project.prefer_static,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            engine.configure()  # 只为取命令串，屏蔽引擎的 [xxx configure] 回显
        cmd = _rel(captured[0]) if captured else None
        test_enabled = bool((recipe.test or {}).get("enabled", False))
        recipes[name] = {
            "build": recipe.build,
            "libtype": engine.libtype,
            "deps": list(recipe.deps),
            "patches": list(recipe.patches),
            "submodules": dict(recipe.submodules),
            # 平台归属是快照的一部分：Windows 侧必须看到 X11/Mesa 那批包声明了
            # `platforms: [linux]`（它们不进任何 Windows build plan，CI 全量枚举
            # 时靠这条跳过），directx-headers/directxmath/egl-headers 反之。
            "platforms": list(recipe.platforms),
            "configure_cmd": cmd,
            "test_cmd": _rel(engine.test_command()) if test_enabled else None,
            "test_enabled": test_enabled,
            "test_env": dict((recipe.test or {}).get("env") or {}),
            "known_failures": list((recipe.test or {}).get("known_failures") or []),
            "post_install": [_rel(c) for c in recipe.post_install.get("commands", [])],
            "data": {
                "install_files": dict(recipe.data.get("install_files") or {}),
                "text_files": {
                    k: _rel(str(v))
                    for k, v in (recipe.data.get("text_files") or {}).items()
                },
            },
        }
    # order() 依赖图现在按目标解析（家族块可增删 deps），所以拓扑序也纳入快照：
    # 这几个入口覆盖了 libadwaita 全闭包、appstream 独立链、运行期数据包。
    plans = {
        root: builder.order([root])
        for root in ("libadwaita", "appstream", "gtk", "shared-mime-info")
    }
    return {
        "prelude": [_rel(line) for line in builder.tc.prelude()],
        "bash_argv": [_rel(a) for a in builder.tc.bash_argv()],
        "plans": plans,
        "recipes": recipes,
    }


class WindowsPlanUnchanged(unittest.TestCase):
    """重构 targets 门控/框架改动后，Windows 两个目标的计划必须逐字节不变。"""

    @classmethod
    def setUpClass(cls) -> None:
        if not GOLDEN.exists():
            raise unittest.SkipTest(f"missing golden snapshot: {GOLDEN}")
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    def test_targets_present(self):
        self.assertEqual(sorted(self.golden), sorted(WINDOWS_TARGETS))

    def test_toolchain_prelude_unchanged(self):
        for target in WINDOWS_TARGETS:
            with self.subTest(target=target):
                plan = capture_plan(target)
                self.assertEqual(
                    self.golden[target]["prelude"], plan["prelude"],
                    f"{target}: prelude 变化会波及全部 recipe 的编译/链接参数",
                )
                self.assertEqual(self.golden[target]["bash_argv"], plan["bash_argv"])

    def test_recipe_plan_unchanged(self):
        for target in WINDOWS_TARGETS:
            plan = capture_plan(target)
            self.assertEqual(
                sorted(self.golden[target]["recipes"]), sorted(plan["recipes"])
            )
            for name, want in self.golden[target]["recipes"].items():
                got = plan["recipes"][name]
                with self.subTest(target=target, recipe=name):
                    self.assertEqual(want, got)

    def test_build_plans_unchanged(self):
        """闭包拓扑序也进比对：以前只写进快照没人查，等于没守。

        deps 的家族门控、新包、`platforms:` 归属都会改变 order() 的结果，
        而计划顺序直接决定"谁先装进 sysroot"——Mesa 那种隐性依赖就是靠
        顺序侥幸通过的，顺序变化必须显式暴露出来。
        """
        for target in WINDOWS_TARGETS:
            plan = capture_plan(target)
            for root, want in sorted(self.golden[target]["plans"].items()):
                with self.subTest(target=target, root=root):
                    self.assertEqual(want, plan["plans"][root])


if __name__ == "__main__":
    unittest.main()

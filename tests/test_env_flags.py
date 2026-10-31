"""用户编译参数环境变量（CC/CXX/CFLAGS/CPPFLAGS/LDFLAGS 等）的传递与失效机制。

覆盖三条回归风险：
1. prelude 加性导出：调用方设置的工具/编译参数必须保留（${VAR:-默认}、
   ${VAR:+ ...}），不能被框架默认值覆盖；
2. cmake 引擎显式注入：设了 CFLAGS/CXXFLAGS/CPPFLAGS/LDFLAGS 时 configure
   命令带 -DCMAKE_C_FLAGS 等，未设置时不改变命令（与旧行为逐字节一致）；
3. 环境指纹失效：这些变量变化 ⇒ 该 recipe 的 build 目录与阶段 stamp 被废弃，
   强制重新 configure；环境不变时不动；旧框架留下的无指纹 workspace 首次
   构建即强制重配。

运行：PYTHONPATH=. python3 -m unittest discover -s tests
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gtkcross.builder import Builder, _ENGINE_CLS, env_fingerprint
from gtkcross.config import ProjectConfig
from gtkcross.recipe import load_recipes

ROOT = Path(__file__).resolve().parent.parent
LINUX_TARGET = "linux-native"
WINDOWS_TARGETS = ("msys2-mingw64", "msys2-ucrt64")

_TRACKED = (
    "CC", "CXX", "AR", "RANLIB", "NM", "STRIP",
    "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "LIBRARY_PATH",
)


def _blank_tracked():
    """把这些变量的宿主残留清空（CI/开发机可能带着自己的 CFLAGS 等）。"""
    return mock.patch.dict(os.environ, {}, clear=True)


class EnvFingerprint(unittest.TestCase):
    def setUp(self):
        self.project = ProjectConfig.load(ROOT)

    def test_tracked_change_alters_fingerprint(self):
        with _blank_tracked():
            base = env_fingerprint()
        with _blank_tracked(), mock.patch.dict(os.environ, {"CFLAGS": "-O2"}):
            with_cflags = env_fingerprint()
        with _blank_tracked(), mock.patch.dict(
            os.environ, {"CFLAGS": "-O2", "CC": "clang"}
        ):
            with_cc = env_fingerprint()
        self.assertNotEqual(base, with_cflags)
        self.assertNotEqual(with_cflags, with_cc)

    def test_same_env_same_fingerprint(self):
        with _blank_tracked(), mock.patch.dict(
            os.environ, {"CFLAGS": "-O2", "LDFLAGS": "-s"}
        ):
            fp1 = env_fingerprint()
            fp2 = env_fingerprint()
        self.assertEqual(fp1, fp2)

    def test_untracked_var_ignored(self):
        with _blank_tracked(), mock.patch.dict(os.environ, {"CFLAGS": "-O2"}):
            fp1 = env_fingerprint()
        with _blank_tracked(), mock.patch.dict(
            os.environ, {"CFLAGS": "-O2", "SOME_OTHER_VAR": "x"}
        ):
            fp2 = env_fingerprint()
        self.assertEqual(fp1, fp2)


class PreludeAdditive(unittest.TestCase):
    """prelude 必须以"保留调用方值"的形式导出，而不是覆盖。"""

    def setUp(self):
        self.project = ProjectConfig.load(ROOT)

    def _prelude(self, target: str):
        b = Builder(self.project, target, jobs=1)
        return "\n".join(b.tc.prelude())

    def test_tools_use_default_only_when_unset(self):
        for t in WINDOWS_TARGETS + (LINUX_TARGET,):
            with self.subTest(target=t):
                p = self._prelude(t)
                # 工具默认值只在调用方未设置时生效
                self.assertIn('export CC="${CC:-gcc}"', p)
                self.assertIn('export CXX="${CXX:-g++}"', p)
                self.assertIn('export PKG_CONFIG="${PKG_CONFIG:-pkg-config}"', p)
                # 不再有硬编码覆盖（旧形态是 export CC='gcc'）
                self.assertNotIn("export CC='gcc'", p)

    def test_cppflags_additive(self):
        for t in WINDOWS_TARGETS + (LINUX_TARGET,):
            with self.subTest(target=t):
                p = self._prelude(t)
                self.assertIn(
                    'export CPPFLAGS="-I$SYSROOT/include${CPPFLAGS:+ $CPPFLAGS}"',
                    p,
                )

    def test_ldflags_additive(self):
        for t in WINDOWS_TARGETS:
            with self.subTest(target=t):
                p = self._prelude(t)
                self.assertIn(
                    'export LDFLAGS="-L$SYSROOT/lib${LDFLAGS:+ $LDFLAGS}"', p
                )
        p = self._prelude(LINUX_TARGET)
        self.assertIn("-lm${LDFLAGS:+ $LDFLAGS}", p)

    def test_library_path_additive(self):
        for t in WINDOWS_TARGETS + (LINUX_TARGET,):
            with self.subTest(target=t):
                p = self._prelude(t)
                self.assertIn(
                    'export LIBRARY_PATH="$SYSROOT/lib'
                    '${LIBRARY_PATH:+:$LIBRARY_PATH}"',
                    p,
                )


class CmakeEnvInjection(unittest.TestCase):
    """设了编译参数环境变量时，cmake configure 命令必须显式带上。"""

    def setUp(self):
        self.project = ProjectConfig.load(ROOT)

    def _setup_cmd(self, target: str = LINUX_TARGET) -> str:
        b = Builder(self.project, target, jobs=1)
        r = b.recipes["zlib"].for_target(target, b.tc.target_os)
        engine = _ENGINE_CLS[r.build](
            r, b.tc, Path("/tmp/ws"), 4,
            default_library=self.project.default_library,
            prefer_static=self.project.prefer_static,
        )
        return engine.setup_cmd()

    def test_no_injection_without_user_flags(self):
        with _blank_tracked():
            cmd = self._setup_cmd()
        self.assertNotIn("CMAKE_C_FLAGS", cmd)
        self.assertNotIn("CMAKE_CXX_FLAGS", cmd)
        self.assertNotIn("LINKER_FLAGS", cmd)
        # recipe 自己的 defines 不受影响
        self.assertIn("-DBUILD_SHARED_LIBS=", cmd)

    def test_injects_all_when_cflags_set(self):
        with _blank_tracked(), mock.patch.dict(os.environ, {"CFLAGS": "-O2"}):
            cmd = self._setup_cmd()
        self.assertIn('-DCMAKE_C_FLAGS="$CFLAGS $CPPFLAGS"', cmd)
        self.assertIn('-DCMAKE_CXX_FLAGS="$CXXFLAGS $CPPFLAGS"', cmd)
        self.assertIn('-DCMAKE_EXE_LINKER_FLAGS="$LDFLAGS"', cmd)
        self.assertIn('-DCMAKE_SHARED_LINKER_FLAGS="$LDFLAGS"', cmd)
        self.assertIn('-DCMAKE_MODULE_LINKER_FLAGS="$LDFLAGS"', cmd)

    def test_injects_when_only_ldflags_set(self):
        with _blank_tracked(), mock.patch.dict(os.environ, {"LDFLAGS": "-s"}):
            cmd = self._setup_cmd()
        self.assertIn('-DCMAKE_EXE_LINKER_FLAGS="$LDFLAGS"', cmd)
        # LDFLAGS 单设时 C/CXX flags 一并注入（避免 CMake 从环境重复合并）
        self.assertIn('-DCMAKE_C_FLAGS="$CFLAGS $CPPFLAGS"', cmd)


class EnvInvalidation(unittest.TestCase):
    """环境指纹变化 ⇒ 该 recipe 的 build 目录与阶段 stamp 被废弃。"""

    def setUp(self):
        self.project = ProjectConfig.load(ROOT)
        self.recipes = load_recipes(ROOT / "recipes")
        self.builder = Builder(self.project, LINUX_TARGET, jobs=1)

    def _recipe(self, name: str):
        return self.recipes[name].for_target(LINUX_TARGET, self.builder.tc.target_os)

    def _fresh_ws(self) -> Path:
        """全新 workspace：只有 done/ 目录，无 stamp、无 build。"""
        ws = Path(tempfile.mkdtemp(prefix="gtkcross-env-"))
        (ws / "done").mkdir()
        return ws

    def _mark_configured(self, ws: Path,
                         stamps=("configure", "compile", "install")) -> None:
        """模拟"已配置完成"：写阶段 stamp + build 目录。"""
        done = ws / "done"
        done.mkdir(exist_ok=True)
        for s in stamps:
            (done / f"{s}.stamp").write_text("ok\n", encoding="utf-8")
        (ws / "build").mkdir(exist_ok=True)
        (ws / "build" / "CMakeCache.txt").write_text("x", encoding="utf-8")

    def test_env_change_wipes_stamps_and_build_dir(self):
        ws = self._fresh_ws()
        r = self._recipe("zlib")  # cmake
        # 先以 CFLAGS=-O2 配置并记录指纹
        with _blank_tracked(), mock.patch.dict(os.environ, {"CFLAGS": "-O2"}):
            self.builder._invalidate_on_env_change("zlib", r, ws)
        self._mark_configured(ws)
        self.assertTrue((ws / "done" / "configure.stamp").exists())
        self.assertTrue((ws / "build").exists())
        # 换成 -O3：stamps 与 build 目录必须被废弃
        with _blank_tracked(), mock.patch.dict(os.environ, {"CFLAGS": "-O3"}):
            self.builder._invalidate_on_env_change("zlib", r, ws)
        self.assertFalse((ws / "done" / "configure.stamp").exists())
        self.assertFalse((ws / "done" / "compile.stamp").exists())
        self.assertFalse((ws / "done" / "install.stamp").exists())
        self.assertFalse((ws / "build").exists())
        # 新指纹已记录
        with _blank_tracked(), mock.patch.dict(os.environ, {"CFLAGS": "-O3"}):
            self.assertEqual(
                (ws / "done" / "env-fp.stamp").read_text(encoding="utf-8"),
                env_fingerprint(),
            )

    def test_same_env_no_invalidation(self):
        ws = self._fresh_ws()
        r = self._recipe("zlib")
        with _blank_tracked(), mock.patch.dict(os.environ, {"CFLAGS": "-O2"}):
            self.builder._invalidate_on_env_change("zlib", r, ws)
        self._mark_configured(ws)
        with _blank_tracked(), mock.patch.dict(os.environ, {"CFLAGS": "-O2"}):
            self.builder._invalidate_on_env_change("zlib", r, ws)
        # 环境未变：不删任何东西
        self.assertTrue((ws / "done" / "configure.stamp").exists())
        self.assertTrue((ws / "build").exists())

    def test_stale_ws_without_fingerprint_gets_wiped(self):
        """旧框架留下的 workspace：有 stamp 但没记指纹 ⇒ 首次构建强制重配。"""
        ws = self._fresh_ws()
        self._mark_configured(ws)
        r = self._recipe("zlib")
        with _blank_tracked():
            self.builder._invalidate_on_env_change("zlib", r, ws)
        self.assertFalse((ws / "done" / "configure.stamp").exists())
        self.assertFalse((ws / "build").exists())
        self.assertTrue((ws / "done" / "env-fp.stamp").exists())

    def test_fresh_ws_records_fingerprint_without_wipe(self):
        ws = self._fresh_ws()
        r = self._recipe("zlib")
        with _blank_tracked():
            self.builder._invalidate_on_env_change("zlib", r, ws)
        self.assertTrue((ws / "done" / "env-fp.stamp").exists())
        # 全新 workspace 无配置痕迹：不删除任何东西（本来也没有）
        self.assertFalse((ws / "done" / "configure.stamp").exists())
        self.assertFalse((ws / "build").exists())

    def test_data_engine_skipped(self):
        """data 包无编译参数：环境变化不得触发重装。"""
        ws = self._fresh_ws()
        (ws / "done" / "install.stamp").write_text("ok\n", encoding="utf-8")
        r = self._recipe("egl-headers")
        self.assertEqual(r.build, "data")
        with _blank_tracked(), mock.patch.dict(os.environ, {"CFLAGS": "-O3"}):
            self.builder._invalidate_on_env_change("egl-headers", r, ws)
        self.assertTrue((ws / "done" / "install.stamp").exists())
        self.assertFalse((ws / "done" / "env-fp.stamp").exists())

    def test_autotools_config_cache_removed(self):
        """autotools in-source 构建：config.cache 会被 -C 复用，须一并清除。"""
        ws = self._fresh_ws()
        r = self._recipe("libffi")  # autotools
        with _blank_tracked(), mock.patch.dict(os.environ, {"CFLAGS": "-O2"}):
            self.builder._invalidate_on_env_change("libffi", r, ws)
        # 模拟 configure 产物
        src = ws / "src"
        src.mkdir()
        (src / "config.cache").write_text("ac_cv_x=1\n", encoding="utf-8")
        (src / "config.status").write_text("x\n", encoding="utf-8")
        self._mark_configured(ws)
        # 环境变化：config.cache/config.status/stamp 一并清除
        with _blank_tracked(), mock.patch.dict(os.environ, {"CFLAGS": "-O3"}):
            self.builder._invalidate_on_env_change("libffi", r, ws)
        self.assertFalse((src / "config.cache").exists())
        self.assertFalse((src / "config.status").exists())
        self.assertFalse((ws / "done" / "configure.stamp").exists())


if __name__ == "__main__":
    unittest.main()
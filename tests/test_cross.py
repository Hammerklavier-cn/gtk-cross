"""交叉构建的工具链/引擎行为。

这些测试只捕获命令，不实际 spawn 交叉编译器；真正的端到端验证在 CI 的
cross job 与本地 `gtkcross doctor`/smoke build 里。
"""

from __future__ import annotations

import configparser
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gtkcross.builder import Builder, _ENGINE_CLS
from gtkcross.config import ProjectConfig
from gtkcross.toolchain import Toolchain, posix

ROOT = Path(__file__).resolve().parent.parent


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def capture_configure(builder: Builder, name: str) -> str:
    recipe = builder.resolved_recipe(name)
    out: list[str] = []
    builder.tc.expect = lambda script, cwd=None, _c=out: _c.append(script)
    engine = _ENGINE_CLS[recipe.build](
        recipe,
        builder.tc,
        Path("/tmp/ws"),
        1,
        default_library=builder.project.default_library,
        prefer_static=builder.project.prefer_static,
    )
    engine.configure()
    return out[0]


class CrossToolchains(unittest.TestCase):
    def setUp(self):
        self.project = ProjectConfig.load(ROOT)

    def test_fields(self):
        cases = {
            "linux-mingw-x64": ("windows", "x86_64", "x86_64-w64-mingw32"),
            "linux-aarch64": ("linux", "aarch64", "aarch64-linux-gnu"),
        }
        for target, (os_name, arch, triple) in cases.items():
            with self.subTest(target=target):
                b = Builder(self.project, target, jobs=1)
                self.assertTrue(b.cross)
                self.assertEqual(b.tc.target_os, os_name)
                self.assertEqual(b.tc.target_arch, arch)
                self.assertEqual(b.tc.host_triple, triple)
                self.assertTrue(b.tc.native_sysroot)
                self.assertTrue(b.tc.cross_file and b.tc.cross_file.exists())
                self.assertTrue(b.tc.native_file and b.tc.native_file.exists())
                self.assertTrue(
                    b.tc.native_pkg_config and b.tc.native_pkg_config.exists()
                )
                self.assertIn(
                    f"{b.tc._machine_path(b.native_sysroot)}/lib/pkgconfig",
                    b.tc.native_pkg_config.read_text(encoding="utf-8"),
                )
                self.assertTrue(b.tc.cmake_toolchain and b.tc.cmake_toolchain.exists())

        # v4 不再是专用 toolchain：由 linux-native --force-cross 合成（与
        # Windows v4 同一机制，见 test_windows_force_cross_keeps_generators_
        # baseline）。测试也会在 aarch64 runner 上跑，先钉死 host_arch 才能
        # 断言合成的 arch/triple。
        with mock.patch(
            "gtkcross.toolchain.host_arch", return_value="x86_64"
        ):
            v4 = Builder(self.project, "linux-native", jobs=1, force_cross=True)
        self.assertTrue(v4.cross)
        self.assertEqual(v4.tc.target_os, "linux")
        self.assertEqual(v4.tc.target_arch, "x86_64")
        self.assertEqual(v4.tc.host_triple, "x86_64-unknown-linux-gnu")
        # native 工具前缀来自 native_target: linux-tools，与原专用 target 一致。
        self.assertEqual(v4.native_sysroot, ROOT / "out/linux-tools")
        self.assertTrue(v4.tc.cross_file and v4.tc.cross_file.exists())
        self.assertTrue(v4.tc.native_file and v4.tc.native_file.exists())
        self.assertTrue(v4.tc.cmake_toolchain and v4.tc.cmake_toolchain.exists())

    def test_meson_cross_file(self):
        b = Builder(self.project, "linux-mingw-x64", jobs=1)
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(b.tc.cross_file)
        self.assertEqual(_unquote(cfg["host_machine"]["system"]), "windows")
        self.assertEqual(_unquote(cfg["host_machine"]["cpu_family"]), "x86_64")
        self.assertEqual(cfg["properties"]["needs_exe_wrapper"], "true")
        self.assertIn("x86_64-w64-mingw32-gcc", _unquote(cfg["binaries"]["c"]))
        self.assertIn(f"-L{posix(b.sysroot)}/lib", _unquote(cfg["binaries"]["c"]))
        self.assertIn(
            "x86_64-w64-mingw32-windres", _unquote(cfg["binaries"]["windres"])
        )
        self.assertIn(
            f"-L{posix(b.sysroot)}/lib", cfg["built-in options"]["c_args"]
        )
        self.assertIn(
            f"-L{posix(b.sysroot)}/lib", cfg["built-in options"]["c_link_args"]
        )

        a = Builder(self.project, "linux-aarch64", jobs=1)
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(a.tc.cross_file)
        self.assertEqual(_unquote(cfg["host_machine"]["system"]), "linux")
        self.assertEqual(_unquote(cfg["host_machine"]["cpu_family"]), "aarch64")
        self.assertIn("aarch64-linux-gnu-gcc", _unquote(cfg["binaries"]["c"]))
        self.assertIn("-fPIC", cfg["built-in options"]["c_args"])
        self.assertIn(
            "-I" + posix(a.sysroot / "include"),
            cfg["built-in options"]["c_args"],
        )

        # v4 的 -march 现在只来自调用方 CFLAGS（同 Windows），cross file 的
        # c_args 经 _target_cflags() 收编。
        with mock.patch(
            "gtkcross.toolchain.host_arch", return_value="x86_64"
        ), mock.patch.dict(
            os.environ,
            {"CFLAGS": "-march=x86-64-v4", "CXXFLAGS": "-march=x86-64-v4"},
            clear=False,
        ):
            v4 = Builder(self.project, "linux-native", jobs=1, force_cross=True)
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(v4.tc.cross_file)
        self.assertEqual(_unquote(cfg["host_machine"]["cpu_family"]), "x86_64")
        self.assertIn("-march=x86-64-v4", cfg["built-in options"]["c_args"])
        cmake = v4.tc.cmake_toolchain.read_text(encoding="utf-8")
        self.assertIn(f'-I{posix(v4.sysroot)}/include', cmake)
        self.assertIn("CMAKE_EXE_LINKER_FLAGS_INIT", cmake)
        self.assertIn("cross-no-run.sh", cmake)

    def test_windows_machine_files_keep_drive_letter_paths(self):
        """Meson/CMake 是原生 Windows 程序，machine file 里不能用 /c/...。"""
        with tempfile.TemporaryDirectory() as tmp:
            tc = Toolchain(
                {"host": "windows", "cross": True, "target_os": "windows"},
                "probe",
                Path("C:/msys64/out/sysroot"),
            )
            tc.native_sysroot = Path("C:/msys64/out/tools")
            tc.write_cross_files(Path(tmp))
            cross = tc.cross_file.read_text(encoding="utf-8")
            self.assertIn("-IC:/msys64/out/sysroot/include", cross)
            self.assertIn("-LC:/msys64/out/sysroot/lib", cross)
            wrapper = tc.native_pkg_config.read_text(encoding="utf-8")
            self.assertIn(
                "PKG_CONFIG_LIBDIR=\"C:/msys64/out/tools/lib/pkgconfig;"
                "C:/msys64/out/tools/share/pkgconfig\"",
                wrapper,
            )
            self.assertNotIn("/c/msys64", cross)
            self.assertNotIn("/c/msys64", wrapper)

    def test_cross_prelude_separates_native_and_target(self):
        b = Builder(self.project, "linux-aarch64", jobs=1)
        prelude = "\n".join(b.tc.prelude())
        self.assertIn('export NATIVE_SYSROOT="', prelude)
        self.assertIn("export PATH=$NATIVE_SYSROOT/bin:$SYSROOT/bin:", prelude)
        self.assertIn('export LD_LIBRARY_PATH="$NATIVE_SYSROOT/lib', prelude)
        self.assertIn("export PKG_CONFIG_ALLOW_CROSS=1", prelude)
        # target flags 只能进 cross file / configure 前缀，不能进全局 CFLAGS
        self.assertIn('export CFLAGS="${CFLAGS:-}"', prelude)
        self.assertNotIn("-march=x86-64-v4", prelude)
        self.assertNotIn(" -fPIC", prelude)

        w = Builder(
            self.project, "msys2-ucrt64", jobs=1, force_cross=True,
            native_sysroot=ROOT / "out/msys2-ucrt64",
        )
        prelude = "\n".join(w.tc.prelude())
        self.assertIn('export CFLAGS="${CFLAGS:-}"', prelude)
        self.assertNotIn("-march=x86-64-v4", prelude)
        self.assertIn("export PATH=$NATIVE_SYSROOT/bin:$SYSROOT/bin:", prelude)

    def test_prelude_as_export_follows_arch_relation(self):
        """tools 里的 as 只能在 binutils 架构与宿主不同时导出。

        libvpx 的 configure 只在 AS 为空/auto 时探测 nasm/yasm；同架构
        cross（linux-native --force-cross）若预设 AS=as 会静默跳过探测，
        GNU as 拿 yasm 的 `-f elf64` 去编 nasm 语法的 .asm 直接失败
        （2026-10-31 的 linux-x64-v4 libvpx 构建事故）。同架构不得导出，
        异架构（linux-aarch64）必须导出 target binutils——两头都钉死。
        """
        v4 = Builder(self.project, "linux-native", jobs=1, force_cross=True)
        self.assertNotIn("export AS=", "\n".join(v4.tc.prelude()))

        a = Builder(self.project, "linux-aarch64", jobs=1)
        self.assertIn(
            'export AS="${AS:-aarch64-linux-gnu-as}"',
            "\n".join(a.tc.prelude()),
        )


class CrossEngines(unittest.TestCase):
    def setUp(self):
        self.project = ProjectConfig.load(ROOT)

    def test_autotools_host_build_and_flags(self):
        b = Builder(self.project, "linux-aarch64", jobs=1)
        cmd = capture_configure(b, "libffi")
        self.assertIn("--host=aarch64-linux-gnu", cmd)
        self.assertIn("--build=x86_64-pc-linux-gnu", cmd)
        self.assertIn("CFLAGS=-fPIC", cmd)

        w = Builder(self.project, "linux-mingw-x64", jobs=1)
        cmd = capture_configure(w, "libffi")
        self.assertIn("--host=x86_64-w64-mingw32", cmd)
        self.assertIn("--build=x86_64-pc-linux-gnu", cmd)
        self.assertNotIn("-fPIC", cmd)

    def test_meson_cross_file_is_passed(self):
        with mock.patch.dict(
            os.environ,
            {"CFLAGS": "-march=x86-64-v4", "CXXFLAGS": "-march=x86-64-v4"},
            clear=False,
        ):
            b = Builder(self.project, "linux-native", jobs=1, force_cross=True)
        cmd = capture_configure(b, "glib-base")
        self.assertIn("--cross-file=", cmd)
        self.assertIn("--native-file=", cmd)
        self.assertIn("meson-cross.ini", cmd)
        self.assertIn("meson-native.ini", cmd)
        self.assertIn("-march=x86-64-v4", b.tc.cross_file.read_text())

    def test_cmake_toolchain_is_passed(self):
        b = Builder(self.project, "linux-mingw-x64", jobs=1)
        cmd = capture_configure(b, "zlib")
        self.assertIn("-DCMAKE_TOOLCHAIN_FILE=", cmd)
        self.assertIn("cmake-toolchain.cmake", cmd)

    def test_cross_disables_introspection_and_test_builds(self):
        b = Builder(self.project, "linux-aarch64", jobs=1)
        gi = " ".join(b.resolved_recipe("gobject-introspection").meson["options"])
        self.assertIn("-Dbuild_introspection_data=false", gi)
        self.assertIn("-Dtests=false", gi)
        glib = " ".join(b.resolved_recipe("glib").meson["options"])
        self.assertIn("-Dintrospection=disabled", glib)
        self.assertIn("-Dtests=false", glib)
        gtk = " ".join(b.resolved_recipe("gtk").meson["options"])
        self.assertIn("-Dintrospection=disabled", gtk)
        self.assertIn("-Dbuild-tests=false", gtk)
        smi = " ".join(
            b.resolved_recipe("shared-mime-info").meson["options"]
        )
        self.assertIn("-Dupdate-mimedb=false", smi)
        libxmlb = " ".join(
            b.resolved_recipe("libxmlb").meson["options"]
        )
        self.assertIn("-Dintrospection=false", libxmlb)
        self.assertNotIn("-Dintrospection=disabled", libxmlb)
        self.assertEqual(
            b.resolved_recipe("expat").cmake["defines"]["EXPAT_BUILD_TESTS"],
            "OFF",
        )
        w = Builder(self.project, "linux-mingw-x64", jobs=1)
        self.assertEqual(
            w.resolved_recipe("expat").cmake["defines"][
                "EXPAT_WITH_ARC4RANDOM_BUF"
            ],
            "OFF",
        )
        # 目标侧 g-i 在 cross 中跳过（上游会用目标编译器编 Python 扩展）
        self.assertNotIn("gobject-introspection", b.order(["glib"]))
        self.assertNotIn("appstream", b.order(["appstream"]))
        self.assertIn(
            "gobject-introspection",
            Builder(self.project, "linux-native", jobs=1).order(["glib"]),
        )
        # 原始 recipe 不被改写（native 构建/快照仍应看到 enabled）
        self.assertIn(
            "-Dintrospection=enabled", b.recipes["glib"].meson["options"]
        )
        native = Builder(self.project, "linux-native", jobs=1)
        native_smi = " ".join(
            native.resolved_recipe("shared-mime-info").meson["options"]
        )
        self.assertIn("-Dupdate-mimedb=true", native_smi)

    def test_libvpx_target_per_cross_target(self):
        a = Builder(self.project, "linux-aarch64", jobs=1)
        self.assertIn("--target=arm64-linux-gcc", capture_configure(a, "libvpx"))
        w = Builder(self.project, "linux-mingw-x64", jobs=1)
        wcmd = capture_configure(w, "libvpx")
        self.assertIn("--target=x86_64-win64-gcc", wcmd)
        self.assertIn("--as=nasm", wcmd)

    def test_openssl_target_for_aarch64_cross(self):
        b = Builder(self.project, "linux-aarch64", jobs=1)
        self.assertIn("linux-aarch64", capture_configure(b, "openssl").split())


class ForceCross(unittest.TestCase):
    def test_same_arch_force_cross_uses_separate_tree(self):
        project = ProjectConfig.load(ROOT)
        native = Builder(project, "linux-native", jobs=1)
        forced = Builder(project, "linux-native", jobs=1, force_cross=True)
        self.assertFalse(native.cross)
        self.assertTrue(forced.cross)
        self.assertEqual(forced.build_key, "linux-native-cross")
        self.assertEqual(forced.sysroot.name, "linux-native-cross")
        self.assertNotEqual(native.sysroot, forced.sysroot)
        self.assertTrue(forced.tc.cross_file and forced.tc.cross_file.exists())
        self.assertIn(
            "needs_exe_wrapper = true",
            forced.tc.cross_file.read_text(encoding="utf-8"),
        )

    def test_windows_force_cross_keeps_generators_baseline(self):
        project = ProjectConfig.load(ROOT)
        # MSYS2 UCRT64 是 x86_64 工具链；测试在 CI 的 aarch64 runner 上也要
        # 验证 Windows 路径，故固定 host_arch，而不是把架构写进生产配置。
        with mock.patch(
            "gtkcross.toolchain.host_arch", return_value="x86_64"
        ), mock.patch.dict(
            os.environ,
            {"CFLAGS": "-march=x86-64-v4", "CXXFLAGS": "-march=x86-64-v4"},
            clear=False,
        ):
            b = Builder(
                project, "msys2-ucrt64", jobs=1, force_cross=True,
            )
            self.assertTrue(b.cross)
            # 未显式传 --native-sysroot 时，同架构 force-cross 默认用该
            # target 的 baseline 前缀作为 build machine 工具 sysroot。
            self.assertEqual(b.native_sysroot, ROOT / "out/msys2-ucrt64")
            self.assertEqual(b.sysroot.name, "msys2-ucrt64-cross")
            self.assertEqual(b.build_key, "msys2-ucrt64-cross")
            self.assertEqual(b.tc.build_triple, "x86_64-pc-msys")
            self.assertEqual(b.tc.build_cc, "gcc")
            cross = b.tc.cross_file.read_text(encoding="utf-8")
            self.assertIn("-march=x86-64-v4", cross)
            native = b.tc.native_file.read_text(encoding="utf-8")
            self.assertIn("c = 'gcc'", native)
            self.assertNotIn("-march=x86-64-v4", native)
            wrapper = b.tc.native_pkg_config.read_text(encoding="utf-8")
            self.assertIn(
                f"{b.native_sysroot.as_posix()}/lib/pkgconfig;", wrapper
            )

    def test_cross_selector_applies_only_to_cross(self):
        project = ProjectConfig.load(ROOT)
        native = Builder(project, "msys2-ucrt64", jobs=1)
        forced = Builder(
            project, "msys2-ucrt64", jobs=1, force_cross=True,
            native_sysroot=ROOT / "out/msys2-ucrt64",
        )
        patch = "adwaita-icon-theme-0001-skip-icon-cache-in-cross.patch"
        self.assertNotIn(
            patch, native.resolved_recipe("adwaita-icon-theme").patches
        )
        self.assertIn(
            patch, forced.resolved_recipe("adwaita-icon-theme").patches
        )
        self.assertNotIn(
            "EXPAT_BUILD_TESTS",
            native.resolved_recipe("expat").cmake["defines"],
        )
        self.assertEqual(
            forced.resolved_recipe("expat").cmake["defines"][
                "EXPAT_BUILD_TESTS"
            ],
            "OFF",
        )


class CrossPlatformGating(unittest.TestCase):
    def setUp(self):
        self.project = ProjectConfig.load(ROOT)

    def test_windows_cross_uses_windows_family(self):
        b = Builder(self.project, "linux-mingw-x64", jobs=1)
        gtk = " ".join(b.resolved_recipe("gtk").meson["options"])
        self.assertIn("-Dx11-backend=false", gtk)
        self.assertIn(
            "gtk-0001-fallback-to-windows-locale.patch",
            b.resolved_recipe("gtk").patches,
        )
        self.assertIn(
            "glib-0001-win32-package-parser-no-winrt.patch",
            b.resolved_recipe("glib-base").patches,
        )
        self.assertIn(
            "glib-0001-win32-package-parser-no-winrt.patch",
            b.resolved_recipe("glib").patches,
        )
        self.assertIn(
            "adwaita-icon-theme-0001-skip-icon-cache-in-cross.patch",
            b.resolved_recipe("adwaita-icon-theme").patches,
        )
        self.assertIn("--target=x86_64-win64-gcc", capture_configure(b, "libvpx"))
        self.assertNotIn("mesa", b.order(["libadwaita"]))

    def test_aarch64_cross_uses_linux_family(self):
        b = Builder(self.project, "linux-aarch64", jobs=1)
        gtk = " ".join(b.resolved_recipe("gtk").meson["options"])
        self.assertIn("-Dx11-backend=true", gtk)
        self.assertIn("mesa", b.order(["libadwaita"]))


if __name__ == "__main__":
    unittest.main()

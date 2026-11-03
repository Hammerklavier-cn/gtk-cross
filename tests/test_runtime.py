"""Tests for the dep-pack based runtime-DLL collection (gtkcross/runtime.py)."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gtkcross.runtime import (
    RuntimeResult,
    _install_resolved,
    _is_system_name,
    _parse_resolved,
    collect_runtime,
    find_dep_pack,
    runtime_roots,
    source_dlls,
)
from gtkcross.toolchain import Toolchain


def _write(path: Path, data: bytes = b"MZ fake") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class RuntimeRootsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def test_msys2_prefix_uses_msystem_subsystem(self):
        bin_dir = self.base / "mingw64" / "bin"
        _write(bin_dir / "libstdc++-6.dll")
        tc = Toolchain(
            {
                "host": "windows",
                "target_os": "windows",
                "msys2_root": str(self.base),
                "msystem": "MINGW64",
            },
            "fake",
            self.base / "sysroot",
        )
        self.assertEqual(runtime_roots(tc), [bin_dir])

    def test_ucrt64_is_not_mapped_to_mingw64(self):
        bin_dir = self.base / "ucrt64" / "bin"
        _write(bin_dir / "libgcc_s_seh-1.dll")
        tc = Toolchain(
            {
                "host": "windows",
                "target_os": "windows",
                "msys2_root": str(self.base),
                "msystem": "UCRT64",
            },
            "fake",
            self.base / "sysroot",
        )
        self.assertEqual(runtime_roots(tc), [bin_dir])

    def test_missing_runtime_directory_is_an_error(self):
        tc = Toolchain(
            {
                "host": "windows",
                "target_os": "windows",
                "msys2_root": str(self.base),
                "msystem": "MINGW64",
            },
            "fake",
            self.base / "sysroot",
        )
        with self.assertRaises(ValueError):
            runtime_roots(tc)


class FindDepPackTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def test_explicit_file(self):
        exe = _write(self.base / "dep-pack.exe")
        self.assertEqual(find_dep_pack(exe), exe.resolve())

    def test_explicit_directory(self):
        exe = _write(self.base / "dep-pack.exe")
        self.assertEqual(find_dep_pack(self.base), exe.resolve())

    def test_missing_explicit_path_is_an_error(self):
        with self.assertRaises(ValueError):
            find_dep_pack(self.base / "nope")

    def test_environment_variable(self):
        exe = _write(self.base / "dep-pack")
        with mock.patch.dict(
            "os.environ", {"DEP_PACK": str(exe)}, clear=False
        ):
            self.assertEqual(find_dep_pack(), exe.resolve())


class SourceDllsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sysroot = Path(self._tmp.name)

    def test_bin_plugins_and_loaders_are_all_sources(self):
        _write(self.sysroot / "bin" / "libgtk-4-1.dll")
        _write(self.sysroot / "lib" / "gstreamer-1.0" / "libgstd3d12.dll")
        _write(
            self.sysroot
            / "lib"
            / "gdk-pixbuf-2.0"
            / "2.10.0"
            / "loaders"
            / "libpixbufloader-png.dll"
        )
        _write(self.sysroot / "lib" / "libgtk-4.dll.a")
        _write(self.sysroot / "include" / "not-a-dll.dll")

        names = sorted(p.name for p in source_dlls(self.sysroot))
        self.assertEqual(
            names,
            [
                "libgstd3d12.dll",
                "libgtk-4-1.dll",
                "libpixbufloader-png.dll",
            ],
        )


class ParseResolvedTest(unittest.TestCase):
    def test_verbatim_prefix_and_ansi_are_removed(self):
        log = (
            "\x1b[32m INFO\x1b[0m dep_pack > Will copy "
            "\\\\?\\C:\\msys64\\mingw64\\bin\\libstdc++-6.dll\n"
            " INFO dep_pack > Will copy C:\\build\\out\\bin\\libgtk-4-1.dll\n"
        )
        paths = _parse_resolved(log)
        self.assertEqual(
            [p.name for p in paths],
            ["libstdc++-6.dll", "libgtk-4-1.dll"],
        )
        self.assertFalse(str(paths[0]).startswith("\\\\?\\"))


class InstallResolvedTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.sysroot = self.base / "sysroot"
        self.root = self.base / "mingw64" / "bin"
        self.output = self.base / "dep-pack-out"

        self.gtk = _write(self.sysroot / "bin" / "libgtk-4-1.dll")
        self.plugin = _write(
            self.sysroot / "lib" / "gstreamer-1.0" / "libgstd3d12.dll"
        )
        # The toolchain prefix also contains pacman's glib!  A filename-only
        # match must not replace the recipe's own libglib.
        self.glib = _write(self.sysroot / "bin" / "libglib-2.0-0.dll", b"recipe")
        _write(self.root / "libglib-2.0-0.dll", b"pacman")
        # Toolchain-owned runtimes.
        self.stdc = _write(self.root / "libstdc++-6.dll", b"toolchain stdc++")
        _write(self.root / "libgcc_s_seh-1.dll", b"toolchain gcc")
        # A dependency from neither the sysroot nor the toolchain.
        self.foreign = self.base / "elsewhere" / "foreign.dll"

    def test_copies_runtime_and_never_overwrites_recipe_dlls(self):
        copied, updated, present, foreign = _install_resolved(
            [
                self.gtk,
                self.plugin,
                self.root / "libstdc++-6.dll",
                self.root / "libgcc_s_seh-1.dll",
                self.root / "libglib-2.0-0.dll",
                self.foreign,
            ],
            self.sysroot,
            [self.root],
        )
        self.assertEqual(copied, ["libgcc_s_seh-1.dll", "libstdc++-6.dll"])
        self.assertEqual(updated, [])
        self.assertIn("libgtk-4-1.dll", present)
        self.assertIn("libgstd3d12.dll", present)
        self.assertIn("libglib-2.0-0.dll", present)
        self.assertEqual(foreign, ["foreign.dll"])
        # The recipe's glib keeps its content; runtime files come from the
        # toolchain, not from dep-pack's flat output.
        self.assertEqual(self.glib.read_bytes(), b"recipe")
        self.assertEqual(
            (self.sysroot / "bin" / "libstdc++-6.dll").read_bytes(),
            b"toolchain stdc++",
        )

    def test_dry_run_does_not_write(self):
        copied, _, _, _ = _install_resolved(
            [self.root / "libstdc++-6.dll"],
            self.sysroot,
            [self.root],
            dry_run=True,
        )
        self.assertEqual(copied, ["libstdc++-6.dll"])
        self.assertFalse((self.sysroot / "bin" / "libstdc++-6.dll").exists())

    def test_stale_runtime_is_replaced(self):
        stale = _write(
            self.sysroot / "bin" / "libstdc++-6.dll", b"stale other gcc"
        )
        copied, updated, _, _ = _install_resolved(
            [stale, self.root / "libstdc++-6.dll"],
            self.sysroot,
            [self.root],
        )
        self.assertEqual(copied, [])
        self.assertEqual(updated, ["libstdc++-6.dll"])
        self.assertEqual(stale.read_bytes(), b"toolchain stdc++")

    def test_already_collected_runtime_is_idempotent(self):
        _install_resolved(
            [self.root / "libstdc++-6.dll"], self.sysroot, [self.root]
        )
        copied, updated, present, _ = _install_resolved(
            [self.root / "libstdc++-6.dll"], self.sysroot, [self.root]
        )
        self.assertEqual(copied, [])
        self.assertEqual(updated, [])
        self.assertIn("libstdc++-6.dll", present)


class CollectRuntimeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.sysroot = self.base / "out" / "msys2-mingw64"
        self.root = self.base / "mingw64" / "bin"
        _write(self.sysroot / "bin" / "libgtk-4-1.dll")
        _write(self.sysroot / "bin" / "libgstd3d12-1.0-0.dll")
        self.stdc = _write(self.root / "libstdc++-6.dll", b"toolchain")
        self.tc = Toolchain(
            {
                "host": "windows",
                "target_os": "windows",
                "msys2_root": str(self.base),
                "msystem": "MINGW64",
            },
            "fake",
            self.sysroot,
        )
        self.dep_pack = _write(self.base / "dep-pack.exe", b"fake exe")

    def _fake_run(self, cmd, **kwargs):
        stderr = (
            " INFO  dep_pack > Will copy "
            f"{self.sysroot / 'bin' / 'libgtk-4-1.dll'}\n"
            " INFO  dep_pack > Will copy "
            f"{self.stdc}\n"
            " WARN  dep_pack > Library api-ms-win-crt-runtime-l1-1-0.dll "
            "not found in api-ms-win-crt-runtime-l1-1-0.dll\n"
        )
        return subprocess.CompletedProcess(cmd, 0, "", stderr)

    def test_end_to_end_with_fake_dep_pack(self):
        with mock.patch("gtkcross.runtime.subprocess.run", self._fake_run):
            result = collect_runtime(
                self.sysroot, self.tc, self.dep_pack
            )
        self.assertIsInstance(result, RuntimeResult)
        self.assertEqual(result.copied, ["libstdc++-6.dll"])
        self.assertTrue(
            (self.sysroot / "bin" / "libstdc++-6.dll").is_file()
        )
        self.assertEqual(
            (self.sysroot / "bin" / "libstdc++-6.dll").read_bytes(),
            b"toolchain",
        )
        # API-set warnings are expected and must not fail the run.
        self.assertEqual(result.external_unresolved, [])

    def test_non_windows_target_is_rejected(self):
        linux = Toolchain(
            {"host": "linux", "target_os": "linux"}, "linux", self.sysroot
        )
        with self.assertRaises(ValueError):
            collect_runtime(self.sysroot, linux, self.dep_pack)


class SystemNameTest(unittest.TestCase):
    def test_api_sets_are_system(self):
        self.assertTrue(_is_system_name("api-ms-win-crt-runtime-l1-1-0.dll"))
        self.assertTrue(_is_system_name("ext-ms-win-foo.dll"))

    def test_real_system_dll_is_system(self):
        self.assertTrue(_is_system_name("kernel32.dll"))


if __name__ == "__main__":
    unittest.main()

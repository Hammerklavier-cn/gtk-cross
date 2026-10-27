"""Tests for the relocatable sysroot export (gtkcross/export.py)."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from gtkcross.export import (
    _relocate_linkname,
    _sysroot_forms,
    export_sysroot,
    normalize_pc,
)

PC_TEMPLATE = """\
prefix={prefix}
exec_prefix=${{prefix}}
libdir=${{prefix}}/lib
includedir=${{prefix}}/include

Name: foo
Description: test package
Version: 1.0
Libs: -L${{libdir}} -lfoo
Cflags: -I${{includedir}}
"""


class NormalizePcTest(unittest.TestCase):
    def test_windows_prefix_becomes_pcfiledir(self):
        sysroot = Path("C:/build/out/msys2-mingw64")
        pc_dir = sysroot / "lib" / "pkgconfig"
        text = PC_TEMPLATE.format(prefix="/c/build/out/msys2-mingw64")

        out = normalize_pc(text, sysroot, pc_dir)

        self.assertIn("prefix=${pcfiledir}/../..", out)
        self.assertNotIn("/c/build/out", out)
        self.assertIn("Libs: -L${libdir} -lfoo", out)

    def test_share_pkgconfig_uses_the_same_relative_prefix(self):
        sysroot = Path("C:/build/out/msys2-mingw64")
        pc_dir = sysroot / "share" / "pkgconfig"
        text = PC_TEMPLATE.format(prefix="C:/build/out/msys2-mingw64")

        out = normalize_pc(text, sysroot, pc_dir)

        self.assertIn("prefix=${pcfiledir}/../..", out)
        self.assertNotIn("C:/build", out)

    def test_residual_windows_path_is_rejected(self):
        sysroot = Path("C:/build/out/msys2-mingw64")
        text = PC_TEMPLATE.format(prefix="/c/build/out/msys2-mingw64")
        text += "Libs.private: -L/c/other/toolchain/lib\n"

        with self.assertRaises(ValueError):
            normalize_pc(text, sysroot, sysroot / "lib" / "pkgconfig")

    def test_relative_prefix_is_left_alone(self):
        sysroot = Path("/tmp/sysroot")
        pc_dir = sysroot / "lib" / "pkgconfig"
        text = PC_TEMPLATE.format(prefix="${pcfiledir}/../..")

        out = normalize_pc(text, sysroot, pc_dir)

        self.assertEqual(out, text)

    def test_crlf_is_normalized(self):
        sysroot = Path("C:/build/out/msys2-mingw64")
        pc_dir = sysroot / "lib" / "pkgconfig"
        text = PC_TEMPLATE.format(prefix="/c/build/out/msys2-mingw64")
        text = text.replace("\n", "\r\n")

        out = normalize_pc(text, sysroot, pc_dir)

        self.assertNotIn("\r", out)
        self.assertIn("prefix=${pcfiledir}/../..\n", out)


class ExportSysrootTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.sysroot = self.base / "out" / "msys2-mingw64"
        self._write(
            self.sysroot / "lib" / "pkgconfig" / "foo.pc",
            PC_TEMPLATE.format(prefix=str(self.sysroot)),
        )
        self._write(
            self.sysroot / "share" / "pkgconfig" / "bar.pc",
            PC_TEMPLATE.format(prefix=str(self.sysroot)),
        )
        self._write(self.sysroot / "bin" / "gtk-4.dll", b"MZ fake dll")
        self._write(self.sysroot / "lib" / "libgtk-4.dll.a", b"!<arch>\n")
        self._write(self.sysroot / "include" / "gtk" / "gtk.h", b"/* header */\n")

    @staticmethod
    def _write(path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            data = data.encode("utf-8")
        path.write_bytes(data)

    def _export(self, name="gtk-cross.tar"):
        out = self.base / name
        result = export_sysroot(
            self.sysroot, out, target="msys2-mingw64"
        )
        return out, result

    def test_archive_layout_prefix_and_modes(self):
        out, result = self._export()
        self.assertTrue(out.exists())
        self.assertEqual(result.pc_files, 2)

        with tarfile.open(out, "r:") as tf:
            names = set(tf.getnames())
            self.assertIn("gtk-cross-msys2-mingw64/lib/pkgconfig/foo.pc", names)
            self.assertIn("gtk-cross-msys2-mingw64/bin/gtk-4.dll", names)
            self.assertIn(
                "gtk-cross-msys2-mingw64/lib/libgtk-4.dll.a", names
            )

            pc = tf.extractfile(
                "gtk-cross-msys2-mingw64/lib/pkgconfig/foo.pc"
            ).read().decode("utf-8")
            self.assertIn("prefix=${pcfiledir}/../..", pc)
            self.assertNotIn(str(self.sysroot), pc)

            shared_pc = tf.extractfile(
                "gtk-cross-msys2-mingw64/share/pkgconfig/bar.pc"
            ).read().decode("utf-8")
            self.assertIn("prefix=${pcfiledir}/../..", shared_pc)

            self.assertEqual(
                tf.getmember("gtk-cross-msys2-mingw64/bin/gtk-4.dll").mode,
                0o755,
            )
            self.assertEqual(
                tf.getmember(
                    "gtk-cross-msys2-mingw64/lib/libgtk-4.dll.a"
                ).mode,
                0o644,
            )
            self.assertEqual(
                tf.getmember(
                    "gtk-cross-msys2-mingw64/lib/pkgconfig/foo.pc"
                ).mode,
                0o644,
            )

    def test_custom_archive_root(self):
        out = self.base / "custom.tar"
        export_sysroot(
            self.sysroot,
            out,
            target="msys2-mingw64",
            archive_root="gtk-cross-linux-native-x86_64",
        )
        with tarfile.open(out, "r:") as tf:
            self.assertIn(
                "gtk-cross-linux-native-x86_64/lib/pkgconfig/foo.pc",
                tf.getnames(),
            )
            self.assertNotIn(
                "gtk-cross-msys2-mingw64/lib/pkgconfig/foo.pc",
                tf.getnames(),
            )

    def test_unsafe_archive_root_is_rejected(self):
        with self.assertRaises(ValueError):
            export_sysroot(
                self.sysroot,
                self.base / "x.tar",
                target="msys2-mingw64",
                archive_root="../evil",
            )

    def test_absolute_symlink_inside_sysroot_is_relocated(self):
        target = self.sysroot / "lib" / "libgtk-4.dll.a"
        link = self.sysroot / "lib" / "abs-link.dll.a"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("平台不允许创建符号链接")

        out, _ = self._export()
        with tarfile.open(out, "r:") as tf:
            member = tf.getmember(
                "gtk-cross-msys2-mingw64/lib/abs-link.dll.a"
            )
            self.assertTrue(member.issym())
            self.assertFalse(os.path.isabs(member.linkname))
            self.assertEqual(member.linkname, "libgtk-4.dll.a")

    def test_symlink_outside_sysroot_warns_but_is_kept(self):
        link = self.sysroot / "lib" / "outside.dll.a"
        try:
            os.symlink("/opt/other/libfoo.a", link)
        except (OSError, NotImplementedError):
            self.skipTest("平台不允许创建符号链接")

        out, result = self._export()
        self.assertTrue(any("sysroot 外" in w for w in result.warnings))
        with tarfile.open(out, "r:") as tf:
            member = tf.getmember(
                "gtk-cross-msys2-mingw64/lib/outside.dll.a"
            )
            self.assertEqual(member.linkname, "/opt/other/libfoo.a")

    def test_bad_output_suffix_is_rejected(self):
        with self.assertRaises(ValueError):
            export_sysroot(
                self.sysroot, self.base / "out.zip", target="msys2-mingw64"
            )

    def test_output_inside_sysroot_is_rejected(self):
        with self.assertRaises(ValueError):
            export_sysroot(
                self.sysroot,
                self.sysroot / "gtk-cross.tar",
                target="msys2-mingw64",
            )

    def test_invalid_zstd_level_is_rejected(self):
        with self.assertRaises(ValueError):
            export_sysroot(
                self.sysroot,
                self.base / "gtk-cross.tar.zst",
                target="msys2-mingw64",
                zstd_level=0,
            )

    @unittest.skipUnless(shutil.which("zstd"), "zstd 未安装")
    def test_zstd_archive(self):
        out, _ = self._export("gtk-cross.tar.zst")
        self.assertEqual(out.read_bytes()[:4], b"\x28\xb5\x2f\xfd")
        raw = subprocess.run(
            ["zstd", "-dc", str(out)], check=True, capture_output=True
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tf:
            self.assertIn(
                "gtk-cross-msys2-mingw64/lib/pkgconfig/foo.pc",
                set(tf.getnames()),
            )


class RelocateLinknameTest(unittest.TestCase):
    """os.readlink 在 Windows 返回 NT 的 substitute name（`\\\\?\\C:\\...`）。

    绝对目标才会被规范成那种形式，所以这条路径只能在 Windows 上碰到；用纯
    函数测，免得依赖"能不能建符号链接"这种平台特权。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sysroot = Path(self._tmp.name) / "msys2-mingw64"
        (self.sysroot / "lib").mkdir(parents=True)
        self.link = self.sysroot / "lib" / "abs-link.dll.a"
        self.forms = _sysroot_forms(self.sysroot)

    def _relocate(self, linkname):
        return _relocate_linkname(linkname, self.link, self.sysroot, self.forms)

    def test_win32_file_prefix_is_relocated(self):
        linkname = "\\\\?\\" + str(self.sysroot) + "\\lib\\libgtk-4.dll.a"
        self.assertEqual(self._relocate(linkname), ("libgtk-4.dll.a", None))

    def test_nt_substitute_name_prefix_is_relocated(self):
        linkname = "\\??\\" + str(self.sysroot) + "\\lib\\libgtk-4.dll.a"
        self.assertEqual(self._relocate(linkname), ("libgtk-4.dll.a", None))

    def test_unc_prefix_is_relocated(self):
        sysroot = Path("//server/share/sysroot")
        linkname = "\\\\?\\UNC\\server\\share\\sysroot\\lib\\libfoo.a"
        new, warning = _relocate_linkname(
            linkname, sysroot / "lib" / "l.a", sysroot, _sysroot_forms(sysroot)
        )
        self.assertEqual((new, warning), ("libfoo.a", None))

    def test_verbatim_target_outside_sysroot_is_kept_verbatim(self):
        linkname = "\\\\?\\C:\\other\\libfoo.a"
        new, warning = self._relocate(linkname)
        self.assertEqual(new, linkname)
        self.assertIn("sysroot 外", warning)

    def test_sibling_directory_is_not_mistaken_for_the_sysroot(self):
        linkname = os.path.join(str(self.sysroot) + "-extra", "lib", "libfoo.a")
        new, warning = self._relocate(linkname)
        self.assertEqual(new, linkname)
        self.assertIn("sysroot 外", warning)


if __name__ == "__main__":
    unittest.main()
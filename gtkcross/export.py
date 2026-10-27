"""Export a built sysroot as a relocatable archive.

Windows system roots are configured with an absolute ``--prefix`` (for example
``/c/Users/.../out/msys2-mingw64``).  Publishing that tree as-is would make
every ``.pc`` file point at the build machine, so the archive is unusable after
download.  This module rewrites the pkg-config metadata to be relative to the
``.pc`` file itself (``${pcfiledir}/../..``); pkgconf expands that variable and
is the pkg-config implementation this project and Fedora use.  The result can be
extracted anywhere and consumed with only ``PKG_CONFIG_LIBDIR`` -- no
``PKG_CONFIG_SYSROOT_DIR`` required.

Only pkg-config metadata is relocated.  CMake package files and libtool
``.la`` files may still contain absolute paths; the published sysroot is
intended for pkg-config consumers (C/meson and cargo/gtk4-rs).
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List

from .toolchain import posix

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


@dataclass
class ExportResult:
    path: Path
    size: int
    sha256: str
    files: int
    pc_files: int
    warnings: List[str] = field(default_factory=list)


def _sysroot_forms(sysroot: Path) -> List[str]:
    """Every textual form the build may have written into a .pc file.

    On Windows the same root appears as ``C:/...`` (CMake), ``C:\\...`` (native
    tools) and ``/c/...`` (the path format this framework passes as --prefix).
    Longest first so a nested path is replaced before its parent.
    """
    forms = {str(sysroot), sysroot.as_posix(), posix(sysroot)}
    cleaned = {f.rstrip("/\\") for f in forms if f}
    return sorted(cleaned, key=len, reverse=True)


def _is_windows_root(sysroot: Path) -> bool:
    s = str(sysroot)
    return bool(_WINDOWS_DRIVE.match(s)) or bool(re.match(r"^/[A-Za-z]/", s))


def _is_absolute(value: str) -> bool:
    return (
        value.startswith("/")
        or value.startswith("\\")
        or bool(_WINDOWS_DRIVE.match(value))
    )


def _relative_prefix(sysroot: Path, pc_dir: Path) -> str:
    rel = Path(os.path.relpath(sysroot, pc_dir)).as_posix()
    return "${pcfiledir}" if rel == "." else f"${{pcfiledir}}/{rel}"


def normalize_pc(text: str, sysroot: Path, pc_dir: Path | None = None) -> str:
    """Make one .pc file relocatable, or raise if a build path survives."""
    # Windows builds can leave CRLF in .pc files; pkgconf treats the trailing
    # \r as part of the value on Linux.  Normalize once for the whole archive.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    pc_dir = pc_dir or (sysroot / "lib" / "pkgconfig")
    prefix = _relative_prefix(sysroot, pc_dir)

    m = re.search(r"(?m)^prefix\s*=\s*(.*)$", text)
    original_prefix = m.group(1).strip() if m else ""

    forms = _sysroot_forms(sysroot)
    for form in forms:
        text = text.replace(form, prefix)

    # An absolute prefix that was *not* under the sysroot (for example a stale
    # /usr or another Windows drive) is still wrong for a hermetic tree.  A
    # prefix that was already relative (${pcfiledir}/...) is left alone.
    if original_prefix and _is_absolute(original_prefix):
        text = re.sub(r"(?m)^prefix\s*=.*$", f"prefix={prefix}", text, count=1)

    for form in forms:
        if form in text:
            raise ValueError(
                f"{pc_dir}: .pc 中仍有构建机路径 {form!r}，无法作为可重定位产物"
            )

    if _is_windows_root(sysroot):
        for needle in ("/c/", "C:/", "C:\\"):
            if needle in text:
                raise ValueError(
                    f"{pc_dir}: .pc 中仍有 Windows 绝对路径（{needle!r}），"
                    "无法作为可重定位产物"
                )

    return text


def _relocate_linkname(
    linkname: str, link_path: Path, sysroot: Path, forms: List[str]
) -> tuple[str, str | None]:
    """Turn an absolute symlink into sysroot into a relative one."""
    posix_link = linkname.replace("\\", "/")
    if not _is_absolute(posix_link):
        return linkname, None

    for form in forms:
        f = form.replace("\\", "/")
        if posix_link == f or posix_link.startswith(f + "/"):
            rel = posix_link[len(f):].lstrip("/")
            if not rel:
                return ".", None
            target = sysroot / rel
            new = os.path.relpath(target, link_path.parent).replace(os.sep, "/")
            return new, None

    return linkname, f"符号链接指向 sysroot 外（原样保留）: {link_path} -> {linkname}"


def _walk_entries(root: Path) -> Iterator[os.DirEntry]:
    for entry in sorted(os.scandir(root), key=lambda e: e.name):
        yield entry
        if entry.is_dir(follow_symlinks=False):
            yield from _walk_entries(Path(entry.path))


def _add_tree(
    tf: tarfile.TarFile,
    sysroot: Path,
    archive_root: str | None,
    warnings: List[str],
) -> tuple[int, int]:
    files = 0
    pc_files = 0
    forms = _sysroot_forms(sysroot)

    for entry in _walk_entries(sysroot):
        path = Path(entry.path)
        rel = path.relative_to(sysroot).as_posix()
        arcname = f"{archive_root}/{rel}" if archive_root else rel
        info = tf.gettarinfo(str(path), arcname=arcname)
        if info is None:
            continue

        if entry.is_symlink():
            info.mode = 0o777
            new_link, warning = _relocate_linkname(
                info.linkname, path, sysroot, forms
            )
            info.linkname = new_link
            if warning:
                warnings.append(warning)
            tf.addfile(info)
            continue

        if entry.is_dir(follow_symlinks=False):
            info.mode = 0o755
            tf.addfile(info)
            continue

        # A .pc symlink is kept as a link; its target is a regular file that
        # gets normalized on its own iteration.
        if path.suffix == ".pc" and path.parent.name == "pkgconfig":
            try:
                original = path.read_bytes().decode("utf-8")
            except UnicodeDecodeError as e:
                raise ValueError(
                    f"{path}: 不是 UTF-8 文本，无法重写 pkg-config 前缀"
                ) from e
            normalized = normalize_pc(original, sysroot, path.parent)
            data = normalized.encode("utf-8")
            info.size = len(data)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))
            pc_files += 1
        else:
            # Windows stat() does not carry meaningful POSIX mode bits.
            # bin/ is where the target-side executables live, and .dll/.exe
            # are the only things a consumer may want to execute under Wine.
            info.mode = (
                0o755
                if rel.startswith("bin/") or path.suffix.lower() in (".exe", ".dll")
                else 0o644
            )
            with open(path, "rb") as f:
                tf.addfile(info, f)
        files += 1

    return files, pc_files


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def export_sysroot(
    sysroot: Path,
    output: Path,
    *,
    target: str | None = None,
    archive_root: str | None = None,
    zstd_level: int = 15,
) -> ExportResult:
    """Write a relocatable tar(.zst) of ``sysroot``.

    ``archive_root`` is the top-level directory inside the archive.  It
    defaults to ``gtk-cross-<target>``; callers that publish architecture-
    specific trees (``linux-native`` on x86_64/aarch64) pass an explicit root.
    """
    sysroot = Path(sysroot)
    output = Path(output)
    if not sysroot.is_dir():
        raise ValueError(f"sysroot 不存在: {sysroot}")
    if not 1 <= zstd_level <= 22:
        raise ValueError("zstd 压缩级别需在 1..22 之间")
    try:
        output.resolve().relative_to(sysroot.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("输出文件不能位于 sysroot 内（归档会包含自身）")

    if archive_root is None and target:
        archive_root = f"gtk-cross-{target}"
    if archive_root is not None:
        root_path = Path(archive_root)
        if root_path.is_absolute() or ".." in root_path.parts:
            raise ValueError("archive_root 必须是相对路径且不含 ..")

    compressed = output.name.endswith(".tar.zst") or output.suffix == ".zst"
    if not compressed and output.suffix not in ("", ".tar"):
        raise ValueError("输出文件名需以 .tar 或 .tar.zst 结尾")

    output.parent.mkdir(parents=True, exist_ok=True)
    warnings: List[str] = []

    if compressed:
        fd, tmp_name = tempfile.mkstemp(
            prefix=output.name + ".", suffix=".tar", dir=output.parent
        )
        os.close(fd)
        tar_path = Path(tmp_name)
    else:
        tar_path = output

    try:
        with tarfile.open(tar_path, "w", format=tarfile.PAX_FORMAT) as tf:
            if archive_root:
                info = tarfile.TarInfo(archive_root)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.mtime = int(time.time())
                tf.addfile(info)
            files, pc_files = _add_tree(tf, sysroot, archive_root, warnings)
    except Exception:
        if compressed and tar_path.exists():
            tar_path.unlink()
        raise

    if compressed:
        zstd = shutil.which("zstd")
        if not zstd:
            tar_path.unlink(missing_ok=True)
            raise RuntimeError(
                "压缩 .tar.zst 需要 zstd；请安装 zstd，或改用 -o xxx.tar"
            )
        try:
            subprocess.run(
                [zstd, "-T0", f"-{zstd_level}", "-f", str(tar_path),
                 "-o", str(output)],
                check=True,
            )
        finally:
            tar_path.unlink(missing_ok=True)

    return ExportResult(
        path=output,
        size=output.stat().st_size,
        sha256=_sha256(output),
        files=files,
        pc_files=pc_files,
        warnings=warnings,
    )
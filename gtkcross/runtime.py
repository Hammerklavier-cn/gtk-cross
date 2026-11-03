"""Collect external (toolchain) runtime DLLs into a Windows sysroot.

A Windows sysroot built by gtk-cross contains the DLLs produced by the recipes,
but the MinGW runtime (``libstdc++-6.dll``, ``libgcc_s_seh-1.dll``,
``libwinpthread-1.dll`` and, for MSYS2, ``libdl.dll``) belongs to the compiler
toolchain and is not installed by any recipe.  Consumers that ship
``bin/*.dll`` therefore have to get those files from somewhere.  Picking them
from the wrong compiler (for example the Linux cross linker instead of the
MSYS2 GCC that built the sysroot) produces the classic Windows error

    无法定位程序输入点 _ZSt15__get_once_callv 于动态链接库 libgstd3d12-1.0-0.dll 上

because the imported C++ runtime is older than the DLL that imports it.

This module runs ``dep-pack`` (recursive PE import analysis) in dry-run mode
over every DLL in the sysroot.  dep-pack prints the resolved absolute path of
each dependency; only those that live in the toolchain prefix
(``C:/msys64/<msystem>/bin`` for MSYS2) are copied into ``<sysroot>/bin``.
Using the resolved path instead of the bare filename is essential: the MSYS2
prefix also contains pacman-provided copies of glib, gtk, harfbuzz, ... and a
filename-only match would overwrite the recipes' own DLLs.

``gtkcross export`` then packages the result.  Only Windows hosts are handled
by the CLI: on Linux the sysroot is built with the same mingw-w64 compiler that
links consumers, so its runtime is already the one the consumer uses.
"""

from __future__ import annotations

import filecmp
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .toolchain import Toolchain

# dep-pack is a Rust binary; the released Windows build is used in CI.
DEP_PACK_URL = (
    "https://gitcode.com/hammerklavier/dep-pack/releases/download/v0.1.0/"
    "dep-pack_x86_64-pc-windows-gnu.tar.xz"
)
DEP_PACK_SHA256 = (
    "d97031616b766a36b574471793f3fe500b3f312b3652abe6bc95ed4e792db24d"
)

_UNRESOLVED_RE = re.compile(r"Library\s+(\S+)\s+not found")
_WILL_COPY_RE = re.compile(r"Will copy\s+(.+?)\s*$", re.MULTILINE)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_VERBATIM_PREFIXES = ("\\\\?\\", "\\??\\", "//?/", "/??/")

_API_SET_PREFIXES = ("api-ms-win-", "ext-ms-")

# Compiler runtime DLLs.  These are safe to replace in the sysroot if an older
# version was copied there earlier: their names can never collide with a recipe
# DLL.  Other external DLLs are only added when absent, so a same-named recipe
# DLL (glib, harfbuzz, ...) is never overwritten.
_TOOLCHAIN_RUNTIME_PREFIXES = (
    "libstdc++",
    "libgcc_",
    "libwinpthread",
    "libssp",
    "libgomp",
    "libquadmath",
    "libatomic",
    "libdl",
)

# Fallback when SystemRoot is not available (tests on Linux, or a Windows host
# whose System32 is not visible).  API sets are handled separately.
_SYSTEM_DLLS = {
    "advapi32.dll", "bcrypt.dll", "comctl32.dll", "comdlg32.dll",
    "crypt32.dll", "d2d1.dll", "d3d11.dll", "d3d12.dll", "dwmapi.dll",
    "dxgi.dll", "gdi32.dll", "imm32.dll", "iphlpapi.dll", "kernel32.dll",
    "msvcrt.dll", "ntdll.dll", "ole32.dll", "oleaut32.dll", "opengl32.dll",
    "powrprof.dll", "secur32.dll", "setupapi.dll", "shell32.dll",
    "shlwapi.dll", "ucrtbase.dll", "user32.dll", "uxtheme.dll",
    "version.dll", "winhttp.dll", "winmm.dll", "ws2_32.dll",
    "wtsapi32.dll",
}


@dataclass
class RuntimeResult:
    """Outcome of one runtime-collection run."""

    copied: List[str] = field(default_factory=list)
    # Existing sysroot runtime DLLs that differed from the toolchain copy and
    # were replaced (for example a libstdc++ from another GCC).
    updated: List[str] = field(default_factory=list)
    # Dependencies that already exist in the sysroot (recipe DLLs).
    already_present: List[str] = field(default_factory=list)
    # Dependencies resolved outside both the sysroot and the toolchain prefix.
    # These are not copied; a non-empty list means the closure is not hermetic.
    foreign: List[str] = field(default_factory=list)
    # Import names dep-pack could not resolve at all.
    unresolved: List[str] = field(default_factory=list)
    runtime_roots: List[Path] = field(default_factory=list)

    @property
    def external_unresolved(self) -> List[str]:
        """Unresolved names that are neither Windows API sets nor system DLLs."""
        return [
            name
            for name in self.unresolved
            if not _is_system_name(name)
        ]


def _is_system_name(name: str) -> bool:
    """Whether an unresolved import is supplied by Windows itself."""
    lower = name.lower()
    if lower.startswith(_API_SET_PREFIXES) or lower in _SYSTEM_DLLS:
        return True
    if os.name != "nt":
        return False
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    for directory in (system_root / "System32", system_root):
        try:
            if (directory / name).is_file():
                return True
        except OSError:
            pass
    return False


def _strip_verbatim(value: str) -> str:
    """Drop the NT verbatim prefix dep-pack inherits from ``realpath``."""
    for prefix in _VERBATIM_PREFIXES:
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def find_dep_pack(explicit: str | Path | None = None) -> Path:
    """Locate the dep-pack executable.

    Precedence: ``--dep-pack`` > ``$DEP_PACK`` > ``PATH``.  The Windows release
    archive contains ``dep-pack_x86_64-pc-windows-gnu/dep-pack.exe``; callers
    may pass either the executable or that directory.
    """
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("DEP_PACK")
    if env:
        candidates.append(Path(env))
    for candidate in candidates:
        path = candidate
        if path.is_dir():
            for name in ("dep-pack", "dep-pack.exe"):
                exe = path / name
                if exe.is_file():
                    return exe.resolve()
            raise ValueError(f"目录中没有 dep-pack: {candidate}")
        if path.is_file():
            return path.resolve()
        raise ValueError(f"dep-pack 不存在: {candidate}")
    for name in ("dep-pack", "dep-pack.exe"):
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    raise ValueError(
        "找不到 dep-pack；用 --dep-pack 指定，或把它放到 PATH，"
        f"或设置 DEP_PACK。发布包: {DEP_PACK_URL}"
    )


def runtime_roots(tc: Toolchain) -> List[Path]:
    """Toolchain directories that provide the MinGW runtime DLLs.

    MSYS2 puts them in ``<msys2_root>/<msystem>/bin`` (mingw64, ucrt64, ...).
    Linux mingw-w64 distributions split libstdc++ (under ``/usr/lib/gcc/...``)
    and libwinpthread (under ``/usr/<triple>/...``); all plausible directories
    are returned so a missing file in the first one does not hide the rest.
    """
    cfg = tc.cfg
    if tc.host == "windows":
        root = cfg.get("msys2_root")
        if not root:
            raise ValueError(f"{tc.name}: 缺少 msys2_root，无法定位 MinGW 运行库")
        subsystem = (cfg.get("msystem") or "MINGW64").lower()
        if subsystem == "msys":
            subsystem = "usr"
        paths = [Path(root) / subsystem / "bin"]
    else:
        triple = str(cfg.get("host_triple") or tc.host_triple or "")
        if not triple:
            raise ValueError(f"{tc.name}: 缺少 host_triple，无法定位 MinGW 运行库")
        paths = [
            Path("/usr") / triple / "bin",
            Path("/usr") / triple / "lib",
            Path("/usr") / triple / "sys-root" / "mingw" / "bin",
            Path("/usr") / "lib" / triple,
        ]
        gcc_root = Path("/usr") / "lib" / "gcc" / triple
        if gcc_root.is_dir():
            paths.extend(
                sorted(
                    (p for p in gcc_root.iterdir() if p.is_dir()),
                    key=lambda p: p.name,
                    reverse=True,
                )
            )

    # Keep only directories that actually contain at least one runtime DLL.
    roots: List[Path] = []
    for path in paths:
        if path.is_dir() and any(
            (path / name).is_file()
            for name in (
                "libstdc++-6.dll",
                "libgcc_s_seh-1.dll",
                "libwinpthread-1.dll",
                "libdl.dll",
            )
        ):
            roots.append(path)
    if not roots:
        checked = ", ".join(str(p) for p in paths)
        raise ValueError(
            f"{tc.name}: 在以下目录找不到 MinGW 运行库 DLL: {checked}"
        )
    return roots


def source_dlls(sysroot: Path) -> List[Path]:
    """Every runtime DLL in the sysroot (bin/ and lib/, including plugins).

    The GStreamer plugins under ``lib/gstreamer-1.0`` and the gdk-pixbuf
    loaders are not in any executable's static import graph, but they still
    import the C++ runtime.  Scanning the whole runtime tree is therefore
    intentional; headers and data directories never contain DLLs.
    """
    sysroot = Path(sysroot)
    dlls: List[Path] = []
    for sub in ("bin", "lib"):
        root = sysroot / sub
        if root.is_dir():
            dlls.extend(
                p
                for p in root.rglob("*.dll")
                if p.is_file() and p.suffix.lower() == ".dll"
            )
    return sorted(dlls, key=lambda p: str(p).lower())


def _dep_pack_env(
    sysroot: Path,
    source_dirs: List[Path],
    roots: List[Path],
    dep_pack: Path,
) -> dict[str, str]:
    """Environment for dep-pack with a hermetic DLL search path.

    Windows resolves PE imports via the process DLL search path, and lddtree
    follows ``PATH``.  Put the sysroot first (its own DLLs must win), then only
    the toolchain runtime directories, then the Windows system directories.
    The caller's PATH is deliberately not included: a stray MSYS2 prefix from
    another MSYSTEM must not leak into the closure.
    """
    env = dict(os.environ)
    env["RUST_LOG"] = "info"
    if os.name != "nt":
        return env

    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    parts = [
        str(sysroot / "bin"),
        # Plugins/loaders live outside bin/; their siblings must still be
        # resolvable so dep-pack does not report them as missing.
        *(str(directory) for directory in source_dirs),
        *(str(root) for root in roots),
        str(system_root / "System32"),
        str(system_root),
        str(dep_pack.parent),
    ]
    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: List[str] = []
    for part in parts:
        key = part.lower()
        if key not in seen:
            seen.add(key)
            unique.append(part)
    env["PATH"] = os.pathsep.join(unique)
    return env


def _run_dep_pack(
    dep_pack: Path,
    sources: List[Path],
    ignore_paths: List[Path],
    env: dict[str, str],
) -> str:
    """Run dep-pack in dry-run mode and return its stderr (log output)."""
    cmd = [str(dep_pack), "-n"]
    for source in sources:
        cmd += ["-s", str(source)]
    for ignored in ignore_paths:
        cmd += ["-i", str(ignored)]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=900,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("dep-pack 分析超时（900s）") from e
    except OSError as e:
        raise RuntimeError(f"无法运行 dep-pack: {e}") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"dep-pack 失败（exit {proc.returncode}）: {detail[-2000:]}"
        )
    # dep-pack logs at INFO level on stderr; stdout is normally empty.
    return proc.stderr or ""


def _parse_resolved(log: str) -> List[Path]:
    """Extract the absolute paths dep-pack's dry-run says it would copy."""
    text = _ANSI_RE.sub("", log)
    paths: List[Path] = []
    for match in _WILL_COPY_RE.finditer(text):
        raw = _strip_verbatim(match.group(1).strip().strip('"'))
        if raw:
            # Dep-pack runs on Windows, but tests (and future Linux hosts) may
            # parse its log on another platform; normalize separators so
            # Path(...).name still yields the basename.
            paths.append(Path(raw.replace("\\", "/")))
    return paths


def _is_runtime_name(name: str) -> bool:
    lower = name.lower()
    return any(lower.startswith(prefix) for prefix in _TOOLCHAIN_RUNTIME_PREFIXES)


def _index_root(root: Path) -> dict[str, Path]:
    """case-insensitive basename -> path map for one runtime directory."""
    index: dict[str, Path] = {}
    for path in root.glob("*.dll"):
        if path.is_file():
            index.setdefault(path.name.lower(), path)
    return index


def _install_resolved(
    resolved: List[Path],
    sysroot: Path,
    roots: List[Path],
    *,
    dry_run: bool = False,
) -> tuple[List[str], List[str], List[str], List[str]]:
    """Install resolved toolchain dependencies into ``<sysroot>/bin``.

    ``resolved`` is the set of dependencies dep-pack would copy, with their
    original absolute paths.  Paths inside the sysroot are recipe DLLs and are
    left alone.  Paths inside the toolchain prefix are copied in; the
    toolchain copy is the authority.  A name that already exists in the sysroot
    is never overwritten unless it is a compiler runtime DLL (``libstdc++``,
    ``libgcc``, ...), because the MSYS2 prefix also contains pacman versions of
    glib/gtk/harfbuzz and a filename-only match would clobber the recipes'
    output.
    """
    sysroot = sysroot.resolve()
    root_indexes = [_index_root(root) for root in roots]
    existing = {
        p.name.lower(): p
        for p in sysroot.rglob("*.dll")
        if p.is_file()
    }

    copied: List[str] = []
    updated: List[str] = []
    already_present: List[str] = []
    foreign: List[str] = []

    for path in resolved:
        try:
            path = path.resolve()
        except OSError:
            pass
        name = path.name
        key = name.lower()
        current = existing.get(key)

        try:
            inside_sysroot = path.is_relative_to(sysroot)
        except (OSError, ValueError):
            inside_sysroot = False

        source = None
        for index in root_indexes:
            if key in index:
                source = index[key]
                break

        if inside_sysroot:
            # A compiler runtime that was copied into the sysroot earlier may
            # be stale.  dep-pack resolves it from the sysroot (bin/ is first
            # on PATH), so this is the only place that can correct it.  Never
            # do this for non-runtime names: glib and friends also exist in
            # the MSYS2 prefix under the same basename.
            if (
                source is not None
                and _is_runtime_name(name)
                and (current is None or not filecmp.cmp(current, source, shallow=False))
            ):
                if not dry_run:
                    dest = sysroot / "bin" / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
                    existing[key] = dest
                updated.append(name)
                continue
            already_present.append(name)
            continue

        if source is None:
            if current is not None:
                already_present.append(name)
            else:
                foreign.append(name)
            continue

        if current is not None:
            if not _is_runtime_name(name):
                # A recipe DLL with the same basename.  It may have been
                # resolved from the toolchain prefix because the plugin dir is
                # not on the normal loader path; never overwrite it.
                already_present.append(name)
                continue
            if filecmp.cmp(current, source, shallow=False):
                already_present.append(name)
                continue

        if not dry_run:
            dest = sysroot / "bin" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            existing[key] = dest
        if current is None:
            copied.append(name)
        else:
            updated.append(name)

    return (
        sorted(set(copied)),
        sorted(set(updated)),
        sorted(set(already_present)),
        sorted(set(foreign)),
    )


def collect_runtime(
    sysroot: Path,
    tc: Toolchain,
    dep_pack: Path,
    *,
    dry_run: bool = False,
) -> RuntimeResult:
    """Run dep-pack over ``sysroot`` and install external runtime DLLs."""
    sysroot = Path(sysroot)
    if tc.target_os != "windows":
        raise ValueError(
            f"{tc.name}: runtime 仅适用于 Windows target（当前 target_os="
            f"{tc.target_os}）"
        )
    if not sysroot.is_dir():
        raise ValueError(f"sysroot 不存在: {sysroot}")

    roots = runtime_roots(tc)
    sources = source_dlls(sysroot)
    if not sources:
        raise ValueError(f"{sysroot} 下没有任何 DLL；先构建 target")

    source_dirs = sorted(
        {p.parent for p in sources}, key=lambda p: str(p).lower()
    )
    env = _dep_pack_env(sysroot, source_dirs, roots, dep_pack)
    ignore_paths: List[Path] = []
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        if system_root.is_dir():
            ignore_paths.append(system_root)

    log = _run_dep_pack(dep_pack, sources, ignore_paths, env)
    resolved = _parse_resolved(log)
    if not resolved:
        raise RuntimeError(
            "dep-pack 没有报告任何依赖（日志格式可能已变化）；"
            "拒绝静默产出不完整的运行库集合"
        )
    copied, updated, already_present, foreign = _install_resolved(
        resolved, sysroot, roots, dry_run=dry_run
    )

    unresolved = sorted(set(_UNRESOLVED_RE.findall(log)))
    return RuntimeResult(
        copied=copied,
        updated=updated,
        already_present=already_present,
        foreign=foreign,
        unresolved=unresolved,
        runtime_roots=roots,
    )

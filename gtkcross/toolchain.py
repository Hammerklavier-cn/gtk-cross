"""Toolchain abstraction: host environment + target cross configuration.

Each target maps to a toolchains/<name>.yaml which describes:
- how to invoke the host shell (on Windows: MSYS2 bash with MSYSTEM=MINGW64)
- tool names (cc/cxx/meson/cmake/pkg-config/...)
- base environment with $SYSROOT placeholders (isolation from system libs)

All build commands are executed as login bash scripts through this layer so
that PATH/MSYSTEM are set up correctly on every host platform.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import yaml


def posix(path: Path | str) -> str:
    """Windows path (C:/x) -> MSYS posix (/c/x) for use inside bash."""
    p = str(path).replace("\\", "/")
    if len(p) >= 3 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]
    return p


class Toolchain:
    """A (host, target) build environment."""

    def __init__(self, cfg: dict, name: str, sysroot: Path):
        self.cfg = cfg
        self.name = name
        self.sysroot = sysroot
        self.host = cfg.get("host", "windows")

    @classmethod
    def load(cls, path: Path, sysroot: Path) -> "Toolchain":
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cls(cfg, path.stem, sysroot)

    # -- environment -----------------------------------------------------

    def bash_argv(self) -> List[str]:
        if self.host == "windows":
            root = self.cfg["msys2_root"]
            return [str(Path(root) / "usr" / "bin" / "bash.exe"), "-lc"]
        return ["bash", "-lc"]

    def _subsystem_dir(self) -> str:
        """MSYSTEM -> MSYS2 subsystem bin dir: MINGW64 -> mingw64, UCRT64 -> ucrt64."""
        msys = (self.cfg.get("msystem") or "MINGW64").lower()
        return "usr" if msys == "msys" else msys

    def base_path(self) -> str:
        """PATH entries (posix), sysroot first: our DLLs/libs win over system."""
        entries = [f"$SYSROOT/bin"]
        if self.host == "windows":
            root = posix(Path(self.cfg["msys2_root"]))
            sub = self._subsystem_dir()
            entries += [f"{root}/{sub}/bin", f"{root}/usr/bin"]
        else:
            entries += ["/usr/bin", "/bin"]
        return ":".join(entries)

    def prelude(self) -> List[str]:
        """Bash exports applied before every build command.

        Values containing $SYSROOT must be expanded by the shell, so they
        are emitted with double quotes (or bare, in assignment context).
        """
        lines = [f"export PATH={self.base_path()}"]
        tools = self.cfg.get("tools", {})
        for key, val in tools.items():
            lines.append(f"export {key.upper()}='{val}'")
        for k, v in self.cfg.get("env", {}).items():
            lines.append(f"export {k}=\"{v}\"")
        # Bias every compiler search at our sysroot (headers/libs first).
        lines.append('export CPPFLAGS="-I$SYSROOT/include"')
        # 注：曾尝试在 LDFLAGS 加 -static-libgcc/-static-libstdc++ 以消除产物对
        # MSYS2 工具链运行时（libgcc_s_seh-1.dll / libstdc++-6.dll）的依赖，
        # 实测不可靠：meson 对 link_language=cpp 的工程会在 -Wl,--start-group
        # 内显式追加 -lstdc++，该参数位于 -static-libstdc++ 之后，动态导入库
        # 仍被选中（harfbuzz 主库仍依赖 libstdc++-6.dll，仅 subset 侥幸清掉）。
        # 时灵时不灵的全局开关比不加更糟，且多份 C++ 运行时在跨 DLL 场景有
        # 风险，故不启用。这些运行时 DLL 由 MSYS2 工具链提供，非本项目产物。
        lines.append('export LDFLAGS="-L$SYSROOT/lib"')
        # 让 gcc 的 *内建* 库搜索目录包含 $SYSROOT/lib。LDFLAGS 里的 -L 做不到
        # 这件事，而 g-ir-scanner 只读内建目录：
        #   giscanner/ccompiler.py resolve_windows_libs() 走 GCC 分支时，
        #   libsearch = options.library_paths + `gcc -print-search-dirs` 的
        #   libraries: 各目录，然后按 lib<name>.dll.a / lib<name>.a / ... 逐个
        #   os.path.exists() 探测。
        # 而 options.library_paths 来自扫描器的 --library-path 参数（对应 -L），
        # 上游 gobject-introspection 的 meson.build 并不传它（实测 .dat 里只有
        # --library= 与 --pkg=），故 libsearch 实际只剩 gcc 内建目录。
        # 实测：`gcc -L<anything> -print-search-dirs` 与不加 -L 的输出**完全相同**，
        # 所以仅靠 LDFLAGS 无法让扫描器看到 sysroot 里的 libglib-2.0.dll.a，
        # 会报 "ERROR: can't resolve libraries to shared libraries: glib-2.0,
        # gobject-2.0"；LIBRARY_PATH 是唯一能进入该列表的环境变量。
        # 本机 ucrt64 之所以曾通过，是因为系统恰好装了 glib2
        # （C:/msys64/ucrt64/lib/libglib-2.0.dll.a 正好落在 gcc 内建目录里），
        # 属侥幸；干净环境（CI 镜像、本机 mingw64）必失败。
        # 只加自己的 sysroot，不引入系统路径，不损 hermetic（且与 -L 同源）。
        lines.append('export LIBRARY_PATH="$SYSROOT/lib"')
        # Login bash (-l) sources /etc/profile.d/000-msys2.sh which exports
        # XDG_DATA_DIRS pointing at the MSYS2 prefixes.  On Windows GLib uses
        # a non-empty XDG_DATA_DIRS *exclusively* (g_build_system_data_dirs)
        # and skips the DLL/exe-relative "share" fallback, so GSettings schema
        # sources end up NULL -> "source != NULL" assertions in tests/apps.
        # Point it at our sysroot so meson test finds gschemas.compiled.
        # MSYS2 auto-converts the POSIX path to Windows form for native exes.
        lines.append('export XDG_DATA_DIRS="$SYSROOT/share"')
        return lines

    def wrap(self, script: str) -> List[str]:
        """Return argv running a bash script in this toolchain environment."""
        full = "\n".join(self.prelude() + [script])
        return self.bash_argv() + ["set -e;" + full]

    def run(
        self,
        script: str,
        cwd: Path | None = None,
        print_cmd: bool = True,
        capture: bool = False,
    ) -> subprocess.CompletedProcess:
        """Run a bash script in this toolchain environment.

        子进程输出经 Python 管道流式中继（不继承 fd）：非 capture 模式
        逐行写控制台并复制到构建日志（events.sink_write）；capture 模式
        只落日志不上屏（由调用方解析摘要），stdout 返回值不变。
        """
        from .events import sink_write

        argv = self.wrap(script)
        if print_cmd:
            print(f"$ bash -lc … {script[:160]}")
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ, "MSYSTEM": self.cfg.get("msystem", "MINGW64"),
                 "SYSROOT": posix(self.sysroot)},
        )
        chunks: list[bytes] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            chunks.append(line)
            sink_write(line)
            if not capture:
                try:
                    sys.stdout.write(line.decode("utf-8", errors="replace"))
                    sys.stdout.flush()
                except OSError:
                    pass
        proc.stdout.close()
        returncode = proc.wait()
        out = b"".join(chunks)
        return subprocess.CompletedProcess(argv, returncode, stdout=out)

    def expect(self, script: str, cwd: Path | None = None) -> None:
        r = self.run(script, cwd)
        if r.returncode != 0:
            raise RuntimeError(
                f"command failed (exit {r.returncode}) in {cwd or self.sysroot}: "
                f"{script[:200]}"
            )

    # -- capability checks -------------------------------------------------

    def doctor(self) -> List[str]:
        problems: List[str] = []
        for name in ("cc", "cxx", "pkg_config", "meson", "ninja", "cmake", "make"):
            tool = (self.cfg.get("tools") or {}).get(name)
            if not tool:
                continue
            r = self.run(f"{tool} --version >/dev/null 2>&1", print_cmd=False)
            if r.returncode != 0:
                problems.append(f"{name} ({tool}): not found on PATH")
        if self.host == "windows" and not Path(self.cfg["msys2_root"]).exists():
            problems.append(f"MSYS2 root {self.cfg['msys2_root']} does not exist")
        return problems

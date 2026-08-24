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
        lines.append('export LDFLAGS="-L$SYSROOT/lib"')
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

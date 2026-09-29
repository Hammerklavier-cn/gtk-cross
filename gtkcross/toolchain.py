"""Toolchain abstraction: host environment + target cross configuration.

Each target maps to a toolchains/<name>.yaml which describes:
- how to invoke the host shell (on Windows: MSYS2 bash with MSYSTEM=MINGW64)
- tool names (cc/cxx/meson/cmake/pkg-config/...)
- base environment with $SYSROOT placeholders (isolation from system libs)
- target_os: the OS family the *output* belongs to (windows | linux); recipes'
  `targets:` family selectors match on it, so Windows-only patches/options are
  written once for every Windows target. Defaults to `host` for native
  toolchains and must be set explicitly on a cross toolchain.

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
        # 目标 OS 家族：recipe 的 targets: 家族选择器（windows / linux）按它匹配。
        # 缺省回退到 host——msys2-* 这类"宿主=目标"的原生工具链两者相同；交叉
        # 工具链（如 linux 宿主产 win64 目标）必须显式写 target_os，否则会把
        # Windows 专属补丁应用到 Linux 目标上。
        self.target_os = cfg.get("target_os") or self.host

    @classmethod
    def load(cls, path: Path, sysroot: Path) -> "Toolchain":
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cls(cfg, path.stem, sysroot)

    @property
    def shared_suffixes(self) -> List[str]:
        """本平台"共享变体"的文件名后缀（判断 .pc 描述静态还是共享库用）。

        Windows 的共享库带导入库 libNAME.dll.a；Linux/ELF 直接看 libNAME.so。
        """
        if self.target_os == "windows":
            return [".dll.a"]
        return [".so"]

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
            entries += ["/usr/local/bin", "/usr/bin", "/bin"]
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
        if self.target_os == "windows":
            lines.append('export LDFLAGS="-L$SYSROOT/lib"')
        else:
            # Linux/ELF 上 LDFLAGS 需要三件事，缺一在实测中都会失败：
            #   -L                       ：在自己的 sysroot 里找库
            #   -Wl,-rpath-link          ：链接期解析传递依赖。glib 经 sysroot 的
            #     iconv.h 用的是 GNU libiconv（`iconv_open` 被宏重写成
            #     `libiconv_open`，实测不加 -liconv 直接 undefined reference），
            #     于是 libglib-2.0.so 的 DT_NEEDED 带 libiconv.so.2；meson 给
            #     可执行文件加 -Wl,--no-undefined，只有 -L 时报
            #     "libiconv.so.2 ... not found (try using -rpath or -rpath-link)"
            #   -Wl,-rpath               ：让产物**自带** sysroot 搜索路径。
            #     glib 自带测试里有两处用"清洗过的环境"启动子进程
            #     （gschema-compile.c 的 execve(argv, envp={NULL})、
            #     gsubprocess 的 /env 用 setenv 覆盖整张环境表），此时
            #     LD_LIBRARY_PATH 不存在，实测报
            #       gsubprocess-testprog: error while loading shared libraries:
            #         libiconv.so.2: cannot open shared object file
            #     并连带 3 项测试失败（spawn-test / gschema-compile / gsubprocess）。
            #     Windows 上同一测试是过的：PE 的 DLL 搜索默认含"可执行文件所在
            #     目录"，不依赖环境变量。把闭包做成自定位，也顺带让 sysroot 产物
            #     在不设 LD_LIBRARY_PATH 时可直接运行。
            #   -lm                      ：ELF 上的 libm 与 Windows 的 CRT 同类
            #     （msvcrt/ucrt + mingw 的 libmingwex），不是"外部依赖包"，所以
            #     不放进 sysroot 是对的，但它得能被链上。不显式给的后果实测过：
            #     libtiff 的 cmake/FindCMath.cmake 先
            #     check_symbol_exists(pow "math.h" ...)（无 -lm ⇒ 失败），再
            #     find_library(CMath_LIBRARY NAMES m)（被本项目的
            #     CMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY 挡在 sysroot 内 ⇒ 空），
            #     于是 CMakeLists.txt:171 的 find_package(CMath REQUIRED) 直接
            #     "Configuring incomplete"。mingw 上同一探测是过的——它的 spec
            #     文件默认就把 pow 链上了。
            lines.append(
                'export LDFLAGS="-L$SYSROOT/lib '
                '-Wl,-rpath-link,$SYSROOT/lib -Wl,-rpath,$SYSROOT/lib -lm"'
            )
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
        if self.target_os != "windows":
            # 静态优先策略（default_library: static）在 Linux 上要求所有 .a 都是
            # PIC：它们最终会被链进 glib/cairo/pango/gtk 这些 .so，非 PIC 目标文件
            # 在链接共享库时报
            #   relocation R_X86_64_32 against `.rodata' can not be used when
            #   making a shared object; recompile with -fPIC
            # Windows（PE/COFF）没有这个概念，故只在非 Windows 目标注入。
            # 保留调用方已有的 CFLAGS（`${CFLAGS:-}`）以免 -l 参数被清空。
            lines.append('export CFLAGS="${CFLAGS:-} -fPIC"')
            lines.append('export CXXFLAGS="${CXXFLAGS:-} -fPIC"')
            # 让构建期能**运行**刚产出的工具（glib-compile-resources /
            # g-ir-compiler / glib-compile-schemas 等）。它们链接 sysroot 里的
            # 自建共享库，而 meson 只会为构建目录里的产物设 build-rpath，从
            # pkg-config 带进来的外部前缀库不在其中。Linux 上实测这条前缀依赖是
            # $SYSROOT/lib/libiconv.so.2（readelf -d libglib-2.0.so 的 NEEDED 只有
            # libiconv.so.2 与 libc.so.6；NLS 那侧走 glibc 自带的 libintl，
            # 见 recipes/platform-notes.md 第 8 条）。实测报：
            #   glib-compile-resources: error while loading shared libraries:
            #     libiconv.so.2: cannot open shared object file
            #   FAILED: gio/tests/test5.gresource (exit 127)
            # Windows 上同一问题由 base_path() 把 $SYSROOT/bin 前置 PATH 解决，
            # ELF 上的对等机制就是前置 LD_LIBRARY_PATH（保留调用方已有的值）。

            lines.append(
                'export LD_LIBRARY_PATH="$SYSROOT/lib'
                '${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"'
            )
        # Login bash (-l) sources /etc/profile.d/000-msys2.sh which exports
        # XDG_DATA_DIRS pointing at the MSYS2 prefixes.  On Windows GLib uses
        # a non-empty XDG_DATA_DIRS *exclusively* (g_build_system_data_dirs)
        # and skips the DLL/exe-relative "share" fallback, so GSettings schema
        # sources end up NULL -> "source != NULL" assertions in tests/apps.
        # Point it at our sysroot so meson test finds gschemas.compiled.
        # MSYS2 auto-converts the POSIX path to Windows form for native exes.
        if self.target_os == "windows":
            lines.append('export XDG_DATA_DIRS="$SYSROOT/share"')
        else:
            # Linux 上不能只给 sysroot/share：GTK 找图标主题（hicolor 等）、
            # GTK/GDK 测试找系统数据目录都要走 XDG_DATA_DIRS，覆盖成单一目录会
            # 让宿主主题/图标缺失。sysroot 前置即可保证自建产物优先。
            lines.append(
                'export XDG_DATA_DIRS="$SYSROOT/share:/usr/local/share:/usr/share"'
            )
            # 构建与测试期不得读开发机的桌面配置（hermetic 的一部分，和
            # PKG_CONFIG_LIBDIR 整体替换、XDG_DATA_DIRS 前置 sysroot 同一类）。
            # 实测触发过程（linux-x64，libadwaita 首跑）：68 项测试**全部**
            # SIGABRT，报错只有一行，而且与本项目产物无关——
            #   Gtk-WARNING **: Unknown key gtk-modules in
            #     /home/<user>/.config/gtk-4.0/settings.ini
            # GTK 会读 $XDG_CONFIG_HOME/gtk-4.0/settings.ini，而 libadwaita 的
            # 测试环境自带 G_DEBUG=fatal-warnings（meson 传的 run_env 里），
            # 于是宿主桌面上一条给 GTK3 用的遗留设置就让每个测试进程 abort。
            # 指向 sysroot 下一个空的配置目录后，配置来源只剩"自建 sysroot +
            # GTK 默认值"。Windows 目标不动：那边现况全绿，且 APPDATA 语义不同。
            lines.append('export XDG_CONFIG_HOME="$SYSROOT/etc/xdg"')
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
        env = {**os.environ, "SYSROOT": posix(self.sysroot)}
        # MSYSTEM 只在 MSYS2 宿主注入。此前它无条件写入（缺省 MINGW64），
        # 于是 Linux 原生构建的子进程也带着 MSYSTEM=MINGW64 —— 上游构建脚本
        # （meson/autotools/各包自己的 shell 片段）常用 `test -n "$MSYSTEM"`
        # 判定"我在 MSYS2 里"并切换路径转换、依赖探测等行为，在 Linux 宿主上
        # 这是假信号。
        if self.host == "windows":
            env["MSYSTEM"] = self.cfg.get("msystem", "MINGW64")
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
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

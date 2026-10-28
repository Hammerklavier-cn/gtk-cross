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
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List

import yaml


def posix(path: Path | str) -> str:
    """Windows path (C:/x) -> MSYS posix (/c/x) for use inside bash."""
    p = str(path).replace("\\", "/")
    if len(p) >= 3 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]
    return p


def host_arch() -> str:
    """Host CPU name normalised to the Meson/ELF spelling."""
    return {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }.get(platform.machine().lower(), platform.machine().lower())


def _ini_str(value: str) -> str:
    # Meson machine files deliberately reject double-quoted strings; use single
    # quotes and escape the two characters that matter to its parser.
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _ini_list(values: List[str]) -> str:
    return "[" + ", ".join(_ini_str(v) for v in values) + "]"


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
        # 交叉编译：host 是执行构建的机器（决定 shell / 构建工具），target_*
        # 是产物平台。cross=True 时 prelude 会把 native sysroot 与 target sysroot
        # 分开，并让三个引擎走 cross 路径。
        self.cross = bool(cfg.get("cross", False))
        self.build_arch = str(cfg.get("build_arch") or host_arch())
        self.target_arch = str(cfg.get("target_arch") or self.build_arch)
        self.host_triple = cfg.get("host_triple")
        # autotools 的 --build 三元组。原生 MSYS2 只写了 host_triple，缺省与它
        # 相同，保持既有命令逐字节不变。
        self.build_triple = cfg.get("build_triple") or self.host_triple
        self.target_cflags = shlex.split(str(cfg.get("target_cflags") or ""))
        self.target_cxxflags = shlex.split(
            str(cfg.get("target_cxxflags") or "")
        ) or list(self.target_cflags)
        self.exe_wrapper = self._parse_wrapper(cfg.get("exe_wrapper"))
        # Builder 在初始化时补齐：native tools sysroot 与生成的 cross 文件。
        self.native_sysroot: Path | None = None
        self.cross_file: Path | None = None
        self.native_file: Path | None = None
        self.native_pkg_config: Path | None = None
        self.cmake_toolchain: Path | None = None
        self.cmake_no_run: Path | None = None

    @staticmethod
    def _parse_wrapper(value) -> List[str]:
        if not value:
            return []
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value]
        return shlex.split(str(value))

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

    # -- cross flags / generated toolchain files --------------------------

    def _target_include_flags(self) -> List[str]:
        """Target headers for Meson/CMake cross files.

        Meson 的 cross 构建不会把环境 `CPPFLAGS` 传给 target compiler
        （它只用于 build machine）；CMake 的 toolchain 同理。因此 sysroot
        的 -I 必须显式写进 cross file / CMAKE_*_FLAGS_INIT，否则 glib 的
        iconv 探测会命中宿主 /usr/include/iconv.h，链接期缺 -liconv。
        """
        return [f"-I{posix(self.sysroot)}/include"]

    def _target_cflags(self) -> List[str]:
        """Target compiler flags: 工具链声明的 arch flags + 用户 CFLAGS。

        Cross 构建里 CFLAGS **不**直接进全局环境（那会让 Meson 的 native
        build 也拿到目标 ISA flags）；由 cross file / CMake toolchain /
        autotools 的 configure 前缀单独注入。Linux 目标的 -fPIC 也在这一层
        追加，因为它只影响 target 静态库。
        """
        flags = list(self.target_cflags)
        for flag in shlex.split(os.environ.get("CFLAGS", "")):
            if flag not in flags:
                flags.append(flag)
        if self.target_os != "windows" and "-fPIC" not in flags:
            flags.append("-fPIC")
        return flags

    def _target_cxxflags(self) -> List[str]:
        flags = list(self.target_cxxflags)
        for flag in shlex.split(os.environ.get("CXXFLAGS", "")):
            if flag not in flags:
                flags.append(flag)
        if self.target_os != "windows" and "-fPIC" not in flags:
            flags.append("-fPIC")
        return flags

    def _target_cppflags(self) -> List[str]:
        return shlex.split(os.environ.get("CPPFLAGS", ""))

    def _target_ldflags(self) -> List[str]:
        root = posix(self.sysroot)
        flags = [f"-L{root}/lib"]
        if self.target_os != "windows":
            flags += [
                f"-Wl,-rpath-link,{root}/lib",
                f"-Wl,-rpath,{root}/lib",
                "-lm",
            ]
        flags += shlex.split(os.environ.get("LDFLAGS", ""))
        return flags

    def meson_native_file_text(self) -> str:
        """Native (build machine) file for Meson cross builds.

        GTK 的 Meson 通过 `dependency('gio-2.0', native: true)` 取
        glib-compile-resources / glib-mkenums；如果 native pkg-config 仍指向
        target sysroot，它会拿到 target 的二进制并在构建机上执行（v4 在无
        AVX-512 的 CPU 上 SIGILL）。native file 把 pkg-config 指到
        linux-tools 的 .pc。
        """
        if not self.native_pkg_config:
            raise ValueError("native pkg-config wrapper is not generated")
        return (
            "[binaries]\n"
            f"pkg-config = {_ini_str(posix(self.native_pkg_config))}\n"
        )

    def meson_cross_file_text(self) -> str:
        """Generate a Meson cross file for this toolchain.

        build_machine 与 host_machine 可以同 arch（x86-64-v4 就是这种情况）；
        `needs_exe_wrapper = true` 告诉 Meson 不要直接执行目标二进制，否则
        Meson 会按 system/cpu_family 相同推断"可以跑"，又回到 SIGILL。
        """
        tools = self.cfg.get("tools") or {}
        link_search = f"-L{posix(self.sysroot)}/lib"
        out: List[str] = ["[binaries]"]
        for key, tool_key in (
            ("c", "cc"),
            ("cpp", "cxx"),
            ("ar", "ar"),
            ("strip", "strip"),
            ("ranlib", "ranlib"),
            ("nm", "nm"),
            ("objcopy", "objcopy"),
        ):
            value = tools.get(tool_key)
            if value:
                # c/cpp 带 -L：Meson 的 cc.has_function()/find_library()
                # 链接检查不会使用 c_link_args，甚至会把 c_args 里的 -L
                # 过滤掉；把搜索路径钉在编译器 argv 上才能让 -liconv 这类
                # checker 链接成功。
                if key in ("c", "cpp"):
                    out.append(f"{key} = {_ini_list([value, link_search])}")
                else:
                    out.append(f"{key} = {_ini_str(value)}")
        if self.target_os == "windows":
            for key, tool_key in (("windres", "windres"), ("dlltool", "dlltool")):
                value = tools.get(tool_key)
                if value:
                    out.append(f"{key} = {_ini_str(value)}")
        pkg_config = tools.get("pkg_config")
        if pkg_config:
            out.append(f"pkg-config = {_ini_str(pkg_config)}")
        if self.exe_wrapper:
            out.append(f"exe_wrapper = {_ini_list(self.exe_wrapper)}")

        link_search = [f"-L{posix(self.sysroot)}/lib"]
        cflags = (
            self._target_include_flags()
            + self._target_cppflags()
            + link_search
            + self._target_cflags()
        )
        cxxflags = (
            self._target_include_flags()
            + self._target_cppflags()
            + link_search
            + self._target_cxxflags()
        )
        ldflags = self._target_ldflags()
        if cflags or cxxflags or ldflags:
            out += ["", "[built-in options]"]
            if cflags:
                out.append(f"c_args = {_ini_list(cflags)}")
            if cxxflags:
                out.append(f"cpp_args = {_ini_list(cxxflags)}")
            if ldflags:
                out.append(f"c_link_args = {_ini_list(ldflags)}")
                out.append(f"cpp_link_args = {_ini_list(ldflags)}")

        system = "windows" if self.target_os == "windows" else "linux"
        out += [
            "",
            "[host_machine]",
            f"system = {_ini_str(system)}",
            f"cpu_family = {_ini_str(self.target_arch)}",
            f"cpu = {_ini_str(self.target_arch)}",
            "endian = 'little'",
            "",
            "[properties]",
            "needs_exe_wrapper = true",
        ]
        return "\n".join(out) + "\n"

    def cmake_toolchain_text(self) -> str:
        """Generate a CMake toolchain file for cross builds."""
        tools = self.cfg.get("tools") or {}
        system = "Windows" if self.target_os == "windows" else "Linux"
        out = [
            f"set(CMAKE_SYSTEM_NAME {system})",
            f"set(CMAKE_SYSTEM_PROCESSOR {self.target_arch})",
        ]
        for var, tool_key in (
            ("CMAKE_C_COMPILER", "cc"),
            ("CMAKE_CXX_COMPILER", "cxx"),
            ("CMAKE_AR", "ar"),
            ("CMAKE_RANLIB", "ranlib"),
            ("CMAKE_STRIP", "strip"),
            ("CMAKE_NM", "nm"),
            ("CMAKE_OBJCOPY", "objcopy"),
        ):
            value = tools.get(tool_key)
            if value:
                out.append(f'set({var} "{value}")')
        if self.target_os == "windows" and tools.get("windres"):
            out.append(f'set(CMAKE_RC_COMPILER "{tools["windres"]}")')

        cflags = " ".join(
            self._target_include_flags()
            + self._target_cppflags()
            + self._target_cflags()
        )
        cxxflags = " ".join(
            self._target_include_flags()
            + self._target_cppflags()
            + self._target_cxxflags()
        )
        ldflags = " ".join(self._target_ldflags())
        if cflags:
            out.append(f'set(CMAKE_C_FLAGS_INIT "{cflags}")')
        if cxxflags:
            out.append(f'set(CMAKE_CXX_FLAGS_INIT "{cxxflags}")')
        if ldflags:
            for var in (
                "CMAKE_EXE_LINKER_FLAGS_INIT",
                "CMAKE_SHARED_LINKER_FLAGS_INIT",
                "CMAKE_MODULE_LINKER_FLAGS_INIT",
            ):
                out.append(f'set({var} "{ldflags}")')

        root = posix(self.sysroot)
        out += [
            f'set(CMAKE_FIND_ROOT_PATH "{root}")',
            "set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM BOTH)",
            "set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)",
            "set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)",
            "set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)",
            f'set(CMAKE_PREFIX_PATH "{root}")',
        ]
        if self.exe_wrapper:
            out.append(
                'set(CMAKE_CROSSCOMPILING_EMULATOR "'
                + ";".join(self.exe_wrapper)
                + '")'
            )
        elif self.host == "linux":
            # 没有真正的 emulator 时也不能让 CMake 直接 exec 目标二进制
            # （v4 会 SIGILL）。给一个固定失败、不执行参数的 wrapper：try_run
            # 得到"失败/未知"，走项目的 false 分支，configure 继续。
            emu = posix(self.cmake_no_run or Path("/bin/false"))
            out.append(f'set(CMAKE_CROSSCOMPILING_EMULATOR "{emu}")')
        return "\n".join(out) + "\n"

    def write_cross_files(self, build_dir: Path) -> None:
        """Write generated Meson/CMake cross files into the target workspace."""
        if not self.cross:
            return
        build_dir.mkdir(parents=True, exist_ok=True)
        if self.host == "linux" and self.native_sysroot:
            root = posix(self.native_sysroot)
            self.native_pkg_config = build_dir / "native-pkg-config.sh"
            self.native_pkg_config.write_text(
                "#!/bin/sh\n"
                f'export PKG_CONFIG_LIBDIR="{root}/lib/pkgconfig:{root}/share/pkgconfig"\n'
                "export PKG_CONFIG_PATH=\n"
                'exec pkg-config "$@"\n',
                encoding="utf-8",
            )
            self.native_pkg_config.chmod(0o755)
            self.native_file = build_dir / "meson-native.ini"
            self.native_file.write_text(
                self.meson_native_file_text(), encoding="utf-8"
            )
        self.cross_file = build_dir / "meson-cross.ini"
        self.cross_file.write_text(self.meson_cross_file_text(), encoding="utf-8")
        # CMake 的 try_run 需要 emulator；没有真 emulator 时用一个"不执行、
        # 固定失败"的脚本，避免把目标二进制直接 exec。
        if self.host == "linux" and not self.exe_wrapper:
            self.cmake_no_run = build_dir / "cross-no-run.sh"
            self.cmake_no_run.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            self.cmake_no_run.chmod(0o755)
        self.cmake_toolchain = build_dir / "cmake-toolchain.cmake"
        self.cmake_toolchain.write_text(
            self.cmake_toolchain_text(), encoding="utf-8"
        )

    def autotools_target_env(self) -> str:
        """`CFLAGS=... CXXFLAGS=...` prefix for cross autotools configure."""
        if not self.cross:
            return ""
        cflags = " ".join(self._target_cflags())
        cxxflags = " ".join(self._target_cxxflags())
        parts = []
        if cflags:
            parts.append(f"CFLAGS={shlex.quote(cflags)}")
        if cxxflags:
            parts.append(f"CXXFLAGS={shlex.quote(cxxflags)}")
        return (" ".join(parts) + " ") if parts else ""

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
        """PATH entries (posix), sysroot first: our DLLs/libs win over system.

        Cross 构建要先找 native tools sysroot（build 机可执行的
        glib-compile-resources 等），再找 target sysroot。target bin 仍放在
        后面：Python 生成器（glib-mkenums 等）可以直接跑，真正链接了目标库的
        可执行文件若被误找到会以 "cannot run target binary" 明确失败。
        """
        entries = []
        if self.cross and self.native_sysroot:
            entries.append("$NATIVE_SYSROOT/bin")
        entries.append("$SYSROOT/bin")
        if self.host == "windows":
            root = posix(Path(self.cfg["msys2_root"]))
            sub = self._subsystem_dir()
            entries += [f"{root}/{sub}/bin", f"{root}/usr/bin"]
        else:
            entries += ["/usr/local/bin", "/usr/bin", "/bin"]
            # Linux 宿主保留调用方 PATH 的剩余部分：固定列表**覆盖**外层环境，
            # 而 CI（pipx/用户级安装，~/.local/bin）与发行版都可能把工具装在
            # 固定列表之外。追加在末尾——sysroot/系统目录仍优先，hermetic 不破。
            # Windows/MSYS2 不做：那边 PATH 由 MSYSTEM 子系统目录决定，引入外部
            # 路径反而会破坏工具链一致性。
            entries += ['${PATH:+:$PATH}']
        return ":".join(entries)

    def prelude(self) -> List[str]:
        """Bash exports applied before every build command.

        Values containing $SYSROOT must be expanded by the shell, so they
        are emitted with double quotes (or bare, in assignment context).
        """
        lines: List[str] = []
        if self.cross:
            if not self.native_sysroot:
                raise ValueError(
                    f"{self.name}: cross toolchain requires native_sysroot "
                    "(set native_target on the target or pass --native-sysroot)"
                )
            lines.append(f'export NATIVE_SYSROOT="{posix(self.native_sysroot)}"')
        lines.append(f"export PATH={self.base_path()}")
        tools = self.cfg.get("tools", {})
        # tools 用 `${VAR:-默认}` 而非直接赋值：调用方环境里显式设置的
        # CC/CXX/PKG_CONFIG 等必须保留（用户要由此自定义编译器与参数），
        # 只在未设置时才落入工具链默认值。
        for key, val in tools.items():
            k = key.upper()
            lines.append(f'export {k}="${{{k}:-{val}}}"')
        for k, v in self.cfg.get("env", {}).items():
            lines.append(f"export {k}=\"{v}\"")
        # Bias every compiler search at our sysroot (headers/libs first).
        # 调用方已有的 CPPFLAGS 追加在后（sysroot 头目录优先，hermetic 不被
        # 用户 -I 遮蔽；用户的 -D/-I 补充参数随后生效）。
        lines.append('export CPPFLAGS="-I$SYSROOT/include${CPPFLAGS:+ $CPPFLAGS}"')
        # 注：曾尝试在 LDFLAGS 加 -static-libgcc/-static-libstdc++ 以消除产物对
        # MSYS2 工具链运行时（libgcc_s_seh-1.dll / libstdc++-6.dll）的依赖，
        # 实测不可靠：meson 对 link_language=cpp 的工程会在 -Wl,--start-group
        # 内显式追加 -lstdc++，该参数位于 -static-libstdc++ 之后，动态导入库
        # 仍被选中（harfbuzz 主库仍依赖 libstdc++-6.dll，仅 subset 侥幸清掉）。
        # 时灵时不灵的全局开关比不加更糟，且多份 C++ 运行时在跨 DLL 场景有
        # 风险，故不启用。这些运行时 DLL 由 MSYS2 工具链提供，非本项目产物。
        if self.target_os == "windows":
            lines.append('export LDFLAGS="-L$SYSROOT/lib${LDFLAGS:+ $LDFLAGS}"')
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
                '-Wl,-rpath-link,$SYSROOT/lib -Wl,-rpath,$SYSROOT/lib '
                '-lm${LDFLAGS:+ $LDFLAGS}"'
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
        # 调用方已有的 LIBRARY_PATH（: 分隔的目录列表）追加在后。
        lines.append('export LIBRARY_PATH="$SYSROOT/lib${LIBRARY_PATH:+:$LIBRARY_PATH}"')
        if self.cross:
            # 目标 flags 不在这里导出：Meson 走 cross file、CMake 走 toolchain
            # file、autotools 走 configure 前缀。全局 CFLAGS 只保留调用方值，
            # 这样 Meson 在 cross 构建里为 build machine 编的工具不会被目标
            # ISA flags 污染。
            lines.append('export CFLAGS="${CFLAGS:-}"')
            lines.append('export CXXFLAGS="${CXXFLAGS:-}"')
            # build-time tools 一律用 native sysroot 的库。target sysroot 里的
            # .so 是另一种架构（或 PE DLL），不能也不应该进 native loader 的
            # 搜索路径。
            lines.append(
                'export LD_LIBRARY_PATH="$NATIVE_SYSROOT/lib'
                '${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"'
            )
            lines.append('export PKG_CONFIG_ALLOW_CROSS=1')
            lines.append(
                'export XDG_DATA_DIRS="$NATIVE_SYSROOT/share:$SYSROOT/share:'
                '/usr/local/share:/usr/share"'
            )
            lines.append('export XDG_CONFIG_HOME="$NATIVE_SYSROOT/etc/xdg"')
            return lines
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
            # 实测触发过程（linux-native，libadwaita 首跑）：68 项测试**全部**
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
        if self.native_sysroot:
            env["NATIVE_SYSROOT"] = posix(self.native_sysroot)
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
        for name in (
            "cc", "cxx", "ar", "strip", "as", "ld", "windres", "dlltool",
            "pkg_config", "meson", "ninja", "cmake", "make",
        ):
            tool = (self.cfg.get("tools") or {}).get(name)
            if not tool:
                continue
            r = self.run(f"{tool} --version >/dev/null 2>&1", print_cmd=False)
            if r.returncode != 0:
                problems.append(f"{name} ({tool}): not found on PATH")
        if self.cross:
            if not self.native_sysroot:
                problems.append(
                    "cross toolchain has no native_sysroot "
                    "(set native_target on the target or --native-sysroot)"
                )
            elif not (Path(self.native_sysroot) / "bin").is_dir():
                problems.append(
                    f"native tools sysroot missing or incomplete: "
                    f"{self.native_sysroot}/bin "
                    f"(build the linux-tools target first)"
                )
            cc = (self.cfg.get("tools") or {}).get("cc")
            if cc:
                r = self.run(f"{cc} -dumpmachine", print_cmd=False, capture=True)
                triple = (r.stdout or b"").decode("utf-8", "replace").strip()
                if r.returncode != 0:
                    problems.append(f"cross compiler {cc} cannot report its target")
                elif self.target_arch and self.target_arch not in triple:
                    problems.append(
                        f"cross compiler {cc} reports {triple!r}, "
                        f"expected target arch {self.target_arch}"
                    )
            if self.target_os == "windows" and cc:
                # gst-plugins-bad d3d12 / GTK win32 需要 d2d1_3.h；mingw-w64
                # 11 (Ubuntu 24.04) 没有，直接给出可操作错误而不是构建到一半。
                r = self.run(
                    f"printf '#include <d2d1_3.h>\\n' | "
                    f"{cc} -E -x c - >/dev/null 2>&1",
                    print_cmd=False,
                )
                if r.returncode != 0:
                    problems.append(
                        "mingw-w64 headers lack d2d1_3.h; use mingw-w64 >= 13 "
                        "(Ubuntu 26.04 or newer Fedora mingw64-* packages)"
                    )
        if self.host == "windows" and not Path(self.cfg["msys2_root"]).exists():
            problems.append(f"MSYS2 root {self.cfg['msys2_root']} does not exist")
        # 无头容器（CI）用 GTKCROSS_TEST_WRAPPER 给测试命令加前缀（见
        # gtkcross/engines.py 的 test_wrapper）。变量设了但命令不存在时，失败会
        # 表现为每个测试非零退出——所以在这里直接报出缺哪个可执行文件。
        wrapper = (os.environ.get("GTKCROSS_TEST_WRAPPER") or "").strip()
        if wrapper and not shutil.which(wrapper.split()[0]):
            problems.append(
                f"GTKCROSS_TEST_WRAPPER={wrapper}: "
                f"{wrapper.split()[0]} not found on PATH"
            )
        return problems

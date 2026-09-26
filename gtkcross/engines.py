"""Build engines: meson / cmake / autotools with sysroot isolation."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from .recipe import Recipe
from .toolchain import Toolchain, posix


class BuildError(RuntimeError):
    pass


class Engine:
    """One recipe build inside a per-target, per-recipe workspace."""

    def __init__(
        self,
        recipe: Recipe,
        tc: Toolchain,
        workspace: Path,
        jobs: int,
        default_library: str = "static",
        prefer_static: bool = True,
    ):
        self.recipe = recipe
        self.tc = tc
        self.ws = workspace  # build/<target>/<recipe>/
        self.jobs = jobs
        self.src_dir = workspace / "src"
        self.build_dir = workspace / "build"
        self.sysroot = tc.sysroot
        self.done = workspace / "done"
        # 项目级库形态默认值；recipe.default_library 可单独覆盖
        self.default_library = default_library
        self.prefer_static = prefer_static

    @property
    def libtype(self) -> str:
        """本 recipe 实际的库形态（static | shared）。"""
        return self.recipe.libtype(self.default_library)

    @property
    def static(self) -> bool:
        return self.libtype != "shared"

    # -- stamp helpers (idempotent, resumable) ----------------------------

    def stamp(self, key: str) -> Path:
        return self.done / (key + ".stamp")

    def apply(self, key: str, fn) -> None:
        s = self.stamp(key)
        if s.exists():
            print(f"  [skip] {key}")
            return
        fn()
        self.done.mkdir(parents=True, exist_ok=True)
        s.write_text("ok\n", encoding="utf-8")

    # -- phases --------------------------------------------------------------

    def configure(self) -> None:
        raise NotImplementedError

    def compile(self) -> None:
        raise NotImplementedError

    def install(self) -> None:
        raise NotImplementedError

    def test_command(self) -> str | None:
        """返回测试命令（在 workspace 目录执行）；None 表示无测试。"""
        return None


class MesonEngine(Engine):
    def setup_cmd(self) -> str:
        opts = self.recipe.meson.get("options", [])
        opt_str = " ".join(opts)
        cross = self.recipe.meson.get("cross_args", "")
        # 库形态由项目/recipe 配置驱动（默认 static：只出 .a，不出 DLL）
        libtype = self.libtype
        # prefer_static：dependency() 优先选 .a，并以 pkg-config --static 查询，
        # 从而带出 .pc 的 Cflags.private（XML_STATIC / PCRE2_STATIC 等静态消费宏）
        prefer = "true" if self.prefer_static else "false"
        return (
            f"meson setup {posix(self.build_dir)} {posix(self.src_dir)} "
            f"--prefix={posix(self.sysroot)} --libdir=lib -Dbuildtype=release "
            f"-Ddefault_library={libtype} -Dprefer_static={prefer} {opt_str} {cross}"
        )

    def configure(self) -> None:
        print(f"  [meson setup] {self.recipe.name}")
        self.tc.expect(self.setup_cmd(), cwd=self.ws)

    def compile(self) -> None:
        self.tc.expect(
            f"meson compile -C {posix(self.build_dir)} -j {self.jobs}", cwd=self.ws
        )

    def install(self) -> None:
        self.tc.expect(f"meson install -C {posix(self.build_dir)}", cwd=self.ws)

    def test_command(self) -> str | None:
        # recipe test.env: 以 `env K=V` 前缀注入测试进程环境（shell 层继承，
        # 不覆盖 meson.build 里 test_env 显式设置的变量）
        env = self.recipe.test.get("env") or {}
        prefix = " ".join(f"{k}={v}" for k, v in env.items())
        prefix = f"env {prefix} " if prefix else ""
        return (
            f"{prefix}meson test -C {posix(self.build_dir)} --print-errorlogs "
            f"-j {self.jobs}"
        )


class CmakeEngine(Engine):
    def setup_cmd(self) -> str:
        defines = dict(self.recipe.cmake.get("defines", {}))
        # 库形态统一由 default_library 驱动，注入 CMake 标准开关
        # BUILD_SHARED_LIBS（默认 OFF，所以共享包必须显式置 ON）。
        # recipe 显式声明了同名键则以 recipe 为准（个别包用别的开关名，
        # 如 EXPAT_SHARED_LIBS / PNG_SHARED / ENABLE_SHARED / ZLIB_BUILD_SHARED）。
        defines.setdefault("BUILD_SHARED_LIBS", "OFF" if self.static else "ON")
        def_str = " ".join(f"-D{k}={v}" for k, v in defines.items())
        sr = posix(self.sysroot)
        # Hermetic sysroot: never pick up libs/headers/CMake packages outside
        # our prefix; only programs (compilers/tools) may come from the
        # toolchain PATH (CMAKE_FIND_ROOT_PATH_MODE_PROGRAM=BOTH).
        return (
            f"cmake -S {posix(self.src_dir)} -B {posix(self.build_dir)} -G Ninja "
            f"-DCMAKE_INSTALL_PREFIX={sr} "
            f"-DCMAKE_INSTALL_LIBDIR=lib "
            f"-DCMAKE_BUILD_TYPE=Release "
            f"-DCMAKE_FIND_ROOT_PATH={sr} "
            f"-DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY "
            f"-DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY "
            f"-DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=ONLY "
            f"-DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=BOTH "
            f"-DCMAKE_PREFIX_PATH={sr} "
            f"{def_str}"
        )

    def configure(self) -> None:
        print(f"  [cmake configure] {self.recipe.name}")
        self.tc.expect(self.setup_cmd(), cwd=self.ws)

    def compile(self) -> None:
        self.tc.expect(
            f"cmake --build {posix(self.build_dir)} -j {self.jobs}", cwd=self.ws
        )

    def install(self) -> None:
        self.tc.expect(f"cmake --install {posix(self.build_dir)}", cwd=self.ws)

    def test_command(self) -> str | None:
        return (
            f"ctest --test-dir {posix(self.build_dir)} --output-on-failure "
            f"-j {self.jobs}"
        )


class AutotoolsEngine(Engine):
    def source_posix(self) -> str:
        """Source dir containing configure (default: src root)."""
        sd = self.recipe.autotools.get("source_dir", "")
        return posix(self.src_dir / sd) if sd else posix(self.src_dir)

    def subdir_posix(self) -> str:
        """Dir make operates on (default: same as source_dir)."""
        sub = self.recipe.autotools.get(
            "subdir",
            self.recipe.autotools.get("source_dir", ""),
        )
        return posix(self.src_dir / sub) if sub else posix(self.src_dir)

    def configure(self) -> None:
        opts = list(self.recipe.autotools.get("configure", []))
        # 库形态统一由 default_library 驱动（除非 recipe 自己声明了 --enable/--disable-shared）
        if not any("--enable-shared" in o or "--disable-shared" in o for o in opts):
            opts.append("--disable-shared" if self.static else "--enable-shared")
        if not any("--enable-static" in o or "--disable-static" in o for o in opts):
            opts.append("--enable-static" if self.static else "--disable-static")
        opt_str = " ".join(opts)
        envs = self.recipe.autotools.get("env", {})
        env_str = " ".join(f"{k}={v}" for k, v in envs.items())
        host = self.tc.cfg.get("host_triple")
        host_str = f"--host={host} --build={host}" if host else ""
        cmd = (
            f"cd {self.source_posix()} && {env_str} ./configure -C "
            f"--prefix={posix(self.sysroot)} {host_str} {opt_str}"
        )
        print(f"  [autotools configure] {self.recipe.name}")
        self.tc.expect(cmd, cwd=self.ws)

    def compile(self) -> None:
        self.tc.expect(f"make -C {self.subdir_posix()} -j {self.jobs}", cwd=self.ws)

    def install(self) -> None:
        self.tc.expect(f"make -C {self.subdir_posix()} install", cwd=self.ws)

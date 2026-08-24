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

    def __init__(self, recipe: Recipe, tc: Toolchain, workspace: Path, jobs: int):
        self.recipe = recipe
        self.tc = tc
        self.ws = workspace  # build/<target>/<recipe>/
        self.jobs = jobs
        self.src_dir = workspace / "src"
        self.build_dir = workspace / "build"
        self.sysroot = tc.sysroot
        self.done = workspace / "done"

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
        return (
            f"meson setup {posix(self.build_dir)} {posix(self.src_dir)} "
            f"--prefix={posix(self.sysroot)} --libdir=lib -Dbuildtype=release "
            f"-Ddefault_library=shared {opt_str} {cross}"
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
        return (
            f"meson test -C {posix(self.build_dir)} --print-errorlogs "
            f"-j {self.jobs}"
        )


class CmakeEngine(Engine):
    def setup_cmd(self) -> str:
        defines = self.recipe.cmake.get("defines", {})
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
        opts = self.recipe.autotools.get("configure", [])
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

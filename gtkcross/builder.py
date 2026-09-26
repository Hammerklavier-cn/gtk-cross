"""Build orchestration for one target: resolve, fetch, build, install."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .config import ProjectConfig
from .download import extract, fetch
from .engines import AutotoolsEngine, BuildError, CmakeEngine, Engine, MesonEngine
from .events import EventLog
from .recipe import Recipe, load_recipes
from .resolver import resolve
from .toolchain import Toolchain, posix

_ENGINE_CLS = {"meson": MesonEngine, "cmake": CmakeEngine, "autotools": AutotoolsEngine}
_LOCKFILE = "versions.lock.yaml"

# .pc 的字段：Requires.private / Libs.private / Cflags.private 需提升为公开字段
_PC_KEY_RE = re.compile(r"^([A-Za-z0-9_.]+)\s*:\s*(.*)$")
_PC_PRIVATE_RE = re.compile(r"^(Requires|Libs|Cflags)\.private\s*:\s*(.*)$")


def publish_pc_private_fields(path: Path) -> bool:
    """把 .pc 的私有字段并入同名公开字段（幂等），返回是否有改动。

    静态库不携带依赖信息：其传递依赖（-lz、-lintl）与静态消费宏
    （-DXML_STATIC、-DPCRE2_STATIC 等）只写在 Requires.private /
    Libs.private / Cflags.private 里，且仅在 `pkg-config --static` 查询时
    返回。本框架因 prefer_static=false（否则 meson 会在编译器默认搜索目录
    里找到 MSYS2 系统静态库，既破坏 hermetic 又符号不匹配）不会以 --static
    查询，于是下游就缺了这些参数——表现为 libpng16.a 未定义引用 deflate*、
    harfbuzz 等链接失败。静态包安装后提升私有字段即可。
    """
    text = path.read_text(encoding="utf-8")
    private: Dict[str, str] = {}
    for line in text.splitlines():
        m = _PC_PRIVATE_RE.match(line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            private[key] = f"{private.get(key, '')} {val}".strip()
    if not private:
        return False
    out: List[str] = []
    for line in text.splitlines():
        if _PC_PRIVATE_RE.match(line):
            continue  # 内容并入公开字段后删除
        m = _PC_KEY_RE.match(line)
        if m and m.group(1) in ("Requires", "Libs", "Cflags"):
            key, val = m.group(1), m.group(2).strip()
            extra = private.pop(key, "")
            tokens = val.split()
            for tok in extra.split():
                if tok not in tokens:
                    tokens.append(tok)
            out.append(f"{key}: {' '.join(tokens)}".rstrip())
            continue
        out.append(line)
    # 公开字段原本缺失时补一行
    for key, extra in private.items():
        if extra:
            out.append(f"{key}: {extra}")
    new_text = "\n".join(out) + "\n"
    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


class Builder:
    """Fetches and builds recipes in dependency order into a sysroot."""

    def __init__(self, project: ProjectConfig, target: str, jobs: int = 0):
        self.project = project
        self.target = target
        self.recipes = load_recipes(project.root / "recipes")
        self.sysroot = project.sysroot(target)
        self.jobs = jobs or (min(32, int(__import__("os").cpu_count() or 4)))
        tconf = project.target(target)
        tc_path = project.root / "toolchains" / f"{tconf['toolchain']}.yaml"
        self.tc = Toolchain.load(tc_path, self.sysroot)
        # 追加式事件日志：记录构建起止与测试异常事件（known 失败出现/缺席等）
        self.events = EventLog(project.root / "gtkcross-events.log", target)

    # -- lockfile ---------------------------------------------------------

    def lock(self) -> dict:
        p = self.project.root / _LOCKFILE
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def save_lock(self, entry: dict) -> None:
        p = self.project.root / _LOCKFILE
        data = self.lock()
        data.update(entry)
        with open(p, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True)

    # -- recipe ordering ----------------------------------------------------

    def order(self, names: List[str]) -> List[str]:
        graph = {n: list(r.deps) for n, r in self.recipes.items()}
        return resolve(graph, names)

    # -- per-recipe build ---------------------------------------------------

    def build_recipe(self, name: str) -> None:
        recipe = self.recipes[name].for_target(self.target)
        ws = self.project.build_dir / self.target / name
        src = ws / "src"
        ok_marker = ws / "src" / ".gtkcross-extract-ok"

        # 1. source: download + extract (verify or auto-lock sha256)
        if not ok_marker.exists():
            locked = self.lock().get(name) or {}
            sources = recipe.source_urls
            cand_shas = {s["sha256"] for s in sources if s["sha256"]}
            if locked.get("sha256"):
                # 锁定校验：命中任一候选源即可（镜像可携带不同字节与哈希）
                if cand_shas and locked["sha256"] not in cand_shas:
                    raise BuildError(
                        f"{name}: locked sha256 {locked['sha256']} matches no "
                        f"recipe source ({sorted(cand_shas)}); fix recipe or re-lock"
                    )
                want = locked["sha256"]
            else:
                want = sources[0]["sha256"]
            archive, actual, used_url = fetch(
                sources, self.project.downloads_dir / name, want
            )
            self.save_lock(
                {name: {"version": recipe.version, "url": used_url,
                        "sha256": actual}}
            )
            print(f"  [fetch] {name} {recipe.version} sha256={actual[:16]}…")
            src = extract(archive, ws / "src")
            self._apply_post_source(name, recipe, ws, src)
        else:
            print(f"  [use] {name}: source already present")
        # submodule 目录填充：幂等，标记存在时也会补全缺失的 vendored 源码
        self._apply_submodules(name, recipe, ws, src)

        # 2..4. configure / compile / install
        engine = _ENGINE_CLS[recipe.build](
            recipe,
            self.tc,
            ws,
            self.jobs,
            default_library=self.project.default_library,
            prefer_static=self.project.prefer_static,
        )
        engine.apply("configure", engine.configure)
        engine.apply("compile", engine.compile)
        print(f"  [install] {name} -> {self.sysroot}")
        engine.apply("install", engine.install)
        published = self._publish_static_pc()
        if published:
            print(f"  [pc] {name}: 提升静态 .pc 私有字段 -> {', '.join(published)}")
        self._apply_post_install(name, recipe)
        self._apply_test(name, engine)

    def _apply_post_source(self, name: str, recipe: Recipe, ws: Path, src: Path) -> None:
        """Apply recipe patches to the extracted source tree.

        Uses GNU patch (not git apply): in a directory that is part of a git
        work tree (our build/ lives inside the project repo), git apply
        silently skips patches against untracked files ("Skipped patch",
        exit 0, no changes applied).  GNU patch applies unconditionally.
        """
        if not recipe.patches:
            return
        tc = self.tc
        for p in recipe.patches:
            pf = self.project.root / "patches" / p
            if not pf.exists():
                raise BuildError(f"{name}: patch not found: {pf}")
            print(f"  [patch] {name}: {p}")
            tc.expect(
                f"cd {posix(src)} && patch -p1 --batch < {posix(pf)}",
                cwd=ws,
            )

    def _apply_submodules(self, name: str, recipe: Recipe, ws: Path, src: Path) -> None:
        """Populate vendored submodule dirs from already-built dep source trees.

        Tag artefacts of projects like SPIRV-Tools/shaderc ship without their
        git submodules; the recipe lists {dest_subdir: dep_recipe_name} and we
        copy the dep's extracted source tree in place before configure.
        """
        for rel, dep in recipe.submodules.items():
            dep_src = self.project.build_dir / self.target / dep / "src"
            target = src / rel
            if not dep_src.exists():
                raise BuildError(
                    f"{name}: submodule source {dep_src} not found "
                    f"(dep '{dep}' must be built first)"
                )
            if target.exists():
                continue
            print(f"  [submodule] {name}: {rel} <- {dep}")
            shutil.copytree(dep_src, target)

    def _pkgconfig_dirs(self) -> List[Path]:
        return [self.sysroot / "lib" / "pkgconfig",
                self.sysroot / "share" / "pkgconfig"]

    def _pc_is_static(self, pc: Path) -> bool:
        """判断 .pc 描述的库在本 sysroot 里是否为静态。

        依据 `Libs:` 里的 -lNAME 反查：libNAME.a 存在且 libNAME.dll.a 不存在
        即为静态。共享库的导入库是 libNAME.dll.a，因此能区分开。
        不依赖 mtime——CMake/meson 重装时常报 "Up-to-date" 而不改写 .pc。
        """
        libdir = self.sysroot / "lib"
        try:
            text = pc.read_text(encoding="utf-8")
        except OSError:
            return False
        for line in text.splitlines():
            if not line.startswith("Libs:"):
                continue
            for tok in line.split():
                if not tok.startswith("-l") or len(tok) <= 2:
                    continue
                name = tok[2:]
                if (libdir / f"lib{name}.a").exists() and not (
                    libdir / f"lib{name}.dll.a"
                ).exists():
                    return True
        return False

    def _publish_static_pc(self) -> List[str]:
        """提升静态库 .pc 的私有字段（幂等）。

        静态包安装后调用；共享库的 .pc 不动（保持既有已验证形态）。
        """
        changed: List[str] = []
        for d in self._pkgconfig_dirs():
            if not d.is_dir():
                continue
            for p in sorted(d.glob("*.pc")):
                if not self._pc_is_static(p):
                    continue
                if publish_pc_private_fields(p):
                    changed.append(p.name)
        return changed

    def _apply_post_install(self, name: str, recipe: Recipe) -> None:
        cmds = recipe.post_install.get("commands", [])
        for cmd in cmds:
            self.tc.expect(cmd, cwd=self.sysroot)

    # -- tests ------------------------------------------------------------

    # meson test 输出行示例:
    #   43/336 glib+core - glib:option-context   ERROR  0.10s  exit status 3
    #   331/336 gio - glib:socket               OK     5.49s  29 subtests passed
    #   326/336 lint+no-valgrind - glib:black.sh SKIP  0.03s
    # ctest 输出行示例:
    #   1/1 Test #1: pngtest .....................   Passed    0.00 sec
    _TEST_LINE = re.compile(
        r"^\s*(\d+)/(\d+)\s+(?P<name>.+?)\s+(?P<status>OK|FAIL|ERROR|SKIP)\s"
    )
    _CTEST_LINE = re.compile(
        r"^\s*\d+/\d+\s+Test\s+#\d+:\s+(?P<name>[^:]+?)\s+"
        r"(?P<status>Passed|Failed|\*\*\*Failed|\*\*\*Timeout)"
    )

    def _parse_test_output(self, out: str) -> dict:
        """解析 meson test 输出 -> {status: [names]}，name 去掉 suite 前缀。

        meson test 的输出中失败项会出现两次（进度行 + Summary of
        Failures 段），这里按测试名去重。
        """
        res: Dict[str, List[str]] = {}
        for line in out.splitlines():
            m = self._TEST_LINE.match(line) or self._CTEST_LINE.match(line)
            if not m:
                continue
            full = m.group("name").strip()
            short = full.split(" - ", 1)[-1].strip()
            status = m.group("status")
            # ctest 的状态名归一化到 meson 词汇
            if status == "Passed":
                status = "OK"
            elif status in ("Failed", "***Failed", "***Timeout"):
                status = "ERROR"
            seen = res.setdefault(status, [])
            if short not in seen:
                seen.append(short)
        return res

    def _apply_test(self, name: str, engine: Engine) -> None:
        t = engine.recipe.test or {}
        if not t.get("enabled", False):
            return
        cmd = engine.test_command()
        if not cmd:
            print(f"  [test] {name}: engine 无测试命令")
            return
        s = engine.stamp("test")
        if s.exists():
            print(f"  [skip] test")
            return
        print(f"  [test] {name}…")
        r = self.tc.run(cmd, cwd=engine.ws, capture=True, print_cmd=False)
        out = (r.stdout or b"").decode(errors="replace")
        res = self._parse_test_output(out)
        fails = res.get("ERROR", []) + res.get("FAIL", [])
        # `known_failures:` 仅有注释时 YAML 解析为 None 而非缺键
        known = t.get("known_failures") or []
        if not fails:
            if r.returncode != 0:
                # 非零退出码但未解析出失败行（输出可能被截断/格式未知）
                print(
                    f"  [test] {name}: 退出码 {r.returncode} 但未解析到失败行，"
                    f"以下为输出尾部:"
                )
                for line in out.splitlines()[-10:]:
                    print(f"    | {line}")
                self.events.log(
                    "tests-exit-anomaly",
                    f"{name}: 退出码 {r.returncode} 但未解析到失败行",
                )
                raise BuildError(
                    f"{name}: test runner exited {r.returncode} with "
                    f"no parsed failures"
                )
            ok_n = len(res.get("OK", []))
            print(f"  [test] {name}: 全部通过（OK={ok_n}）")
            self.events.log("tests-pass", f"{name}: OK={ok_n}")
            engine.done.mkdir(parents=True, exist_ok=True)
            s.write_text("ok\n", encoding="utf-8")
            return
        unexpected = [f for f in fails if f not in known]
        if unexpected:
            print(f"  [test] {name}: FAILED (unexpected):")
            for f in unexpected:
                print(f"    - {f}")
            self.events.log(
                "tests-unexpected",
                f"{name}: {', '.join(unexpected)}",
            )
            raise BuildError(
                f"{name}: unexpected test failures: {', '.join(unexpected)}"
            )
        print(
            f"  [test] {name}: {len(fails)} 项已知失败（已忽略）: "
            f"{', '.join(fails)}"
        )
        # known failure 如期性核对：出现与缺席（缺席=测试转好或环境变化，值得留意）
        self.events.log(
            "tests-known-observed",
            f"{name}: {len(fails)} 项如期出现: {', '.join(fails)}",
        )
        absent = [k for k in known if k not in fails]
        if absent:
            self.events.log(
                "tests-known-absent",
                f"{name}: {len(absent)} 项预期失败未出现: {', '.join(absent)}",
            )
        engine.done.mkdir(parents=True, exist_ok=True)
        s.write_text("ok\n", encoding="utf-8")

    # -- entry point ---------------------------------------------------------

    def build(self, names: List[str]) -> None:
        order = self.order(names)
        print(f"=== build plan ({self.target}): {' -> '.join(order)}")
        self.sysroot.mkdir(parents=True, exist_ok=True)
        self.events.log("run-start", f"plan: {' -> '.join(order)}")
        try:
            for name in order:
                print(f"--- {name} ({self.recipes[name].version}) ---")
                try:
                    self.build_recipe(name)
                except Exception as e:
                    self.events.log(
                        "recipe-failed",
                        f"{name}: {type(e).__name__}: {e}",
                    )
                    raise
        except Exception as e:
            self.events.log("run-failed", f"{type(e).__name__}: {e}")
            raise
        print(f"=== done: {len(order)} recipes, sysroot={self.sysroot}")
        self.events.log("run-ok", f"{len(order)} recipes, sysroot={self.sysroot}")

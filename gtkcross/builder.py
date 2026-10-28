"""Build orchestration for one target: resolve, fetch, build, install."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .config import ProjectConfig
from .download import extract, fetch
from .engines import (
    AutotoolsEngine,
    BuildError,
    CmakeEngine,
    DataEngine,
    Engine,
    MesonEngine,
)
from .events import EventLog
from .recipe import Recipe, load_recipes
from .resolver import resolve
from .toolchain import Toolchain, posix

_ENGINE_CLS = {
    "meson": MesonEngine,
    "cmake": CmakeEngine,
    "autotools": AutotoolsEngine,
    "data": DataEngine,
}
_LOCKFILE = "versions.lock.yaml"

# 参与构建参数的用户环境变量（prelude 加性保留它们，见 toolchain.py）。
# 任一变量值变化 ⇒ 已配置的 build 目录（meson 的 coredata / cmake 的
# CMakeCache）里固化的参数过期，必须删目录重配。只跟踪"用户可配置"维度；
# 框架自身注入的 sysroot 参数属 toolchain 配置，其变更走既有"删 build 目录"
# 约定，不在此列。
_ENV_TRACKED = (
    "CC", "CXX", "AR", "RANLIB", "NM", "STRIP",
    "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "LIBRARY_PATH",
)


def env_fingerprint() -> str:
    """当前环境下的编译参数指纹（sha256，固定变量顺序）。"""
    h = hashlib.sha256()
    for k in _ENV_TRACKED:
        h.update(f"{k}={os.environ.get(k, '')}\0".encode("utf-8"))
    return h.hexdigest()

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

    def __init__(self, project: ProjectConfig, target: str, jobs: int = 0,
                 ignore_test_failures: bool = False, skip_tests: bool = False):
        self.project = project
        self.target = target
        # True 时测试失败（unexpected / 退出码异常）只告警不中断构建。与
        # known_failures 不同：这不是登记——所有失败都会原样打印并落盘
        # test-failure.log、记入事件日志，只是不 raise。用于一次性诊断。
        self.ignore_test_failures = ignore_test_failures
        # True 时**完全不运行**自带测试（configure/compile/install 照常）。
        # 与 ignore_test_failures 的区别：后者仍执行测试命令，只是失败不
        # 中断；前者连命令都不发。CI 里 x86-64-v4 变体在宿主 CPU 不支持
        # AVX-512 时用这个产出"未跑测试的 v4 sysroot"（发布仍有三份资产）。
        self.skip_tests = skip_tests
        self.recipes = load_recipes(project.root / "recipes")
        self.sysroot = project.sysroot(target)
        self.jobs = jobs or (min(32, int(__import__("os").cpu_count() or 4)))
        tconf = project.target(target)
        tc_path = project.root / "toolchains" / f"{tconf['toolchain']}.yaml"
        self.tc = Toolchain.load(tc_path, self.sysroot)
        # recipe 的 targets: 家族选择器按它匹配（windows | linux）
        self.target_os = self.tc.target_os
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
        # 依赖图必须按**目标**解析：recipe 的 targets: 家族块可以给特定平台增删
        # deps（例如 directx-headers/directxmath 只属于 Windows）。用未解析的 base
        # deps 建图的话，被裁掉的依赖仍会进入 build plan，而按名字预置依赖时又
        # 会在 Linux 计划里带上 Windows 专属包。
        graph = {
            n: list(r.for_target(self.target, self.target_os).deps)
            for n, r in self.recipes.items()
        }
        # 平台归属的一致性检查：把"某平台的包依赖了不属于该平台的包"当作配置错误
        # 直接报出，而不是让它在 build plan 里冒出来（那会产出一个本平台不该有
        # 的 sysroot 条目，例如 Linux 侧混进 egl-headers 与 Mesa 争抢 EGL 头）。
        # 只检查**本身属于本平台**的包：libdrm 的 deps 里写 libpciaccess 是对的，
        # 它在 Windows 目标上根本不会被构建，它的依赖边与 Windows 无关。
        for name, deps in graph.items():
            if not self.recipes[name].applies_to(self.target, self.target_os):
                continue
            for dep in deps:
                if not self.recipes[dep].applies_to(self.target, self.target_os):
                    raise ValueError(
                        f"{name}: depends on {dep!r}, which declares "
                        f"platforms={self.recipes[dep].platforms} and does not "
                        f"apply to target {self.target}"
                    )
        return resolve(graph, names)

    def not_applicable(self, names: List[str]) -> List[str]:
        """requests 里声明了"不属于本平台"的那部分（`platforms:`）。"""
        return [
            n for n in names
            if not self.recipes[n].applies_to(self.target, self.target_os)
        ]

    # -- per-recipe build ---------------------------------------------------

    def build_recipe(self, name: str) -> None:
        recipe = self.recipes[name].for_target(self.target, self.target_os)
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
        # 编译参数环境（调用方 CC/CFLAGS/...）与上次配置时不同 ⇒ 删掉整个
        # build 目录与阶段 stamp 强制重配。光删 stamp 不够：meson 的 coredata
        # 与 cmake 的 CMakeCache.txt 固化着旧参数（框架既有坑，见 NOTES）。
        self._invalidate_on_env_change(name, recipe, ws)
        engine.apply("configure", engine.configure)
        engine.apply("compile", engine.compile)
        print(f"  [install] {name} -> {self.sysroot}")
        engine.apply("install", engine.install)
        published = self._publish_static_pc()
        if published:
            print(f"  [pc] {name}: 提升静态 .pc 私有字段 -> {', '.join(published)}")
        self._apply_post_install(name, recipe)
        self._apply_test(name, engine)

    def _invalidate_on_env_change(self, name: str, recipe: Recipe, ws: Path) -> None:
        """调用方编译参数环境（CC/CFLAGS/...）变化时，废弃本 recipe 的缓存。

        prelude 把这些变量加性透传给 configure（见 toolchain.py），而
        meson/cmake 只在 setup/configure 时把它们固化进 build 目录（meson 的
        coredata、cmake 的 CMakeCache.txt），之后环境再变也不会被重读——光删
        stamp 重跑 compile 换不了参数（NOTES 里反复踩过的坑：改构建选项后要
        连 build 目录一起删）。故指纹不匹配时连 build 目录与全部阶段 stamp
        一起删，强制重新 configure；随后写入新指纹。

        data 引擎没有编译参数（纯文件拷贝），跳过；否则 install stamp 每次
        环境变化都被失效，白做重装。
        """
        if recipe.build == "data":
            return
        fp = env_fingerprint()
        done = ws / "done"
        fp_path = done / "env-fp.stamp"
        prev = fp_path.read_text(encoding="utf-8") if fp_path.exists() else None
        if prev == fp:
            return
        # 环境与上次配置不同，或旧 workspace 有 stamp 但没记指纹（框架升级前
        # 构建的，coredata/CMakeCache 里的参数未知）——都按"缓存参数过期"处理，
        # 强制重配。完全没有配置痕迹（全新 workspace）时无可废弃，直接记指纹。
        if prev is not None or any(done.glob("*.stamp")):
            print(
                f"  [env] {name}: 编译参数环境变化，废弃缓存重新 configure"
            )
            # meson/cmake 的 build 目录（ninja/meson-private/CMakeCache.txt）
            shutil.rmtree(ws / "build", ignore_errors=True)
            if recipe.build == "autotools":
                # autotools 是 in-source 构建且 configure 带 -C（config cache）：
                # config.cache 会按变量缓存探测结果，CFLAGS 变了旧结论未必成立。
                # 产物在 source_dir（默认 src 根）里。
                sub = recipe.autotools.get("source_dir", "")
                root = ws / "src" / sub if sub else ws / "src"
                for f in ("config.cache", "config.status", "config.log"):
                    p = root / f
                    if p.is_file():
                        p.unlink()
            for s in done.glob("*.stamp"):
                if s.name != "env-fp.stamp":
                    s.unlink()
        done.mkdir(parents=True, exist_ok=True)
        fp_path.write_text(fp, encoding="utf-8")

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

        依据 `Libs:` 里的 -lNAME 反查：libNAME.a 存在、且本平台"共享变体"不存
        在，即为静态。共享变体的名字随平台而不同（Windows 是导入库
        libNAME.dll.a，ELF 是 libNAME.so），故由 toolchain 的 shared_suffixes
        给出，不能在这里硬编码 Windows 形态——否则 Linux 上共享包的 .pc 会被
        误判成静态并把 Libs.private 提升进 Libs。
        不依赖 mtime——CMake/meson 重装时常报 "Up-to-date" 而不改写 .pc。
        """
        libdir = self.sysroot / "lib"
        suffixes = self.tc.shared_suffixes
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
                if not (libdir / f"lib{name}.a").exists():
                    continue
                if any(
                    (libdir / f"lib{name}{sfx}").exists() for sfx in suffixes
                ):
                    continue
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
        if self.skip_tests:
            print(f"  [skip] test（--no-test）")
            return
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
                if self.ignore_test_failures:
                    print(f"  [test] {name}: 忽略（--ignore-test-failures）")
                    return
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
            # 落盘完整输出：capture=True 的 meson/ctest 输出不会进控制台或
            # 构建日志，若只打印测试名，偶发失败事后就无从诊断——曾遇到
            # gobject-introspection:warn-callback-invalid-scope 只报一次、
            # 之后数十次复跑全过，而失败详情在日志里已无迹可寻。
            # 用固定文件名，便于事后与 CI 产物收集。
            detail = engine.ws / "test-failure.log"
            try:
                detail.write_text(out, encoding="utf-8", errors="replace")
                print(f"  [test] {name}: 完整输出已保存 -> {detail}")
            except OSError as e:
                print(f"  [test] {name}: 保存完整输出失败: {e}")
            # 同时回显尾部，让失败在 CI 日志里就能看到，无需下载产物
            print(f"  [test] {name}: 输出尾部 {min(30, len(out.splitlines()))} 行:")
            for line in out.splitlines()[-30:]:
                print(f"    | {line}")
            print(f"  [test] {name}: FAILED (unexpected):")
            for f in unexpected:
                print(f"    - {f}")
            self.events.log(
                "tests-unexpected",
                f"{name}: {', '.join(unexpected)}; detail: {detail}",
            )
            if self.ignore_test_failures:
                print(
                    f"  [test] {name}: 忽略 {len(unexpected)} 项 unexpected "
                    f"失败（--ignore-test-failures）"
                )
                return
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
        skipped = self.not_applicable(names)
        if skipped:
            print(
                f"[skip] 不属于 {self.target}（recipe 的 platforms 声明）: "
                f"{', '.join(skipped)}"
            )
        order = self.order([n for n in names if n not in set(skipped)])
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

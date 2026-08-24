"""gtkcross command line interface."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .builder import Builder
from .config import ProjectConfig
from .recipe import load_recipes


def _builder(args) -> Builder:
    project = ProjectConfig.load()
    return Builder(project, args.target)


def cmd_list(args) -> int:
    recipes = load_recipes(ProjectConfig.load().root / "recipes")
    rows = sorted(recipes.values(), key=lambda r: r.name)
    print(f"{'name':<16} {'version':<10} {'build':<10} deps")
    for r in rows:
        print(f"{r.name:<16} {r.version:<10} {r.build:<10} {', '.join(r.deps)}")
    return 0


def cmd_info(args) -> int:
    project = ProjectConfig.load()
    recipes = load_recipes(project.root / "recipes")
    r = recipes.get(args.recipe)
    if not r:
        print(f"unknown recipe {args.recipe!r}; use 'list'", file=sys.stderr)
        return 1
    print(f"name:    {r.name}")
    print(f"version: {r.version}")
    print(f"build:   {r.build}")
    print(f"deps:    {', '.join(r.deps) or '(none)'}")
    print(f"source:  {r.source['url']}")
    print(f"sha256:  {r.source.get('sha256') or '(auto-locked in versions.lock.yaml)'}")
    if r.targets:
        print(f"targets: {', '.join(sorted(r.targets))}")
    return 0


def cmd_graph(args) -> int:
    b = _builder(args)
    try:
        order = b.order([args.recipe])
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    recipes = b.recipes
    visited = set()

    def walk(name: str, depth: int) -> None:
        print("  " * depth + name)
        if name in visited:
            return
        visited.add(name)
        for dep in recipes[name].deps:
            walk(dep, depth + 1)

    walk(args.recipe, 0)
    return 0


def cmd_env(args) -> int:
    b = _builder(args)
    print(f"# toolchain: {b.tc.cfg.get('name') or b.tc.name}  target={args.target}")
    print(f"# sysroot:   {b.sysroot}")
    for line in b.tc.prelude():
        print(line.replace('"', "'"))
    if args.script:
        print("\n# 附加参数: 直接以 bash 子命令运行\n# 例如: gtkcross env msys2-mingw64 bash\n")
    return 0


def cmd_doctor(args) -> int:
    b = _builder(args)
    problems = b.tc.doctor()
    if problems:
        print(f"toolchain check FAILED ({args.target}):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"toolchain OK ({args.target}):")
    for name in ("cc", "cxx", "pkg_config", "meson", "ninja", "cmake", "make"):
        tool = (b.tc.cfg.get("tools") or {}).get(name)
        if tool:
            b.tc.run(
                f"printf '  {name:<10}'; {tool} --version | head -1",
                print_cmd=False,
            )
    return 0


def cmd_lock(args) -> int:
    b = _builder(args)
    order = b.order(args.names)
    for name in order:
        recipe = b.recipes[name].for_target(args.target)
        locked = b.lock().get(name) or {}
        want = recipe.source.get("sha256") or locked.get("sha256", "")
        from .download import fetch

        archive, actual, used_url = fetch(
            recipe.source_urls, b.project.downloads_dir / name, want
        )
        b.save_lock(
            {name: {"version": recipe.version, "url": used_url,
                    "sha256": actual}}
        )
        print(f"locked {name} {recipe.version}: {actual}")
    return 0


def cmd_build(args) -> int:
    from .events import OutputTee
    from pathlib import Path

    b = Builder(ProjectConfig.load(), args.target, jobs=args.jobs)
    # fd 级 tee：构建全程（含 bash/meson/gcc 子进程输出）复制一份到当前目录
    with OutputTee(Path("gtkcross-build.log")):
        try:
            b.build(args.names)
        except (ValueError, RuntimeError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="gtkcross",
        description="从源码构建 GTK4/libadwaita 依赖树的可复现构建框架",
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_target(sp: argparse.ArgumentParser) -> None:
        # 默认值回退到 gtk-cross.yaml 的 default_target（main 中统一解析）
        sp.add_argument("-t", "--target", default=None)

    sp = sub.add_parser("list", help="列出全部 recipe")
    sp.set_defaults(fn=cmd_list)

    sp = sub.add_parser("info", help="查看单个 recipe")
    sp.add_argument("recipe")
    sp.set_defaults(fn=cmd_info)

    sp = sub.add_parser("graph", help="打印依赖树")
    add_target(sp)
    sp.add_argument("recipe")
    sp.set_defaults(fn=cmd_graph)

    sp = sub.add_parser("env", help="导出构建环境（供外部编译使用）")
    add_target(sp)
    sp.add_argument("--script", action="store_true", help="输出可 source 的脚本头")
    sp.set_defaults(fn=cmd_env)

    sp = sub.add_parser("doctor", help="检查工具链")
    add_target(sp)
    sp.set_defaults(fn=cmd_doctor)

    sp = sub.add_parser("lock", help="下载源码并锁定 sha256（不构建）")
    add_target(sp)
    sp.add_argument("names", nargs="+")
    sp.set_defaults(fn=cmd_lock)

    sp = sub.add_parser("build", help="按依赖序构建 recipe")
    add_target(sp)
    sp.add_argument("names", nargs="+")
    sp.add_argument("-j", "--jobs", type=int, default=0,
                    help="并行度（默认 = CPU 数）")
    sp.set_defaults(fn=cmd_build)

    args = p.parse_args(argv)
    if getattr(args, "target", None) is None:
        args.target = ProjectConfig.load().default_target
    try:
        return args.fn(args)
    except (ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Recipe model and YAML loading.

A recipe is a declarative description of one dependency:
source (url + sha256), build system, options, and per-target overrides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge override into base (lists are replaced)."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Recipe:
    name: str
    version: str
    source: Dict[str, Any]  # url, sha256, mirrors ("" = auto-lock on first fetch)
    build: str  # meson | cmake | autotools
    deps: List[str] = field(default_factory=list)
    meson: Dict[str, Any] = field(default_factory=dict)
    cmake: Dict[str, Any] = field(default_factory=dict)
    autotools: Dict[str, Any] = field(default_factory=dict)
    post_install: Dict[str, Any] = field(default_factory=dict)
    test: Dict[str, Any] = field(default_factory=dict)
    patches: List[str] = field(default_factory=list)
    # 目标子目录 -> 依赖 recipe 名：解包后把依赖源码树复制到本包源码的子目录
    # （用于 tag 打包不含 git submodule 的工程，如 SPIRV-Tools/shaderc）
    submodules: Dict[str, str] = field(default_factory=dict)
    targets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # 库链接形态覆盖（static | shared）；空 = 用项目级 default_library。
    # 少数包必须保留动态产物时（上游无静态构建路径）用它单独放开。
    default_library: str = ""

    @property
    def source_urls(self) -> List[Dict[str, str]]:
        """所有候选下载源：主源在前、镜像在后；每项 {url, sha256}。"""
        return [
            {"url": self.source["url"], "sha256": self.source.get("sha256", "")},
        ] + list(self.source.get("mirrors", []))

    def libtype(self, project_default: str) -> str:
        """本 recipe 实际使用的库形态（static | shared）。"""
        return self.default_library or project_default

    def for_target(self, target: str) -> "Recipe":
        """Return a copy with per-target overrides applied."""
        override = self.targets.get(target)
        if not override:
            return self
        return Recipe(
            name=self.name,
            version=self.version,
            source=_deep_merge(self.source, override.get("source", {})),
            build=override.get("build", self.build),
            deps=override.get("deps", self.deps),
            meson=_deep_merge(self.meson, override.get("meson", {})),
            cmake=_deep_merge(self.cmake, override.get("cmake", {})),
            autotools=_deep_merge(self.autotools, override.get("autotools", {})),
            post_install=_deep_merge(self.post_install, override.get("post_install", {})),
            test=_deep_merge(self.test, override.get("test", {})),
            patches=override.get("patches", self.patches),
            submodules=override.get("submodules", self.submodules),
            targets={},
            default_library=override.get("default_library", self.default_library),
        )


def load_recipe(path: Path) -> Recipe:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    missing = [k for k in ("name", "version", "source", "build") if k not in data]
    if missing:
        raise ValueError(f"{path}: missing required field(s): {missing}")
    src = data["source"]
    if not src.get("url"):
        raise ValueError(f"{path}: source.url is required")
    mirrors = []
    for m in src.get("mirrors") or []:
        if not m.get("url"):
            raise ValueError(f"{path}: source.mirrors[].url is required")
        mirrors.append({"url": m["url"], "sha256": m.get("sha256", "")})
    source = {"url": src["url"], "sha256": src.get("sha256", "")}
    if mirrors:
        source["mirrors"] = mirrors
    return Recipe(
        name=data["name"],
        version=str(data["version"]),
        source=source,
        build=data["build"],
        deps=data.get("deps", []),
        meson=data.get("meson", {}),
        cmake=data.get("cmake", {}),
        autotools=data.get("autotools", {}),
        post_install=data.get("post_install", {}),
        test=data.get("test", {}),
        patches=data.get("patches", []),
        submodules=data.get("submodules", {}),
        targets=data.get("targets", {}),
        default_library=data.get("default_library", ""),
    )


def load_recipes(recipes_dir: Path) -> Dict[str, Recipe]:
    recipes: Dict[str, Recipe] = {}
    for path in sorted(recipes_dir.glob("*.yaml")):
        r = load_recipe(path)
        if r.name in recipes:
            raise ValueError(f"duplicate recipe name {r.name!r}")
        recipes[r.name] = r
    return recipes

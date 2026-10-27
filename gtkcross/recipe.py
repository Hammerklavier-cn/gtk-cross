"""Recipe model and YAML loading.

A recipe is a declarative description of one dependency:
source (url + sha256), build system, options, and per-target overrides.

`targets:` is how a recipe says "this patch / option / dependency belongs only
to one platform".  A selector is either an exact target name (`msys2-ucrt64`)
or a toolchain OS family (`windows` — every target whose toolchain declares
`target_os: windows`), and one block may list several selectors at once so
that shared items are written once:

    targets:
      - for: [windows]                  # OS 家族：msys2-mingw64 + msys2-ucrt64 共用
        meson:
          options: [-Dc_link_args=-Wl,--undefined=_tls_used]
        deps: [libiconv, gettext]
      - for: [msys2-mingw64]            # 精确名，叠加在家族块之后
        patches: [gtk-0001-fallback-to-windows-locale.patch]

The legacy mapping form still works (each key is one selector):

    targets:
      msys2-mingw64:
        patches: [gtk-0001-fallback-to-windows-locale.patch]

`platforms:` is orthogonal and answers a different question — not "what does
this package look like on each platform" but "does this package exist on this
platform at all" (e.g. the X11/Mesa stack is only self-built on Linux, while
egl-headers is only needed on Windows and would fight Mesa for include/EGL in
the sysroot).  Selectors use the same language as `for:`; omitted means "all
platforms".  `Builder.build` skips requests outside the current target and
`Builder.order` raises when a package that *is* in scope depends on one that
is not.

Merge semantics relative to the recipe base — which is meant to hold only
platform-neutral items:

- items under the list keys in `_APPEND_PATHS` (`patches`, `deps`,
  `submodules`, `meson.options`, `autotools.configure`,
  `test.known_failures`) are **appended to the base list, whatever the
  selector is**.  A block shared by several targets must only ever *add*:
  with replace semantics `for: [msys2-mingw64, msys2-ucrt64]` would silently
  drop the base patches/deps on both targets (the tests caught this);
- dicts (`meson`/`cmake`/`autotools`/`test`/`source`/`data`) deep-merge and
  their *leaf* values are replaced for the same key, so one platform can give
  a different value to the same define;
- scalars (`build`, `default_library`) are replaced;
- OS-family blocks apply before exact-target blocks, so for the replace-y keys
  the more specific block wins.  For appended lists the later block's items
  just come later in the command line (meson/cmake honour the last occurrence
  of a repeated `-D` key).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

# OS 家族选择器（与 toolchains/*.yaml 的 target_os 取值一致）。
OS_FAMILIES = ("windows", "linux", "darwin")

# 按「追加」合并的列表路径；其余键按「替换」合并。
_APPEND_PATHS = {
    ("patches",),
    ("deps",),
    ("submodules",),
    ("meson", "options"),
    ("autotools", "configure"),
    ("test", "known_failures"),
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge override into base (lists are replaced)."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _merge(base: Dict[str, Any], override: Dict[str, Any],
           path: Tuple[str, ...] = ()) -> Dict[str, Any]:
    """Merge one override block into a field dict.

    `_APPEND_PATHS` 下的列表追加到 base 之后（永不丢弃既有条目）；dict 深合并、
    叶子值覆盖；其余（标量、非追加路径的列表）覆盖。
    """
    out = dict(base)
    for k, v in override.items():
        p = path + (k,)
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v, p)
        elif p in _APPEND_PATHS and isinstance(v, list):
            base_list = list(out.get(k) or [])
            # 去重追加：一个块里同时写家族名和该家族的精确 target 名
            # （`for: [windows, msys2-mingw64]`）时，同一份 body 会被拆成两个
            # 匹配块，不去重就会把每条补丁/依赖/选项塞两遍。取值不同的选项
            # （-Dfoo=1 / -Dfoo=2）字符串不同，不受影响，仍能后者覆盖前者。
            out[k] = base_list + [item for item in v if item not in base_list]
        else:
            out[k] = v
    return out


def _normalize_targets(
    name: str, targets: Any
) -> List[Tuple[int, List[str], Dict[str, Any]]]:
    """Return ordered (specificity, selectors, body) blocks; validate the shape.

    specificity 0 = OS family, 1 = exact target name; it only decides the
    *order* blocks are applied in (family first, so an exact-target block can
    still win on dict/scalar keys).
    """
    if not targets:
        return []
    blocks: List[Tuple[int, List[str], Dict[str, Any]]] = []

    def add(sel: Any, body: Dict[str, Any], where: str) -> None:
        if not isinstance(sel, str) or not sel:
            raise ValueError(f"{name}: {where} 的选择器必须是非空字符串")
        if set(body) & {"for", "targets"}:
            raise ValueError(f"{name}: {where} 的覆盖体里不能再写 for/targets")
        spec = 0 if sel in OS_FAMILIES else 1
        blocks.append((spec, [sel], body))

    if isinstance(targets, dict):
        for sel, body in targets.items():
            if not isinstance(body, dict):
                raise ValueError(f"{name}: targets.{sel} 必须是映射")
            add(sel, body, f"targets.{sel}")
    elif isinstance(targets, list):
        for i, item in enumerate(targets):
            if not isinstance(item, dict):
                raise ValueError(f"{name}: targets[{i}] 必须是映射")
            sel = item.get("for", item.get("targets"))
            if sel is None:
                raise ValueError(f"{name}: targets[{i}] 缺少 for: 选择器")
            if isinstance(sel, str):
                sel = [sel]
            if not isinstance(sel, list) or not sel:
                raise ValueError(f"{name}: targets[{i}].for 必须是字符串或字符串列表")
            body = {k: v for k, v in item.items() if k not in ("for", "targets")}
            for one in sel:
                add(one, body, f"targets[{i}]")
    else:
        raise ValueError(f"{name}: targets 必须是映射或列表")

    # 稳定排序：家族块先、精确 target 后；同类保持 YAML 书写顺序
    blocks.sort(key=lambda b: b[0])
    return blocks


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
    # 纯数据包（build: data）的声明：install_files / text_files，见 engines.DataEngine
    data: Dict[str, Any] = field(default_factory=dict)
    post_install: Dict[str, Any] = field(default_factory=dict)
    test: Dict[str, Any] = field(default_factory=dict)
    patches: List[str] = field(default_factory=list)
    # 目标子目录 -> 依赖 recipe 名：解包后把依赖源码树复制到本包源码的子目录
    # （用于 tag 打包不含 git submodule 的工程，如 SPIRV-Tools/shaderc）
    submodules: Dict[str, str] = field(default_factory=dict)
    # 平台/目标覆盖块；形态见模块 docstring（映射或 for: 列表）
    targets: Any = field(default_factory=dict)
    # 本包**属于哪些 target 的构建范围**（选择器语法与 targets: 的 `for:` 相同：
    # OS 家族名或精确 target 名；空 = 全平台）。与 targets: 的区别：targets: 是
    # "同一个包在不同平台上取不同值"，platforms: 是"这个包只在某些平台上存在"。
    # 典型例子：X 客户端栈与 Mesa 只在 Linux 侧自建，Windows 侧既不需要也不该
    # 构建（egl-headers 会与之争抢 sysroot 里的 EGL 头）；反之 directx-headers/
    # directxmath/egl-headers 只属于 Windows。CI 按 `list` 全量枚举 recipe，
    # 归属由这里声明，不在 workflow 里抄一份排除名单（那份必然与 recipe 漂移）。
    platforms: List[str] = field(default_factory=list)
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

    def selectors(self) -> List[str]:
        """本 recipe 声明过的选择器（家族名或 target 名），供 CLI 展示。"""
        seen: List[str] = []
        for _, sels, _ in _normalize_targets(self.name, self.targets):
            for s in sels:
                if s not in seen:
                    seen.append(s)
        return seen

    def applies_to(self, target: str, target_os: str = "") -> bool:
        """本包是否属于该 target 的构建范围（`platforms:` 为空则属于所有平台）。"""
        if not self.platforms:
            return True
        return any(s == target or (target_os and s == target_os)
                   for s in self.platforms)

    def _fields(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "build": self.build,
            "deps": self.deps,
            "meson": self.meson,
            "cmake": self.cmake,
            "autotools": self.autotools,
            "data": self.data,
            "post_install": self.post_install,
            "test": self.test,
            "patches": self.patches,
            "submodules": self.submodules,
            "default_library": self.default_library,
        }

    def for_target(self, target: str, target_os: str = "") -> "Recipe":
        """Return a copy with every matching override block applied.

        `target_os` is the toolchain's OS family (toolchains/*.yaml `target_os`);
        blocks whose selector is that family apply here as well, so Windows-only
        patches/options are written once for all Windows targets.
        """
        blocks = _normalize_targets(self.name, self.targets)
        matched = [
            (spec, body)
            for spec, sels, body in blocks
            if target in sels or (target_os and target_os in sels)
        ]
        if not matched:
            return self
        fields = self._fields()
        for _spec, body in matched:
            fields = _merge(fields, body)
        return Recipe(
            name=self.name,
            version=self.version,
            targets={},
            # platforms 是"包属于哪些平台"的身份信息，不随 target 覆盖变化，
            # 也不在 _fields() 里（否则一个 targets: 块就能改自己的归属）
            platforms=list(self.platforms),
            **fields,
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
    targets = data.get("targets", {})
    # 早失败：targets 形态写错时立刻报出文件名，而不是等到某个 target 构建时
    _normalize_targets(data["name"], targets)
    platforms = data.get("platforms") or []
    if not isinstance(platforms, list) or any(
        not isinstance(s, str) or not s for s in platforms
    ):
        raise ValueError(f"{path}: platforms 必须是非空字符串列表")
    return Recipe(
        name=data["name"],
        version=str(data["version"]),
        source=source,
        build=data["build"],
        deps=data.get("deps", []),
        meson=data.get("meson", {}),
        cmake=data.get("cmake", {}),
        autotools=data.get("autotools", {}),
        data=data.get("data", {}),
        post_install=data.get("post_install", {}),
        test=data.get("test", {}),
        patches=data.get("patches", []),
        submodules=data.get("submodules", {}),
        targets=targets,
        platforms=list(platforms),
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

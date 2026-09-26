"""Project configuration loading (gtk-cross.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import yaml


@dataclass
class ProjectConfig:
    root: Path
    default_target: str = "msys2-mingw64"
    targets: Dict[str, Dict] = field(default_factory=dict)
    downloads_dir: Path = Path("downloads")
    build_dir: Path = Path("build")
    # 库默认链接形态：static = 只产出 .a（不生成 DLL）；shared 为旧的动态行为。
    # recipe 可用同名键覆盖（见 Recipe.default_library）。
    default_library: str = "static"
    # meson 的 prefer_static：dependency() 优先选 .a 并以 --static 查询
    # pkg-config，带出 .pc 的 Cflags.private 静态消费宏。
    prefer_static: bool = True

    @classmethod
    def load(cls, root: Path | None = None) -> "ProjectConfig":
        root = Path(root or Path.cwd())
        cfg: dict = {}
        cfg_path = root / "gtk-cross.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        targets = cfg.get("targets") or {}
        if not targets:
            # fall back to a single native target keyed by default_target
            targets = {cfg.get("default_target", "msys2-mingw64"): {}}
        return cls(
            root=root,
            default_target=cfg.get("default_target", "msys2-mingw64"),
            targets=targets,
            downloads_dir=root / (cfg.get("downloads_dir", "downloads")),
            build_dir=root / (cfg.get("build_dir", "build")),
            default_library=cfg.get("default_library", "static"),
            prefer_static=bool(cfg.get("prefer_static", True)),
        )

    def target(self, name: str) -> Dict:
        if name not in self.targets:
            raise KeyError(
                f"unknown target {name!r}; defined: {sorted(self.targets)}"
            )
        return self.targets[name]

    def sysroot(self, name: str) -> Path:
        t = self.target(name)
        prefix = t.get("prefix") or f"out/{name}"
        return self.root / prefix

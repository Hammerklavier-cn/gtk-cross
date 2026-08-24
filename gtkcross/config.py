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

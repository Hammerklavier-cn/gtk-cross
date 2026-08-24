"""Source fetching, integrity check, and extraction with caching."""

from __future__ import annotations

import hashlib
import shutil
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List

_UA = f"gtkcross/0.1 (+https://atomgit.com/gtk-cross)"
_OK_MARKER = ".gtkcross-extract-ok"


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(
    sources: List[Dict[str, str]],
    dest_dir: Path,
    sha256: str = "",
) -> tuple[Path, str, str]:
    """Download the first reachable candidate source (cached), verify sha256.

    sources: [{url, sha256}]，按顺序尝试；候选自带 sha256 优先，
    否则用共享的 sha256 参数（'' = 算出即接受并自动锁定）。
    缓存文件哈希与预期不符时丢弃重下；下载失败会清理残留文件。

    Returns (archive_path, actual_sha256, url_used).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    errors: List[str] = []
    for src in sources:
        url = src["url"]
        want = src.get("sha256") or sha256
        name = url.rsplit("/", 1)[-1]
        archive = dest_dir / name
        try:
            if archive.exists():
                actual = compute_sha256(archive)
                if not want or actual == want:
                    return archive, actual, url
                # 缓存与预期不符（配方/锁定已变更）: 丢弃后重下
                print(f"  [cache-discard] {name}: {actual[:16]}… != {want[:16]}…")
                archive.unlink()
            print(f"  downloading {url}")
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            # 超时兜底：黑洞/挂起的主机不应卡死整个构建
            with urllib.request.urlopen(req, timeout=180) as resp:
                with open(archive, "wb") as out:
                    shutil.copyfileobj(resp, out)
            actual = compute_sha256(archive)
            if want and actual != want:
                raise ValueError(f"sha256 mismatch: expected {want}, got {actual}")
            return archive, actual, url
        except Exception as e:  # 任一候选失败均回退到下一个源
            errors.append(f"{url}: {e}")
            if archive.exists():
                archive.unlink()  # 残留/错误内容不留在缓存中
            print(f"  [mirror-fallback] {url}: {e}")
    raise RuntimeError(
        f"all {len(sources)} download source(s) failed for {dest_dir.name}:\n  "
        + "\n  ".join(errors)
    )


def extract(archive: Path, dest: Path) -> Path:
    """Extract archive into dest (deterministic), stripping one leading dir level.

    dest is always the final source directory. A successful extraction is
    marked with a completion marker: a half-extracted tree (e.g. process
    killed mid-run) is never reused as valid source.
    """
    marker = dest / _OK_MARKER
    if marker.exists():
        return dest
    if dest.exists() and any(dest.iterdir()):
        # partial tree from a killed run: drop it and re-extract
        shutil.rmtree(dest)
    stage = dest.with_name(dest.name + ".extract")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    print(f"  extracting {archive.name}")
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(stage)
    else:
        with tarfile.open(archive) as t:
            for member in t.getmembers():
                try:
                    t.extract(member, stage, filter="data")
                except (tarfile.LinkOutsideDestinationError,
                        tarfile.FilterError,
                        OSError) as e:
                    # 部分上游包的测试样例含故意损坏/越界 symlink
                    # （如 appstream tests/samples 中的 badlink），跳过该成员
                    print(f"  [extract-skip] {member.name}: {e}")
    entries = [p for p in stage.iterdir()]
    src_root = entries[0] if len(entries) == 1 and entries[0].is_dir() else stage
    dest.mkdir(parents=True, exist_ok=True)
    for p in src_root.iterdir():
        shutil.move(str(p), dest / p.name)
    shutil.rmtree(stage)
    marker.write_text("ok\n", encoding="utf-8")
    return dest

"""Source fetching, integrity check, and extraction with caching."""

from __future__ import annotations

import contextlib
import hashlib
import shutil
import socket
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List

_UA = f"gtkcross/0.1 (+https://atomgit.com/gtk-cross)"
_OK_MARKER = ".gtkcross-extract-ok"


@contextlib.contextmanager
def _prefer_ipv4():
    """把 A 记录排到 AAAA 之前（只改顺序，不禁用 IPv6）。

    存在这样一类网络故障：同一主机名的 IPv4 与 IPv6 出口返回**不同的证书**，
    IPv6 那条被中间设备换成一张 subject 为空、SAN 里只有 IP 地址的证书，于是
    按主机名校验的 TLS 客户端必然报 CERTIFICATE_VERIFY_FAILED / Hostname
    mismatch。实测（2026-09-29，本机 Fedora 44）：
      gstreamer.freedesktop.org
        IPv4 199.232.115.52      -> CN=gstreamer.freedesktop.org, SAN 同名  ✓
        IPv6 2406:cb42:0:2018::2 -> subject 空，SAN=critical {IP 117.55.193.154,
                                    IP 2406:CB42:...}                        ✗
    curl 看不出来是因为它先试 IPv4；`socket.create_connection` 按 getaddrinfo
    的返回顺序走，走到 IPv6 就失败。
    这里只在重试时调整顺序；机器若只有 IPv6 连通（过滤后为空）则退回原始结果。
    """
    orig = socket.getaddrinfo

    def reordered(*args, **kwargs):
        res = orig(*args, **kwargs)
        v4 = [r for r in res if r[0] == socket.AF_INET]
        return (v4 + [r for r in res if r[0] != socket.AF_INET]) if v4 else res

    socket.getaddrinfo = reordered
    try:
        yield
    finally:
        socket.getaddrinfo = orig


def _download_to(req: "urllib.request.Request", archive: Path) -> None:
    """抓取 url 内容写入 archive（超时兜底：黑洞主机不应卡死整个构建）。"""
    with urllib.request.urlopen(req, timeout=180) as resp:
        with open(archive, "wb") as out:
            shutil.copyfileobj(resp, out)


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
            try:
                _download_to(req, archive)
            except Exception:
                # 首次失败先按"IPv6 出口证书被换"这一类故障重试一次（A 记录优先），
                # 仍失败才判这个源不可用、继续下一个候选源。
                archive.unlink(missing_ok=True)
                with _prefer_ipv4():
                    _download_to(req, archive)
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
                    # （如 appstream tests/samples 中的 badlink），跳过该成员。
                    # 注：归档内合法的 symlink 无需在此兜底——无符号链接权限时
                    # tarfile 自己会退化为"复制目标文件内容"
                    # （TarFile.makelink_with_filter 的既有行为），
                    # adwaita-icon-theme 的 2 个 symlink 图标即由此完整落盘。
                    print(f"  [extract-skip] {member.name}: {e}")
    entries = [p for p in stage.iterdir()]
    src_root = entries[0] if len(entries) == 1 and entries[0].is_dir() else stage
    dest.mkdir(parents=True, exist_ok=True)
    for p in src_root.iterdir():
        shutil.move(str(p), dest / p.name)
    shutil.rmtree(stage)
    marker.write_text("ok\n", encoding="utf-8")
    return dest

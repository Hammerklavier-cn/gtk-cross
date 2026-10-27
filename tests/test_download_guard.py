"""下载护栏：内容必须是可识别的 tar/zip，文件名与"能连上"都不算证据。

起因（2026-09-29，落地 recipes/openssl.yaml 时实测到）：某个镜像路径返回
31,669 字节的 HTML 404 错误页，`curl -I` 报 200、带 Range 的 GET 报 206，
文件名又正好是 `openssl-3.5.8.tar.gz`；`gtkcross lock` 在没有预期 sha256 时
"算出即接受"，于是错误页被当源码包写进了 versions.lock.yaml。

运行：PYTHONPATH=. python3 -m unittest discover -s tests
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from gtkcross import download

HTML_404 = (b"<!DOCTYPE html>\n<html lang=\"en\">\n  <head>\n    <title>"
            + b"Not Found</title>\n  </head>\n  <body>\n" + b"x" * 200
            + b"</body>\n</html>\n")
JUNK = b"\x00\x01\x02 not a container at all"


def tar_gz_bytes() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        payload = b"hello\n"
        info = tarfile.TarInfo("pkg/hello.txt")
        info.size = len(payload)
        t.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("pkg/hello.txt", "hello\n")
    return buf.getvalue()


class ArchiveKind(unittest.TestCase):
    """archive_kind 只看内容：同一份字节换任何扩展名结论不变。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _kind(self, payload: bytes, name: str) -> str:
        p = self.dir / name
        p.write_bytes(payload)
        return download.archive_kind(p)

    def test_detects_tar_and_zip_by_content(self):
        self.assertEqual(self._kind(tar_gz_bytes(), "whatever.tar.gz"), "tar")
        self.assertEqual(self._kind(tar_gz_bytes(), "wrongname.zip"), "tar")
        self.assertEqual(self._kind(zip_bytes(), "whatever.zip"), "zip")

    def test_rejects_error_page_and_empty(self):
        self.assertEqual(self._kind(HTML_404, "openssl-3.5.8.tar.gz"), "")
        self.assertEqual(self._kind(JUNK, "pkg.tar.gz"), "")
        self.assertEqual(self._kind(b"", "pkg.tar.gz"), "")

    def test_error_page_hint(self):
        p = self.dir / "page.tar.gz"
        p.write_bytes(HTML_404)
        self.assertTrue(download._looks_like_error_page(p))
        p.write_bytes(JUNK)
        self.assertFalse(download._looks_like_error_page(p))


class FetchGuard(unittest.TestCase):
    """fetch 必须拒绝非容器内容、清理残留，并且不遮蔽原有的哈希校验。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = Path(self.tmp.name) / "downloads" / "pkg"

    def _fetch(self, payloads: dict, sha256: str = ""):
        """payloads: url -> 响应字节（替掉 _download_to，不碰网络）。"""
        def fake_download(req, archive: Path) -> None:
            archive.write_bytes(payloads[req.full_url])

        sources = [{"url": u, "sha256": ""} for u in payloads]
        with mock.patch.object(download, "_download_to", fake_download):
            return download.fetch(sources, self.dest, sha256)

    def test_accepts_real_tarball(self):
        url = "https://example.invalid/pkg-1.0.tar.gz"
        path, actual, used = self._fetch({url: tar_gz_bytes()})
        self.assertEqual(used, url)
        self.assertEqual(actual, hashlib.sha256(tar_gz_bytes()).hexdigest())
        self.assertEqual(path.read_bytes(), tar_gz_bytes())

    def test_rejects_error_page_and_leaves_no_cache_poison(self):
        url = "https://example.invalid/openssl-3.5.8.tar.gz"
        with self.assertRaises(RuntimeError) as ctx:
            self._fetch({url: HTML_404})
        msg = str(ctx.exception)
        self.assertIn("不是可识别的 tar/zip", msg)
        self.assertIn("HTML", msg)          # 直接说出病因，不用人去猜
        self.assertIn(str(len(HTML_404)), msg)
        # 残留必须清掉：否则下次运行会走"缓存命中"分支绕过校验
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_hash_check_still_applies_to_valid_content(self):
        url = "https://example.invalid/pkg-1.0.tar.gz"
        with self.assertRaises(RuntimeError) as ctx:
            self._fetch({url: tar_gz_bytes()},
                        sha256=hashlib.sha256(b"other bytes").hexdigest())
        self.assertIn("sha256 mismatch", str(ctx.exception))

    def test_bad_source_falls_back_to_next_candidate(self):
        bad = "https://first.invalid/pkg.tar.gz"
        good = "https://second.invalid/pkg.tar.gz"
        path, actual, used = self._fetch({bad: HTML_404, good: tar_gz_bytes()})
        self.assertEqual(used, good)
        self.assertEqual(download.archive_kind(path), "tar")
        self.assertTrue(actual)


if __name__ == "__main__":
    unittest.main()

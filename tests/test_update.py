"""更新（GitHub の Releases から新しい版を入れる）と、インストールした形の版の切り替えのテスト。"""

import hashlib
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from lablog import install, update
from lablog.update import Updater, download, extract_release, latest_release, parse_version, version_key


def make_zip(version: str, extra: dict | None = None, init_version: str | None = None) -> bytes:
    buf = io.BytesIO()
    top = f"LabLog-{version}/"
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(top + "LabLog.pyw", "# LabLog")
        z.writestr(top + "lablog/__init__.py", f'__version__ = "{init_version or version}"\n')
        z.writestr(top + "lablog/main.py", "def main(argv=None): pass\n")
        for name, text in (extra or {}).items():
            z.writestr(name, text)
    return buf.getvalue()


def release(version, prerelease=False, draft=False, asset=True, size=None, digest=None):
    assets = [{"name": f"LabLog-{version}.zip", "browser_download_url": f"https://example.invalid/{version}.zip",
               "size": size, "digest": digest}] if asset else []
    return {"tag_name": f"v{version}", "name": f"LabLog {version}", "body": "## 変更点\n- いろいろ",
            "prerelease": prerelease, "draft": draft, "assets": assets, "html_url": "https://example.invalid/r"}


class FakeOpener:
    """urllib.request.urlopen の代わり。URL ごとに返す中身を決めておく。"""

    def __init__(self, routes: dict):
        self.routes = routes

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        body = self.routes[url.split("?")[0]]
        return io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode())


class VersionTest(unittest.TestCase):
    def test_order(self):
        order = ["0.9.0", "1.0.0a1", "1.0.0b1", "1.0.0b2", "1.0.0rc1", "1.0.0", "1.0.1", "1.10.0"]
        self.assertEqual(sorted(reversed(order), key=version_key), order)

    def test_parse(self):
        self.assertEqual(parse_version("v1.2.3"), parse_version("1.2.3"))
        for bad in ("1.2", "1.2.3-beta", "latest", ""):
            self.assertIsNone(parse_version(bad))


class ReleaseTest(unittest.TestCase):
    API = "https://api.example.invalid/releases"

    def latest(self, channel, releases):
        return latest_release(channel, self.API, FakeOpener({self.API: releases}))

    def test_stable_skips_prerelease_and_draft(self):
        rels = [release("1.1.0b1", prerelease=True), release("1.2.0", draft=True), release("1.0.1"), release("1.0.0")]
        self.assertEqual(self.latest("stable", rels)["version"], "1.0.1")
        self.assertEqual(self.latest("beta", rels)["version"], "1.1.0b1")

    def test_release_without_zip_is_ignored(self):
        self.assertEqual(self.latest("stable", [release("1.1.0", asset=False), release("1.0.0")])["version"], "1.0.0")
        self.assertIsNone(self.latest("stable", []))


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = make_zip("1.0.1")
        self.opener = FakeOpener({"https://example.invalid/1.0.1.zip": self.data})
        self.dest = Path(self.tmp.name) / "a.zip"

    def test_ok_with_digest(self):
        digest = "sha256:" + hashlib.sha256(self.data).hexdigest()
        download("https://example.invalid/1.0.1.zip", self.dest, len(self.data), digest, opener=self.opener)
        self.assertEqual(self.dest.read_bytes(), self.data)

    def test_wrong_digest_or_size(self):
        with self.assertRaises(RuntimeError):
            download("https://example.invalid/1.0.1.zip", self.dest, len(self.data), "sha256:" + "0" * 64,
                     opener=self.opener)
        with self.assertRaises(RuntimeError):
            download("https://example.invalid/1.0.1.zip", self.dest, len(self.data) + 1, None, opener=self.opener)


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.versions = self.root / "versions"
        self.versions.mkdir()

    def write(self, data: bytes) -> Path:
        p = self.root / "r.zip"
        p.write_bytes(data)
        return p

    def test_extract(self):
        target = extract_release(self.write(make_zip("1.0.1")), "1.0.1", self.versions)
        self.assertEqual(target, self.versions / "1.0.1")
        self.assertTrue((target / "lablog" / "main.py").is_file())
        self.assertFalse((self.versions / "1.0.1.tmp").exists())

    def test_rejects_files_outside(self):
        with self.assertRaises(RuntimeError):
            extract_release(self.write(make_zip("1.0.1", {"LabLog-1.0.1/../../evil.py": "x"})), "1.0.1", self.versions)
        with self.assertRaises(RuntimeError):
            extract_release(self.write(make_zip("1.0.1", {"other/evil.py": "x"})), "1.0.1", self.versions)
        self.assertFalse((self.root / "evil.py").exists())

    def test_rejects_wrong_version(self):
        with self.assertRaises(RuntimeError):
            extract_release(self.write(make_zip("1.0.1", init_version="0.0.1")), "1.0.1", self.versions)


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        for v in ("0.8.0", "0.8.5", "0.9.0"):
            (self.base / "versions" / v).mkdir(parents=True)
        install.write_versions(self.base, {"current": "0.9.0", "previous": "0.8.5"})
        self.data = make_zip("1.0.1")
        self.restarted = threading.Event()

    def run_update(self, data: bytes):
        opener = FakeOpener({"https://example.invalid/1.0.1.zip": data})
        up = Updater(self.restarted.set, base=self.base, opener=opener)
        up.start({"version": "1.0.1", "url": "https://example.invalid/1.0.1.zip", "size": len(data)})
        for _ in range(100):
            if up.status()["state"] not in ("running",):
                break
            self.restarted.wait(0.05)
        return up.status()

    def test_update_switches_version_and_restarts(self):
        job = self.run_update(self.data)
        self.assertEqual(job["state"], "restarting")
        self.assertTrue(self.restarted.is_set())
        self.assertEqual(install.read_versions(self.base), {"current": "1.0.1", "previous": "0.9.0"})
        kept = sorted(p.name for p in (self.base / "versions").iterdir())
        self.assertEqual(kept, ["0.8.5", "0.9.0", "1.0.1"])  # 新しいものから 3 つ（いまの版と前の版を含む）

    def test_failed_update_keeps_current_version(self):
        job = self.run_update(make_zip("1.0.1", init_version="9.9.9"))
        self.assertEqual(job["state"], "error")
        self.assertFalse(self.restarted.is_set())
        self.assertEqual(install.read_versions(self.base)["current"], "0.9.0")

    def test_not_installed(self):
        up = Updater(lambda: None, base=None)
        self.assertFalse(up.installed)
        with self.assertRaises(RuntimeError):
            up.start({"version": "1.0.1", "url": "https://example.invalid/1.0.1.zip"})


class InstallBaseTest(unittest.TestCase):
    def test_detect(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            app = base / "versions" / "1.0.0"
            app.mkdir(parents=True)
            self.assertIsNone(install.install_base(app))  # version.json がまだ無い
            install.write_versions(base, {"current": "1.0.0", "previous": None})
            self.assertEqual(install.install_base(app), base)
            self.assertIsNone(install.install_base(Path(tmp)))  # 開発用のフォルダ


if __name__ == "__main__":
    unittest.main()

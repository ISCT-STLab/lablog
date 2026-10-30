"""更新（GitHub の Releases から新しい版を入れる）と、インストールした形の版の切り替えのテスト。"""

import hashlib
import io
import json
import tempfile
import threading
import unittest
import uuid
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
        digest = "sha256:" + hashlib.sha256(self.data).hexdigest()
        with self.assertRaises(RuntimeError):
            download("https://example.invalid/1.0.1.zip", self.dest, len(self.data) + 1, digest, opener=self.opener)

    def test_without_digest(self):
        """SHA-256 が付いていないリリースは使わない（ダウンロードしたものが正しいか確かめられない）。"""
        with self.assertRaises(RuntimeError):
            download("https://example.invalid/1.0.1.zip", self.dest, len(self.data), None, opener=self.opener)


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
        up.start({"version": "1.0.1", "url": "https://example.invalid/1.0.1.zip", "size": len(data),
                  "digest": "sha256:" + hashlib.sha256(data).hexdigest()})
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


class RegisterAppTest(unittest.TestCase):
    """Windows の「インストールされているアプリ」への登録。本物の登録先ではなく、テスト用のキーに書く。"""

    def setUp(self):
        import winreg
        self.winreg = winreg
        self.key = rf"Software\LabLogTest-{uuid.uuid4().hex}"

        def remove_key():
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self.key)
            except FileNotFoundError:  # 登録しないテスト
                pass
        self.addCleanup(remove_key)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name) / "Programs" / "Lab Log"  # 空白を含むパスでも
        (self.base / "versions" / "1.0.0").mkdir(parents=True)
        (self.base / "uninstall.cmd").write_text("@echo off", encoding="utf-8")

    def value(self, name):
        with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, self.key) as k:
            return self.winreg.QueryValueEx(k, name)[0]

    def test_register_and_update_version(self):
        install.register_app(self.base, "1.0.0", self.key)
        self.assertEqual(self.value("DisplayName"), "LabLog")
        self.assertEqual(self.value("DisplayVersion"), "1.0.0")
        self.assertEqual(self.value("InstallLocation"), str(self.base))
        self.assertEqual(self.value("NoModify"), 1)
        self.assertTrue(Path(self.value("DisplayIcon")).is_file())
        cmd = self.value("UninstallString")
        self.assertTrue(cmd.startswith('"') and ' /c ""' in cmd and cmd.endswith('"'))
        self.assertIn(f'"{self.base / "uninstall.cmd"}" -Confirmed -InstallDir "{self.base}"', cmd)
        self.assertIn(f'-UninstallKey "{self.key}"', cmd)  # テスト用のキーなら、アンインストールにも渡す
        # 更新したあとに起動すると、バージョンだけ変わり、インストールした日はそのまま
        with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, self.key, 0, self.winreg.KEY_SET_VALUE) as k:
            self.winreg.SetValueEx(k, "InstallDate", 0, self.winreg.REG_SZ, "20260101")
        install.register_app(self.base, "1.1.0", self.key)
        self.assertEqual(self.value("DisplayVersion"), "1.1.0")
        self.assertEqual(self.value("InstallDate"), "20260101")

    def test_refresh_uninstaller_after_update(self):
        """画面から更新したあと：一番上の uninstall.ps1 が前のバージョンのままなら、動いているバージョンのものに替える。"""
        app = self.base / "versions" / "1.1.0"
        app.mkdir()
        (app / "uninstall.ps1").write_text("# new", encoding="utf-8")
        (self.base / "uninstall.ps1").write_text("# old", encoding="utf-8")
        install.refresh_uninstaller(self.base, app)
        self.assertEqual((self.base / "uninstall.ps1").read_text(encoding="utf-8"), "# new")
        self.assertEqual((self.base / "uninstall.cmd").read_text(encoding="utf-8"), "@echo off")  # 無いものはそのまま
        install.refresh_uninstaller(self.base, self.base / "versions" / "1.0.0")  # setup で入れた形（versions に無い）
        self.assertEqual((self.base / "uninstall.ps1").read_text(encoding="utf-8"), "# new")


if __name__ == "__main__":
    unittest.main()

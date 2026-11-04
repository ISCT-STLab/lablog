"""ブラウザ処理の回帰テストを Node.js で実行する（開発時のみ）。スマホ用ビューアーの公開用ファイルの作り方も確かめる。"""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import publish_viewer  # noqa: E402


class WebTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "JavaScript のテストには Node.js が必要です")
    def test_report_and_quit(self):
        result = subprocess.run([shutil.which("node"), "--test", "tests/web_report.test.cjs", "tests/web_qr.test.cjs",
                                 "tests/web_cloud.test.cjs", "tests/web_sw.test.cjs"],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class PublishViewerTest(unittest.TestCase):
    def test_service_worker_gets_channel_and_the_same_release_as_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            for beta, channel, folder in ((False, "stable", site), (True, "beta", site / "beta")):
                publish_viewer.build(site, "", beta)
                config = (folder / "config.js").read_text(encoding="utf-8")
                release = json.loads(config[config.index("{"):config.rindex("}") + 1])["release"]
                sw = (folder / "sw.js").read_text(encoding="utf-8")
                self.assertNotIn(publish_viewer.SW_PLACEHOLDER, sw)
                self.assertIn(f'const CHANNEL = "{channel}", BUILD = {json.dumps(release)};', sw)
                self.assertRegex(release, r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?-\d{14}$")
            # 開発用の sw.js はそのまま（PC のダッシュボードは Service Worker を使わない）
            self.assertIn(publish_viewer.SW_PLACEHOLDER, (ROOT / "lablog" / "web" / "sw.js").read_text(encoding="utf-8"))

"""ブラウザ処理の回帰テストを Node.js で実行する（開発時のみ）。"""
from pathlib import Path
import shutil
import subprocess
import unittest


class WebTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "JavaScript のテストには Node.js が必要です")
    def test_report_and_quit(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([shutil.which("node"), "--test", "tests/web_report.test.cjs"],
                                cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

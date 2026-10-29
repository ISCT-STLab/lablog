"""初回の設定（記録を始める時期）と、ローカル AI の準備のテスト。"""

import json
import tempfile
import threading
import time
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from lablog import aisetup, db
from lablog.aisetup import OllamaSetup, recommended_model
from lablog.collectors import first_since
from lablog.config import Settings

from tests.test_presence import make_conn


class SetupDoneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "settings.json"

    def test_new_install_is_not_set_up(self):
        self.assertFalse(Settings(self.path).get("setup_done"))

    def test_existing_user_is_already_set_up(self):
        self.path.write_text('{"wifi": {"univ": ["ScienceTokyo"]}}', encoding="utf-8")  # 前の版の設定
        self.assertTrue(Settings(self.path).get("setup_done"))

    def test_saved_flag_is_kept(self):
        s = Settings(self.path)
        s.update({"setup_done": False})
        self.assertFalse(Settings(self.path).get("setup_done"))


class FirstSinceTest(unittest.TestCase):
    def test_reads_from_morning_or_after_setup(self):
        conn = make_conn()
        now = datetime(2026, 10, 20, 15).timestamp()
        morning = datetime(2026, 10, 20, 5).timestamp()
        self.assertEqual(first_since(conn, now), morning)
        db.kv_set(conn, "record_since", datetime(2026, 10, 20, 13).timestamp())  # 13 時に初回の設定を終えた
        self.assertEqual(first_since(conn, now), datetime(2026, 10, 20, 13).timestamp())


class RecommendedModelTest(unittest.TestCase):
    def test_by_memory(self):
        self.assertEqual(recommended_model(31.7), "qwen3:8b")
        self.assertEqual(recommended_model(15.6), "qwen3:8b")  # 16 GB の PC（Windows の報告は少し少ない）
        self.assertEqual(recommended_model(7.8), "qwen3:4b")
        self.assertIsNone(recommended_model(3.9))


class FakeOllama(BaseHTTPRequestHandler):
    """Ollama の API のうち、/api/tags と /api/pull（少しずつ進む）だけをまねる。"""
    fail = False

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"models": []}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.end_headers()
        events = [{"status": "pulling manifest"}, {"status": "pulling a1", "total": 100, "completed": 40},
                  {"status": "pulling a1", "total": 100, "completed": 100}]
        events.append({"error": "disk full"} if self.fail else {"status": "success"})
        for e in events:
            self.wfile.write((json.dumps(e) + "\n").encode())


class OllamaSetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.settings = Settings(Path(self.tmp.name) / "settings.json")
        self.settings.update({"ollama": {"enabled": False, "url": f"http://127.0.0.1:{self.server.server_address[1]}",
                                         "model": "qwen3:8b"}})
        self.setup = OllamaSetup(self.settings)

    def wait(self):
        for _ in range(100):
            if self.setup.status()["state"] != "running":
                return self.setup.status()
            time.sleep(0.05)
        self.fail("終わりませんでした")

    def test_pull_model_and_turn_ai_on(self):
        self.setup.start(install=False, model="qwen3:4b")
        job = self.wait()
        self.assertEqual(job["state"], "done")
        self.assertEqual(job["progress"], 1.0)
        cfg = self.settings.get("ollama")
        self.assertEqual((cfg["enabled"], cfg["model"]), (True, "qwen3:4b"))

    def test_pull_error_is_reported(self):
        FakeOllama.fail = True
        self.addCleanup(setattr, FakeOllama, "fail", False)
        self.setup.start(install=False, model="qwen3:4b")
        job = self.wait()
        self.assertEqual(job["state"], "error")
        self.assertIn("disk full", job["message"])
        self.assertEqual(self.settings.get("ollama")["model"], "qwen3:8b")  # 失敗したら設定は変えない

    def test_unknown_model_is_rejected(self):
        with self.assertRaises(ValueError):
            self.setup.start(install=False, model="llama-huge")

    def test_install_without_winget(self):
        with mock.patch.object(aisetup.shutil, "which", return_value=None):
            self.setup.start(install=True, model=None)
            job = self.wait()
        self.assertEqual(job["state"], "error")
        self.assertIn("ollama.com", job["message"])

    def test_one_job_at_a_time(self):
        self.setup._job = {"state": "running"}
        with self.assertRaises(RuntimeError):
            self.setup.start(install=False, model="qwen3:4b")


if __name__ == "__main__":
    unittest.main()

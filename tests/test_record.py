"""記録する材料の設定（record・ollama.enabled）のテスト。"""

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from lablog import report
from lablog.activity import EXCLUDED_LABEL, UNRECORDED_LABEL, ActivityRecorder
from lablog.config import Settings

from tests.test_presence import make_conn

FG = ("code.exe", "secret.py - Visual Studio Code")


class AppsModeTest(unittest.TestCase):
    def record(self, mode, fg=FG, excluded=frozenset()):
        conn = make_conn()
        recorder = ActivityRecorder(conn, 5)
        recorder.tick(1000.0, fg, 0, 300, "大学", set(excluded), False, mode)
        return dict(conn.execute("SELECT exe, title, idle FROM segments").fetchone())

    def test_title(self):
        self.assertEqual(self.record("title"), {"exe": "code.exe", "title": FG[1], "idle": 0})

    def test_app_only(self):
        self.assertEqual(self.record("app"), {"exe": "code.exe", "title": "", "idle": 0})

    def test_off_keeps_only_time(self):
        self.assertEqual(self.record("off"), {"exe": UNRECORDED_LABEL, "title": "", "idle": 0})

    def test_excluded_app(self):
        self.assertEqual(self.record("app", excluded={"code.exe"}), {"exe": EXCLUDED_LABEL, "title": "", "idle": 0})

    def test_idle_is_still_idle(self):
        self.assertEqual(self.record("off", fg=None), {"exe": "", "title": "", "idle": 1})


class RecordSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "settings.json"

    def test_defaults_record_everything(self):
        s = Settings(self.path)
        self.assertEqual(s.get("record"), {"apps": "title", "files": True, "git": True, "recent_files": True})
        self.assertTrue(s.get("ollama")["enabled"])

    def test_old_settings_get_new_keys(self):
        # record も ollama.enabled も無い前の版の設定
        self.path.write_text('{"ollama": {"url": "http://127.0.0.1:11434", "model": "qwen3:8b", "auto": false}}',
                             encoding="utf-8")
        s = Settings(self.path)
        self.assertEqual(s.get("record")["apps"], "title")
        self.assertEqual(s.get("ollama"), {"enabled": True, "url": "http://127.0.0.1:11434", "model": "qwen3:8b",
                                           "auto": False})

    def test_update(self):
        s = Settings(self.path)
        s.update({"record": {"apps": "off", "files": False, "git": True, "recent_files": False}})
        self.assertEqual(Settings(self.path).get("record"),
                         {"apps": "off", "files": False, "git": True, "recent_files": False})
        with self.assertRaises(ValueError):
            s.update({"record": {"apps": "everything"}})


class ReportWithoutAITest(unittest.TestCase):
    def test_ai_off_does_not_ask_ollama(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings = Settings(Path(tmp.name) / "settings.json")
        settings.update({"ollama": {"enabled": False, "url": "http://127.0.0.1:11434", "model": "qwen3:8b"}})
        conn = make_conn()
        d = date(2026, 10, 20)
        t = datetime(2026, 10, 20, 10).timestamp()
        conn.execute("INSERT INTO stays (start_ts, end_ts, status, end_reason) VALUES (?, ?, 'closed', 'offcampus')",
                     (t, t + 3600))
        with mock.patch.object(report, "ollama_status") as status, mock.patch.object(report, "ollama_generate") as gen:
            result = report.generate_report(conn, settings, d)
        status.assert_not_called()
        gen.assert_not_called()
        self.assertEqual(result["source"], "rule")


if __name__ == "__main__":
    unittest.main()

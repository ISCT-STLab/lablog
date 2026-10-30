"""ウィンドウ名から分からない作業の補い方（Claude Code の会話・画面の文字）のテスト。"""

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from lablog.collectors import import_claude_code
from lablog.config import SCREEN_TEXT_APPS, Settings
from lablog.report import build_digest
from lablog.screentext import tidy_lines
from lablog.summary import day_summary

from tests.test_presence import make_conn

D = date(2026, 10, 20)


def at(hour, minute=0):
    return datetime(D.year, D.month, D.day, hour, minute).timestamp()


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


class ClaudeCodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "projects"
        (self.root / "C--work-LabLog").mkdir(parents=True)
        self.log = self.root / "C--work-LabLog" / "s1.jsonl"
        self.settings = Settings(Path(self.tmp.name) / "settings.json")
        self.conn = make_conn()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, *lines, raw=""):
        with open(self.log, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            fh.write(raw)

    def message(self, ts, kind="user"):
        return {"type": kind, "sessionId": "s1", "timestamp": iso(ts), "cwd": "C:\\work\\LabLog",
                "message": {"content": "会話の中身（保存しない）"}}

    def test_import_titles_and_minutes_only(self):
        self.write(self.message(at(4)),  # 前日（朝 5 時より前）の分は、初回は取り込まない
                   self.message(at(10, 0, )), self.message(at(10, 0) + 30, "assistant"), self.message(at(10, 2)),
                   {"type": "custom-title", "customTitle": "研究室訪問記録アプリ", "sessionId": "s1"})
        import_claude_code(self.conn, at(12), self.root)
        session = dict(self.conn.execute("SELECT * FROM ai_sessions").fetchone())
        self.assertEqual((session["title"], session["folder"]), ("研究室訪問記録アプリ", "LabLog"))
        minutes = [r[0] for r in self.conn.execute("SELECT minute FROM ai_activity ORDER BY minute")]
        self.assertEqual(minutes, [at(10), at(10, 2)])
        dump = json.dumps([dict(r) for r in self.conn.execute("SELECT * FROM ai_sessions")], ensure_ascii=False)
        self.assertNotIn("会話の中身", dump)

        # 続きだけを読む。書きかけの最後の行は、書き終わってから読む
        self.write(self.message(at(11)), raw=json.dumps(self.message(at(11, 30)))[:40])
        import_claude_code(self.conn, at(12), self.root)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ai_activity").fetchone()[0], 3)
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(self.message(at(11, 30)))[40:] + "\n")
        import_claude_code(self.conn, at(12), self.root)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ai_activity").fetchone()[0], 4)

    def test_claude_window_gets_session_title(self):
        self.write({"type": "custom-title", "customTitle": "研究室訪問記録アプリ", "sessionId": "s1"},
                   *[self.message(at(10, m)) for m in range(0, 30, 3)])
        import_claude_code(self.conn, at(9), self.root)
        self.conn.execute("INSERT INTO segments (start_ts, end_ts, exe, title, idle, location) "
                          "VALUES (?, ?, 'claude.exe', 'Claude', 0, '自宅')", (at(10), at(10, 30)))
        s = day_summary(self.conn, self.settings, D, at(20))
        [app] = s["apps"]
        self.assertEqual(app["titles"][0]["title"], "Claude Code：研究室訪問記録アプリ（LabLog）")
        self.assertIn("Claude Code：研究室訪問記録アプリ（LabLog）", build_digest(s))

        self.settings.update({"claude_code": {"enabled": False}})
        s = day_summary(self.conn, self.settings, D, at(20))
        self.assertEqual(s["apps"][0]["titles"][0]["title"], "Claude")


class ScreenTextTest(unittest.TestCase):
    def test_tidy_keeps_center_and_joins_japanese(self):
        lines = [{"t": "過 去 の 会 話", "x": 0.08, "y": 0.2},          # 左の一覧
                 {"t": "日 報 の 整 形 を 直 す", "x": 0.45, "y": 0.5},
                 {"t": "preprocess.py の 修 正", "x": 0.4, "y": 0.3},
                 {"t": "成 果 物", "x": 0.92, "y": 0.1}]                 # 右のパネル
        self.assertEqual(tidy_lines(lines), "preprocess.py の修正\n日報の整形を直す")

    def test_digest_uses_screen_texts_sparingly(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings = Settings(Path(tmp.name) / "settings.json")
        conn = make_conn()
        for i in range(12):
            conn.execute("INSERT INTO screen_texts (ts, exe, title, text) VALUES (?, 'claude.exe', 'Claude', ?)",
                         (at(10) + i * 300, f"話題{i} " + "あ" * 500))
        s = day_summary(conn, settings, D, at(20))
        self.assertEqual(len(s["screen_texts"]), 12)
        part = build_digest(s).split("画面から読み取った文字")[1]
        self.assertEqual(part.count("\n- [Claude"), 6)            # 1 日から 6 件だけ
        self.assertTrue(all(len(line) < 330 for line in part.splitlines()))  # 1 件 300 文字まで

    def test_default_apps(self):
        apps = SCREEN_TEXT_APPS
        self.assertIn("matlab.exe", apps)
        self.assertIn("windowsterminal.exe", apps)
        self.assertNotIn("chrome.exe", apps)   # 閲覧履歴があるブラウザは読まない
        self.assertNotIn("code.exe", apps)     # ウィンドウ名にファイル名が出る

    def test_saved_apps_are_not_widened(self):
        """前のバージョンの初期値のままでも、読み取るアプリを勝手に増やさない。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "settings.json"
        path.write_text(json.dumps({"screen_text": {"enabled": True, "apps": ["claude.exe", "chatgpt.exe"]}}),
                        encoding="utf-8")
        self.assertEqual(Settings(path).get("screen_text")["apps"], ["claude.exe", "chatgpt.exe"])


if __name__ == "__main__":
    unittest.main()

"""1.0.0b1 のレビューで見つかった不具合が、起きなくなったことを確かめる（F01〜F12 は指摘の番号）。"""

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
import zipfile
import io
from datetime import date
from pathlib import Path
from unittest import mock

from lablog import backup, collectors, db, report
from lablog.cloud import MARKER, MARKER_APP, CloudSync, cloud_day, unlink_for_uninstall
from lablog.collectors import (CHROME_EPOCH_OFFSET, _import_profile, _lnk_target, import_claude_code, poll_repo,
                               track_sources)
from lablog.config import VALIDATORS, Settings

from tests.test_presence import make_conn


class TempDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        # 初めて取り込むときの「今日の朝 5 時から」は、テストする時刻に関わらないようにする
        patcher = mock.patch.object(collectors, "first_since", return_value=0)
        patcher.start()
        self.addCleanup(patcher.stop)


class PauseTest(TempDirTest):
    """F01：一時停止中のコミットと、会話の題名を取り込まない。"""

    def test_git_commit_during_pause(self):
        if not shutil.which("git"):
            self.skipTest("git がありません")
        repo = self.root / "repo"
        repo.mkdir()
        now = time.time()

        def git(*args, when=None):
            env = os.environ | ({"GIT_AUTHOR_DATE": f"@{int(when)} +0900", "GIT_COMMITTER_DATE": f"@{int(when)} +0900"}
                                if when else {})
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)
        git("init", "-q")
        git("config", "user.email", "test@example.invalid")
        git("config", "user.name", "test")
        git("commit", "-q", "--allow-empty", "-m", "PAUSED_COMMIT", when=now - 2700)
        git("commit", "-q", "--allow-empty", "-m", "NORMAL_COMMIT", when=now - 600)
        conn = make_conn()
        conn.execute("INSERT INTO pauses (start_ts, end_ts) VALUES (?, ?)", (now - 3600, now - 1800))
        db.kv_set(conn, f"git:{repo}:since", now - 7200)
        poll_repo(conn, str(repo), now)
        subjects = [r[0] for r in conn.execute("SELECT subject FROM commits")]
        self.assertEqual(subjects, ["NORMAL_COMMIT"])

    def write_session(self, lines):
        folder = self.root / "claude" / "project"
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "s.jsonl", "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(json.dumps(line) + "\n")

    @staticmethod
    def iso(ts):
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))

    def test_claude_title_during_pause(self):
        now = time.time()
        conn = make_conn()
        conn.execute("INSERT INTO pauses (start_ts, end_ts) VALUES (?, ?)", (now - 3600, now - 1800))
        self.write_session([
            {"sessionId": "s1", "type": "user", "timestamp": self.iso(now - 2700), "cwd": "C:\\work\\secret"},
            {"sessionId": "s1", "type": "custom-title", "customTitle": "PAUSED_TITLE"},
        ])
        import_claude_code(conn, now, self.root / "claude")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_sessions").fetchone()[0], 0)
        # 再開したあとの題名は取り込む
        self.write_session([
            {"sessionId": "s1", "type": "user", "timestamp": self.iso(now - 600)},
            {"sessionId": "s1", "type": "custom-title", "customTitle": "LATER_TITLE"},
        ])
        import_claude_code(conn, now, self.root / "claude")
        self.assertEqual(conn.execute("SELECT title FROM ai_sessions").fetchone()[0], "LATER_TITLE")

    def test_claude_title_while_paused_now(self):
        """時刻のある行が無い題名の変更は、いま一時停止中なら取り込まない。"""
        now = time.time()
        conn = make_conn()
        conn.execute("INSERT INTO pauses (start_ts) VALUES (?)", (now - 60,))  # 終わっていない一時停止
        self.write_session([{"sessionId": "s1", "type": "custom-title", "customTitle": "PAUSED_TITLE"}])
        import_claude_code(conn, now, self.root / "claude")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_sessions").fetchone()[0], 0)


class ReenableTest(TempDirTest):
    """F02：オフにしていた期間の閲覧履歴を、オンに戻しても取り込まない。"""

    def make_history(self):
        path = self.root / "History"
        src = sqlite3.connect(path)
        src.executescript("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT);"
                          "CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER, transition INTEGER);")
        src.close()
        return path

    def visit(self, path, visit_id, ts, title):
        src = sqlite3.connect(path)
        src.execute("INSERT INTO urls (id, url, title) VALUES (?, ?, ?)", (visit_id, f"https://example.invalid/{visit_id}", title))
        src.execute("INSERT INTO visits (id, url, visit_time, transition) VALUES (?, ?, ?, 0)",
                    (visit_id, visit_id, int((ts + CHROME_EPOCH_OFFSET) * 1_000_000)))
        src.commit()
        src.close()
        os.utime(path, (ts, ts))  # 前回読んだときから変わったことが分かるように

    def test_visits_while_disabled_are_skipped(self):
        now = time.time()
        conn = make_conn()
        hist = self.make_history()
        tmp = self.root / "tmp"
        tmp.mkdir()
        track_sources(conn, {"browser:chrome": True}, now - 400)
        self.visit(hist, 1, now - 300, "INITIAL_VISIT")
        _import_profile(conn, "chrome", "Chrome", "Default", hist, tmp, now - 290)
        track_sources(conn, {"browser:chrome": False}, now - 250)       # オフにする
        self.visit(hist, 2, now - 200, "VISIT_WHILE_DISABLED")
        track_sources(conn, {"browser:chrome": True}, now - 150)        # オンに戻す
        self.visit(hist, 3, now - 100, "VISIT_AFTER")
        _import_profile(conn, "chrome", "Chrome", "Default", hist, tmp, now)
        titles = [r[0] for r in conn.execute("SELECT title FROM web_visits ORDER BY ts")]
        self.assertEqual(titles, ["INITIAL_VISIT", "VISIT_AFTER"])


class SettingsTest(unittest.TestCase):
    """F03：壊れた設定ファイルから、記録を始めない。"""

    def test_broken_file_is_set_aside_and_setup_starts_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"setup_done": true, "browsers": {"chrome": false', encoding="utf-8")
            s = Settings(path)
            self.assertFalse(s.get("setup_done"))  # 初期設定を終えるまでは何も記録しない
            self.assertTrue(any(p.name.startswith("settings.broken-") for p in Path(tmp).iterdir()))


class ReportTest(unittest.TestCase):
    """F04：AI の応答を待つあいだに保存した日報を、作った内容で上書きしない。"""

    def test_saved_while_generating(self):
        conn = make_conn()
        d = date.today()
        start = time.mktime((d.year, d.month, d.day, 10, 0, 0, 0, 0, -1))
        conn.execute("INSERT INTO stays (start_ts, end_ts, status) VALUES (?, ?, 'closed')", (start, start + 3600))
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(Path(tmp) / "settings.json")

            def generate(cfg, digest):
                report.save_report(conn, d, "USER_SAVED_TEXT")  # 待っているあいだに画面で保存した
                return "AI_GENERATED_TEXT"
            with mock.patch.object(report, "ollama_status", return_value={"model_ready": True}), \
                    mock.patch.object(report, "ollama_generate", side_effect=generate):
                result = report.generate_report(conn, settings, d)
        row = conn.execute("SELECT text, edited FROM reports").fetchone()
        self.assertEqual((row["text"], row["edited"]), ("USER_SAVED_TEXT", 1))
        self.assertEqual(result["text"], "USER_SAVED_TEXT")


class CloudModelTest(unittest.TestCase):
    """F07：PC の外で動く Ollama のモデル（クラウド）は使わない。"""

    def test_rejected(self):
        for name in ("gpt-oss:120b-cloud", "glm-4.6:cloud"):
            with self.assertRaises(ValueError):
                VALIDATORS["ollama"]({"url": "http://127.0.0.1:11434", "model": name})
        self.assertEqual(VALIDATORS["ollama"]({"url": "http://127.0.0.1:11434", "model": "qwen3:8b"})["model"], "qwen3:8b")


class RestoreTest(unittest.TestCase):
    """F08・F09・F12：バックアップを別の PC で戻したとき。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        patcher = mock.patch.dict(os.environ, {"LABLOG_DATA_DIR": str(self.data)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_other_pc_gets_new_device_id(self):
        conn = make_conn()
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(Path(tmp) / "settings.json")
            first = CloudSync(settings, dict)._device(conn)["id"]
            self.assertEqual(CloudSync(settings, dict)._device(conn)["id"], first)  # 同じ PC なら同じ
            db.kv_set(conn, "device_machine", "another-pc")                          # 別の PC で戻した
            self.assertNotEqual(CloudSync(settings, dict)._device(conn)["id"], first)

    def test_ai_record_is_not_in_backup(self):
        conn = db.connect()
        db.init(conn)
        (self.data / "ai.json").write_text('{"ollama": true, "models": ["qwen3:8b"]}', encoding="utf-8")
        (self.data / "settings.json").write_text("{}", encoding="utf-8")
        names = zipfile.ZipFile(io.BytesIO(backup.make_backup(conn))).namelist()
        conn.close()
        self.assertNotIn("ai.json", names)

    def test_rejects_db_without_needed_columns(self):
        bad = self.data / "bad.db"
        c = sqlite3.connect(bad)
        c.execute("CREATE TABLE stays (id INTEGER PRIMARY KEY)")  # start_ts などが無い
        c.close()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.write(bad, "lablog.db")
        with self.assertRaises(ValueError):
            backup.check_backup(buf.getvalue())


class SecureDeleteTest(unittest.TestCase):
    """F11：消した画面の文字が、DB のファイルにも WAL にも残らない。"""

    def test_deleted_text_is_gone_from_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lablog.db"
            conn = db.connect(path)
            db.init(conn)
            conn.execute("INSERT INTO screen_texts (ts, exe, title, text) VALUES (1, 'a.exe', 't', ?)",
                         ("SECRET_MARKER_" * 50,))
            conn.execute("DELETE FROM screen_texts")
            db.forget_deleted(conn)
            conn.close()
            for f in Path(tmp).iterdir():
                self.assertNotIn(b"SECRET_MARKER_", f.read_bytes(), f.name)


class LnkTest(unittest.TestCase):
    def test_broken_shortcut_does_not_hang(self):
        """終わりの印が無い壊れたショートカットでも、読み続けて止まらない。"""
        data = bytearray(0x4C)
        data[:4] = b"\x4c\x00\x00\x00"
        data[0x14:0x18] = (0x02).to_bytes(4, "little")    # HasLinkInfo だけ
        info = bytearray(0x24)
        info[4:8] = (0x24).to_bytes(4, "little")          # LinkInfo の見出しの大きさ
        info[28:32] = (0x24).to_bytes(4, "little")        # Unicode のパスの位置
        data += info + "AB".encode("utf-16-le")           # 終わりの 00 00 が無い
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.lnk"
            path.write_bytes(bytes(data))
            with self.assertRaises(ValueError):
                _lnk_target(path)


class CloudReportTest(unittest.TestCase):
    """F06：日報をスマホに送らない設定。スマホからの修正を断ったときは理由を残す。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.settings = Settings(self.root / "settings.json")

    def test_report_is_not_sent_when_turned_off(self):
        s = {"date": "2026-10-30", "date_label": "", "range": [0, 1], "now": 0, "stays": [], "stay_total": 0,
             "work_total": 0, "work_by_location": {}, "categories": [], "category_names": [], "timeline": [],
             "report": {"text": "CONFIDENTIAL_EXPERIMENT.docx", "edited": 0, "generated_ts": 1, "source": "rule"}}
        self.assertIsNotNone(cloud_day(s)["report"])
        self.assertIsNone(cloud_day(s, reports=False)["report"])
        self.assertTrue(self.settings.get("cloud")["reports"])  # 初めはオン

    def test_rejected_edit_keeps_reason(self):
        edits = self.root / "od" / "edits"
        edits.mkdir(parents=True)
        (edits / "1-a.json").write_text(json.dumps({"type": "report", "date": "2026-10-30", "text": 5, "ts": 1}),
                                        encoding="utf-8")
        CloudSync(self.settings, dict).import_edits(make_conn(), self.root / "od", time.time())
        info = json.loads((edits / "1-a.rejected").read_text(encoding="utf-8"))
        self.assertIn("日報", info["error"])
        self.assertFalse((edits / "1-a.json").exists())


class UninstallUnlinkTest(unittest.TestCase):
    """アンインストールのときに、Microsoft アカウントとの連携を解除する。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name) / "data"
        self.folder = Path(tmp.name) / "OneDrive" / "アプリ" / "LabLog"
        self.data.mkdir()
        self.folder.mkdir(parents=True)
        patcher = mock.patch.dict(os.environ, {"LABLOG_DATA_DIR": str(self.data)})
        patcher.start()
        self.addCleanup(patcher.stop)
        conn = db.connect()
        db.init(conn)
        self.device = CloudSync(Settings(), dict)._device(conn)["id"]
        conn.close()
        s = Settings()
        s.update({"setup_done": True, "cloud": {"enabled": True, "folder": str(self.folder), "viewer_url": ""}})
        (self.folder / MARKER).write_text(json.dumps({"app": MARKER_APP}), encoding="utf-8")
        (self.folder / "days").mkdir()
        (self.folder / "days" / "2026-10-30.json").write_text("{}", encoding="utf-8")

    def meta(self, device_id, exported):
        (self.folder / "meta.json").write_text(json.dumps({"device": {"id": device_id, "name": "PC"},
                                                           "exported": exported}), encoding="utf-8")

    def test_unlinks_this_pc(self):
        self.meta(self.device, time.time())
        message = unlink_for_uninstall()
        self.assertIn("消し", message)
        self.assertFalse((self.folder / "days").exists())
        self.assertTrue((self.folder / "unlinked.json").exists())  # スマホ版がサインアウトする
        self.assertFalse(Settings().get("cloud")["enabled"])        # 入れ直しても、勝手に書き出さない

    def test_keeps_records_of_other_pc(self):
        self.meta("another-pc", time.time())
        message = unlink_for_uninstall()
        self.assertIn("そのまま", message)
        self.assertTrue((self.folder / "days" / "2026-10-30.json").exists())
        self.assertFalse((self.folder / "unlinked.json").exists())

    def test_nothing_when_not_linked(self):
        s = Settings()
        s.update({"cloud": s.get("cloud") | {"enabled": False}})
        self.assertEqual(unlink_for_uninstall(), "")
        self.assertTrue((self.folder / "days").exists())


if __name__ == "__main__":
    unittest.main()

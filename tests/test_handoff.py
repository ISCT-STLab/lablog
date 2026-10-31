"""1.0.0b5 の引き継ぎレビュー：同期元の判定と時刻のない会話タイトル。"""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from lablog import backup, cloud, collectors, config, db
from lablog.config import Settings
from tests.test_presence import make_conn


class ClaudeRenameTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "project").mkdir()
        self.path = self.root / "project" / "session.jsonl"
        self.conn = make_conn()
        self.addCleanup(self.conn.close)
        self.now = time.time()
        patch = mock.patch.object(collectors, "first_since", return_value=self.now - 3600)
        patch.start()
        self.addCleanup(patch.stop)
        self.append({"type": "user", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.now - 10))},
                    {"type": "custom-title", "customTitle": "original"})
        self.scan(0)

    def append(self, *lines):
        with self.path.open("a", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps({"sessionId": "s", **line}) + "\n")

    def scan(self, seconds):
        collectors.import_claude_code(self.conn, self.now + seconds, self.root)

    def title(self):
        return self.conn.execute("SELECT title FROM ai_sessions WHERE session_id = 's'").fetchone()[0]

    def test_title_only_rename(self):
        self.append({"type": "custom-title", "customTitle": "renamed"})
        self.scan(60)
        self.assertEqual(self.title(), "renamed")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ai_activity").fetchone()[0], 1)

    def test_pause_then_resume(self):
        self.conn.execute("INSERT INTO pauses (start_ts, end_ts) VALUES (?, ?)", (self.now + 10, self.now + 20))
        self.append({"type": "custom-title", "customTitle": "private"})
        self.scan(60)
        self.assertEqual(self.title(), "original")
        self.append({"type": "custom-title", "customTitle": "public"})
        self.scan(120)
        self.assertEqual(self.title(), "public")

    def test_disable_then_resume(self):
        collectors.track_sources(self.conn, {"claude_code": False}, self.now + 10)
        self.append({"type": "custom-title", "customTitle": "private"})
        collectors.track_sources(self.conn, {"claude_code": True}, self.now + 20)
        self.scan(60)
        self.assertEqual(self.title(), "original")
        self.append({"type": "custom-title", "customTitle": "public"})
        self.scan(120)
        self.assertEqual(self.title(), "public")

    def test_new_file_without_timestamp_is_excluded(self):
        self.path = self.root / "project" / "new.jsonl"
        self.append({"type": "custom-title", "customTitle": "unknown"})
        self.scan(60)
        self.assertEqual(self.title(), "original")

    def test_replaced_file_without_timestamp_is_excluded(self):
        replacement = self.path.with_suffix(".tmp")
        replacement.write_text(json.dumps({"sessionId": "s", "type": "custom-title", "customTitle": "unknown"})
                               + "\n" + " " * 500, encoding="utf-8")
        replacement.replace(self.path)
        self.scan(60)
        self.assertEqual(self.title(), "original")

    def test_partial_title_keeps_original_observation_time(self):
        self.conn.execute("INSERT INTO pauses (start_ts, end_ts) VALUES (?, ?)", (self.now + 10, self.now + 20))
        with self.path.open("a", encoding="utf-8") as f:
            f.write('{"sessionId":"s","type":"custom-title","customTitle":"private')
        self.scan(30)
        self.scan(60)
        with self.path.open("a", encoding="utf-8") as f:
            f.write('"}\n')
        self.scan(90)
        self.assertEqual(self.title(), "original")

    def test_file_disappears_across_pause(self):
        parked = self.path.with_suffix(".parked")
        self.path.rename(parked)
        self.conn.execute("INSERT INTO pauses (start_ts, end_ts) VALUES (?, ?)", (self.now + 10, self.now + 20))
        self.scan(30)
        parked.rename(self.path)
        self.append({"type": "custom-title", "customTitle": "private"})
        self.scan(60)
        self.assertEqual(self.title(), "original")


class RestoreWriterTest(unittest.TestCase):
    def test_machine_identity_and_explicit_takeover(self):
        for original_machine in ("machine-a", "machine-b", None):
            with self.subTest(original_machine=original_machine), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = root / "lablog.db"
                conn = db.connect(path)
                db.init(conn)
                db.kv_set(conn, "device_id", "old-id")
                db.kv_set(conn, "device_id_before_restore", "stale-id")
                if original_machine:
                    db.kv_set(conn, "device_machine", original_machine)
                conn.close()
                with mock.patch.object(cloud, "machine_id", return_value="machine-b"), \
                     mock.patch.dict(os.environ, {"COMPUTERNAME": "SAME-NAME"}):
                    backup._forget_other_pc(path)
                    conn = db.connect(path)
                    try:
                        sync = cloud.CloudSync(Settings(root / "settings.json"), dict)
                        now = time.time()
                        (root / "meta.json").write_text(json.dumps({
                            "device": {"id": "old-id", "name": "SAME-NAME"}, "exported": now,
                        }), encoding="utf-8")
                        if original_machine == "machine-b":
                            self.assertEqual(sync._device(conn)["id"], "old-id")
                            self.assertIsNone(sync._other_writer(conn, root, now, False))
                        else:
                            self.assertNotEqual(sync._device(conn)["id"], "old-id")
                            self.assertIsNotNone(sync._other_writer(conn, root, now, False))
                            self.assertIsNone(sync._other_writer(conn, root, now, True))
                    finally:
                        conn.close()


class SettingsSaveTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "settings.json"
        self.settings = Settings(self.path)

    @staticmethod
    def sharing_error():
        error = PermissionError("synthetic Windows sharing violation")
        error.winerror = 32
        return error

    def test_temporary_lock_is_retried(self):
        replace = os.replace
        attempts = []

        def locked(src, dst):
            attempts.append(1)
            if len(attempts) < 3:
                raise self.sharing_error()
            return replace(src, dst)

        with mock.patch.object(config.os, "replace", side_effect=locked):
            self.settings.update({"setup_done": True})
        self.assertEqual(len(attempts), 3)
        self.assertTrue(Settings(self.path).get("setup_done"))

    def test_permanent_failure_keeps_memory_and_file_unchanged(self):
        before, contents = self.settings.snapshot(), self.path.read_bytes()
        with mock.patch.object(config.os, "replace", side_effect=self.sharing_error()) as replace:
            with self.assertRaises(PermissionError):
                self.settings.update({"setup_done": True, "port": 9999})
            self.assertLessEqual(replace.call_count, 5)
        self.assertEqual(self.settings.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), contents)

    def test_write_error_rolls_back_memory(self):
        before = self.settings.snapshot()
        with mock.patch.object(Path, "write_text", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.settings.update({"setup_done": True})
        self.assertEqual(self.settings.snapshot(), before)

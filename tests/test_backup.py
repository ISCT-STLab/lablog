"""記録のバックアップと、そこからの戻しのテスト。"""

import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from lablog import backup, db


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        patcher = mock.patch.dict(os.environ, {"LABLOG_DATA_DIR": str(self.data)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_data(self, stays: int, setting: str):
        conn = db.connect()
        db.init(conn)
        for i in range(stays):
            conn.execute("INSERT INTO stays (start_ts, end_ts, status) VALUES (?, ?, 'closed')",
                         (1_800_000_000 + i * 86400, 1_800_000_000 + i * 86400 + 3600))
        (self.data / "settings.json").write_text(json.dumps({"marker": setting}), encoding="utf-8")
        return conn

    def stays(self) -> int:
        conn = sqlite3.connect(self.data / "lablog.db")
        try:
            return conn.execute("SELECT COUNT(*) FROM stays").fetchone()[0]
        finally:
            conn.close()

    def test_backup_and_restore(self):
        conn = self.make_data(3, "前の PC")
        zip_bytes = backup.make_backup(conn)
        conn.close()
        names = zipfile.ZipFile(io.BytesIO(zip_bytes)).namelist()
        self.assertEqual(sorted(names), ["lablog.db", "settings.json"])
        # 新しく入れ直した（記録が空の）状態に、戻す
        for p in self.data.iterdir():
            p.unlink()
        self.make_data(0, "新しい PC").close()
        self.assertEqual(backup.queue_restore(zip_bytes)["stays"], 3)
        self.assertTrue(backup.apply_pending_restore())
        self.assertEqual(self.stays(), 3)
        self.assertEqual(json.loads((self.data / "settings.json").read_text(encoding="utf-8"))["marker"], "前の PC")
        [kept] = [p for p in self.data.iterdir() if p.name.startswith("before-restore-")]
        self.assertTrue((kept / "lablog.db").exists())  # 差し替える前の記録は残す
        self.assertFalse((self.data / backup.PENDING).exists())
        self.assertFalse(backup.apply_pending_restore())  # 1 回だけ

    def test_zip_from_uninstall_with_wal(self):
        """アンインストールのときに作る ZIP（DB の書き込みが -wal に残ったまま）も戻せる。"""
        path = self.data / "lablog.db"
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        db.init(conn)
        conn.execute("INSERT INTO stays (start_ts, end_ts, status) VALUES (1800000000, 1800003600, 'closed')")
        conn.commit()
        self.assertTrue((self.data / "lablog.db-wal").exists())
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.write(path, "lablog.db")
            z.write(self.data / "lablog.db-wal", "lablog.db-wal")
        conn.close()
        self.assertEqual(backup.check_backup(buf.getvalue())["stays"], 1)

    def test_rejects_other_files(self):
        with self.assertRaises(ValueError):
            backup.check_backup(b"not a zip")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("photo.jpg", b"x")
        with self.assertRaises(ValueError):
            backup.check_backup(buf.getvalue())
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("lablog.db", b"this is not sqlite")
        with self.assertRaises(ValueError):
            backup.check_backup(buf.getvalue())
        self.assertFalse((self.data / backup.PENDING).exists())  # 断ったものは置かない

    def test_rejects_backup_from_newer_version(self):
        conn = self.make_data(1, "x")
        conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
        zip_bytes = backup.make_backup(conn)
        conn.close()
        with self.assertRaises(ValueError):
            backup.check_backup(zip_bytes)

    def test_broken_pending_file_is_ignored(self):
        self.make_data(2, "x").close()
        (self.data / backup.PENDING).write_bytes(b"broken")
        self.assertFalse(backup.apply_pending_restore())
        self.assertEqual(self.stays(), 2)  # いまの記録のまま


if __name__ == "__main__":
    unittest.main()

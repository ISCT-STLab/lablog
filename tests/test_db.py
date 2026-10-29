"""DB の版と移行のテスト。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lablog import db


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)  # 接続を閉じてから消す（後に登録したものから先に動く）
        self.path = Path(self.tmp.name) / "lablog.db"

    def open(self):
        conn = db.connect(self.path)
        self.addCleanup(conn.close)
        return conn

    def test_new_db_is_version_1(self):
        conn = self.open()
        db.init(conn)
        self.assertEqual(db.schema_version(conn), 1)

    def test_db_before_versioning_becomes_version_1(self):
        conn = self.open()
        conn.executescript(db.SCHEMA)  # 版を持つ前の LabLog が作った DB
        conn.execute("INSERT INTO kv (key, value) VALUES ('paused', '0')")
        db.init(conn)
        self.assertEqual(db.schema_version(conn), 1)
        self.assertEqual(db.kv_get(conn, "paused"), "0")

    def test_migration_runs_once_with_backup(self):
        conn = self.open()
        db.init(conn)
        migrations = [(2, "ALTER TABLE stays ADD COLUMN note TEXT; CREATE TABLE extra (id INTEGER)")]
        with mock.patch.object(db, "MIGRATIONS", migrations), mock.patch.object(db, "SCHEMA_VERSION", 2):
            db.init(conn)
            db.init(conn)  # 2 回目は何もしない
        self.assertEqual(db.schema_version(conn), 2)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(stays)")]
        self.assertIn("note", cols)
        backups = list((self.path.parent / "backups").glob("lablog-*-v1.db"))
        self.assertEqual(len(backups), 1)
        old = sqlite3.connect(backups[0])
        try:
            self.assertEqual(old.execute("PRAGMA user_version").fetchone()[0], 1)
        finally:
            old.close()

    def test_failed_migration_keeps_old_version(self):
        conn = self.open()
        db.init(conn)
        migrations = [(2, "ALTER TABLE stays ADD COLUMN note TEXT; ALTER TABLE no_such_table ADD COLUMN x")]
        with mock.patch.object(db, "MIGRATIONS", migrations), mock.patch.object(db, "SCHEMA_VERSION", 2):
            with self.assertRaises(sqlite3.OperationalError):
                db.init(conn)
        self.assertEqual(db.schema_version(conn), 1)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(stays)")]
        self.assertNotIn("note", cols)  # 途中まで適用した分も戻る

    def test_newer_db_is_left_alone(self):
        conn = self.open()
        db.init(conn)
        conn.execute("PRAGMA user_version = 99")  # 新しい版で作られた DB（更新を戻したときなど）
        db.init(conn)
        self.assertEqual(db.schema_version(conn), 99)


if __name__ == "__main__":
    unittest.main()

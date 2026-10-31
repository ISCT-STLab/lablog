"""再インストール・同期遅延・部分的な同期失敗からの復旧。実 OneDrive は使わない。"""
import os
import unittest
from unittest import mock

from tests import test_cloud as fixture
from tests.test_cloud import D, H


class RelinkTest(unittest.TestCase):
    setUp = fixture.CloudTest.setUp
    tearDown = fixture.CloudTest.tearDown
    unlink = fixture.CloudTest.unlink

    def test_delayed_marker_is_found_on_next_check_without_button(self):
        self.settings.update({"cloud": {"enabled": True, "folder": "", "viewer_url": ""}})
        marker = self.out / "viewer.json"
        def discover():
            return self.out if marker.exists() else None
        with mock.patch.dict(os.environ, {"LABLOG_DATA_DIR": ""}), \
                mock.patch("lablog.cloud.find_viewer_folder", side_effect=discover):
            self.sync.run_once(self.conn, self.now)
            self.assertIsNone(self.sync.folder)
            marker.write_text('{"app":"LabLog viewer","signed_in":1}', encoding="utf-8")
            self.sync.run_once(self.conn, self.now + 30)
        self.assertEqual(self.sync.folder, self.out)
        self.assertTrue((self.out / "meta.json").exists())

    def test_new_signin_reexports_deleted_history(self):
        old_day = D.replace(day=10)
        self.conn.execute("INSERT INTO stays (start_ts,end_ts,status,end_reason) VALUES (?,?,'closed','offcampus')",
                          (self.now - 10 * 86400, self.now - 10 * 86400 + H))
        marker = self.out / "viewer.json"
        marker.write_text('{"app":"LabLog viewer","signed_in":1}', encoding="utf-8")
        self.sync.run_once(self.conn, self.now)
        old_file = self.out / "days" / f"{old_day.isoformat()}.json"
        self.assertTrue(old_file.exists())
        old_file.unlink()
        marker.write_text('{"app":"LabLog viewer","signed_in":2}', encoding="utf-8")
        self.sync.run_once(self.conn, self.now + 30)
        self.assertTrue(old_file.exists())

    def test_missing_metadata_reexports_history_without_new_marker(self):
        self.sync.run_once(self.conn, self.now)
        day_file = self.out / "days" / f"{D.isoformat()}.json"
        day_file.unlink()
        (self.out / "meta.json").unlink()
        self.sync.run_once(self.conn, self.now + 30)
        self.assertTrue(day_file.exists())
        self.assertTrue((self.out / "meta.json").exists())

    def test_export_failure_retries_all_days_next_time(self):
        original = self.sync._write
        failed = False
        def fail_once(path, obj, ignore=()):
            nonlocal failed
            if path.name == "meta.json" and not failed:
                failed = True
                raise PermissionError("OneDrive is replacing a file")
            return original(path, obj, ignore)
        with mock.patch.object(self.sync, "_write", side_effect=fail_once):
            with self.assertRaises(PermissionError):
                self.sync.run_once(self.conn, self.now)
            self.sync.run_once(self.conn, self.now + 30)
        self.assertEqual(self.sync.last_export, self.now + 30)
        self.assertTrue((self.out / "meta.json").exists())

    def test_explicit_sync_restores_a_deleted_file_despite_cached_hash(self):
        self.sync.run_once(self.conn, self.now)
        day_file = self.out / "days" / f"{D.isoformat()}.json"
        day_file.unlink()
        self.sync.request_export()
        self.sync.run_once(self.conn, self.now + 30)
        self.assertTrue(day_file.exists())

    def test_new_phone_marker_does_not_override_disabled_pc(self):
        self.sync.run_once(self.conn, self.now)
        self.unlink()
        (self.out / "viewer.json").write_text('{"app":"LabLog viewer","signed_in":2}', encoding="utf-8")
        self.sync.run_once(self.conn, self.now + 60)
        self.assertFalse((self.out / "meta.json").exists())
        self.assertTrue((self.out / "unlinked.json").exists())

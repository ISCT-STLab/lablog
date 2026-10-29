"""OneDrive への書き出しと、iPhone・iPad からの修正の取り込みのテスト。"""

import json
import os
import tempfile
import time
import unittest
from datetime import date, datetime
from pathlib import Path

from lablog.cloud import CloudSync, apply_edit, cloud_day, find_viewer_folder
from lablog.config import Settings
from lablog.days import day_range
from lablog.summary import day_summary

from tests.test_presence import make_conn

D = date(2026, 10, 20)
T0 = day_range(D)[0]  # 5:00
H = 3600


def at(hour, minute=0):
    return datetime(D.year, D.month, D.day, hour, minute).timestamp()


class CloudTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.out = root / "onedrive"
        self.out.mkdir()
        self.settings = Settings(root / "settings.json")
        self.settings.update({"setup_done": True,
                              "cloud": {"enabled": True, "folder": str(self.out), "viewer_url": ""}})
        self.conn = make_conn()
        self.conn.execute("INSERT INTO stays (start_ts, end_ts, status, end_reason) VALUES (?, ?, 'closed', 'offcampus')",
                          (at(9), at(18)))
        self.conn.execute("INSERT INTO segments (start_ts, end_ts, exe, title, idle, location) "
                          "VALUES (?, ?, 'code.exe', '秘密のファイル名.py - Visual Studio Code', 0, '大学')",
                          (at(10), at(11)))
        self.conn.execute("INSERT INTO web_visits (browser, profile, visit_id, ts, url, title, domain) "
                          "VALUES ('chrome', 'Default', 1, ?, 'https://example.com/x', '見られたくないページ', "
                          "'example.com')", (at(12),))
        self.sync = CloudSync(self.settings, lambda: {"state": "out", "since": None, "location": "大学",
                                                      "paused": False, "ssids": ["Lab-WiFi"]})
        self.now = at(20)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cloud_day_has_only_summary(self):
        s = cloud_day(day_summary(self.conn, self.settings, D, self.now))
        text = json.dumps(s, ensure_ascii=False)
        self.assertNotIn("秘密のファイル名", text)
        self.assertNotIn("見られたくないページ", text)
        self.assertNotIn("example.com", text)
        self.assertEqual(set(s["timeline"][0]), {"cat", "start", "end"})
        self.assertEqual(s["work_total"], H)
        self.assertEqual(s["stay_total"], 9 * H)

    def test_nothing_is_exported_before_setup(self):
        self.settings.update({"setup_done": False})
        self.sync.run_once(self.conn, self.now)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_export_writes_files_and_skips_unchanged(self):
        self.sync.run_once(self.conn, self.now)
        day_file = self.out / "days" / f"{D.isoformat()}.json"
        meta = json.loads((self.out / "meta.json").read_text(encoding="utf-8"))
        self.assertTrue(day_file.exists())
        self.assertTrue((self.out / "months" / "2026-10.json").exists())
        self.assertNotIn("ssids", meta["status"])  # Wi-Fi 名は出さない
        before = day_file.stat().st_mtime_ns
        time.sleep(0.02)
        self.sync.request_export()
        self.sync.run_once(self.conn, self.now + 600)  # 時刻だけ違う → 書き直さない
        self.assertEqual(day_file.stat().st_mtime_ns, before)

    def test_edits_from_viewer(self):
        self.sync.run_once(self.conn, self.now)
        edits = self.out / "edits"
        edits.mkdir()
        stay_id = self.conn.execute("SELECT id FROM stays").fetchone()["id"]
        (edits / "1-a.json").write_text(json.dumps({"type": "report", "date": D.isoformat(), "text": "iPhone で書いた",
                                                    "ts": self.now + 1}), encoding="utf-8")
        (edits / "2-b.json").write_text(json.dumps({"type": "stay_save", "id": stay_id, "start": at(8, 30),
                                                    "end": at(18), "date": D.isoformat(), "ts": self.now + 2}),
                                        encoding="utf-8")
        (edits / "3-c.json").write_text(json.dumps({"type": "stay_delete", "id": True, "ts": self.now + 3}),
                                        encoding="utf-8")
        self.sync.run_once(self.conn, self.now + 60)

        report = self.conn.execute("SELECT * FROM reports").fetchone()
        self.assertEqual((report["text"], report["edited"]), ("iPhone で書いた", 1))
        self.assertEqual(self.conn.execute("SELECT start_ts FROM stays").fetchone()["start_ts"], at(8, 30))
        self.assertEqual(sorted(p.name for p in edits.iterdir()), ["3-c.rejected"])  # id が true は受け付けない
        day = json.loads((self.out / "days" / f"{D.isoformat()}.json").read_text(encoding="utf-8"))
        self.assertEqual(day["report"]["text"], "iPhone で書いた")
        self.assertEqual(day["stays"][0]["start"], at(8, 30))

    def test_apply_edit_validation(self):
        with self.assertRaises(ValueError):
            apply_edit(self.conn, {"type": "stay_save", "id": None, "start": at(10), "end": at(9)})
        with self.assertRaises(ValueError):
            apply_edit(self.conn, {"type": "stay_save", "id": None, "start": "10:00", "end": at(11)})
        with self.assertRaises(ValueError):
            apply_edit(self.conn, {"type": "stay_save", "id": 999, "start": at(10), "end": at(11)})
        with self.assertRaises(ValueError):
            apply_edit(self.conn, {"type": "settings", "port": 80})
        self.assertEqual(apply_edit(self.conn, {"type": "stay_delete", "id": 999}), set())
        self.assertEqual(apply_edit(self.conn, {"type": "stay_save", "id": None, "start": at(13), "end": at(15)}), {D})

    def test_unreadable_edit_waits_for_sync(self):
        self.sync.run_once(self.conn, self.now)
        edits = self.out / "edits"
        edits.mkdir()
        partial = edits / "9-z.json"
        partial.write_text('{"type": "rep', encoding="utf-8")
        self.sync.import_edits(self.conn, self.out, time.time())
        self.assertTrue(partial.exists())  # 同期の途中かもしれないので、まだ消さない
        old = time.time() - 3600
        os.utime(partial, (old, old))
        self.sync.import_edits(self.conn, self.out, time.time())
        self.assertFalse(partial.exists())
        self.assertTrue((edits / "9-z.rejected").exists())

    def test_find_viewer_folder_by_marker(self):
        root = Path(self.tmp.name) / "OneDrive"
        (root / "アプリ" / "Clipchamp").mkdir(parents=True)
        (root / "アプリ" / "LabLog").mkdir()
        (root / "アプリ" / "LabLog" / "viewer.json").write_text('{"app": "other"}', encoding="utf-8")
        self.assertIsNone(find_viewer_folder(root))
        (root / "アプリ" / "Graph").mkdir()  # OneDrive がこの名前で作ることがある
        (root / "アプリ" / "Graph" / "viewer.json").write_text('{"app": "LabLog viewer"}', encoding="utf-8")
        self.assertEqual(find_viewer_folder(root), root / "アプリ" / "Graph")

    def test_disabled_writes_nothing(self):
        self.settings.update({"cloud": {"enabled": False, "folder": str(self.out), "viewer_url": ""}})
        self.sync.run_once(self.conn, self.now)
        self.assertEqual(list(self.out.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

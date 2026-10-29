"""入退室判定のテスト。python -m unittest discover tests で実行する。"""

import sqlite3
import unittest

from lablog import db
from lablog.activity import ActivityRecorder
from lablog.presence import Presence
from lablog.wifi import HOME, IGNORED, OFFLINE, OTHER, UNIV, classify

H = 3600
M = 60
T0 = 1_800_000_000.0  # 適当な基準時刻


def make_conn():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    db.init(conn)
    return conn


class PresenceTest(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()
        self.p = Presence(self.conn, leave_after_sec=4 * H, idle_threshold_sec=5 * M, gap_sec=3 * M)

    def run_ticks(self, start, end, net, idle=0.0, step=15):
        t = start
        while t <= end:
            self.p.tick(t, net, idle)
            t += step

    def stays(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM stays ORDER BY start_ts")]

    def test_arrive_and_leave_home(self):
        self.run_ticks(T0, T0 + 2 * H, UNIV)
        self.run_ticks(T0 + 2 * H + 15, T0 + 2 * H + 10 * M, OFFLINE)  # 移動中
        self.p.tick(T0 + 3 * H, HOME, 0)                                 # 自宅の Wi-Fi
        [s] = self.stays()
        self.assertEqual(s["status"], "closed")
        self.assertEqual(s["start_ts"], T0)
        self.assertEqual(s["end_ts"], T0 + 2 * H)  # 退出時刻は Wi-Fi が切れた時刻
        self.assertEqual(s["end_reason"], "offcampus")

    def test_short_disconnect_continues_stay(self):
        self.run_ticks(T0, T0 + H, UNIV)
        self.run_ticks(T0 + H + 15, T0 + 2 * H, OFFLINE)  # ゼミで PC を閉じた
        self.assertEqual(self.p.status_view(T0 + 2 * H)["state"], "tentative")
        self.run_ticks(T0 + 2 * H + 15, T0 + 3 * H, UNIV)
        [s] = self.stays()
        self.assertEqual(s["status"], "in")
        self.assertIsNone(s["tentative_since"])

    def test_sleep_gap_then_return_within_limit(self):
        self.run_ticks(T0, T0 + H, UNIV)
        # スリープ中はトラッカーが止まる。3 時間後に大学で再開。
        self.p.tick(T0 + 4 * H, UNIV, 0)
        [s] = self.stays()
        self.assertEqual(s["status"], "in")
        self.assertEqual(s["start_ts"], T0)

    def test_sleep_overnight_next_day_at_lab(self):
        self.run_ticks(T0, T0 + H, UNIV)
        # 家で PC を開かず、翌朝そのまま研究室へ（15 時間後）
        self.p.tick(T0 + 16 * H, UNIV, 0)
        first, second = self.stays()
        self.assertEqual(first["status"], "closed")
        self.assertEqual(first["end_ts"], T0 + H)
        self.assertEqual(first["end_reason"], "timeout")
        self.assertEqual(second["status"], "in")
        self.assertEqual(second["start_ts"], T0 + 16 * H)

    def test_tentative_expires_while_offline(self):
        self.run_ticks(T0, T0 + H, UNIV)
        self.run_ticks(T0 + H + 15, T0 + 5 * H + 30, OFFLINE)
        [s] = self.stays()
        self.assertEqual(s["status"], "closed")
        self.assertEqual(s["end_ts"], T0 + H)

    def test_overnight_work_is_one_stay(self):
        # 夜通し作業しても、操作していれば 1 つの滞在のまま
        self.run_ticks(T0, T0 + 14 * H, UNIV, idle=30, step=60)
        [s] = self.stays()
        self.assertEqual(s["status"], "in")

    def test_left_pc_open_and_went_home(self):
        self.run_ticks(T0, T0 + H, UNIV)
        last_input = T0 + H
        t = last_input + 15
        while t <= last_input + 4 * H + 60:
            self.p.tick(t, UNIV, t - last_input)
            t += 15
        [s] = self.stays()
        self.assertEqual(s["status"], "closed")
        self.assertAlmostEqual(s["end_ts"], last_input, delta=1)
        self.assertEqual(s["end_reason"], "idle")
        # 操作がないあいだは新しい滞在を作らない
        self.p.tick(t + 15, UNIV, t + 15 - last_input)
        self.assertEqual(len(self.stays()), 1)

    def test_direct_switch_to_other_wifi(self):
        self.run_ticks(T0, T0 + H, UNIV)
        self.p.tick(T0 + H + 15, OTHER, 0)  # カフェの Wi-Fi に直接切り替わった
        [s] = self.stays()
        self.assertEqual(s["status"], "closed")
        self.assertEqual(s["end_ts"], T0 + H)

    def test_no_stay_when_idle_on_campus_wifi(self):
        self.p.tick(T0, UNIV, 10 * M)
        self.assertEqual(self.stays(), [])

    def test_home_only_creates_nothing(self):
        self.run_ticks(T0, T0 + H, HOME)
        self.assertEqual(self.stays(), [])

    def test_tethering_keeps_stay(self):
        # 研究室の Wi-Fi が不調でテザリングに切り替えても、滞在は続く
        self.run_ticks(T0, T0 + H, UNIV)
        self.run_ticks(T0 + H + 15, T0 + 3 * H, IGNORED)
        self.assertEqual(self.p.status_view(T0 + 3 * H)["state"], "in")
        self.p.tick(T0 + 4 * H, HOME, 0)  # テザリングのまま帰宅して自宅の Wi-Fi に
        [s] = self.stays()
        self.assertEqual((s["status"], s["end_reason"]), ("closed", "offcampus"))
        self.assertEqual(s["end_ts"], T0 + 3 * H)

    def test_tethering_while_tentative_stays_tentative(self):
        self.run_ticks(T0, T0 + H, UNIV)
        self.run_ticks(T0 + H + 15, T0 + H + 30 * M, OFFLINE)
        self.run_ticks(T0 + H + 30 * M + 15, T0 + 2 * H, IGNORED)
        [s] = self.stays()
        self.assertEqual((s["status"], s["tentative_since"]), ("tentative", T0 + H))
        self.run_ticks(T0 + 5 * H + 15, T0 + 5 * H + 60, IGNORED)  # 仮退出から 4 時間
        [s] = self.stays()
        self.assertEqual((s["status"], s["end_reason"], s["end_ts"]), ("closed", "timeout", T0 + H))

    def test_tethering_alone_creates_nothing(self):
        self.run_ticks(T0, T0 + H, IGNORED)
        self.assertEqual(self.stays(), [])

    def test_classify(self):
        cfg = {"univ": ["ScienceTokyo"], "home": ["Home-WiFi"], "ignore": ["My iPhone"]}
        self.assertEqual(classify(["ScienceTokyo"], cfg), UNIV)
        self.assertEqual(classify(["My iPhone"], cfg), IGNORED)
        self.assertEqual(classify(["eduroam"], cfg), OTHER)
        self.assertEqual(classify([], cfg), OFFLINE)
        self.assertEqual(classify(["My iPhone"], {"univ": [], "home": []}), OTHER)  # 古い設定でも動く


class ActivityTest(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()
        self.rec = ActivityRecorder(self.conn, interval_sec=5)

    def segs(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM segments ORDER BY start_ts")]

    def tick(self, t, fg, idle=0.0, paused=False):
        self.rec.tick(t, fg, idle, 300, "大学", {"line.exe"}, paused)

    def test_merges_same_window_and_splits_on_switch(self):
        for i in range(10):
            self.tick(T0 + i * 5, ("Code.exe", "a.py - VS Code"))
        self.tick(T0 + 50, ("chrome.exe", "arXiv"))
        a, b = self.segs()
        self.assertEqual((a["start_ts"], a["end_ts"]), (T0, T0 + 45))
        self.assertEqual(b["start_ts"], T0 + 45)

    def test_idle_trims_back_to_last_input(self):
        for i in range(0, 400, 5):
            idle = max(0, i - 60)  # 60 秒後から操作が止まる
            self.tick(T0 + i, ("Code.exe", "a.py"), idle)
        work, away = self.segs()
        self.assertAlmostEqual(work["end_ts"], T0 + 60, delta=5)
        self.assertEqual(away["idle"], 1)
        self.assertAlmostEqual(away["start_ts"], T0 + 60, delta=5)

    def test_excluded_app_hides_title(self):
        self.tick(T0, ("LINE.exe", "秘密のトーク"))
        [s] = self.segs()
        self.assertEqual(s["exe"], "(除外)")
        self.assertEqual(s["title"], "")

    def test_paused_records_nothing(self):
        self.tick(T0, ("Code.exe", "a.py"), paused=True)
        self.assertEqual(self.segs(), [])


if __name__ == "__main__":
    unittest.main()

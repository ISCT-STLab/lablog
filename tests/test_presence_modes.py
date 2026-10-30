"""PC の使い方ごとの入退室の判定と、手動の入室・退室のテスト。"""

import json
import tempfile
import unittest
from pathlib import Path

from datetime import datetime

from lablog.config import Settings
from lablog.days import recent_clock_time
from lablog.presence import Presence, judge_net, work_location
from lablog.wifi import HOME, IGNORED, OFFLINE, OTHER, UNIV

from tests.test_presence import make_conn

T0 = 1_800_000_000.0
MIN = 60
H = 3600


class JudgeTest(unittest.TestCase):
    def test_judge_net(self):
        self.assertEqual(judge_net("wifi", HOME, True, False), HOME)
        self.assertEqual(judge_net("wifi", UNIV, True, True), OFFLINE)  # リモートデスクトップ中は研究室にいない
        self.assertEqual(judge_net("activity", OFFLINE, True, False), UNIV)  # 置いたまま：手元で操作中なら研究室
        self.assertEqual(judge_net("activity", OFFLINE, False, False), OFFLINE)
        self.assertEqual(judge_net("activity", OFFLINE, True, True), OFFLINE)
        self.assertEqual(judge_net("off", UNIV, True, False), OTHER)

    def test_work_location(self):
        self.assertEqual(work_location("wifi", UNIV, "in", False), "大学")
        self.assertEqual(work_location("wifi", IGNORED, "in", False), "大学")
        self.assertEqual(work_location("wifi", IGNORED, "out", False), "その他")
        self.assertEqual(work_location("activity", OFFLINE, "out", False), "大学")
        self.assertEqual(work_location("activity", OFFLINE, "out", True), "自宅")
        self.assertEqual(work_location("off", OFFLINE, "out", False), "自宅")
        self.assertEqual(work_location("off", OFFLINE, "out", False, "other"), "その他")
        self.assertEqual(work_location("manual", OFFLINE, "in", False), "大学")
        self.assertEqual(work_location("manual", OFFLINE, "out", False), "その他")

    def test_manual_judge_net(self):
        self.assertEqual(judge_net("manual", UNIV, True, False), IGNORED)  # Wi-Fi も操作も入退室に使わない
        self.assertEqual(judge_net("manual", UNIV, True, True), OFFLINE)


class OldSettingsTest(unittest.TestCase):
    def test_old_mode_names_are_renamed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            for old, new in (("carry", "wifi"), ("stay", "activity"), ("home", "off")):
                path.write_text(json.dumps({"presence": {"mode": old, "away_min": 15}}), encoding="utf-8")
                self.assertEqual(Settings(path).get("presence")["mode"], new)
            s = Settings(path)
            s.update({"presence": {"mode": "stay"}})  # 前の版の画面から保存しても受け付ける
            self.assertEqual(s.get("presence")["mode"], "activity")


class ModeTest(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()
        self.p = Presence(self.conn, leave_after_sec=4 * H, idle_threshold_sec=5 * MIN)

    def stays(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM stays ORDER BY id")]

    def tick(self, t, mode, wifi=OFFLINE, active=True, remote=False, idle=0):
        return self.p.tick(T0 + t, judge_net(mode, wifi, active, remote), idle, by_wifi=mode == "wifi" and not remote)

    def test_stay_mode_follows_local_activity(self):
        self.tick(0, "activity")
        self.assertEqual(self.stays()[0]["status"], "in")
        for t in range(15, 2 * H, 15):  # 2 時間、手元で操作
            self.tick(t, "activity")
        self.tick(2 * H, "activity", active=False)  # 離れた
        stay = self.stays()[0]
        self.assertEqual(stay["status"], "tentative")
        self.assertEqual(stay["tentative_since"], T0 + 2 * H - 15)
        self.tick(3 * H, "activity")  # 1 時間で戻った → 滞在が続く
        self.assertEqual(self.stays()[0]["status"], "in")
        self.tick(3 * H + 15, "activity", active=False)
        self.tick(8 * H, "activity", active=False)  # 4 時間以上戻らない → 最後に操作した時刻で退室
        stay = self.stays()[0]
        self.assertEqual(stay["status"], "closed")
        self.assertEqual(stay["end_ts"], T0 + 3 * H)
        self.assertEqual(len(self.stays()), 1)

    def test_remote_desktop_is_not_in_lab(self):
        self.tick(0, "wifi", UNIV)
        self.tick(5 * MIN, "wifi", UNIV)
        self.tick(H, "wifi", UNIV, remote=True)  # 研究室のノート PC に自宅から入った
        self.assertEqual(self.stays()[0]["status"], "tentative")
        self.tick(6 * H, "wifi", UNIV, remote=True)
        self.assertEqual(self.stays()[0]["status"], "closed")
        self.assertEqual(len(self.stays()), 1)  # リモートのあいだは新しく入室しない

    def test_off_mode_never_opens(self):
        self.tick(0, "off", UNIV)
        self.assertEqual(self.stays(), [])

    def test_manual_mode_only_buttons(self):
        for t in range(0, H, 15):  # 研究室で操作していても、自動では入室にしない
            self.tick(t, "manual", UNIV)
        self.assertEqual(self.stays(), [])
        self.p.manual_enter(T0 + H)
        for t in range(H + 15, 2 * H, 15):
            self.tick(t, "manual", OTHER)
        self.assertEqual(self.stays()[0]["status"], "in")
        # 退室を押し忘れ、PC をつけたまま帰った：4 時間操作がなければ、最後に操作した時刻で退室
        for t in range(2 * H, 6 * H + 30, 15):
            self.tick(t, "manual", OTHER, idle=t - 2 * H)
        stay = self.stays()[0]
        self.assertEqual(stay["status"], "closed")
        self.assertEqual(stay["end_ts"], T0 + 2 * H)

    def test_manual_mode_pc_stopped(self):
        self.p.manual_enter(T0)
        for t in range(15, 10 * MIN + 1, 15):
            self.tick(t, "manual", OTHER)
        self.tick(6 * H, "manual", OTHER)  # 退室を押し忘れ、PC を閉じて帰った → 最後に動いていた時刻で退室
        stay = self.stays()[0]
        self.assertEqual((stay["status"], stay["end_ts"]), ("closed", T0 + 10 * MIN))

    def test_short_stays_are_not_kept(self):
        self.p.manual_enter(T0)
        self.p.manual_leave(T0 + 30)  # 入室をすぐ取り消した
        self.assertEqual(self.stays(), [])
        self.tick(H, "wifi", UNIV)
        self.tick(H + 15, "wifi", UNIV)
        self.tick(H + 30, "wifi", HOME)  # 研究室の Wi-Fi に一瞬つながっただけ
        self.assertEqual(self.stays(), [])
        self.tick(2 * H, "wifi", UNIV)
        self.tick(2 * H + 2 * MIN, "wifi", UNIV)
        self.tick(2 * H + 3 * MIN, "wifi", HOME)  # 2 分いた → 残す
        self.assertEqual(len(self.stays()), 1)

    def test_manual_enter_stays_when_wifi_is_unreadable(self):
        self.p.manual_enter(T0)
        for t in range(15, H, 15):  # Wi-Fi で判定しているが、有線・位置情報がオフで Wi-Fi 名を読めない
            self.tick(t, "wifi", OFFLINE)
        self.assertEqual(self.stays()[0]["status"], "in")
        self.tick(H, "wifi", OFFLINE, remote=True)  # 外からリモートデスクトップで入った → 研究室にはいない
        self.assertEqual(self.stays()[0]["status"], "tentative")

    def test_manual_mode_resumes_after_sleep(self):
        self.p.manual_enter(T0)
        self.tick(15, "manual", OTHER)
        self.tick(H, "manual", OTHER, idle=H)  # 昼休みに PC を閉じた → 仮退室
        self.assertEqual(self.stays()[0]["status"], "tentative")
        self.tick(H + 15, "manual", OTHER)       # 戻って操作した → 在室に戻る（入室を押し直さなくてよい）
        self.assertEqual(self.stays()[0]["status"], "in")
        for t in range(H + 30, 6 * H, 15):       # 午後も作業 → 昼休みの時刻で退室にしない
            self.tick(t, "manual", OTHER)
        self.assertEqual(self.stays()[0]["status"], "in")

    def test_manual_leave_waits_until_leaving_lab(self):
        self.tick(0, "wifi", UNIV)
        self.p.manual_leave(T0 + H)
        stay = self.stays()[0]
        self.assertEqual((stay["status"], stay["end_ts"], stay["end_reason"]), ("closed", T0 + H, "manual_out"))
        self.tick(H + 15, "wifi", UNIV)  # まだ研究室の Wi-Fi のまま → 入室にしない
        self.assertEqual(len(self.stays()), 1)
        self.tick(H + 30, "wifi", OFFLINE)  # Wi-Fi が切れた（研究室を離れた）
        self.tick(2 * H, "wifi", UNIV)  # 戻ってきた → 入室
        self.assertEqual(len(self.stays()), 2)

    def test_manual_leave_in_stay_mode_waits_until_away(self):
        self.tick(0, "activity")
        self.p.manual_leave(T0 + H)
        self.tick(H + 15, "activity")  # 押したあとも操作している
        self.assertEqual(len(self.stays()), 1)
        self.tick(H + 20 * MIN, "activity", active=False)
        self.tick(14 * H, "activity")  # 次の朝
        self.assertEqual(len(self.stays()), 2)

    def test_manual_enter_survives_unregistered_wifi(self):
        self.p.manual_enter(T0)
        for t in range(15, H + 1, 15):  # eduroam など未登録の Wi-Fi のまま 1 時間
            self.tick(t, "wifi", OTHER)
        self.assertEqual(self.stays()[0]["status"], "in")
        self.p.manual_leave(T0 + 2 * H)
        self.assertEqual(self.stays()[0]["end_ts"], T0 + 2 * H)
        self.tick(2 * H + 15, "wifi", OTHER)
        self.assertEqual(len(self.stays()), 1)

    def test_manual_enter_resumes_tentative_stay(self):
        self.tick(0, "wifi", UNIV)
        self.tick(H, "wifi", OFFLINE)
        self.assertEqual(self.stays()[0]["status"], "tentative")
        self.p.manual_enter(T0 + 2 * H)
        self.assertEqual([s["status"] for s in self.stays()], ["in"])

    def test_manual_enter_after_expired_tentative_opens_new_stay(self):
        self.tick(0, "wifi", UNIV)
        self.tick(5 * MIN, "wifi", UNIV)
        self.tick(H, "wifi", OFFLINE)
        self.p.manual_enter(T0 + 6 * H)
        self.assertEqual([s["status"] for s in self.stays()], ["closed", "in"])
        self.assertEqual(self.stays()[0]["end_ts"], T0 + 5 * MIN)  # 仮退室した時刻で閉じる

    def test_manual_leave_tentative_uses_time_of_leaving(self):
        self.tick(0, "wifi", UNIV)
        self.tick(5 * MIN, "wifi", UNIV)
        self.tick(H, "wifi", OFFLINE)
        self.p.manual_leave(T0 + 2 * H)
        self.assertEqual(self.stays()[0]["end_ts"], T0 + 5 * MIN)


class ManualTimeTest(unittest.TestCase):
    """押し忘れたときに、時刻を指定して入室・退室する。"""

    def setUp(self):
        self.conn = make_conn()
        self.p = Presence(self.conn, leave_after_sec=4 * H, idle_threshold_sec=5 * MIN)
        self.now = T0 + 10 * H

    def stays(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM stays ORDER BY id")]

    def test_enter_at_earlier_time(self):
        self.p.manual_enter(self.now, at=T0 + 9 * H)
        stay = self.stays()[0]
        self.assertEqual((stay["status"], stay["start_ts"], stay["last_seen_ts"]), ("in", T0 + 9 * H, self.now))

    def test_enter_at_time_fixes_start_of_open_stay(self):
        self.p.manual_enter(self.now)  # 遅れて押した
        self.p.manual_enter(self.now + 60, at=T0 + 9 * H)  # ほんとうは 9 時に来ていた
        self.assertEqual([(s["start_ts"], s["manual"]) for s in self.stays()], [(T0 + 9 * H, 1)])

    def test_enter_cannot_overlap_previous_stay(self):
        self.conn.execute("INSERT INTO stays (start_ts, end_ts, status) VALUES (?, ?, 'closed')", (T0, T0 + 9 * H))
        with self.assertRaises(ValueError):
            self.p.manual_enter(self.now, at=T0 + 8 * H)
        self.assertEqual(len(self.stays()), 1)  # 何も変えない

    def test_future_time_is_rejected(self):
        with self.assertRaises(ValueError):
            self.p.manual_enter(self.now, at=self.now + 60)
        self.p.manual_enter(self.now)
        with self.assertRaises(ValueError):
            self.p.manual_leave(self.now, at=self.now + 60)

    def test_leave_at_time(self):
        self.p.manual_enter(T0 + 9 * H)
        self.p.manual_leave(self.now, at=T0 + 9 * H + 30 * MIN)
        stay = self.stays()[0]
        self.assertEqual((stay["status"], stay["end_ts"], stay["end_reason"]), ("closed", T0 + 9 * H + 30 * MIN, "manual_out"))

    def test_leave_before_enter_is_rejected(self):
        self.p.manual_enter(T0 + 9 * H)
        with self.assertRaises(ValueError):
            self.p.manual_leave(self.now, at=T0 + 8 * H)
        self.assertEqual(self.stays()[0]["status"], "in")

    def test_leave_at_time_needs_open_stay(self):
        with self.assertRaises(ValueError):
            self.p.manual_leave(self.now, at=T0 + 9 * H)


class ClockTimeTest(unittest.TestCase):
    def test_recent_clock_time(self):
        now = datetime(2026, 10, 20, 10, 0).timestamp()
        self.assertEqual(recent_clock_time(now, "9:30"), datetime(2026, 10, 20, 9, 30).timestamp())
        self.assertEqual(recent_clock_time(now, "10:00"), now)
        # いまより後の時刻は前日（日付をまたいだあとで、前の晩の退室を入れるとき）
        self.assertEqual(recent_clock_time(now, "23:15"), datetime(2026, 10, 19, 23, 15).timestamp())
        for bad in ("25:00", "9時", ""):
            with self.assertRaises(ValueError):
                recent_clock_time(now, bad)


if __name__ == "__main__":
    unittest.main()

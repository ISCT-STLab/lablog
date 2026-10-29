"""PC の使い方ごとの入退室の判定と、手動の入室・退室のテスト。"""

import unittest

from lablog.presence import Presence, judge_net, work_location
from lablog.wifi import HOME, IGNORED, OFFLINE, OTHER, UNIV

from tests.test_presence import make_conn

T0 = 1_800_000_000.0
MIN = 60
H = 3600


class JudgeTest(unittest.TestCase):
    def test_judge_net(self):
        self.assertEqual(judge_net("carry", HOME, True, False), HOME)
        self.assertEqual(judge_net("carry", UNIV, True, True), OFFLINE)  # リモートデスクトップ中は研究室にいない
        self.assertEqual(judge_net("stay", OFFLINE, True, False), UNIV)  # 置いたまま：手元で操作中なら研究室
        self.assertEqual(judge_net("stay", OFFLINE, False, False), OFFLINE)
        self.assertEqual(judge_net("stay", OFFLINE, True, True), OFFLINE)
        self.assertEqual(judge_net("home", UNIV, True, False), OTHER)

    def test_work_location(self):
        self.assertEqual(work_location("carry", UNIV, "in", False), "大学")
        self.assertEqual(work_location("carry", IGNORED, "in", False), "大学")
        self.assertEqual(work_location("carry", IGNORED, "out", False), "その他")
        self.assertEqual(work_location("stay", OFFLINE, "out", False), "大学")
        self.assertEqual(work_location("stay", OFFLINE, "out", True), "自宅")
        self.assertEqual(work_location("home", OFFLINE, "out", False), "自宅")


class ModeTest(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()
        self.p = Presence(self.conn, leave_after_sec=4 * H, idle_threshold_sec=5 * MIN)

    def stays(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM stays ORDER BY id")]

    def tick(self, t, mode, wifi=OFFLINE, active=True, remote=False, idle=0):
        return self.p.tick(T0 + t, judge_net(mode, wifi, active, remote), idle)

    def test_stay_mode_follows_local_activity(self):
        self.tick(0, "stay")
        self.assertEqual(self.stays()[0]["status"], "in")
        for t in range(15, 2 * H, 15):  # 2 時間、手元で操作
            self.tick(t, "stay")
        self.tick(2 * H, "stay", active=False)  # 離れた
        stay = self.stays()[0]
        self.assertEqual(stay["status"], "tentative")
        self.assertEqual(stay["tentative_since"], T0 + 2 * H - 15)
        self.tick(3 * H, "stay")  # 1 時間で戻った → 滞在が続く
        self.assertEqual(self.stays()[0]["status"], "in")
        self.tick(3 * H + 15, "stay", active=False)
        self.tick(8 * H, "stay", active=False)  # 4 時間以上戻らない → 最後に操作した時刻で退出
        stay = self.stays()[0]
        self.assertEqual(stay["status"], "closed")
        self.assertEqual(stay["end_ts"], T0 + 3 * H)
        self.assertEqual(len(self.stays()), 1)

    def test_remote_desktop_is_not_in_lab(self):
        self.tick(0, "carry", UNIV)
        self.tick(H, "carry", UNIV, remote=True)  # 研究室のノート PC に自宅から入った
        self.assertEqual(self.stays()[0]["status"], "tentative")
        self.tick(6 * H, "carry", UNIV, remote=True)
        self.assertEqual(self.stays()[0]["status"], "closed")
        self.assertEqual(len(self.stays()), 1)  # リモートのあいだは新しく入室しない

    def test_home_mode_never_opens(self):
        self.tick(0, "home", UNIV)
        self.assertEqual(self.stays(), [])

    def test_manual_leave_waits_until_leaving_lab(self):
        self.tick(0, "carry", UNIV)
        self.p.manual_leave(T0 + H)
        stay = self.stays()[0]
        self.assertEqual((stay["status"], stay["end_ts"], stay["end_reason"]), ("closed", T0 + H, "manual_out"))
        self.tick(H + 15, "carry", UNIV)  # まだ研究室の Wi-Fi のまま → 入室にしない
        self.assertEqual(len(self.stays()), 1)
        self.tick(H + 30, "carry", OFFLINE)  # Wi-Fi が切れた（研究室を離れた）
        self.tick(2 * H, "carry", UNIV)  # 戻ってきた → 入室
        self.assertEqual(len(self.stays()), 2)

    def test_manual_leave_in_stay_mode_waits_until_away(self):
        self.tick(0, "stay")
        self.p.manual_leave(T0 + H)
        self.tick(H + 15, "stay")  # 押したあとも操作している
        self.assertEqual(len(self.stays()), 1)
        self.tick(H + 20 * MIN, "stay", active=False)
        self.tick(14 * H, "stay")  # 次の朝
        self.assertEqual(len(self.stays()), 2)

    def test_manual_enter_survives_unregistered_wifi(self):
        self.p.manual_enter(T0)
        for t in range(15, H + 1, 15):  # eduroam など未登録の Wi-Fi のまま 1 時間
            self.tick(t, "carry", OTHER)
        self.assertEqual(self.stays()[0]["status"], "in")
        self.p.manual_leave(T0 + 2 * H)
        self.assertEqual(self.stays()[0]["end_ts"], T0 + 2 * H)
        self.tick(2 * H + 15, "carry", OTHER)
        self.assertEqual(len(self.stays()), 1)

    def test_manual_enter_resumes_tentative_stay(self):
        self.tick(0, "carry", UNIV)
        self.tick(H, "carry", OFFLINE)
        self.assertEqual(self.stays()[0]["status"], "tentative")
        self.p.manual_enter(T0 + 2 * H)
        self.assertEqual([s["status"] for s in self.stays()], ["in"])

    def test_manual_enter_after_expired_tentative_opens_new_stay(self):
        self.tick(0, "carry", UNIV)
        self.tick(H, "carry", OFFLINE)
        self.p.manual_enter(T0 + 6 * H)
        self.assertEqual([s["status"] for s in self.stays()], ["closed", "in"])
        self.assertEqual(self.stays()[0]["end_ts"], T0)  # 仮退出した時刻で閉じる

    def test_manual_leave_tentative_uses_time_of_leaving(self):
        self.tick(0, "carry", UNIV)
        self.tick(H, "carry", OFFLINE)
        self.p.manual_leave(T0 + 2 * H)
        self.assertEqual(self.stays()[0]["end_ts"], T0)


if __name__ == "__main__":
    unittest.main()

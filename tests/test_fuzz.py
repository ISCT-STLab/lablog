"""乱数で作ったいろいろな流れで、入退室の判定と集計が壊れないかを確かめる。

Wi-Fi の切り替え・スリープ・リモートデスクトップ・手動の入室と退室・判定方法の切り替えを、
何百通りもの組み合わせで動かし、毎回「壊れてはいけない条件」を確かめる。
回数を増やすときは、環境変数 LABLOG_FUZZ に回数を入れる（例：LABLOG_FUZZ=3000）。
"""

import os
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from lablog.config import Settings
from lablog.days import day_range, logical_date
from lablog.presence import Presence, delete_stay, judge_net, save_stay
from lablog.summary import day_summary, month_summary
from lablog.wifi import HOME, IGNORED, OFFLINE, OTHER, UNIV

from tests.test_presence import make_conn

H = 3600
ROUNDS = int(os.environ.get("LABLOG_FUZZ", "150"))
MODES = ["wifi", "activity", "manual", "off"]
NETS = [UNIV, HOME, OTHER, IGNORED, OFFLINE]
START = day_range(date(2026, 10, 5))[0] + 3 * H  # 8:00
APPS = [("code.exe", "main.py - Visual Studio Code"), ("chrome.exe", "arXiv - Google Chrome"),
        ("winword.exe", "論文.docx - Word"), ("explorer.exe", ""), ("line.exe", "LINE")]


class Simulation:
    """main.py の記録の繰り返しと同じ順番で、判定を動かす。"""

    def __init__(self, seed: int):
        self.rnd = random.Random(seed)
        self.conn = make_conn()
        self.leave_after = self.rnd.choice([1, 2, 4, 8]) * H
        self.p = Presence(self.conn, leave_after_sec=self.leave_after, idle_threshold_sec=300, gap_sec=180)
        self.mode = self.rnd.choice(MODES)
        self.started_off = self.mode == "off"
        self.ever_switched = False
        self.now = START
        self.last_input = self.now
        self.wifi = self.rnd.choice(NETS)
        self.manual_ok = 0  # 入室ボタンがうまくいった回数

    def step(self):
        rnd = self.rnd
        if rnd.random() < 0.03:
            self.now += rnd.uniform(200, 12 * H)  # スリープ・電源オフ（記録が止まる）
        else:
            self.now += 15
        if rnd.random() < 0.06:
            self.wifi = rnd.choice(NETS)
        if rnd.random() < 0.7:
            self.last_input = self.now - rnd.uniform(0, 10)
        idle = self.now - self.last_input
        remote = rnd.random() < 0.03
        local_active = not remote and idle < 15 * 60
        if rnd.random() < 0.01:
            self.mode = rnd.choice(MODES)
            self.ever_switched = True
        if self.mode != "off" and rnd.random() < 0.015:
            at = None if rnd.random() < 0.7 else self.now - rnd.uniform(0, 3 * H)
            try:
                if rnd.random() < 0.5:
                    self.p.manual_enter(self.now, at)
                    self.manual_ok += 1
                else:
                    self.p.manual_leave(self.now, at)
            except ValueError:
                pass  # 前の滞在と重なる時刻などは、画面にエラーを出して受け付けない
        self.p.tick(self.now, judge_net(self.mode, self.wifi, local_active, remote), idle,
                    by_wifi=self.mode == "wifi" and not remote)

    def stays(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM stays ORDER BY start_ts, id")]


def check_stays(test, sim: Simulation, where: str):
    stays = sim.stays()
    open_ = [s for s in stays if s["status"] != "closed"]
    test.assertLessEqual(len(open_), 1, f"{where}: 開いている滞在が 2 つ以上")
    prev_end = None
    for s in stays:
        test.assertIn(s["status"], ("in", "tentative", "closed"), where)
        test.assertLessEqual(s["start_ts"], sim.now + 1e-6, f"{where}: これからの入室")
        if s["status"] == "closed":
            test.assertIsNotNone(s["end_ts"], f"{where}: 退室の時刻が無い")
            test.assertLessEqual(s["start_ts"], s["end_ts"], f"{where}: 退室が入室より前")
            test.assertLessEqual(s["end_ts"], sim.now + 1e-6, f"{where}: これからの退室")
            if s["end_reason"] != "manual":  # 手で直したもの以外は、1 分未満なら残さない
                test.assertGreaterEqual(s["end_ts"] - s["start_ts"], 60, f"{where}: 1 分未満の滞在が残っている")
        elif s["status"] == "tentative":
            test.assertIsNotNone(s["tentative_since"], f"{where}: 仮退室の時刻が無い")
            test.assertGreaterEqual(s["tentative_since"], s["start_ts"] - 1e-6, f"{where}: 仮退室が入室より前")
        else:
            test.assertIsNone(s["tentative_since"], f"{where}: 在室中なのに仮退室の時刻がある")
        if prev_end is not None:
            test.assertGreaterEqual(s["start_ts"], prev_end - 1e-6, f"{where}: 滞在が重なっている")
        prev_end = s["end_ts"] if s["status"] == "closed" else (s["tentative_since"] or sim.now)
    if sim.started_off and not sim.ever_switched:
        test.assertEqual(stays, [], f"{where}: 記録しない設定なのに滞在がある")
    view = sim.p.status_view(sim.now)
    test.assertIn(view["state"], ("in", "tentative", "out"), where)
    if not open_:
        test.assertEqual(view["state"], "out", f"{where}: 滞在が無いのに在室")


class PresenceFuzzTest(unittest.TestCase):
    def test_random_days(self):
        for seed in range(ROUNDS):
            sim = Simulation(seed)
            for i in range(sim.rnd.randint(50, 700)):
                sim.step()
                if i % 25 == 0:
                    check_stays(self, sim, f"seed={seed} step={i} mode={sim.mode}")
            check_stays(self, sim, f"seed={seed} 最後 mode={sim.mode}")

    def test_random_hand_edits(self):
        """入退室の追加・編集・削除（日別の画面・スマホ版）を混ぜても、重なりやこれからの時刻ができない。"""
        for seed in range(ROUNDS):
            sim = Simulation(seed)
            sim.started_off = False  # 記録しない設定でも、手で足した滞在は残る
            rnd = sim.rnd
            for i in range(rnd.randint(100, 500)):
                sim.step()
                if rnd.random() < 0.05:
                    stays = sim.stays()
                    target = rnd.choice(stays) if stays and rnd.random() < 0.7 else None
                    start = sim.now - rnd.uniform(-H, 10 * H)        # ときどき、これからの時刻
                    end = None if target and target["status"] != "closed" and rnd.random() < 0.5 \
                        else start + rnd.uniform(-H, 5 * H)          # ときどき、入室より前の退室
                    try:
                        if target and rnd.random() < 0.2:
                            delete_stay(sim.conn, target["id"])
                        else:
                            save_stay(sim.conn, target["id"] if target else None, start, end, now=sim.now)
                    except ValueError:
                        pass  # 画面にエラーを出して受け付けない
                    check_stays(self, sim, f"seed={seed} step={i} 手で直したあと")

    def test_manual_mode_never_opens_by_itself(self):
        for seed in range(ROUNDS):
            sim = Simulation(seed)
            sim.mode = "manual"
            sim.started_off = False
            for _ in range(300):
                sim.wifi = sim.rnd.choice(NETS)
                sim.now += 15
                sim.p.tick(sim.now, judge_net("manual", sim.wifi, True, False), 0, by_wifi=False)
            self.assertEqual(sim.stays(), [], f"seed={seed}: 手動なのに自動で入室した")


class SummaryFuzzTest(unittest.TestCase):
    """乱数の記録から作った日・月の集計が、互いに食い違わないか。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(Path(self.tmp.name) / "settings.json")

    def test_day_and_month_agree(self):
        for seed in range(max(10, ROUNDS // 10)):
            rnd = random.Random(seed)
            sim = Simulation(seed)
            for _ in range(rnd.randint(200, 900)):
                sim.step()
            # PC 作業の記録（日をまたぐもの・離席も混ぜる）
            t = START - rnd.uniform(0, 3 * H)
            while t < sim.now:
                dur = rnd.choice([5, 60, 600, 3 * H, 9 * H])
                exe, title = rnd.choice(APPS)
                sim.conn.execute("INSERT INTO segments (start_ts, end_ts, exe, title, idle, location) VALUES (?,?,?,?,?,?)",
                                 (t, t + dur, exe, title, int(rnd.random() < 0.2), rnd.choice(["大学", "自宅", "その他", ""])))
                t += dur + rnd.choice([0, 0, 30, 3600])
            now = sim.now + rnd.uniform(0, 2 * H)
            first, last = logical_date(START - 3 * H), logical_date(now)
            days = {}
            d = first
            while d <= last:
                s = day_summary(sim.conn, self.settings, d, now)
                t0, t1 = s["range"]
                self.assertGreaterEqual(s["stay_total"], 0)
                self.assertAlmostEqual(sum(s["work_by_location"].values()), s["work_total"], places=3)
                self.assertAlmostEqual(sum(c["sec"] for c in s["categories"]), s["work_total"], places=3)
                self.assertLessEqual(s["work_total"], t1 - t0 + 1e-6, f"seed={seed} {d}: 1 日より長い")
                prev = t0
                for b in s["timeline"]:
                    self.assertGreaterEqual(b["start"], prev - 1e-6, f"seed={seed} {d}: タイムラインが重なる")
                    self.assertLessEqual(b["end"], t1 + 1e-6)
                    prev = b["end"]
                days[d] = s
                d += timedelta(days=1)
            for y, m in sorted({(x.year, x.month) for x in days}):
                ms = month_summary(sim.conn, self.settings, y, m, now)
                for entry in ms["days"]:
                    day = date.fromisoformat(entry["date"])
                    if day in days:
                        self.assertAlmostEqual(entry["work"], days[day]["work_total"], places=3,
                                               msg=f"seed={seed} {day}: 日と月の作業時間が違う")
                        self.assertAlmostEqual(entry["stay"], days[day]["stay_total"], places=3,
                                               msg=f"seed={seed} {day}: 日と月の滞在時間が違う")


if __name__ == "__main__":
    unittest.main()


def random_value(rnd: random.Random, depth: int = 0):
    """設定の画面やスマホ版から届きうる、いろいろな形の値（壊れたものも含む）。"""
    simple = [None, True, False, 0, 1, -1, 2.5, 10 ** 9, float("nan"), "", "abc", "1", "wifi", "manual", "stable",
              "http://127.0.0.1:11434", "https://example.com", "C:\\Users", "qwen3:8b", "x" * 300]
    if depth > 2 or rnd.random() < 0.5:
        return rnd.choice(simple)
    if rnd.random() < 0.5:
        return [random_value(rnd, depth + 1) for _ in range(rnd.randint(0, 4))]
    keys = ["enabled", "mode", "away_min", "place", "remote", "univ", "home", "ignore", "apps", "files", "git",
            "recent_files", "chrome", "edge", "hours", "url", "model", "auto", "folder", "viewer_url", "channel",
            "interval_min", "keep_days", "name", "domains", "titles", "junk"]
    return {rnd.choice(keys): random_value(rnd, depth + 1) for _ in range(rnd.randint(0, 6))}


class SettingsFuzzTest(unittest.TestCase):
    """どんな値を保存しようとしても、断るときは ValueError（画面にエラーを出す）だけにし、壊れた設定を残さない。"""

    def test_random_patches(self):
        from lablog.config import DEFAULT_SETTINGS, VALIDATORS
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = Settings(path)
            for seed in range(ROUNDS * 10):
                rnd = random.Random(seed)
                key = rnd.choice(list(VALIDATORS) + ["unknown"])
                # 正しい形をもとに一部だけ壊したもの、またはまったくの乱数
                if key in DEFAULT_SETTINGS and isinstance(DEFAULT_SETTINGS[key], dict) and rnd.random() < 0.6:
                    value = dict(settings.get(key))
                    value[rnd.choice(list(value) + ["junk"])] = random_value(rnd)
                else:
                    value = random_value(rnd)
                before = settings.get(key) if key in VALIDATORS else None
                try:
                    settings.update({key: value})
                except ValueError:
                    self.assertEqual(settings.get(key) if key in VALIDATORS else None, before,
                                     f"seed={seed} {key}={value!r}: 断ったのに変わった")
                    continue
                except Exception as exc:  # ValueError 以外は、サーバーが 500 を返してしまう
                    self.fail(f"seed={seed} {key}={value!r}: {type(exc).__name__}: {exc}")
                reloaded = Settings(path)
                self.assertEqual(reloaded.get(key), settings.get(key), f"seed={seed} {key}: 読み直すと違う")
            # どれだけ壊そうとしても、集計は動く
            s = day_summary(make_conn(), settings, date(2026, 10, 5), START)
            self.assertIn("work_total", s)

    def test_broken_settings_file(self):
        """手で書き換えて壊れた settings.json でも起動でき、どの項目も正しい形になる。"""
        import json as json_
        from lablog.config import VALIDATORS
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            for seed in range(ROUNDS * 2):
                rnd = random.Random(seed)
                data = {rnd.choice(list(VALIDATORS) + ["junk"]): random_value(rnd) for _ in range(rnd.randint(0, 8))}
                text = json_.dumps(data) if rnd.random() < 0.9 else rnd.choice(["", "[1]", "{", "null", "nan"])
                path.write_text(text, encoding="utf-8")
                try:
                    settings = Settings(path)
                except Exception as exc:
                    self.fail(f"seed={seed} {text[:80]}: {type(exc).__name__}: {exc}")
                for key, check in VALIDATORS.items():
                    value = settings.get(key)
                    self.assertEqual(check(value), value, f"seed={seed} {key}: 正しくない値のまま")

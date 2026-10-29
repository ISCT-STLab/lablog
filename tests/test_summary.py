"""1 日分の集計と日報の材料のテスト。"""

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from lablog.categorize import SeenTitles
from lablog.config import Settings
from lablog.report import build_digest, reading_pages
from lablog.summary import day_summary

from tests.test_presence import make_conn

D = date(2026, 10, 20)


def at(hour, minute=0):
    return datetime(D.year, D.month, D.day, hour, minute).timestamp()


class SeenPagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self.tmp.name) / "settings.json")
        self.conn = make_conn()
        self.visit_id = 0

    def tearDown(self):
        self.tmp.cleanup()

    def window(self, start, end, title, exe="chrome.exe"):
        self.conn.execute("INSERT INTO segments (start_ts, end_ts, exe, title, idle, location) VALUES (?, ?, ?, ?, 0, '大学')",
                          (start, end, exe, title))

    def visit(self, ts, title, domain):
        self.visit_id += 1
        self.conn.execute("INSERT INTO web_visits (browser, profile, visit_id, ts, url, title, domain) "
                          "VALUES ('Chrome', 'Default', ?, ?, ?, ?, ?)",
                          (self.visit_id, ts, f"https://{domain}/{self.visit_id}", title, domain))

    def test_only_pages_shown_on_screen_are_kept(self):
        self.window(at(10), at(10, 30), "Attention Is All You Need - arXiv - Google Chrome")
        self.window(at(11), at(11, 5), "(3) 受信トレイ - Gmail - Google Chrome")
        self.visit(at(9), "Attention Is All You Need - arXiv", "arxiv.org")     # 朝に裏で読み込まれ、あとで見た
        self.visit(at(9), "開きっぱなしのタブ - YouTube", "youtube.com")           # 一度も前面に出ていない
        self.visit(at(11), "(1) 受信トレイ - Gmail", "mail.google.com")          # 未読数だけ違う
        self.visit(at(12), "", "example.com")
        s = day_summary(self.conn, self.settings, D, at(20))
        titles = [p["title"] for dom in s["web"] for p in dom["pages"]]
        self.assertEqual(sorted(titles), ["(1) 受信トレイ - Gmail", "Attention Is All You Need - arXiv"])
        self.assertEqual(s["web_hidden"], 2)

    def test_seen_titles_prefix_rules(self):
        seen = SeenTitles(["Overleaf プロジェクト一覧 - Overleaf, Online LaTeX Editor", "GitHub"])
        self.assertIn("Overleaf プロジェクト一覧", seen)  # 長い題名は前方一致でよい
        self.assertNotIn("Git", seen)                      # 短すぎる題名は前方一致にしない
        self.assertIn("GitHub", seen)

    def test_digest_skips_search_and_login_pages(self):
        self.window(at(10), at(11), "Attention Is All You Need - arXiv - Google Chrome")
        self.window(at(11), at(11, 10), "transformer 位置エンコーディング - Google 検索 - Google Chrome")
        self.window(at(11, 10), at(11, 20), "サインイン - MathWorks - Google Chrome")
        self.visit(at(10), "Attention Is All You Need - arXiv", "arxiv.org")
        self.visit(at(11), "transformer 位置エンコーディング - Google 検索", "google.com")
        self.visit(at(11, 10), "サインイン - MathWorks", "mathworks.com")
        self.window(at(12), at(12, 10), "LabLog - Google Chrome")
        self.visit(at(12), "LabLog", "127.0.0.1")  # PC 内のダッシュボード
        s = day_summary(self.conn, self.settings, D, at(20))
        self.assertEqual(reading_pages(s), [("arxiv.org", "Attention Is All You Need - arXiv")])
        digest = build_digest(s)
        self.assertNotIn("Google 検索", digest.split("閲覧したページ")[1])
        self.assertNotIn("サインイン", digest.split("閲覧したページ")[1])


if __name__ == "__main__":
    unittest.main()

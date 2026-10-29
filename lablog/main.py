"""LabLog 本体：記録用のスレッドとダッシュボードのサーバーを起動する。"""

import argparse
import hashlib
import logging
import logging.handlers
import os
import queue
import sys
import threading
import time
import webbrowser
from datetime import timedelta

from . import db, wifi
from .activity import ActivityRecorder
from .cloud import CloudSync
from .collectors import (commit_reminders, discover_repos, import_browser_history, import_claude_code,
                         import_recent_files, poll_repo)
from .config import Settings, data_dir
from .days import day_range, fmt_duration, logical_date
from .filewatch import FileEventSink, WatcherManager
from .presence import Presence
from .report import generate_report
from .screentext import ScreenTextRecorder
from .server import make_server
from .winapi import acquire_single_instance, foreground_window, idle_seconds, show_toast

log = logging.getLogger("lablog")

WIFI_CHECK_SEC = 15
COLLECT_SEC = 300
SCREEN_TEXT_CHECK_SEC = 15
REPO_DISCOVER_SEC = 1800


class App:
    def __init__(self):
        self.settings = Settings()
        self.conn = db.connect()
        db.init(self.conn)
        self.port = self.settings.get("port")
        self._paused = db.kv_get(self.conn, "paused") == "1"
        self._status = {"state": "out", "net": wifi.OFFLINE, "ssids": [], "wifi_error": None,
                        "location": "その他", "idle": 0, "started": time.time()}
        self._status_lock = threading.Lock()
        self._pause_lock = threading.Lock()
        self.repos: list[str] = []
        self._repos_stale = True
        self._wake = threading.Event()
        self.report_jobs: queue.Queue = queue.Queue()
        self.sink = FileEventSink(db.connect(), lambda: self._paused, [str(data_dir())])
        self.watchers = WatcherManager(self.sink)
        self.cloud = CloudSync(self.settings, self.status_snapshot)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    # ---- 状態

    def status_snapshot(self) -> dict:
        with self._status_lock:
            return dict(self._status) | {"paused": self._paused}

    def _set_status(self, **fields):
        with self._status_lock:
            self._status.update(fields)

    def set_paused(self, paused: bool):
        with self._pause_lock:
            if paused == self._paused:
                return
            now = time.time()
            if paused:
                self.conn.execute("INSERT INTO pauses (start_ts) VALUES (?)", (now,))
            else:
                self.conn.execute("UPDATE pauses SET end_ts = ? WHERE end_ts IS NULL", (now,))
            db.kv_set(self.conn, "paused", "1" if paused else "0")
            self._paused = paused
            log.info("記録を%sしました", "一時停止" if paused else "再開")

    def settings_changed(self, keys: set[str]):
        if "watch_folders" in keys:
            self.watchers.sync(self.settings.get("watch_folders"))
            self._repos_stale = True
            self._wake.set()

    def queue_report(self, d, reason: str):
        if self.settings.get("ollama").get("auto", True):
            self.report_jobs.put((d, reason))

    # ---- スレッド

    def activity_loop(self):
        conn = db.connect()
        s = self.settings
        presence = Presence(conn)
        recorder = ActivityRecorder(conn, s.get("sample_interval_sec"))
        last_wifi = 0.0
        net, ssids, wifi_error = wifi.OFFLINE, [], None
        last_day = logical_date(time.time())
        while True:
            started = time.time()
            interval = s.get("sample_interval_sec")
            try:
                idle_threshold = s.get("idle_threshold_min") * 60
                presence.leave_after = s.get("leave_after_hours") * 3600
                presence.idle_threshold = idle_threshold
                recorder.interval = interval
                now = time.time()
                idle = idle_seconds()
                fg = foreground_window()
                if now - last_wifi >= WIFI_CHECK_SEC:
                    last_wifi = now
                    ssids, wifi_error = wifi.current_ssids()
                    net = wifi.classify(ssids, s.get("wifi"))
                    for kind, stay_id in presence.tick(now, net, idle):
                        row = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()
                        log.info("滞在 %s: id=%s %s", kind, stay_id, dict(row) if row else "")
                        if row:
                            self.cloud.mark_dirty(logical_date(row["start_ts"]))
                        if kind == "closed" and row:
                            self.queue_report(logical_date(row["start_ts"]), "退出確定")
                view = presence.status_view(now)
                if net == wifi.IGNORED:  # テザリングなど：滞在中なら大学での作業とみなす
                    location = "大学" if view["state"] == "in" else "その他"
                else:
                    location = wifi.LOCATION_LABELS[net]
                excluded = {e.lower() for e in s.get("excluded_apps")}
                recorder.tick(now, fg, idle, idle_threshold, location, excluded, self._paused)
                today = logical_date(now)
                if today != last_day:
                    self.queue_report(last_day, "日付の切り替わり")
                    last_day = today
                self._set_status(**view, net=net, ssids=ssids, wifi_error=wifi_error,
                                 location=location, idle=idle, last_tick=now)
            except Exception:
                log.exception("記録中にエラーが起きました")
            time.sleep(max(1.0, interval - (time.time() - started)))

    def collect_loop(self):
        conn = db.connect()
        s = self.settings
        last_discover = 0.0
        self._wake.wait(15)
        while True:
            self._wake.clear()
            now = time.time()
            jobs = [("ブラウザ履歴", lambda: import_browser_history(conn, s.get("browsers"), now)),
                    ("最近使ったファイル", lambda: import_recent_files(conn, now))]
            if s.get("claude_code").get("enabled", True):
                jobs.append(("Claude Code の会話", lambda: import_claude_code(conn, now)))
            for name, job in jobs:
                try:
                    job()
                except Exception:
                    log.exception("%sの取り込みでエラーが起きました", name)
            try:
                folders = s.get("watch_folders")
                self.watchers.sync(folders)
                if self._repos_stale or now - last_discover > REPO_DISCOVER_SEC:
                    self.repos = discover_repos(folders)
                    self._repos_stale = False
                    last_discover = now
                for repo in self.repos:
                    try:
                        poll_repo(conn, repo, now)
                    except Exception:
                        log.exception("Git の確認でエラーが起きました: %s", repo)
                self._notify_commits(conn, now)
            except Exception:
                log.exception("Git・フォルダの処理でエラーが起きました")
            self._wake.wait(COLLECT_SEC)

    def screen_text_loop(self):
        """ウィンドウ名から内容が分からないアプリの画面の文字を読み取る（screentext.py）。"""
        conn = db.connect()
        recorder = ScreenTextRecorder(conn)
        last_cleanup = 0.0
        while True:
            try:
                now = time.time()
                cfg = self.settings.get("screen_text")
                if now - last_cleanup >= 3600:
                    recorder.cleanup(now, cfg.get("keep_days", 3))
                    last_cleanup = now
                excluded = {e.lower() for e in self.settings.get("excluded_apps")}
                recorder.tick(now, cfg, excluded, self._paused, idle_seconds())
            except Exception:
                log.exception("画面の文字の読み取りでエラーが起きました")
            time.sleep(SCREEN_TEXT_CHECK_SEC)

    def _notify_commits(self, conn, now: float):
        cfg = self.settings.get("git_notify")
        if not cfg.get("enabled") or self._paused or idle_seconds() > 300:
            return
        for item in commit_reminders(conn, self.repos, cfg.get("hours", 2), now):
            name = os.path.basename(item["repo"])
            show_toast("コミットしていない変更があります",
                       f"{name}：{item['dirty_files']} 件の変更が {fmt_duration(now - item['since'])} "
                       f"コミットされていません", self.url)
            log.info("コミットを促す通知を出しました: %s", item["repo"])

    def report_loop(self):
        conn = db.connect()
        while True:
            d, reason = self.report_jobs.get()
            try:
                row = conn.execute("SELECT * FROM reports WHERE date = ?", (d.isoformat(),)).fetchone()
                # 自分で直した日報や、その日が終わってから作った日報は作り直さない
                if row and (row["edited"] or (row["generated_ts"] or 0) >= day_range(d)[1]):
                    continue
                log.info("日報を自動で作成します（%s, %s）", d, reason)
                generate_report(conn, self.settings, d)
                self.cloud.mark_dirty(d)
            except Exception:
                log.exception("日報の自動作成でエラーが起きました")

    def run(self, open_browser: bool):
        try:
            server = make_server(self, self.port)
        except OSError:
            log.exception("ポート %s を使えません", self.port)
            show_toast("LabLog を起動できませんでした",
                       f"ポート {self.port} が他のアプリに使われています。設定ファイルの port を変えてください。")
            return
        self.watchers.sync(self.settings.get("watch_folders"))
        for target, name in ((self.activity_loop, "activity"), (self.collect_loop, "collect"),
                             (self.report_loop, "report"), (self.cloud.loop, "cloud"),
                             (self.screen_text_loop, "screen-text")):
            threading.Thread(target=target, name=name, daemon=True).start()
        self.queue_report(logical_date(time.time()) - timedelta(days=1), "起動時（前日分が無ければ）")
        log.info("LabLog を起動しました: %s（データ: %s）", self.url, data_dir())
        if open_browser:
            threading.Timer(1.0, webbrowser.open, (self.url,)).start()
        server.serve_forever()


def _setup_logging():
    handler = logging.handlers.RotatingFileHandler(data_dir() / "lablog.log", maxBytes=1_000_000,
                                                   backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None:
        root.addHandler(logging.StreamHandler())
    sys.excepthook = lambda *exc: log.critical("予期しないエラー", exc_info=exc)
    threading.excepthook = lambda a: log.critical("スレッドで予期しないエラー",
                                                  exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def main(argv=None):
    parser = argparse.ArgumentParser(description="研究室の入退室と PC 作業を記録します")
    parser.add_argument("--no-browser", action="store_true", help="起動時にダッシュボードを開かない")
    parser.add_argument("--data-dir", help="データの保存先（テスト用）")
    parser.add_argument("--port", type=int, help="ダッシュボードのポート（テスト用。設定より優先）")
    args = parser.parse_args(argv)
    if args.data_dir:
        os.environ["LABLOG_DATA_DIR"] = args.data_dir

    _setup_logging()
    mutex_name = "Local\\LabLog-" + hashlib.md5(str(data_dir()).lower().encode()).hexdigest()[:10]
    if acquire_single_instance(mutex_name) is None:
        # すでに起動している場合は、ダッシュボードを開くだけにする
        if not args.no_browser:
            webbrowser.open(f"http://127.0.0.1:{args.port or Settings().get('port')}/")
        return
    app = App()
    if args.port:
        app.port = args.port
    app.run(open_browser=not args.no_browser)


if __name__ == "__main__":
    main()

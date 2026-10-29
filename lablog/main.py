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
from .activity import IDLE_EXES, ActivityRecorder
from .cloud import CloudSync
from .collectors import (commit_reminders, discover_repos, import_browser_history, import_claude_code,
                         import_recent_files, poll_repo)
from .config import Settings, dashboard_key, data_dir
from .days import day_range, fmt_duration, logical_date
from .filewatch import FileEventSink, WatcherManager
from .presence import Presence, judge_net, work_location
from .report import generate_report
from .screentext import ScreenTextRecorder
from .server import make_server
from .winapi import acquire_single_instance, foreground_window, idle_seconds, is_remote_session, show_toast

log = logging.getLogger("lablog")

PRESENCE_CHECK_SEC = 15  # 入退室の判定（Wi-Fi の確認）の間隔
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
        self.presence_lock = threading.Lock()  # 自動の判定と、手動の入室・退室が同時に滞在を書き換えないように
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

    @property
    def open_url(self) -> str:
        """ブラウザで開くときの URL（合言葉付き。ブラウザが Cookie に覚えたら、合言葉を外した URL に移る）。"""
        return f"{self.url}?key={dashboard_key()}"

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
        if keys & {"watch_folders", "record"}:
            self.watchers.sync(self._watched_folders())
            self._repos_stale = True
            self._wake.set()

    def _record(self) -> dict:
        return self.settings.get("record")

    def _watched_folders(self) -> list[str]:
        """ファイル編集を記録する設定のときだけ、監視フォルダを見張る。"""
        return self.settings.get("watch_folders") if self._record().get("files", True) else []

    def _presence_settings(self, presence: Presence):
        presence.leave_after = self.settings.get("leave_after_hours") * 3600
        presence.idle_threshold = self.settings.get("idle_threshold_min") * 60

    def _presence_events(self, conn, events):
        for kind, stay_id in events:
            row = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()
            log.info("滞在 %s: id=%s %s", kind, stay_id, dict(row) if row else "")
            if row:
                self.cloud.mark_dirty(logical_date(row["start_ts"]))
            if kind == "closed" and row:
                self.queue_report(logical_date(row["start_ts"]), "退出確定")

    def manual_presence(self, action: str) -> dict:
        """ダッシュボードの入室・退室ボタン。"""
        conn = db.thread_conn()
        presence = Presence(conn)
        self._presence_settings(presence)
        now = time.time()
        with self.presence_lock:
            events = presence.manual_enter(now) if action == "enter" else presence.manual_leave(now)
        self._presence_events(conn, events)
        self.cloud.mark_dirty(logical_date(now))
        log.info("手動で%sしました", "入室" if action == "enter" else "退室")
        self._set_status(**presence.status_view(now))
        return self.status_snapshot()

    def queue_report(self, d, reason: str):
        if self.settings.get("ollama").get("auto", True):
            self.report_jobs.put((d, reason))

    # ---- スレッド

    def activity_loop(self):
        conn = db.connect()
        s = self.settings
        presence = Presence(conn)
        recorder = ActivityRecorder(conn, s.get("sample_interval_sec"))
        last_check = 0.0
        net, ssids, wifi_error = wifi.OFFLINE, [], None
        last_local = 0.0  # 最後に手元で操作した時刻（リモートデスクトップ中・ロック中の操作は含めない）
        last_day = logical_date(time.time())
        while True:
            started = time.time()
            interval = s.get("sample_interval_sec")
            try:
                idle_threshold = s.get("idle_threshold_min") * 60
                self._presence_settings(presence)
                pcfg = s.get("presence")
                mode = pcfg.get("mode", "carry")
                recorder.interval = interval
                now = time.time()
                idle = idle_seconds()
                fg = foreground_window()
                remote = is_remote_session()
                locked = fg is not None and fg[0].lower() in IDLE_EXES
                if not remote and not locked:
                    last_local = max(last_local, now - idle)
                local_idle = now - last_local
                local_active = not remote and not locked and local_idle < pcfg.get("away_min", 15) * 60
                if now - last_check >= PRESENCE_CHECK_SEC:
                    last_check = now
                    if mode == "carry":
                        ssids, wifi_error = wifi.current_ssids()
                        net = wifi.classify(ssids, s.get("wifi"))
                    else:  # Wi-Fi は判定に使わない
                        ssids, wifi_error, net = [], None, wifi.OFFLINE
                    with self.presence_lock:
                        events = presence.tick(now, judge_net(mode, net, local_active, remote), local_idle)
                    self._presence_events(conn, events)
                view = presence.status_view(now)
                location = work_location(mode, net, view["state"], remote)
                excluded = {e.lower() for e in s.get("excluded_apps")}
                recorder.tick(now, fg, idle, idle_threshold, location, excluded, self._paused,
                              self._record().get("apps", "title"))
                today = logical_date(now)
                if today != last_day:
                    self.queue_report(last_day, "日付の切り替わり")
                    last_day = today
                self._set_status(**view, net=net, ssids=ssids, wifi_error=wifi_error,
                                 location=location, idle=idle, last_tick=now, mode=mode, remote=remote)
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
            record = self._record()
            jobs = [("ブラウザ履歴", lambda: import_browser_history(conn, s.get("browsers"), now))]
            if record.get("recent_files", True):
                jobs.append(("最近使ったファイル", lambda: import_recent_files(conn, now)))
            if s.get("claude_code").get("enabled", True):
                jobs.append(("Claude Code の会話", lambda: import_claude_code(conn, now)))
            for name, job in jobs:
                try:
                    job()
                except Exception:
                    log.exception("%sの取り込みでエラーが起きました", name)
            try:
                folders = s.get("watch_folders")
                self.watchers.sync(self._watched_folders())
                if not record.get("git", True):
                    self.repos = []
                    self._repos_stale = True
                elif self._repos_stale or now - last_discover > REPO_DISCOVER_SEC:
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
            server = make_server(self, self.port, dashboard_key())
        except OSError:
            log.exception("ポート %s を使えません", self.port)
            show_toast("LabLog を起動できませんでした",
                       f"ポート {self.port} が他のアプリに使われています。設定ファイルの port を変えてください。")
            return
        self.watchers.sync(self._watched_folders())
        for target, name in ((self.activity_loop, "activity"), (self.collect_loop, "collect"),
                             (self.report_loop, "report"), (self.cloud.loop, "cloud"),
                             (self.screen_text_loop, "screen-text")):
            threading.Thread(target=target, name=name, daemon=True).start()
        self.queue_report(logical_date(time.time()) - timedelta(days=1), "起動時（前日分が無ければ）")
        log.info("LabLog を起動しました: %s（データ: %s）", self.url, data_dir())
        if open_browser:
            threading.Timer(1.0, webbrowser.open, (self.open_url,)).start()
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
            webbrowser.open(f"http://127.0.0.1:{args.port or Settings().get('port')}/?key={dashboard_key()}")
        return
    app = App()
    if args.port:
        app.port = args.port
    app.run(open_browser=not args.no_browser)


if __name__ == "__main__":
    main()

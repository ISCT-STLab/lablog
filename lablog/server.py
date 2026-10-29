"""ダッシュボード用の Web サーバー（127.0.0.1 だけで待ち受ける）。"""

import copy
import csv
import io
import json
import logging
import os
import sys
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db
from .config import DEFAULT_CATEGORIES
from .days import fmt_duration, fmt_hm, logical_date, parse_date
from .presence import delete_stay, save_stay
from .report import generate_report, ollama_status, save_report
from .summary import day_summary, month_summary
from .wifi import WIFI_KINDS
from .winapi import run_quiet

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"
STATIC = {"/": "index.html", "/app.js": "app.js", "/cloud.js": "cloud.js", "/config.js": "config.js",
          "/style.css": "style.css", "/favicon.svg": "favicon.svg", "/manifest.webmanifest": "manifest.webmanifest",
          "/icon-180.png": "icon-180.png", "/icon-192.png": "icon-192.png", "/icon-512.png": "icon-512.png",
          "/fonts/KleeOne-Regular.woff2": "fonts/KleeOne-Regular.woff2", "/fonts/OFL.txt": "fonts/OFL.txt"}
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".webmanifest": "application/manifest+json", ".woff2": "font/woff2",
                 ".txt": "text/plain; charset=utf-8"}

FOLDER_PICKER = """
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
root.lift()
root.focus_force()
print(filedialog.askdirectory(parent=root, title="監視するフォルダを選んでください", mustexist=True) or "")
"""


class ApiError(Exception):
    pass


def _ts(text: str | None) -> float | None:
    return datetime.fromisoformat(text).timestamp() if text else None


class Api:
    def __init__(self, app):
        self.app = app

    # ---- GET

    def get_status(self, q):
        conn = db.thread_conn()
        repos = [dict(r) | {"name": os.path.basename(r["repo"])}
                 for r in conn.execute("SELECT * FROM repo_state ORDER BY repo")
                 if r["repo"] in set(self.app.repos)]
        return self.app.status_snapshot() | {"repos": repos, "today": logical_date(time.time()).isoformat()}

    def get_day(self, q):
        d = parse_date(q["date"]) if q.get("date") else logical_date(time.time())
        return day_summary(db.thread_conn(), self.app.settings, d, time.time())

    def get_month(self, q):
        year, month = map(int, q["month"].split("-"))
        return month_summary(db.thread_conn(), self.app.settings, year, month, time.time())

    def get_settings(self, q):
        count = db.thread_conn().execute("SELECT COUNT(*) FROM screen_texts").fetchone()[0]
        return {"settings": self.app.settings.snapshot(), "data_dir": str(db.db_path().parent),
                "repos": self.app.repos, "ssids": self.app.status_snapshot().get("ssids", []),
                "screen_text_count": count}

    def get_ollama(self, q):
        return ollama_status(self.app.settings.get("ollama"))

    def get_cloud(self, q):
        return self.app.cloud.status()

    # ---- POST

    def post_settings(self, body):
        try:
            snapshot = self.app.settings.update(body)
        except ValueError as exc:
            raise ApiError(str(exc))
        self.app.settings_changed(set(body))
        return {"settings": snapshot}

    def post_categories_reset(self, body):
        return self.post_settings({"categories": copy.deepcopy(DEFAULT_CATEGORIES)})

    def post_wifi_register(self, body):
        kind = body.get("kind")
        ssid = body.get("ssid") or next(iter(self.app.status_snapshot().get("ssids", [])), None)
        if kind not in WIFI_KINDS or not ssid:
            raise ApiError("Wi-Fi に接続していないため登録できません")
        cfg = self.app.settings.get("wifi")
        cfg = {k: [s for s in cfg.get(k, []) if s != ssid] for k in WIFI_KINDS}  # 1 つの種類にだけ登録する
        cfg[kind].append(ssid)
        return self.post_settings({"wifi": cfg})

    def post_wifi_remove(self, body):
        cfg = self.app.settings.get("wifi")
        kind = body.get("kind")
        if kind not in cfg:
            raise ApiError("種類が正しくありません")
        cfg[kind] = [s for s in cfg[kind] if s != body.get("ssid")]
        return self.post_settings({"wifi": cfg})

    def post_folders_pick(self, body):
        env = os.environ | {"PYTHONIOENCODING": "utf-8"}
        try:
            result = run_quiet([sys.executable, "-c", FOLDER_PICKER], timeout=900, env=env)
        except Exception as exc:
            raise ApiError(f"フォルダ選択画面を開けませんでした（{exc}）")
        path = result.stdout.strip()
        return {"path": os.path.normpath(path) if path else None}

    def post_folders_add(self, body):
        path = os.path.normpath(str(body.get("path", "")).strip().strip('"'))
        if not os.path.isdir(path):
            raise ApiError("フォルダが見つかりません")
        folders = self.app.settings.get("watch_folders")
        if any(os.path.normcase(f) == os.path.normcase(path) for f in folders):
            raise ApiError("すでに登録されています")
        return self.post_settings({"watch_folders": folders + [path]})

    def post_folders_remove(self, body):
        folders = [f for f in self.app.settings.get("watch_folders") if f != body.get("path")]
        return self.post_settings({"watch_folders": folders})

    def post_pause(self, body):
        self.app.set_paused(bool(body.get("paused")))
        return self.app.status_snapshot()

    def _stay_day(self, stay_id) -> list:
        row = db.thread_conn().execute("SELECT start_ts FROM stays WHERE id = ?", (stay_id,)).fetchone()
        return [logical_date(row["start_ts"])] if row else []

    def post_stay_save(self, body):
        try:
            start = _ts(body.get("start"))
            before = self._stay_day(body["id"]) if body.get("id") is not None else []
            save_stay(db.thread_conn(), body.get("id"), start, _ts(body.get("end")))
        except (TypeError, ValueError) as exc:
            raise ApiError(str(exc) or "時刻の形式が正しくありません")
        self.app.cloud.mark_dirty(logical_date(start), *before)
        return {"ok": True}

    def post_stay_delete(self, body):
        before = self._stay_day(int(body["id"]))
        delete_stay(db.thread_conn(), int(body["id"]))
        self.app.cloud.mark_dirty(*before)
        return {"ok": True}

    def post_report_generate(self, body):
        d = parse_date(body["date"])
        result = generate_report(db.thread_conn(), self.app.settings, d, force=bool(body.get("force")))
        self.app.cloud.mark_dirty(d)
        return result

    def post_report_save(self, body):
        d = parse_date(body["date"])
        save_report(db.thread_conn(), d, str(body.get("text", "")))
        self.app.cloud.mark_dirty(d)
        return {"ok": True}

    def post_screen_texts_clear(self, body):
        db.thread_conn().execute("DELETE FROM screen_texts")
        return {"ok": True}

    def post_cloud_export(self, body):
        self.app.cloud.request_export()
        return {"ok": True}

    # ---- CSV

    def stays_csv(self, q) -> bytes:
        year, month = map(int, q["month"].split("-"))
        m = month_summary(db.thread_conn(), self.app.settings, year, month, time.time())
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["日付", "曜日", "最初の入室", "最後の退室", "滞在時間", "滞在(分)", "PC作業時間", "PC作業(分)",
                    "うち研究室(分)", "うち自宅(分)"])
        for day in m["days"]:
            d = date.fromisoformat(day["date"])
            w.writerow([day["date"], "月火水木金土日"[d.weekday()],
                        fmt_hm(day["first_in"]) if day["first_in"] else "",
                        fmt_hm(day["last_out"]) if day["last_out"] else "",
                        fmt_duration(day["stay"]) if day["stay"] else "", round(day["stay"] / 60),
                        fmt_duration(day["work"]) if day["work"] else "", round(day["work"] / 60),
                        round(day["by_location"].get("大学", 0) / 60), round(day["by_location"].get("自宅", 0) / 60)])
        return ("﻿" + buf.getvalue()).encode("utf-8")  # Excel で文字化けしないよう BOM 付き


GET_ROUTES = {"/api/status": "get_status", "/api/day": "get_day", "/api/month": "get_month",
              "/api/settings": "get_settings", "/api/ollama": "get_ollama", "/api/cloud": "get_cloud"}
POST_ROUTES = {"/api/settings": "post_settings", "/api/categories/reset": "post_categories_reset",
               "/api/wifi/register": "post_wifi_register",
               "/api/wifi/remove": "post_wifi_remove", "/api/folders/pick": "post_folders_pick",
               "/api/folders/add": "post_folders_add", "/api/folders/remove": "post_folders_remove",
               "/api/pause": "post_pause", "/api/stay/save": "post_stay_save",
               "/api/stay/delete": "post_stay_delete", "/api/report/generate": "post_report_generate",
               "/api/report/save": "post_report_save", "/api/cloud/export": "post_cloud_export",
               "/api/screen_texts/clear": "post_screen_texts_clear"}


def make_handler(api: Api):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LabLog"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def _host_ok(self) -> bool:
            # DNS リバインディング対策：PC 内の名前で来たリクエストだけ受け付ける
            host = self.headers.get("Host", "").rsplit(":", 1)[0].strip("[]")
            return host in ("127.0.0.1", "localhost", "::1")

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status=200):
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _call(self, fn, arg):
            try:
                self._json(fn(arg))
            except ApiError as exc:
                self._json({"error": str(exc)}, 400)
            except (KeyError, ValueError) as exc:
                self._json({"error": f"入力が正しくありません（{exc}）"}, 400)
            except Exception as exc:
                log.exception("API でエラーが起きました")
                self._json({"error": f"エラーが起きました（{exc}）"}, 500)

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path in STATIC:
                file = WEB_DIR / STATIC[url.path]
                return self._send(200, file.read_bytes(), CONTENT_TYPES[file.suffix])
            if url.path == "/api/export/stays.csv":
                name = f"lablog_{q.get('month', 'export')}.csv"
                try:
                    body = api.stays_csv(q)
                except (KeyError, ValueError):
                    return self._json({"error": "month=YYYY-MM を指定してください"}, 400)
                return self._send(200, body, "text/csv; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="{name}"'})
            if url.path in GET_ROUTES:
                return self._call(getattr(api, GET_ROUTES[url.path]), q)
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            # 本文は必ず先に読み切る（読み残すと、同じ接続の次のリクエストが壊れる）
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            # 他のサイトから勝手に操作されないよう、独自ヘッダー付きのリクエストだけ受け付ける
            if not self._host_ok() or self.headers.get("X-LabLog") != "1":
                return self._send(403, b"forbidden", "text/plain")
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                return self._json({"error": "JSON が正しくありません"}, 400)
            path = urlparse(self.path).path
            if path in POST_ROUTES:
                return self._call(getattr(api, POST_ROUTES[path]), body)
            self._send(404, b"not found", "text/plain")

    return Handler


def make_server(app, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Api(app)))
    server.daemon_threads = True
    return server

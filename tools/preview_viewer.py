"""スマホ用ビューアーを PC で試す（OneDrive やサインインを使わずに、表示と操作を確かめる）。

いまの記録の写しから要約を書き出し、ビューアーの画面と一緒に 127.0.0.1 で開く。
ビューアーで直した日報・滞在は写しのほうに取り込む（本物の記録は変えない）。

  python tools/preview_viewer.py             → http://127.0.0.1:8770/
  python tools/preview_viewer.py --viewer-version 9.9.9   → 新しいバージョンのビューアーのふり（PC が古いときの表示を試す）
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "lablog" / "web"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".webmanifest": "application/manifest+json", ".json": "application/json; charset=utf-8",
         ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}
EDIT_NAME = re.compile(r"^[\w.-]+\.json$")


def snapshot(source: Path, work: Path):
    """記録と設定の写しを作る（LabLog が動いていても、一貫した状態で写せる）。"""
    work.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source / 'lablog.db'}?mode=ro", uri=True)
    dst = sqlite3.connect(work / "lablog.db")
    src.backup(dst)
    src.close()
    dst.close()
    if (source / "settings.json").exists():
        (work / "settings.json").write_bytes((source / "settings.json").read_bytes())


def sync_loop(out: Path):
    """PC の LabLog の代わりに、5 秒ごとに直した内容の取り込みと書き出しをする。"""
    from lablog import db
    from lablog.cloud import CloudSync
    from lablog.config import Settings
    from lablog.presence import Presence

    conn = db.connect()
    db.init(conn)
    settings = Settings()
    settings.update({"cloud": {"enabled": True, "folder": str(out), "viewer_url": ""}})
    presence = Presence(conn)
    sync = CloudSync(settings, lambda: presence.status_view(time.time()) | {"location": "大学", "paused": False})
    while True:
        try:
            sync.run_once(conn, time.time())
        except Exception as exc:
            print("書き出しでエラー:", exc, file=sys.stderr)
        time.sleep(5)


def make_handler(out: Path, viewer_version: str):
    config = f"window.LABLOG_CLOUD = {json.dumps({'dev': 'devdata/', 'version': viewer_version})};".encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, status, body: bytes, ctype="text/plain; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _data_path(self, rel: str) -> Path | None:
            path = (out / rel).resolve()
            return path if path.is_relative_to(out.resolve()) else None

        def do_GET(self):
            url = unquote(urlparse(self.path).path)
            if url == "/config.js":
                return self._send(200, config, TYPES[".js"])
            if url == "/devdata/edits/":
                names = sorted(p.name for p in (out / "edits").glob("*.json")) if (out / "edits").is_dir() else []
                return self._send(200, json.dumps(names).encode(), TYPES[".json"])
            if url.startswith("/devdata/"):
                path = self._data_path(url[len("/devdata/"):])
                if path and path.is_file():
                    return self._send(200, path.read_bytes(), TYPES[".json"])
                return self._send(404, b"not found")
            name = "index.html" if url == "/" else url.lstrip("/")
            file = WEB_DIR / name
            # 画面のファイルは web の直下と fonts フォルダ（同梱のフォント）だけを出す
            folder, _, base = name.rpartition("/")
            if folder in ("", "fonts") and "/" not in base and file.is_file() and file.suffix in TYPES:
                return self._send(200, file.read_bytes(), TYPES[file.suffix])
            self._send(404, b"not found")

        def do_PUT(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            url = unquote(urlparse(self.path).path)
            name = url[len("/devdata/edits/"):] if url.startswith("/devdata/edits/") else ""
            if not EDIT_NAME.match(name):
                return self._send(400, b"bad name")
            (out / "edits").mkdir(parents=True, exist_ok=True)
            (out / "edits" / name).write_bytes(body)
            self._send(201, b"{}", TYPES[".json"])

        def do_DELETE(self):  # ビューアーがサインインしたときに消す、連携を解除した印だけ
            if unquote(urlparse(self.path).path) != "/devdata/unlinked.json":
                return self._send(400, b"bad name")
            (out / "unlinked.json").unlink(missing_ok=True)
            self._send(204, b"")

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--source", default=str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LabLog"),
                        help="写す元の記録のフォルダ")
    parser.add_argument("--viewer-version", help="ビューアーのバージョン（初めは現在の LabLog のバージョン）")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="lablog_preview_"))
    snapshot(Path(args.source), work / "data")
    os.environ["LABLOG_DATA_DIR"] = str(work / "data")
    out = work / "onedrive"
    out.mkdir()
    sys.path.insert(0, str(ROOT))
    from lablog import __version__
    threading.Thread(target=sync_loop, args=(out,), daemon=True).start()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(out, args.viewer_version or __version__))
    print(f"ビューアーの試用: http://127.0.0.1:{args.port}/（書き出し先: {out}）", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

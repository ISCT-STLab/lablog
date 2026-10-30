"""記録のバックアップ（ZIP）と、そこからの戻し。PC を替えるときや、入れ直すときに記録を引き継ぐ。

ZIP の中身は、データのフォルダ（%LOCALAPPDATA%\\LabLog）と同じ名前のファイル：
  lablog.db（記録）・settings.json（設定）・ai.json（LabLog が入れた AI）
  アンインストールのときに作るもの（uninstall.ps1）は、lablog.db-wal も入る（DB をそのまま写すため）
ダッシュボードの合言葉（dashboard.key）は入れない（PC ごとのもの）。

戻すときは、動いている LabLog が DB を開いたままでは差し替えられないので、ZIP を restore.zip として置いて
起動し直し、DB を開く前に差し替える（main.py の apply_pending_restore）。差し替える前のデータは
before-restore-<日時> フォルダに残す。
"""

import io
import logging
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import db
from .config import data_dir

log = logging.getLogger(__name__)

FILES = ("lablog.db", "lablog.db-wal", "settings.json", "ai.json")
PENDING = "restore.zip"
MAX_BYTES = 1024 * 1024 * 1024  # 1 GB


def make_backup(conn) -> bytes:
    """いまの記録の ZIP。DB は SQLite の backup で写す（記録しながらでも、一貫した状態で写せる）。"""
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "lablog.db"
        dst = sqlite3.connect(snap)
        conn.backup(dst)
        dst.close()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "lablog.db")
            for name in ("settings.json", "ai.json"):
                path = data_dir() / name
                if path.is_file():
                    z.write(path, name)
        return buf.getvalue()


def check_backup(data: bytes) -> dict:
    """LabLog のバックアップとして戻せるか確かめる。戻せなければ ValueError（画面に出す文）。"""
    if len(data) > MAX_BYTES:
        raise ValueError("ファイルが大きすぎます")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        names = set(z.namelist())
    except zipfile.BadZipFile:
        raise ValueError("LabLog のバックアップ（ZIP）ではありません")
    if "lablog.db" not in names:
        raise ValueError("LabLog のバックアップ（ZIP）ではありません（lablog.db がありません）")
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("lablog.db", "lablog.db-wal"):
            if name in names:
                (Path(tmp) / name).write_bytes(z.read(name))
        try:
            conn = sqlite3.connect(Path(tmp) / "lablog.db")
            try:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                stays = conn.execute("SELECT COUNT(*) FROM stays").fetchone()[0]
                first = conn.execute("SELECT MIN(start_ts) FROM segments").fetchone()[0]
            finally:
                conn.close()
        except sqlite3.DatabaseError:
            raise ValueError("バックアップの記録（lablog.db）を読めません")
    if version > db.SCHEMA_VERSION:
        raise ValueError("新しいバージョンの LabLog で作ったバックアップです。LabLog を更新してから戻してください")
    return {"stays": stays, "since": first}


def queue_restore(data: bytes) -> dict:
    """戻す ZIP を置く。差し替えは、起動し直したときに行う。"""
    info = check_backup(data)
    tmp = data_dir() / (PENDING + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(data_dir() / PENDING)
    return info


def apply_pending_restore() -> bool:
    """置いてある ZIP で記録を差し替える（DB を開く前に呼ぶ）。差し替えたら True。"""
    pending = data_dir() / PENDING
    if not pending.is_file():
        return False
    try:
        with zipfile.ZipFile(pending) as z:
            content = {name: z.read(name) for name in FILES if name in z.namelist()}
    except (OSError, zipfile.BadZipFile):
        log.exception("戻すバックアップを読めませんでした。いまの記録のまま起動します")
        pending.unlink(missing_ok=True)
        return False
    keep = data_dir() / f"before-restore-{datetime.now():%Y%m%d-%H%M%S}"
    keep.mkdir()
    for name in FILES + ("lablog.db-shm",):
        path = data_dir() / name
        if path.exists():
            shutil.move(path, keep / name)
    for name, body in content.items():
        (data_dir() / name).write_bytes(body)
    pending.unlink()
    log.info("バックアップから記録を戻しました（前の記録：%s）", keep)
    return True

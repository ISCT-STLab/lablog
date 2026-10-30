"""記録のバックアップ（ZIP）と、そこからの戻し。PC を替えるときや、入れ直すときに記録を引き継ぐ。

ZIP の中身は、データのフォルダ（%LOCALAPPDATA%\\LabLog）と同じ名前のファイル：
  lablog.db（記録）・settings.json（設定）
  アンインストールのときに作るもの（uninstall.ps1）は、lablog.db-wal も入る（DB をそのまま写すため）
PC ごとのものは入れない：ダッシュボードの合言葉（dashboard.key）、LabLog が入れた AI の記録（ai.json。
別の PC に戻すと、その PC にもとからあった Ollama を、LabLog が入れたものと取り違える）。

戻すときは、動いている LabLog が DB を開いたままでは差し替えられないので、ZIP を restore.zip として置いて
起動し直し、DB を開く前に差し替える（main.py の apply_pending_restore）。差し替える前のデータは
before-restore-<日時> フォルダに残す。
"""

import io
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import db
from .config import data_dir

log = logging.getLogger(__name__)

FILES = ("lablog.db", "lablog.db-wal", "settings.json")
PENDING = "restore.zip"
MAX_BYTES = 1024 * 1024 * 1024  # 1 GB
MAX_UNZIPPED_BYTES = 4 * MAX_BYTES  # 展開したときの大きさ


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
            for name in ("settings.json",):
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
    if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED_BYTES:
        raise ValueError("ファイルが大きすぎます")
    if "settings.json" in names:
        try:
            if not isinstance(json.loads(z.read("settings.json").decode("utf-8")), dict):
                raise ValueError
        except ValueError:
            raise ValueError("バックアップの設定（settings.json）を読めません")
    # 別の場所に写して、起動するときと同じように DB を開いてみる（前のバージョンの DB なら、ここで新しい形に直す）
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("lablog.db", "lablog.db-wal"):
            if name in names:
                (Path(tmp) / name).write_bytes(z.read(name))
        conn = fresh = None
        try:
            conn = sqlite3.connect(Path(tmp) / "lablog.db")
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("バックアップの記録（lablog.db）が壊れています")
            if conn.execute("PRAGMA user_version").fetchone()[0] > db.SCHEMA_VERSION:
                raise ValueError("新しいバージョンの LabLog で作ったバックアップです。LabLog を更新してから戻してください")
            conn.close()
            conn = db.connect(Path(tmp) / "lablog.db")
            db.init(conn)
            fresh = db.connect(":memory:")
            db.init(fresh)
            for (table,) in fresh.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
                want = {r[1] for r in fresh.execute(f"PRAGMA table_info({table})")}
                have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                if not want <= have:
                    raise ValueError("バックアップの記録（lablog.db）の形が、LabLog のものと違います")
            stays = conn.execute("SELECT COUNT(*) FROM stays").fetchone()[0]
            first = conn.execute("SELECT MIN(start_ts) FROM segments").fetchone()[0]
        except sqlite3.DatabaseError:
            raise ValueError("バックアップの記録（lablog.db）を読めません")
        finally:
            for c in (conn, fresh):
                if c is not None:
                    c.close()
    return {"stays": stays, "since": first}


def queue_restore(data: bytes) -> dict:
    """戻す ZIP を置く。差し替えは、起動し直したときに行う。"""
    info = check_backup(data)
    tmp = data_dir() / (PENDING + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(data_dir() / PENDING)
    return info


def _forget_other_pc(db_file: Path):
    """戻した記録が別の PC のもの（または、どの PC のものか分からない前のバージョンのもの）なら、
    スマホ版へ書き出す PC の印を作り直させる（cloud.py の _device）。前の印は、同じ PC だったと分かったときのために残す。"""
    from .cloud import machine_id
    try:
        conn = sqlite3.connect(db_file)
        try:
            kv = dict(conn.execute("SELECT key, value FROM kv WHERE key IN ('device_id', 'device_machine')").fetchall())
            if kv.get("device_id") and kv.get("device_machine") != machine_id():
                conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES ('device_id_before_restore', ?)",
                             (kv["device_id"],))
                conn.execute("DELETE FROM kv WHERE key IN ('device_id', 'device_machine')")
                conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        log.exception("戻した記録の、スマホ版へ書き出す PC の印を確かめられませんでした")


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
    # 1. 戻すファイルを、まず別のフォルダに書き出す（ここで失敗しても、いまの記録には触れていない）
    stage = data_dir() / "restore-staging"
    shutil.rmtree(stage, ignore_errors=True)
    try:
        stage.mkdir()
        for name, body in content.items():
            (stage / name).write_bytes(body)
    except OSError:
        log.exception("バックアップを書き出せませんでした。いまの記録のまま起動します")
        shutil.rmtree(stage, ignore_errors=True)
        pending.unlink(missing_ok=True)
        return False
    # 2. いまの記録を退避して、3. 戻すファイルと入れ替える。途中で失敗したら、置いたものだけを消して、退避したものを戻す
    #   （退避できなかったものは、元の場所にそのまま残っている）
    keep = data_dir() / f"before-restore-{datetime.now():%Y%m%d-%H%M%S}"
    keep.mkdir()
    moved, placed = [], []
    try:
        for name in FILES + ("lablog.db-shm",):
            path = data_dir() / name
            if path.exists():
                shutil.move(path, keep / name)
                moved.append(name)
        for name in content:
            os.replace(stage / name, data_dir() / name)
            placed.append(name)
    except OSError:
        log.exception("バックアップから戻せませんでした。いまの記録のまま起動します")
        for name in placed:
            (data_dir() / name).unlink(missing_ok=True)
        for name in moved:
            try:
                shutil.move(keep / name, data_dir() / name)
            except OSError:
                log.exception("退避した記録を戻せませんでした（%s に残っています）", keep / name)
        shutil.rmtree(stage, ignore_errors=True)
        pending.unlink(missing_ok=True)
        return False
    shutil.rmtree(stage, ignore_errors=True)
    _forget_other_pc(data_dir() / "lablog.db")
    pending.unlink()
    log.info("バックアップから記録を戻しました（前の記録：%s）", keep)
    return True

"""SQLite の接続とテーブル定義。"""

import sqlite3
import threading
from pathlib import Path

from .config import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- 大学での滞在。status: in（在学）/ tentative（仮退出）/ closed（確定）
CREATE TABLE IF NOT EXISTS stays (
    id              INTEGER PRIMARY KEY,
    start_ts        REAL NOT NULL,
    end_ts          REAL,
    last_seen_ts    REAL,
    tentative_since REAL,
    status          TEXT NOT NULL,
    end_reason      TEXT,
    manual          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_stays_start ON stays(start_ts);

-- 前面ウィンドウの記録。idle=1 は離席（一定時間操作なし）。
CREATE TABLE IF NOT EXISTS segments (
    id       INTEGER PRIMARY KEY,
    start_ts REAL NOT NULL,
    end_ts   REAL NOT NULL,
    exe      TEXT NOT NULL DEFAULT '',
    title    TEXT NOT NULL DEFAULT '',
    idle     INTEGER NOT NULL DEFAULT 0,
    location TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_segments_start ON segments(start_ts);
CREATE INDEX IF NOT EXISTS idx_segments_end ON segments(end_ts);

CREATE TABLE IF NOT EXISTS file_events (
    id       INTEGER PRIMARY KEY,
    ts       REAL NOT NULL,
    folder   TEXT NOT NULL,
    path     TEXT NOT NULL,
    action   TEXT NOT NULL,
    size     INTEGER,
    old_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_file_events_ts ON file_events(ts);

CREATE TABLE IF NOT EXISTS web_visits (
    id       INTEGER PRIMARY KEY,
    ts       REAL NOT NULL,
    browser  TEXT NOT NULL,
    profile  TEXT NOT NULL,
    visit_id INTEGER NOT NULL,
    url      TEXT NOT NULL,
    title    TEXT NOT NULL DEFAULT '',
    domain   TEXT NOT NULL DEFAULT '',
    UNIQUE (browser, profile, visit_id)
);
CREATE INDEX IF NOT EXISTS idx_web_visits_ts ON web_visits(ts);

CREATE TABLE IF NOT EXISTS recent_files (
    id   INTEGER PRIMARY KEY,
    ts   REAL NOT NULL,
    path TEXT NOT NULL,
    UNIQUE (path, ts)
);
CREATE INDEX IF NOT EXISTS idx_recent_files_ts ON recent_files(ts);

CREATE TABLE IF NOT EXISTS commits (
    repo    TEXT NOT NULL,
    hash    TEXT NOT NULL,
    ts      REAL NOT NULL,
    author  TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (repo, hash)
);
CREATE INDEX IF NOT EXISTS idx_commits_ts ON commits(ts);

CREATE TABLE IF NOT EXISTS repo_state (
    repo             TEXT PRIMARY KEY,
    branch           TEXT,
    dirty_files      INTEGER NOT NULL DEFAULT 0,
    dirty_since      REAL,
    last_commit_ts   REAL,
    last_notified_ts REAL,
    checked_ts       REAL
);

CREATE TABLE IF NOT EXISTS pauses (
    id       INTEGER PRIMARY KEY,
    start_ts REAL NOT NULL,
    end_ts   REAL
);

CREATE TABLE IF NOT EXISTS reports (
    date         TEXT PRIMARY KEY,
    text         TEXT NOT NULL DEFAULT '',
    edited       INTEGER NOT NULL DEFAULT 0,
    generated_ts REAL,
    source       TEXT
);

-- Claude Code の会話（題名と作業フォルダだけ。会話の中身は保存しない）
CREATE TABLE IF NOT EXISTS ai_sessions (
    source     TEXT NOT NULL,
    session_id TEXT NOT NULL,
    title      TEXT,
    folder     TEXT,
    first_ts   REAL,
    last_ts    REAL,
    PRIMARY KEY (source, session_id)
);
-- 会話でやりとりがあった時刻（分単位）
CREATE TABLE IF NOT EXISTS ai_activity (
    source     TEXT NOT NULL,
    session_id TEXT NOT NULL,
    minute     REAL NOT NULL,
    PRIMARY KEY (source, session_id, minute)
);
CREATE INDEX IF NOT EXISTS idx_ai_activity_minute ON ai_activity(minute);

-- 画面から読み取った文字（日報の材料。設定の日数を過ぎたら消す。画像は保存しない）
CREATE TABLE IF NOT EXISTS screen_texts (
    id    INTEGER PRIMARY KEY,
    ts    REAL NOT NULL,
    exe   TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    text  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_screen_texts_ts ON screen_texts(ts);
"""


def db_path() -> Path:
    return data_dir() / "lablog.db"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or db_path()), timeout=15, isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)


_local = threading.local()


def thread_conn() -> sqlite3.Connection:
    """スレッドごとに 1 本の接続を使い回す。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = connect()
        _local.conn = conn
    return conn


def kv_get(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def kv_set(conn, key: str, value):
    conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


def paused_ranges(conn, t0: float, t1: float, now: float) -> list[tuple[float, float]]:
    rows = conn.execute("SELECT start_ts, end_ts FROM pauses WHERE start_ts < ? AND "
                        "(end_ts IS NULL OR end_ts > ?)", (t1, t0)).fetchall()
    return [(r["start_ts"], r["end_ts"] if r["end_ts"] is not None else now) for r in rows]


def in_ranges(ts: float, ranges: list[tuple[float, float]]) -> bool:
    return any(a <= ts < b for a, b in ranges)

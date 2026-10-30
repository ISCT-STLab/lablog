"""定期的にまとめて取り込む材料：ブラウザ履歴・最近使ったファイル・Git。"""

import json
import logging
import os
import shutil
import sqlite3
import struct
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

from .config import LOCAL_HOSTS, data_dir
from .db import in_ranges, kv_get, kv_set, paused_ranges
from .days import day_range, logical_date
from .filewatch import IGNORE_DIRS
from .winapi import run_quiet

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- ブラウザ履歴

BROWSERS = {
    "chrome": ("Chrome", Path("Google") / "Chrome" / "User Data"),
    "edge": ("Edge", Path("Microsoft") / "Edge" / "User Data"),
}
CHROME_EPOCH_OFFSET = 11644473600  # 1601-01-01 から 1970-01-01 までの秒数
SKIP_SCHEMES = {"chrome", "edge", "about", "chrome-extension", "extension", "devtools", "data", "blob",
                "chrome-search", "edge-extension"}


def first_since(conn, now: float) -> float:
    """初めて取り込むときに、どの時刻から読むか。

    今日（朝 5 時）からの分。ただし初回の設定を終える前の分は読まない（記録してよいか、まだ聞いていないため）。
    """
    return max(day_range(logical_date(now))[0], float(kv_get(conn, "record_since", 0) or 0))


def track_sources(conn, states: dict[str, bool], now: float):
    """記録の材料（ブラウザ・Git など）ごとに、オフからオンにした時刻を覚える（設定を変えたときと、取り込みの前に呼ぶ）。

    取り込みは前回読んだ位置から続きを読むので、覚えておかないと、オフにしていたあいだの分までオンに戻したときに取り込む。
    """
    for name, on in states.items():
        prev = kv_get(conn, f"source:{name}")
        if on and prev != "on":
            kv_set(conn, f"source:{name}", "on")
            if prev == "off":
                kv_set(conn, f"source:{name}:since", now)
        elif not on and prev != "off":
            kv_set(conn, f"source:{name}", "off")


def enabled_since(conn, name: str) -> float:
    """この材料を最後にオンにした時刻。これより前の分は取り込まない（オフにしていた期間）。"""
    return float(kv_get(conn, f"source:{name}:since", 0) or 0)


def _strip_dashboard_key(url: str, parsed) -> str:
    """LabLog がダッシュボードを開いた URL（?key=合言葉）から、合言葉を消す。"""
    if parsed.hostname not in LOCAL_HOSTS or "key=" not in parsed.query:
        return url
    query = urlencode([(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "key"])
    return parsed._replace(query=query).geturl()


def _browser_profiles(base: Path):
    if not base.is_dir():
        return
    for child in base.iterdir():
        if (child / "History").is_file():
            yield child.name, child / "History"


def import_browser_history(conn, browsers_enabled: dict, now: float):
    tmp_dir = data_dir() / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    for key, (label, rel) in BROWSERS.items():
        if not browsers_enabled.get(key, True):
            continue
        for profile, src in _browser_profiles(local / rel):
            try:
                _import_profile(conn, key, label, profile, src, tmp_dir, now)
            except Exception:
                log.exception("%s (%s) の履歴を読み込めませんでした", label, profile)


def _import_profile(conn, key, label, profile, src: Path, tmp_dir: Path, now: float):
    state_key = f"hist:{key}:{profile}"
    mtime = src.stat().st_mtime
    if kv_get(conn, state_key + ":mtime") == str(mtime):
        return
    copy = tmp_dir / f"history_{key}_{profile.replace(' ', '_')}.sqlite"
    shutil.copyfile(src, copy)  # ブラウザが開いていても読めるようにコピーしてから読む
    last_id = int(kv_get(conn, state_key + ":last_id", -1))
    floor = enabled_since(conn, f"browser:{key}")
    since = max(first_since(conn, now), floor)  # 初回は今日（朝 5 時以降）の分だけ取り込む
    src_conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    try:
        max_id = src_conn.execute("SELECT MAX(id) FROM visits").fetchone()[0] or 0
        if last_id > max_id:  # 履歴が削除されて番号が振り直された
            last_id = -1
            since = float(kv_get(conn, state_key + ":last_ts", since))
        if last_id < 0:
            chrome_since = int((since + CHROME_EPOCH_OFFSET) * 1_000_000)
            rows = src_conn.execute(
                "SELECT v.id, v.visit_time, v.transition, u.url, u.title FROM visits v "
                "JOIN urls u ON u.id = v.url WHERE v.visit_time >= ? ORDER BY v.id", (chrome_since,)).fetchall()
        else:
            rows = src_conn.execute(
                "SELECT v.id, v.visit_time, v.transition, u.url, u.title FROM visits v "
                "JOIN urls u ON u.id = v.url WHERE v.id > ? ORDER BY v.id", (last_id,)).fetchall()
    finally:
        src_conn.close()
        try:
            copy.unlink()
        except OSError:
            pass

    pauses = paused_ranges(conn, rows[0][1] / 1e6 - CHROME_EPOCH_OFFSET, now, now) if rows else []
    batch = []
    for visit_id, visit_time, transition, url, title in rows:
        last_id = max(last_id, visit_id)
        ts = visit_time / 1_000_000 - CHROME_EPOCH_OFFSET
        core = transition & 0xFF
        redirect = transition & 0xC0000000
        if core in (3, 4) or (redirect and not transition & 0x20000000):
            continue  # 埋め込みフレームや、途中で転送されたページ
        parsed = urlparse(url)
        if parsed.scheme in SKIP_SCHEMES or ts < floor or in_ranges(ts, pauses):
            continue
        domain =(parsed.hostname or ("file" if parsed.scheme == "file" else "")).removeprefix("www.")
        batch.append((ts, label, profile, visit_id, _strip_dashboard_key(url, parsed), title or "", domain))
    if batch:
        conn.executemany("INSERT OR IGNORE INTO web_visits (ts, browser, profile, visit_id, url, title, domain) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
        kv_set(conn, state_key + ":last_ts", batch[-1][0])
    kv_set(conn, state_key + ":last_id", last_id)
    kv_set(conn, state_key + ":mtime", mtime)


# ---------------------------------------------------------------- Claude Code の会話

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CLAUDE_ACTIVITY = {"user", "assistant"}


def _iso_ts(text) -> float | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def import_claude_code(conn, now: float, root: Path | None = None):
    """Claude Code の会話記録から、会話の題名・作業フォルダ・やりとりした時刻（分単位）だけを取り込む。

    会話の中身は保存しない。ファイルは前回読んだ位置から続きだけを読む。初回は今日（朝 5 時以降）の分だけ。
    """
    root = root or CLAUDE_PROJECTS
    if not root.is_dir():
        return
    since = first_since(conn, now)
    floor = enabled_since(conn, "claude_code")
    pauses = paused_ranges(conn, 0, now, now)
    paused_now = conn.execute("SELECT 1 FROM pauses WHERE end_ts IS NULL").fetchone() is not None
    # 前回すべてのファイルを読んだ時刻。それより後に書き足された分は、前回から今までに書かれたもの
    scanned = float(kv_get(conn, "claude:scanned_at", 0) or 0)
    for path in root.glob("*/*.jsonl"):
        key = f"claude:{path}"
        size = path.stat().st_size
        offset = int(kv_get(conn, key, -1))
        first = offset < 0
        if offset > size:  # ファイルが作り直された
            offset = 0
        if offset == size:
            continue
        with open(path, "rb") as fh:
            fh.seek(max(offset, 0))
            chunk = fh.read(size - max(offset, 0))
        end = chunk.rfind(b"\n") + 1  # 書きかけの最後の行は次回に回す
        if not end:
            continue
        lines = []
        for raw in chunk[:end].splitlines():
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            if isinstance(line, dict) and isinstance(line.get("sessionId"), str):
                lines.append(line)
        stamps = [_iso_ts(line.get("timestamp")) for line in lines]
        # 題名の変更の行には時刻が無いので、前（無ければ後）の行の時刻で、記録してよい時間に付けたものかを決める
        near, prev = [], None
        for ts in stamps:
            prev = ts if ts is not None else prev
            near.append(prev)
        nxt = None
        for i in range(len(stamps) - 1, -1, -1):
            nxt = stamps[i] if stamps[i] is not None else nxt
            near[i] = near[i] if near[i] is not None else nxt

        def allowed(ts):  # 初回の設定より前・オフにしていた期間・一時停止中の分は取り込まない
            return ts is not None and not (first and ts < since) and ts >= floor and not in_ranges(ts, pauses)

        # 題名の変更の行には時刻が無いので、いつ付けたかは「前回読んでから今まで」としか分からない。
        # そのあいだに一時停止やオフがあれば（いま止めていても）、題名は取り込まない（前後の行の時刻は根拠にしない）
        read_from = scanned or since  # 前回の読み込みのあとにできたファイルも、前回から今までに書かれたもの
        titles_ok = (not paused_now and floor <= read_from
                     and not paused_ranges(conn, read_from, now, now))

        sessions: dict[str, dict] = {}
        minutes: set[tuple[str, float]] = set()
        for line, ts, ref in zip(lines, stamps, near):
            sid = line["sessionId"]
            s = sessions.setdefault(sid, {"title": None, "folder": None, "first": None, "last": None})
            kind = line.get("type")
            if kind in ("custom-title", "agent-name") and not (titles_ok and allowed(ref)):
                continue
            if kind == "custom-title" and isinstance(line.get("customTitle"), str):
                s["title"] = line["customTitle"].strip()[:100] or s["title"]
            elif kind == "agent-name" and isinstance(line.get("agentName"), str) and not s["title"]:
                s["title"] = line["agentName"].strip()[:100] or None
            elif kind in CLAUDE_ACTIVITY:
                if ts is None or not allowed(ts):
                    continue
                minutes.add((sid, ts // 60 * 60))
                if isinstance(line.get("cwd"), str):
                    s["folder"] = os.path.basename(line["cwd"].rstrip("\\/")) or None
                s["first"] = min(s["first"] or ts, ts)
                s["last"] = max(s["last"] or ts, ts)
        for sid, s in sessions.items():
            if not (s["title"] or s["first"]):
                continue
            conn.execute(
                "INSERT INTO ai_sessions (source, session_id, title, folder, first_ts, last_ts) "
                "VALUES ('claude-code', ?, ?, ?, ?, ?) ON CONFLICT(source, session_id) DO UPDATE SET "
                "title = COALESCE(excluded.title, title), folder = COALESCE(excluded.folder, folder), "
                "first_ts = MIN(COALESCE(first_ts, excluded.first_ts), COALESCE(excluded.first_ts, first_ts)), "
                "last_ts = MAX(COALESCE(last_ts, excluded.last_ts), COALESCE(excluded.last_ts, last_ts))",
                (sid, s["title"], s["folder"], s["first"], s["last"]))
        conn.executemany("INSERT OR IGNORE INTO ai_activity (source, session_id, minute) VALUES ('claude-code', ?, ?)",
                         sorted(minutes))
        kv_set(conn, key, max(offset, 0) + end)
    kv_set(conn, "claude:scanned_at", now)


# ---------------------------------------------------------------- 最近使ったファイル

def _lnk_target(path: Path) -> str | None:
    """ショートカット（.lnk）からリンク先のパスを読む（MS-SHLLINK 形式）。"""
    data = path.read_bytes()
    if len(data) < 0x4C or data[:4] != b"\x4c\x00\x00\x00":
        return None
    flags = struct.unpack_from("<I", data, 0x14)[0]
    pos = 0x4C
    if flags & 0x01:  # HasLinkTargetIDList
        pos += 2 + struct.unpack_from("<H", data, pos)[0]
    if not flags & 0x02:  # HasLinkInfo
        return None
    info = pos
    header_size = struct.unpack_from("<I", data, info + 4)[0]
    local_off, _, suffix_off = struct.unpack_from("<III", data, info + 16)

    def read_ansi(off):
        end = data.index(b"\x00", off)
        return data[off:end].decode("mbcs", errors="replace")

    def read_unicode(off):
        end = off
        while data[end:end + 2] != b"\x00\x00":
            if end + 2 > len(data):  # 終わりの印が無い壊れたファイル（読み続けて止まらないように）
                raise ValueError("文字列の終わりがありません")
            end += 2
        return data[off:end].decode("utf-16-le", errors="replace")

    if header_size >= 0x24:
        local_u, suffix_u = struct.unpack_from("<II", data, info + 28)
        if local_u:
            base = read_unicode(info + local_u)
            suffix = read_unicode(info + suffix_u) if suffix_u else ""
            return base + suffix
    if not local_off:
        return None
    base = read_ansi(info + local_off)
    suffix = read_ansi(info + suffix_off) if suffix_off else ""
    return base + suffix


def import_recent_files(conn, now: float):
    recent = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
    if not recent.is_dir():
        return
    last = max(float(kv_get(conn, "recent:last_mtime", first_since(conn, now))), enabled_since(conn, "recent_files"))
    pauses = paused_ranges(conn, last, now, now)
    newest = last
    batch = []
    for lnk in recent.glob("*.lnk"):
        try:
            mtime = lnk.stat().st_mtime
            if mtime <= last or in_ranges(mtime, pauses):
                continue
            target = _lnk_target(lnk)
        except (OSError, ValueError, struct.error):
            continue
        newest = max(newest, mtime)
        if not target or os.path.isdir(target):
            continue
        batch.append((mtime, target))
    if batch:
        conn.executemany("INSERT OR IGNORE INTO recent_files (ts, path) VALUES (?, ?)", batch)
    kv_set(conn, "recent:last_mtime", newest)


# ---------------------------------------------------------------- Git

def _git(repo: str, *args: str, timeout: float = 30):
    return run_quiet(["git", "-c", "core.quotepath=false", "-c", "i18n.logOutputEncoding=utf-8",
                      "-C", repo, *args], timeout=timeout)


def discover_repos(folders: list[str], max_depth: int = 4) -> list[str]:
    repos: set[str] = set()
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        try:
            top = _git(folder, "rev-parse", "--show-toplevel", timeout=15)
            if top.returncode == 0 and top.stdout.strip():
                repos.add(os.path.normpath(top.stdout.strip()))
        except Exception:
            pass
        root_depth = folder.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, _ in os.walk(folder):
            if ".git" in dirnames or os.path.isfile(os.path.join(dirpath, ".git")):
                repos.add(os.path.normpath(dirpath))
            depth = dirpath.count(os.sep) - root_depth
            dirnames[:] = [] if depth >= max_depth else [
                d for d in dirnames if d.lower() not in IGNORE_DIRS and not d.startswith(".")]
    return sorted(repos)


def poll_repo(conn, repo: str, now: float):
    state = conn.execute("SELECT * FROM repo_state WHERE repo = ?", (repo,)).fetchone()
    floor = enabled_since(conn, "git")
    since_ts = max(float(kv_get(conn, f"git:{repo}:since", first_since(conn, now))), floor)
    pauses = paused_ranges(conn, since_ts - 60, now, now)
    email = _git(repo, "config", "user.email", timeout=10).stdout.strip()
    args = ["log", "--all", f"--since={datetime.fromtimestamp(since_ts - 60).isoformat(timespec='seconds')}",
            "--format=%H%x1f%ct%x1f%ae%x1f%s"]
    if email:
        args.append(f"--author={email}")
    result = _git(repo, *args)
    if result.returncode == 0:
        rows = []
        for line in result.stdout.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                ts = float(parts[1])
                if ts < floor or in_ranges(ts, pauses):  # オフにしていた期間・一時停止中のコミットは取り込まない
                    continue
                rows.append((repo, parts[0], ts, parts[2], parts[3]))
        if rows:
            conn.executemany("INSERT OR IGNORE INTO commits (repo, hash, ts, author, subject) "
                             "VALUES (?, ?, ?, ?, ?)", rows)
        kv_set(conn, f"git:{repo}:since", now)

    status = _git(repo, "status", "--porcelain=v1")
    dirty = len([l for l in status.stdout.splitlines() if l.strip()]) if status.returncode == 0 else 0
    last_commit = _git(repo, "log", "-1", "--format=%ct").stdout.strip()
    last_commit_ts = float(last_commit) if last_commit else None
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    dirty_since = None
    if dirty:
        prev = state["dirty_since"] if state and state["dirty_since"] else None
        dirty_since = prev or now
    conn.execute(
        "INSERT INTO repo_state (repo, branch, dirty_files, dirty_since, last_commit_ts, checked_ts) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(repo) DO UPDATE SET branch = excluded.branch, "
        "dirty_files = excluded.dirty_files, dirty_since = excluded.dirty_since, "
        "last_commit_ts = excluded.last_commit_ts, checked_ts = excluded.checked_ts",
        (repo, branch, dirty, dirty_since, last_commit_ts, now))


def commit_reminders(conn, repos: list[str], hours: float, now: float) -> list[dict]:
    """作業しているのに長くコミットしていないリポジトリ（通知する分）を返し、通知時刻を記録する。"""
    threshold = hours * 3600
    due = []
    for repo in repos:
        st = conn.execute("SELECT * FROM repo_state WHERE repo = ?", (repo,)).fetchone()
        if not st or not st["dirty_files"]:
            continue
        uncommitted_from = max(st["dirty_since"] or now, st["last_commit_ts"] or 0)
        if now - uncommitted_from < threshold:
            continue
        if st["last_notified_ts"] and now - st["last_notified_ts"] < threshold:
            continue
        # 実際に作業している（最近ファイルを編集した）リポジトリだけ知らせる
        prefix = repo.rstrip("\\") + "\\"
        edited = conn.execute("SELECT 1 FROM file_events WHERE ts > ? AND path LIKE ? ESCAPE '|' LIMIT 1",
                              (now - threshold, _like_prefix(prefix))).fetchone()
        if not edited:
            continue
        conn.execute("UPDATE repo_state SET last_notified_ts = ? WHERE repo = ?", (now, repo))
        due.append({"repo": repo, "dirty_files": st["dirty_files"], "since": uncommitted_from})
    return due


def _like_prefix(prefix: str) -> str:
    return prefix.replace("|", "||").replace("%", "|%").replace("_", "|_") + "%"

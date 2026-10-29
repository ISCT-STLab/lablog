"""要約した記録を OneDrive に書き出し、iPhone・iPad から直した内容を取り込む。

書き出すのは要約（入退室・PC作業時間・分類別の時間・タイムラインの分類・日報）だけ。
ウィンドウ名・閲覧履歴・ファイルのパスなどの元の記録は、この PC の外に出さない。

書き出し先は iPhone・iPad 用ビューアーの OneDrive アプリフォルダ（OneDrive\\アプリ\\LabLog）。
ビューアーで初めてサインインしたときに OneDrive が作り、ビューアーが目印の viewer.json を置く。

  meta.json            状態・最終更新時刻
  days/YYYY-MM-DD.json 1 日分の要約
  months/YYYY-MM.json  1 か月分の集計
  edits/*.json         ビューアーで直した日報・滞在（取り込んだら消す）
"""

import hashlib
import json
import logging
import os
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from . import db
from .categorize import Categorizer
from .config import DAY_START_HOUR
from .days import day_range, logical_date, parse_date
from .presence import delete_stay, save_stay
from .report import save_report
from .summary import day_summary, month_summary

log = logging.getLogger(__name__)
MARKER_APP = "LabLog viewer"

FORMAT_VERSION = 1
MARKER = "viewer.json"
CHECK_SEC = 30          # 直した内容の確認・書き出しの間隔
PERIODIC_SEC = 300      # 今日の分を書き出し直す間隔
DETECT_SEC = 300        # 書き出し先が見つからないときに探し直す間隔
REJECT_AFTER_SEC = 600  # 読めない修正ファイルを、同期の途中ではないとみなすまでの時間
MAX_REPORT_CHARS = 50_000
MIN_TS, MAX_TS = 946_684_800, 4_102_444_800  # 2000〜2100 年

# クラウドに置く 1 日分の項目（apps・files・web など元の記録に近いものは含めない）
DAY_KEYS = ("date", "date_label", "range", "now", "stays", "stay_total", "work_total", "work_by_location",
            "categories", "category_names")
REPORT_KEYS = ("text", "edited", "generated_ts", "source")


def onedrive_root() -> Path | None:
    """個人用 OneDrive のフォルダ（ビューアーは個人の Microsoft アカウントでサインインする）。"""
    for root in (os.environ.get("OneDriveConsumer"), Path.home() / "OneDrive"):
        if root and os.path.isdir(root):
            return Path(root)
    return None


def _is_viewer_marker(path: Path) -> bool:
    try:
        return path.is_file() and json.loads(path.read_text(encoding="utf-8")).get("app") == MARKER_APP
    except (OSError, ValueError, AttributeError):
        return False


def find_viewer_folder(root: Path | None = None) -> Path | None:
    """ビューアーが作ったアプリフォルダ（目印の viewer.json があるもの）を探す。

    フォルダ名はふつう「アプリ\LabLog」だが、OneDrive が「Graph」などの名前で作ることもあるので、名前では決めない。
    """
    root = root or onedrive_root()
    if root is None:
        return None
    found = []
    for apps in root.iterdir():  # 日本語の環境では「アプリ」、英語では「Apps」
        try:
            if apps.is_dir():
                found += [sub for sub in apps.iterdir() if _is_viewer_marker(sub / MARKER)]
        except OSError:
            continue
    return max(found, key=lambda p: (p / MARKER).stat().st_mtime) if found else None


def cloud_day(s: dict) -> dict:
    """1 日分の集計から、クラウドに置いてよい要約だけを取り出す。"""
    out = {k: s[k] for k in DAY_KEYS}
    out["timeline"] = [{"cat": b["cat"], "start": b["start"], "end": b["end"]} for b in s["timeline"]]
    out["report"] = {k: s["report"][k] for k in REPORT_KEYS} if s["report"] else None
    return out


def _ts(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not MIN_TS <= value <= MAX_TS:
        raise ValueError("時刻の形式が正しくありません")
    return float(value)


def _stay_id(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("滞在の指定が正しくありません")
    return value


def apply_edit(conn, edit: dict) -> set[date]:
    """ビューアーで直した内容を 1 件取り込み、書き出し直す日を返す。"""
    kind = edit.get("type")
    if kind == "report":
        d = parse_date(edit["date"])
        text = edit["text"]
        if not isinstance(text, str) or len(text) > MAX_REPORT_CHARS:
            raise ValueError("日報の内容が正しくありません")
        save_report(conn, d, text)
        return {d}
    if kind in ("stay_save", "stay_delete"):
        stay_id = None if kind == "stay_save" and edit.get("id") is None else _stay_id(edit.get("id"))
        days = set()
        if stay_id is not None:
            row = conn.execute("SELECT start_ts FROM stays WHERE id = ?", (stay_id,)).fetchone()
            if row is None:
                if kind == "stay_delete":
                    return set()  # もう消えている
                raise ValueError("直そうとした滞在が見つかりません")
            days.add(logical_date(row["start_ts"]))
        if kind == "stay_delete":
            delete_stay(conn, stay_id)
            return days
        start = _ts(edit["start"])
        end = _ts(edit["end"]) if edit.get("end") is not None else None
        save_stay(conn, stay_id, start, end)
        return days | {logical_date(start)}
    raise ValueError(f"不明な修正です: {kind}")


class CloudSync:
    """OneDrive への書き出しと、ビューアーからの修正の取り込み（専用スレッドで動かす）。"""

    def __init__(self, settings, status_fn):
        self.settings = settings
        self.status_fn = status_fn
        self.folder: Path | None = None
        self.last_export: float | None = None
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._dirty: set[date] = set()
        self._force = False
        self._full = True  # 起動直後は全期間を確かめる
        self._written: dict[str, str] = {}  # 書き出したファイルの中身のハッシュ
        self._last_periodic = 0.0
        self._last_detect = 0.0

    def mark_dirty(self, *days: date):
        with self._lock:
            self._dirty.update(days)
        self._wake.set()

    def request_export(self):
        with self._lock:
            self._force = True
        self._wake.set()

    def _cfg(self) -> dict:
        return {"enabled": True, "folder": "", "viewer_url": ""} | (self.settings.get("cloud") or {})

    def status(self) -> dict:
        cfg = self._cfg()
        return {"enabled": cfg["enabled"], "viewer_url": cfg["viewer_url"],
                "folder": str(self.folder) if self.folder else None,
                "last_export": self.last_export, "last_error": self.last_error}

    def loop(self):
        conn = db.connect()
        while True:
            self._wake.clear()
            try:
                self.run_once(conn, time.time())
                self.last_error = None
            except Exception as exc:
                log.exception("OneDrive への書き出しでエラーが起きました")
                self.last_error = str(exc)
            self._wake.wait(CHECK_SEC)

    def _resolve_folder(self, now: float, force: bool) -> Path | None:
        manual = self._cfg()["folder"]
        if manual:
            folder = Path(manual) if os.path.isdir(manual) else None
        elif self.folder is not None and (self.folder / MARKER).is_file():
            folder = self.folder
        elif force or now - self._last_detect >= DETECT_SEC:
            self._last_detect = now
            folder = find_viewer_folder()
        else:
            folder = None
        if folder != self.folder:
            if folder:
                log.info("OneDrive の書き出し先: %s", folder)
            self.folder = folder
            self._written.clear()
            self._full = True
        return folder

    def run_once(self, conn, now: float):
        with self._lock:
            force, self._force = self._force, False
        if not self._cfg()["enabled"]:
            self.folder = None
            return
        folder = self._resolve_folder(now, force)
        if folder is None:
            return
        changed = self.import_edits(conn, folder, now)
        with self._lock:
            days, self._dirty = self._dirty | changed, set()
            full, self._full = self._full, False
        today = logical_date(now)
        periodic = force or full or now - self._last_periodic >= PERIODIC_SEC
        if periodic:
            self._last_periodic = now
            days |= {today, today - timedelta(days=1)}
        if full:
            days |= self._all_days(conn, today)
        if days or periodic:
            self.export(conn, folder, days, now)

    def _all_days(self, conn, today: date) -> set[date]:
        row = conn.execute("SELECT MIN(t) AS t FROM (SELECT MIN(start_ts) AS t FROM stays "
                           "UNION ALL SELECT MIN(start_ts) FROM segments)").fetchone()
        first = logical_date(row["t"]) if row["t"] else today
        reported = conn.execute("SELECT MIN(date) AS d FROM reports").fetchone()["d"]
        if reported:
            first = min(first, parse_date(reported))
        first = max(first, today - timedelta(days=3 * 366))
        return {first + timedelta(days=i) for i in range((today - first).days + 1)}

    def export(self, conn, folder: Path, days: set[date], now: float):
        today = logical_date(now)
        for d in sorted(x for x in days if x <= today):
            s = cloud_day(day_summary(conn, self.settings, d, now))
            s["now"] = min(now, day_range(d)[1])
            path = folder / "days" / f"{d.isoformat()}.json"
            # 記録の無い日はファイルを作らない（前に書き出していれば、消えたことを反映する）
            if s["stays"] or s["work_total"] or s["report"] or path.exists():
                self._write(path, s, ignore=("now",))
        for y, m in sorted({(d.year, d.month) for d in days if d <= today}):
            self._write(folder / "months" / f"{y:04d}-{m:02d}.json", month_summary(conn, self.settings, y, m, now))
        status = self.status_fn()
        self._write(folder / "meta.json", {
            "version": FORMAT_VERSION, "exported": now, "today": today.isoformat(),
            "day_start_hour": DAY_START_HOUR,
            "status": {k: status.get(k) for k in ("state", "since", "location", "paused")},
            "category_names": Categorizer(self.settings.get("categories")).names,
        })
        self.last_export = now

    def _write(self, path: Path, obj: dict, ignore=()):
        """中身が変わったときだけ書く（OneDrive の同期を無駄に増やさない）。"""
        key = str(path)
        digest = hashlib.sha1(json.dumps({k: v for k, v in obj.items() if k not in ignore},
                                         ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        if key not in self._written and path.exists():
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
                self._written[key] = hashlib.sha1(json.dumps({k: v for k, v in old.items() if k not in ignore},
                                                             ensure_ascii=False, sort_keys=True)
                                                  .encode("utf-8")).hexdigest()
            except (OSError, ValueError):
                pass
        if self._written.get(key) == digest:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name("~$" + path.name)  # 書きかけのファイルが同期されないよう、別名で書いてから置き換える
        tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
        self._written[key] = digest

    def import_edits(self, conn, folder: Path, now: float) -> set[date]:
        edits_dir = folder / "edits"
        if not edits_dir.is_dir():
            return set()
        items = []
        for path in edits_dir.glob("*.json"):
            try:
                edit = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(edit, dict):
                    raise ValueError("形式が正しくありません")
                items.append((edit.get("ts") if isinstance(edit.get("ts"), (int, float)) else 0, path.name, path, edit))
            except (OSError, ValueError):
                # 同期の途中かもしれないので、しばらくは消さずに待つ
                try:
                    if now - path.stat().st_mtime > REJECT_AFTER_SEC:
                        log.warning("iPhone・iPad からの修正ファイルを読めませんでした: %s", path.name)
                        path.replace(path.with_suffix(".rejected"))
                except OSError:
                    pass
        changed: set[date] = set()
        for _, name, path, edit in sorted(items, key=lambda x: (x[0], x[1])):
            try:
                changed |= apply_edit(conn, edit)
                log.info("iPhone・iPad からの修正を取り込みました: %s %s", edit.get("type"), edit.get("date", ""))
                path.unlink(missing_ok=True)
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("iPhone・iPad からの修正を取り込めませんでした（%s）: %s", name, exc)
                path.replace(path.with_suffix(".rejected"))
        return changed

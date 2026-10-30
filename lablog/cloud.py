"""要約した記録を OneDrive に書き出し、スマホから直した内容を取り込む。

書き出すのは要約（入退室・PC作業時間・分類別の時間・タイムラインの分類・日報）だけ。
ウィンドウ名・閲覧履歴・ファイルのパスなどの元の記録は、この PC の外に出さない。

書き出し先は スマホ用ビューアーの OneDrive アプリフォルダ（OneDrive\\アプリ\\LabLog）。
ビューアーで初めてサインインしたときに OneDrive が作り、ビューアーが目印の viewer.json を置く。

  meta.json            状態・最終更新時刻
  days/YYYY-MM-DD.json 1 日分の要約
  months/YYYY-MM.json  1 か月分の集計
  edits/*.json         ビューアーで直した日報・滞在（取り込んだら消す）
  unlinked.json        PC で連携を解除した印（ほかのファイルは消す。ビューアーはこれを見るとサインアウトする）
"""

import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

from . import VIEWER_URL, __version__, db
from .categorize import Categorizer
from .config import DAY_START_HOUR, Settings, data_dir
from .days import day_range, logical_date, parse_date
from .presence import delete_stay, save_stay
from .report import MAX_REPORT_CHARS, save_report
from .summary import day_summary, month_summary

log = logging.getLogger(__name__)
MARKER_APP = "LabLog viewer"

FORMAT_VERSION = 1
MARKER = "viewer.json"
UNLINKED = "unlinked.json"
CHECK_SEC = 30          # 直した内容の確認・書き出しの間隔
PERIODIC_SEC = 300      # 今日の分を書き出し直す間隔
DETECT_SEC = 300        # 書き出し先が見つからないときに探し直す間隔
REJECT_AFTER_SEC = 600  # 読めない修正ファイルを、同期の途中ではないとみなすまでの時間
# 書き出す PC は 1 台だけ（meta.json の device）。その PC がこれだけ書き出していなければ、ほかの PC が引き継ぐ
TAKEOVER_AFTER_SEC = 14 * 86400
MIN_TS, MAX_TS = 946_684_800, 4_102_444_800  # 2000〜2100 年

# クラウドに置く 1 日分の項目（apps・files・web など元の記録に近いものは含めない）
DAY_KEYS = ("date", "date_label", "range", "now", "stays", "stay_total", "work_total", "work_by_location",
            "categories", "category_names")
REPORT_KEYS = ("text", "edited", "generated_ts", "source", "saved_ts")


def onedrive_root() -> Path | None:
    """個人用 OneDrive のフォルダ（ビューアーは個人の Microsoft アカウントでサインインする）。"""
    for root in (os.environ.get("OneDriveConsumer"), Path.home() / "OneDrive"):
        if root and os.path.isdir(root):
            return Path(root)
    return None


def onedrive_account() -> str | None:
    """この PC の個人用 OneDrive にサインインしている Microsoft アカウント（メールアドレス）。

    PC では Microsoft にサインインしない（OneDrive のフォルダに書くだけ）。スマホのビューアーで同じアカウントに
    サインインしてもらうため、設定の画面に出す。
    """
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\OneDrive\Accounts\Personal") as key:
            return str(winreg.QueryValueEx(key, "UserEmail")[0]) or None
    except OSError:
        return None


def machine_id() -> str:
    """この PC とこの Windows のユーザーの印。記録の DB は別の PC へ移せるので、書き出す PC を見分けるのに使う。"""
    guid = os.environ.get("COMPUTERNAME", "")
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                guid = str(winreg.QueryValueEx(key, "MachineGuid")[0])
        except OSError:
            pass
    return hashlib.sha256(f"{guid}|{os.environ.get('USERNAME', '')}".encode()).hexdigest()[:16]


def _is_viewer_marker(path: Path) -> bool:
    try:
        return path.is_file() and json.loads(path.read_text(encoding="utf-8")).get("app") == MARKER_APP
    except (OSError, ValueError, AttributeError):
        return False


def find_viewer_folder(root: Path | None = None) -> Path | None:
    """ビューアーが作ったアプリフォルダ（目印の viewer.json があるもの）を探す。

    フォルダ名はふつう「アプリ/LabLog」だが、OneDrive が「Graph」などの名前で作ることもあるので、名前では決めない。
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


def cloud_day(s: dict, reports: bool = True) -> dict:
    """1 日分の集計から、クラウドに置いてよい要約だけを取り出す。reports：日報も送る（設定の cloud.reports）。"""
    out = {k: s[k] for k in DAY_KEYS}
    out["timeline"] = [{"cat": b["cat"], "start": b["start"], "end": b["end"]} for b in s["timeline"]]
    out["report"] = {k: s["report"].get(k) for k in REPORT_KEYS} if reports and s["report"] else None
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
        self.device: dict | None = None      # この PC の印（id は初回に作る。name は PC の名前）
        self.blocked_by: dict | None = None  # ほかの PC が書き出しているとき、その PC の名前と最終更新
        self._takeover = False
        self._unlink = False

    def mark_dirty(self, *days: date):
        with self._lock:
            self._dirty.update(days)
        self._wake.set()

    def request_export(self):
        with self._lock:
            self._force = True
        self._wake.set()

    def request_full_export(self):
        """全期間を書き出し直す（分類を変えたとき：前の日の分類別の時間も変わる）。"""
        with self._lock:
            self._full = True
        self._wake.set()

    def request_unlink(self):
        """連携の解除（設定でオフにしたとき）。OneDrive 上の記録を消し、スマホ版をサインアウトさせる。"""
        with self._lock:
            self._unlink = True
        self._wake.set()

    def request_takeover(self):
        """書き出す PC をこの PC にする（設定の画面の「この PC から書き出す」）。"""
        with self._lock:
            self._takeover = True
            self._force = True
        self._wake.set()

    def _device(self, conn) -> dict:
        if self.device is None:
            device_id = db.kv_get(conn, "device_id")
            here, was = machine_id(), db.kv_get(conn, "device_machine")
            if device_id and was and was != here:  # バックアップを別の PC で戻した：前の PC とは別の PC として書き出す
                log.info("記録をほかの PC から移したため、この PC の印を作り直します")
                device_id = None
            if not device_id:
                device_id = uuid.uuid4().hex
                db.kv_set(conn, "device_id", device_id)
            if was != here:
                db.kv_set(conn, "device_machine", here)
            self.device = {"id": device_id, "name": os.environ.get("COMPUTERNAME") or "PC"}
        return self.device

    def _other_writer(self, conn, folder: Path, now: float, takeover: bool) -> dict | None:
        """ほかの PC がこの OneDrive に書き出していれば、その PC（名前と最終更新）。この PC が書いてよければ None。"""
        try:
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        other = meta.get("device") or {}
        mine = self._device(conn)
        if not other.get("id") or other["id"] == mine["id"]:  # 印が無いのは前のバージョンが書いたもの
            return None
        if other["id"] == db.kv_get(conn, "device_id_before_restore") and other.get("name") == mine["name"]:
            # どの PC のものか分からない記録を戻したので印を作り直したが、前の印で書き出していたのは、この PC だった
            log.info("記録を戻す前と同じ PC なので、この PC から書き出しを続けます")
            self._written.clear()
            self._full = True
            return None
        exported = meta.get("exported") or 0
        if takeover or now - exported >= TAKEOVER_AFTER_SEC:
            log.info("書き出す PC を、この PC にしました（前の PC：%s）", other.get("name"))
            self._written.clear()  # 前の PC が書いた日の分も、この PC の記録で書き直す
            self._full = True
            return None
        return {"name": other.get("name") or "別の PC", "exported": exported}

    def _cfg(self) -> dict:
        return {"enabled": True, "reports": True, "folder": "", "viewer_url": ""} | (self.settings.get("cloud") or {})

    def status(self) -> dict:
        cfg = self._cfg()
        # viewer_url が空なら配布元のブラウザ版（自分用にベータ版を使うときだけ設定で変える）
        return {"enabled": cfg["enabled"], "viewer_url": cfg["viewer_url"] or VIEWER_URL, "account": onedrive_account(),
                "folder": str(self.folder) if self.folder else None,
                "last_export": self.last_export, "last_error": self.last_error, "blocked_by": self.blocked_by}

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
        elif os.environ.get("LABLOG_DATA_DIR"):
            # テスト用のデータ（--data-dir）で動かしているときは、本物の OneDrive のフォルダを自動では探さない
            # （本物の書き出しをテストの記録で上書きしたり、スマホで直した内容を横取りしたりしないように）
            folder = None
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
            takeover, self._takeover = self._takeover, False
            unlink, self._unlink = self._unlink, False
        if not self._cfg()["enabled"] or not self.settings.get("setup_done", True):
            if unlink and self._cfg()["enabled"] is False:
                self.unlink(conn, now)
            self.folder = None
            return
        folder = self._resolve_folder(now, force)
        if folder is None:
            return
        self.blocked_by = self._other_writer(conn, folder, now, takeover)
        if self.blocked_by:  # 書き出しも、スマホで直した内容の取り込みもしない（書き出している PC が取り込む）
            self._written.clear()
            self._full = True
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

    def unlink(self, conn, now: float) -> str | None:
        """OneDrive 上の記録（要約・修正・目印）を消して unlinked.json を置く。ほかの PC が書き出していれば何もしない。
        消したら "deleted"、ほかの PC が書き出していれば "other"、書き出し先が無ければ None。"""
        folder = self._resolve_folder(now, force=True)
        if folder is None:
            return None
        if self._other_writer(conn, folder, now, takeover=False):
            log.info("ほかの PC が書き出しているため、OneDrive の記録は消しません")
            return "other"
        self.import_edits(conn, folder, now)  # スマホで直したまま、まだ取り込んでいない分は先に取り込む
        for name in ("days", "months", "edits"):
            shutil.rmtree(folder / name, ignore_errors=True)
        for name in ("meta.json", MARKER):
            (folder / name).unlink(missing_ok=True)
        self._write(folder / UNLINKED, {"app": "LabLog", "unlinked": now})
        log.info("連携を解除し、OneDrive の記録を消しました: %s", folder)
        self.folder = None
        self.blocked_by = None
        self.last_export = None
        self._written.clear()
        return "deleted"

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
        reports = self._cfg()["reports"]
        for d in sorted(x for x in days if x <= today):
            s = cloud_day(day_summary(conn, self.settings, d, now), reports)
            s["now"] = min(now, day_range(d)[1])
            path = folder / "days" / f"{d.isoformat()}.json"
            # 記録の無い日はファイルを作らない（前に書き出していれば、消えたことを反映する）
            if s["stays"] or s["work_total"] or s["report"] or path.exists():
                self._write(path, s, ignore=("now",))
        for y, m in sorted({(d.year, d.month) for d in days if d <= today}):
            month = month_summary(conn, self.settings, y, m, now)
            if not reports:  # 日報を送らないときは、日報がある日の印も送らない
                for entry in month["days"]:
                    entry["has_report"] = False
            self._write(folder / "months" / f"{y:04d}-{m:02d}.json", month)
        status = self.status_fn()
        # app_version：ビューアーが、PC の LabLog が古いことを知らせるため。device：書き出している PC の印
        self._write(folder / "meta.json", {
            "version": FORMAT_VERSION, "app_version": __version__, "device": self._device(conn),
            "exported": now, "today": today.isoformat(), "reports": reports,
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
                        log.warning("スマホからの修正ファイルを読めませんでした: %s", path.name)
                        self._reject(path, None, "修正のファイルを読めませんでした")
                except OSError:
                    pass
        changed: set[date] = set()
        for _, name, path, edit in sorted(items, key=lambda x: (x[0], x[1])):
            try:
                changed |= apply_edit(conn, edit)
                log.info("スマホからの修正を取り込みました: %s %s", edit.get("type"), edit.get("date", ""))
                path.unlink(missing_ok=True)
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("スマホからの修正を取り込めませんでした（%s）: %s", name, exc)
                self._reject(path, edit, str(exc) if isinstance(exc, ValueError) else "修正の内容が正しくありません")
        return changed

    @staticmethod
    def _reject(path: Path, edit: dict | None, reason: str):
        """取り込めなかった修正を .rejected に替え、理由を書いておく（スマホ版が読んで、断られたことと理由を出す）。"""
        try:
            path.with_suffix(".rejected").write_text(json.dumps({"error": reason, "edit": edit}, ensure_ascii=False),
                                                     encoding="utf-8")
            path.unlink(missing_ok=True)
        except OSError:
            pass



def unlink_for_uninstall() -> str:
    """アンインストールのとき（uninstall.ps1 が、LabLog を止めてから呼ぶ）：連携していれば解除する。画面に出す文を返す。

    設定の画面で連携をオフにしたときと同じく、この PC が書き出した OneDrive の記録を消し、スマホ版をサインアウトさせる。
    記録を残してアンインストールしたときに、入れ直しても勝手に書き出さないよう、設定の連携もオフにする。
    """
    path = data_dir() / "settings.json"
    if not path.is_file() or not db.db_path().is_file():
        return ""
    try:
        settings = Settings(path)
        cfg = settings.get("cloud")
        if not settings.get("setup_done") or not cfg["enabled"]:
            return ""
        conn = db.connect()
        try:
            db.init(conn)
            done = CloudSync(settings, dict).unlink(conn, time.time())
        finally:
            conn.close()
        settings.update({"cloud": cfg | {"enabled": False}})
    except Exception as exc:
        log.exception("アンインストールのときに連携を解除できませんでした")
        return f"Microsoft アカウントとの連携を解除できませんでした（{exc}）。スマホ版は、サインアウトしてください。"
    if done == "other":
        return "Microsoft アカウントとの連携を解除しました（ほかの PC が書き出しているため、OneDrive の記録はそのままです）"
    if done == "deleted":
        return "Microsoft アカウントとの連携を解除しました（OneDrive の LabLog の記録を消し、スマホ版をサインアウトしました）"
    return "Microsoft アカウントとの連携を解除しました"

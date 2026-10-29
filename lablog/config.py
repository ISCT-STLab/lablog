"""設定とデータ保存場所。

データは OneDrive の外（%LOCALAPPDATA%\\LabLog）に置き、クラウドへ同期されないようにする。
環境変数 LABLOG_DATA_DIR で別の場所を指定できる（テスト用）。
"""

import copy
import json
import os
import secrets
import threading
from pathlib import Path
from urllib.parse import urlparse

APP_NAME = "LabLog"
# 1日の区切り。深夜 0〜5 時の作業は前日分として扱う。
DAY_START_HOUR = 5
OTHER_CATEGORY = "その他"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def data_dir() -> Path:
    override = os.environ.get("LABLOG_DATA_DIR")
    base = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def dashboard_key() -> str:
    """ダッシュボードの合言葉（初回に作り、データの保存場所に置く）。

    保存場所は自分の Windows アカウントのフォルダなので、同じ PC の別のアカウントからは読めない。
    LabLog が開くときだけ URL に付け、ブラウザには Cookie として覚えさせる（server.py）。
    """
    path = data_dir() / "dashboard.key"
    try:
        key = path.read_text(encoding="ascii").strip()
        if len(key) >= 32:
            return key
    except (OSError, ValueError):
        pass
    key = secrets.token_urlsafe(32)
    path.write_text(key, encoding="ascii")
    return key


DEFAULT_CATEGORIES = [
    {
        "name": "コーディング・解析",
        "apps": ["code.exe", "matlab.exe", "windowsterminal.exe", "powershell.exe", "pwsh.exe",
                 "cmd.exe", "conhost.exe", "openconsole.exe", "git-bash.exe", "mintty.exe"],
        "domains": ["github.com", "gitlab.com", "colab.research.google.com", "kaggle.com",
                    "mathworks.com", "localhost", "127.0.0.1"],
        "titles": ["Jupyter"],
    },
    {
        "name": "文書・資料作成",
        "apps": ["winword.exe", "powerpnt.exe", "excel.exe", "onenote.exe"],
        "domains": ["overleaf.com", "docs.google.com", "slides.google.com", "sheets.google.com",
                    "sharepoint.com", "office.com"],
        "titles": [],
    },
    {
        "name": "論文・調べもの",
        "apps": ["acrobat.exe", "acrord32.exe", "sumatrapdf.exe", "zotero.exe"],
        "domains": ["arxiv.org", "scholar.google.com", "scholar.google.co.jp", "ieeexplore.ieee.org",
                    "dl.acm.org", "sciencedirect.com", "link.springer.com", "springer.com",
                    "jstage.jst.go.jp", "cir.nii.ac.jp", "ci.nii.ac.jp", "semanticscholar.org",
                    "researchgate.net", "openreview.net", "paperswithcode.com", "nature.com",
                    "science.org", "mdpi.com", "wiley.com", "ncbi.nlm.nih.gov", "wikipedia.org",
                    "stackoverflow.com", "qiita.com", "zenn.dev", "docs.python.org",
                    # 「=」で始まるものは完全一致（Google 検索だけを対象にし、Gmail などは含めない）
                    "=google.com", "=google.co.jp", "=bing.com"],
        "titles": [],
    },
    {
        "name": "メール・連絡",
        "apps": ["outlook.exe", "olk.exe", "thunderbird.exe", "slack.exe", "line.exe", "discord.exe"],
        "domains": ["mail.google.com", "outlook.office.com", "outlook.office365.com", "outlook.live.com",
                    "slack.com"],
        "titles": [],
    },
    {
        "name": "AI相談",
        "apps": ["claude.exe", "chatgpt.exe"],
        "domains": ["claude.ai", "chatgpt.com", "chat.openai.com", "gemini.google.com",
                    "copilot.microsoft.com", "perplexity.ai"],
        "titles": [],
    },
    {
        "name": "ミーティング",
        "apps": ["ms-teams.exe", "teams.exe", "zoom.exe"],
        "domains": ["teams.microsoft.com", "teams.live.com", "zoom.us", "meet.google.com"],
        "titles": [],
    },
    {
        "name": "タスク・メモ",
        "apps": ["notion.exe", "todo.exe"],
        "domains": ["notion.so", "notion.site", "to-do.office.com", "to-do.live.com"],
        "titles": ["Microsoft To Do"],
    },
    {
        "name": "ファイル整理",
        "apps": ["explorer.exe"],
        "domains": [],
        "titles": [],
    },
]

# 画面の文字を読み取るアプリ：ウィンドウ名から作業内容が分からず、ほかに手がかりもないもの
# （AI チャット、To Do、Teams、Zoom、MATLAB、ターミナル・PowerShell）。
# 読まないもの：ブラウザ（閲覧履歴がある）、Word・Excel・PowerPoint・VS Code・Notion（ウィンドウ名に文書やページの名前が出る）、
# エクスプローラー（ウィンドウ名にフォルダ名が出る）、Git（コミットの記録がある）
SCREEN_TEXT_APPS = ["claude.exe", "chatgpt.exe", "todo.exe", "ms-teams.exe", "teams.exe", "zoom.exe", "matlab.exe",
                    "windowsterminal.exe", "powershell.exe", "pwsh.exe"]
# 前の版の初期値。自分で変えていなければ、読み込むときに新しい初期値へ広げる
OLD_SCREEN_TEXT_APPS = [["claude.exe", "chatgpt.exe"]]

DEFAULT_SETTINGS = {
    "port": 8765,
    "sample_interval_sec": 5,
    "idle_threshold_min": 5,
    "leave_after_hours": 4,
    # ignore：判定に使わない Wi-Fi（iPhone のテザリングなど、大学でも大学の外でも使うもの）
    "wifi": {"univ": [], "home": [], "ignore": []},
    "watch_folders": [],
    # 記録する材料。apps：title（アプリ名とウィンドウ名）/ app（アプリ名だけ）/ off（作業時間だけ）
    # files：監視フォルダのファイル編集、git：コミットと未コミットの変更、recent_files：Windows の「最近使ったファイル」
    "record": {"apps": "title", "files": True, "git": True, "recent_files": True},
    "excluded_apps": [],
    "browsers": {"chrome": True, "edge": True},
    "git_notify": {"enabled": True, "hours": 2},
    # enabled：日報に AI を使う（使わないときは集計から箇条書きで作る）。auto：日報を自動で作る
    "ollama": {"enabled": True, "url": "http://127.0.0.1:11434", "model": "qwen3:8b", "auto": True},
    # ウィンドウ名から作業内容が分からないアプリの補い方
    "claude_code": {"enabled": True},  # Claude Code の会話の題名・作業フォルダ（中身は読まない）
    "screen_text": {"enabled": True, "apps": SCREEN_TEXT_APPS, "interval_min": 5, "keep_days": 3},
    # iPhone・iPad 用ビューアーへの書き出し（要約だけ）。folder が空なら OneDrive のアプリフォルダを自動で探す。
    "cloud": {"enabled": True, "folder": "", "viewer_url": ""},
    "categories": DEFAULT_CATEGORIES,
}


def is_local_url(url: str) -> bool:
    try:
        return (urlparse(url).hostname or "") in LOCAL_HOSTS
    except ValueError:
        return False


def _str_list(value) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("文字列のリストを指定してください")
    return list(dict.fromkeys(v.strip() for v in value if v.strip()))


def _number(lo, hi, cast=float):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
            raise ValueError(f"{lo}〜{hi} の数値を指定してください")
        return cast(value)
    return check


def _wifi(value):
    return {kind: _str_list(value.get(kind, [])) for kind in ("univ", "home", "ignore")}


def _browsers(value):
    return {"chrome": bool(value.get("chrome", True)), "edge": bool(value.get("edge", True))}


def _git_notify(value):
    return {"enabled": bool(value.get("enabled", True)), "hours": _number(0.5, 24)(value.get("hours", 2))}


def _ollama(value):
    url = str(value.get("url", "")).strip()
    if not is_local_url(url):
        raise ValueError("Ollama の URL は PC 内（127.0.0.1 / localhost）だけ指定できます")
    return {"enabled": bool(value.get("enabled", True)), "url": url, "model": str(value.get("model", "")).strip(),
            "auto": bool(value.get("auto", True))}


def _cloud(value):
    folder = str(value.get("folder", "")).strip().strip('"')
    if folder and not os.path.isdir(folder):
        raise ValueError("書き出し先のフォルダが見つかりません")
    viewer_url = str(value.get("viewer_url", "")).strip()
    if viewer_url and not viewer_url.startswith("https://"):
        raise ValueError("ビューアーの URL は https:// で始まるものを指定してください")
    return {"enabled": bool(value.get("enabled", True)), "folder": folder and os.path.normpath(folder),
            "viewer_url": viewer_url}


APP_RECORD_MODES = ("title", "app", "off")


def _record(value):
    apps = value.get("apps", "title")
    if apps not in APP_RECORD_MODES:
        raise ValueError("アプリの記録のしかたが正しくありません")
    return {"apps": apps, "files": bool(value.get("files", True)), "git": bool(value.get("git", True)),
            "recent_files": bool(value.get("recent_files", True))}


def _claude_code(value):
    return {"enabled": bool(value.get("enabled", True))}


def _screen_text(value):
    return {"enabled": bool(value.get("enabled", True)),
            "apps": [s.lower() for s in _str_list(value.get("apps", []))],
            "interval_min": _number(1, 60)(value.get("interval_min", 5)),
            "keep_days": _number(1, 30, int)(value.get("keep_days", 3))}


def _categories(value):
    if not isinstance(value, list):
        raise ValueError("分類ルールの形式が正しくありません")
    out = []
    for c in value:
        name = str(c.get("name", "")).strip()
        if not name or name == OTHER_CATEGORY:
            continue
        out.append({"name": name, "apps": _str_list(c.get("apps", [])),
                    "domains": _str_list(c.get("domains", [])), "titles": _str_list(c.get("titles", []))})
    return out


VALIDATORS = {
    "port": _number(1024, 65535, int),
    "sample_interval_sec": _number(2, 60, int),
    "idle_threshold_min": _number(1, 60),
    "leave_after_hours": _number(1, 24),
    "wifi": _wifi,
    "watch_folders": _str_list,
    "record": _record,
    "excluded_apps": lambda v: [s.lower() for s in _str_list(v)],
    "browsers": _browsers,
    "git_notify": _git_notify,
    "ollama": _ollama,
    "cloud": _cloud,
    "claude_code": _claude_code,
    "screen_text": _screen_text,
    "categories": _categories,
}


class Settings:
    """settings.json を読み書きする。スレッドから同時に触られても壊れないようロックする。"""

    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "settings.json"
        self._lock = threading.RLock()
        self._data = copy.deepcopy(DEFAULT_SETTINGS)
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                for key, value in loaded.items():
                    if key in self._data:
                        # 新しい版で増えた項目（wifi の ignore など）は初期値で補う
                        default = self._data[key]
                        self._data[key] = default | value if isinstance(default, dict) and isinstance(value, dict) else value
            except (OSError, ValueError):
                pass
        screen = self._data.get("screen_text")
        if isinstance(screen, dict) and screen.get("apps") in OLD_SCREEN_TEXT_APPS:
            screen["apps"] = list(SCREEN_TEXT_APPS)
        self.save()

    def get(self, key, default=None):
        with self._lock:
            return copy.deepcopy(self._data.get(key, default))

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, patch: dict) -> dict:
        with self._lock:
            checked = {}
            for key, value in patch.items():
                if key not in VALIDATORS:
                    raise ValueError(f"不明な設定項目です: {key}")
                checked[key] = VALIDATORS[key](value)
            self._data.update(checked)
            self.save()
            return copy.deepcopy(self._data)

    def save(self):
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

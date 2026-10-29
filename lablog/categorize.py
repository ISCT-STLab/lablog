"""アプリ名・サイトから作業の分類を決める。"""

import re

from .config import OTHER_CATEGORY

BROWSER_EXES = {"chrome.exe", "msedge.exe"}

APP_LABELS = {
    "code.exe": "VS Code", "chrome.exe": "Chrome", "msedge.exe": "Edge", "winword.exe": "Word",
    "powerpnt.exe": "PowerPoint", "excel.exe": "Excel", "onenote.exe": "OneNote", "matlab.exe": "MATLAB",
    "windowsterminal.exe": "ターミナル", "powershell.exe": "PowerShell", "pwsh.exe": "PowerShell 7",
    "cmd.exe": "コマンドプロンプト", "explorer.exe": "エクスプローラー", "ms-teams.exe": "Teams",
    "teams.exe": "Teams", "zoom.exe": "Zoom", "claude.exe": "Claude", "chatgpt.exe": "ChatGPT",
    "notion.exe": "Notion", "todo.exe": "Microsoft To Do", "acrobat.exe": "Acrobat",
    "acrord32.exe": "Acrobat Reader", "sumatrapdf.exe": "SumatraPDF", "zotero.exe": "Zotero",
}

# 表示を短くするため、タイトル末尾のアプリ名を落とす
_TITLE_SUFFIX = re.compile(
    r"\s+[-–—]\s+(?:Google Chrome|Microsoft​?\s?Edge|Visual Studio Code|Word|PowerPoint|Excel|"
    r"Notion|Zoom|Microsoft Teams|Teams)\s*$")
_EDGE_PROFILE = re.compile(r"\s+[-–—]\s+(?:個人|職場|Personal|Work|プロファイル \d+|Profile \d+)\s*$")
_MORE_TABS = re.compile(r"\s*(?:and \d+ more pages?|と他 \d+ (?:ページ|個のページ)|ほか \d+ ページ)\s*$")
_COUNTER = re.compile(r"^\(\d+\+?\)\s*")  # 「(3) 受信トレイ」のような未読数


def app_label(exe: str) -> str:
    if not exe:
        return ""
    return APP_LABELS.get(exe.lower(), exe[:-4] if exe.lower().endswith(".exe") else exe)


def clean_title(title: str) -> str:
    t = _TITLE_SUFFIX.sub("", title)
    t = _EDGE_PROFILE.sub("", t)
    t = _MORE_TABS.sub("", t)
    return t.strip()


class Categorizer:
    def __init__(self, categories: list[dict]):
        self.app_map: dict[str, str] = {}
        self.domain_rules: list[tuple[str, str]] = []
        self.title_rules: list[tuple[str, str]] = []
        for c in categories:
            for a in c.get("apps", []):
                self.app_map.setdefault(a.strip().lower(), c["name"])
            for d in c.get("domains", []):
                self.domain_rules.append((d.strip().lower().removeprefix("www."), c["name"]))
            for t in c.get("titles", []):
                if t.strip():
                    self.title_rules.append((t.strip().lower(), c["name"]))
        self.names = [c["name"] for c in categories] + [OTHER_CATEGORY]

    def by_domain(self, domain: str | None) -> str | None:
        domain = (domain or "").lower()
        if not domain:
            return None
        best, best_len = None, -1
        for d, name in self.domain_rules:
            if d.startswith("="):
                hit = domain == d[1:]
            else:
                hit = domain == d or domain.endswith("." + d)
            if hit and len(d) > best_len:
                best, best_len = name, len(d)
        return best

    def by_title(self, title: str) -> str | None:
        t = title.lower()
        for keyword, name in self.title_rules:
            if keyword in t:
                return name
        return None

    def classify(self, exe: str, title: str, domain: str | None = None) -> str:
        e = exe.lower()
        if e in BROWSER_EXES:
            return self.by_domain(domain) or self.by_title(title) or OTHER_CATEGORY
        return self.app_map.get(e) or self.by_title(title) or OTHER_CATEGORY


def norm_title(title: str) -> str:
    return _COUNTER.sub("", clean_title(title or "")).strip()


class SeenTitles:
    """ブラウザで実際に画面に表示したページかどうか（前面のウィンドウの題名になったか）を判定する。

    開いたままのタブがバックグラウンドで読み込まれただけでも閲覧履歴には残るので、
    LabLog が記録した前面のウィンドウの題名と照らし合わせて、見たページだけを残す。
    """

    MIN_PREFIX = 8  # これより短い題名は、前方一致では判定しない（取り違えを防ぐ）

    def __init__(self, window_titles):
        self.titles = {norm_title(t) for t in window_titles} - {""}

    def __contains__(self, page_title: str) -> bool:
        t = norm_title(page_title)
        if not t:
            return False
        if t in self.titles:
            return True
        return len(t) >= self.MIN_PREFIX and any(
            w.startswith(t) or (len(w) >= self.MIN_PREFIX and t.startswith(w)) for w in self.titles)


class TitleDomainMap:
    """ブラウザのウィンドウタイトルから、閲覧履歴を使ってサイト（ドメイン）を推定する。"""

    def __init__(self, visits: list[tuple[str, str]]):
        self.exact: dict[str, str] = {}
        for title, domain in visits:
            if title and domain:
                self.exact[title] = domain
        self.by_length = sorted(((t, d) for t, d in self.exact.items() if len(t) >= 4),
                                key=lambda x: len(x[0]), reverse=True)
        self._cache: dict[str, str | None] = {}

    def lookup(self, window_title: str) -> str | None:
        if window_title in self._cache:
            return self._cache[window_title]
        found = self.exact.get(window_title)
        if found is None:
            for title, domain in self.by_length:
                if window_title.startswith(title):
                    found = domain
                    break
        self._cache[window_title] = found
        return found

"""インストールした PC でのフォルダの形と、バージョンの切り替え（setup.ps1 が作り、更新のときに update.py が使う）。

  %LOCALAPPDATA%\\Programs\\LabLog\\
    LabLog.pyw       起動用（リポジトリの launcher.pyw の写し。version.json のバージョンを起動する）
    version.json     {"current": "1.1.0", "previous": "1.0.0"}。起動できなかったときは "failed" にバージョンが入る
    versions\\1.0.0\\  バージョンごとのアプリ（配布用 ZIP の中身）
    versions\\1.1.0\\

動いているバージョンのフォルダは書き換えない。新しいバージョンは別のフォルダに置いてから version.json で切り替えるので、
更新に失敗しても前のバージョンで起動し直せる。開発用（リポジトリのフォルダ）から動かしているときはこの形ではないので、
LabLog からは更新しない（git で更新する）。
"""

import json
import os
import shutil
from datetime import date
from pathlib import Path

from . import GITHUB_OWNER, GITHUB_REPO

VERSION_FILE = "version.json"
KEEP_VERSIONS = 3  # 現在のバージョンと前のバージョンのほかに、新しいものから残す数の合計
# Windows の「インストールされているアプリ」に出すための登録先（HKEY_CURRENT_USER の下。管理者の権限は要らない）
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\LabLog"


def install_base(app_dir: Path | None = None) -> Path | None:
    """インストールした形で動いていれば、その一番上のフォルダ（LabLog.pyw と version.json があるところ）。"""
    app_dir = app_dir or Path(__file__).resolve().parent.parent
    base = app_dir.parent.parent
    if app_dir.parent.name == "versions" and (base / VERSION_FILE).is_file():
        return base
    return None


def read_versions(base: Path) -> dict:
    try:
        data = json.loads((base / VERSION_FILE).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_versions(base: Path, info: dict):
    tmp = base / (VERSION_FILE + ".tmp")
    tmp.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, base / VERSION_FILE)


def switch_to(base: Path, version: str):
    """次に起動するときのバージョンを version にする（現在のバージョンは前のバージョンとして残す）。"""
    current = read_versions(base).get("current")
    write_versions(base, {"current": version, "previous": current if current != version else None})


def cleanup_versions(base: Path, sort_key):
    """現在のバージョン・前のバージョンと、新しいものから KEEP_VERSIONS 個までを残して、古いバージョンのフォルダを消す。"""
    info = read_versions(base)
    keep = {info.get("current"), info.get("previous")}
    folders = [p for p in (base / "versions").iterdir() if p.is_dir() and not p.name.endswith(".tmp")]
    newest = sorted(folders, key=lambda p: sort_key(p.name), reverse=True)[:KEEP_VERSIONS]
    for p in folders:
        if p.name not in keep and p not in newest:
            shutil.rmtree(p, ignore_errors=True)


def refresh_uninstaller(base, app_dir: Path | None = None):
    """インストール先の一番上にあるアンインストールのファイルを、動いているバージョンのものにそろえる。
    画面から更新したときは versions の下に展開するだけなので、ここで写す（setup.ps1 は一番上に直接写す）。"""
    app_dir = app_dir or Path(__file__).resolve().parent.parent
    for name in ("uninstall.cmd", "uninstall.ps1"):
        src, dst = app_dir / name, Path(base) / name
        if src.is_file() and (not dst.is_file() or src.read_bytes() != dst.read_bytes()):
            shutil.copy2(src, dst)


def register_app(base, version: str, key: str = UNINSTALL_KEY):
    """Windows の「インストールされているアプリ」に LabLog を出し、そこからアンインストールできるようにする。
    setup.ps1 がインストールしたときと、LabLog を起動するたびに呼ぶ（画面から更新したあとも、バージョンの表示が合うように）。"""
    import winreg

    base = Path(base)
    refresh_uninstaller(base)  # 前のバージョンの uninstall.ps1 は、下の -Confirmed を受け付けない
    cmd = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"
    # Windows の設定で確認が済んでいるので -Confirmed。cmd /c は最初と最後の " を取り除くので、全体をもう一組の " で囲む
    args = f'-Confirmed -InstallDir "{base}"' + (f' -UninstallKey "{key}"' if key != UNINSTALL_KEY else "")
    texts = {
        "DisplayName": "LabLog",
        "DisplayVersion": version,
        "Publisher": GITHUB_OWNER,
        "DisplayIcon": str(Path(__file__).resolve().parent / "web" / "icon.ico"),
        "InstallLocation": str(base),
        "UninstallString": f'"{cmd}" /c ""{base / "uninstall.cmd"}" {args}"',
        "URLInfoAbout": f"https://github.com/{GITHUB_REPO}",
    }
    size_kb = sum(p.stat().st_size for p in base.rglob("*") if p.is_file()) // 1024
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        for name, value in texts.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        for name, value in {"EstimatedSize": size_kb, "NoModify": 1, "NoRepair": 1}.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, value)
        try:
            winreg.QueryValueEx(k, "InstallDate")
        except FileNotFoundError:  # 初めて登録した日を残す
            winreg.SetValueEx(k, "InstallDate", 0, winreg.REG_SZ, date.today().strftime("%Y%m%d"))

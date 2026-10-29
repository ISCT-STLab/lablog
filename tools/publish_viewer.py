"""iPhone・iPad 用ビューアーを GitHub Pages に公開する。

ダッシュボードの画面ファイル（lablog/web）だけを公開用のフォルダに写し、ビューアー用の config.js を作る。
記録のデータや設定は写さない。--push を付けると、コミットして GitHub に送る（gh-pages ブランチ）。

  python tools/publish_viewer.py --client-id <Microsoft のアプリ ID> --push
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lablog import GITHUB_REPO, VIEWER_URL  # noqa: E402

WEB_DIR = ROOT / "lablog" / "web"
DEFAULT_SITE = ROOT.parent / "lablog-viewer"
DEFAULT_REMOTE = f"https://github.com/{GITHUB_REPO}.git"
FILES = ["index.html", "style.css", "app.js", "setup.js", "cloud.js", "sw.js", "favicon.svg", "manifest.webmanifest",
         "icon-180.png", "icon-192.png", "icon-512.png", "fonts/KleeOne-Regular.woff2", "fonts/OFL.txt"]
CLIENT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
BRANCH = "gh-pages"
README = f"""# LabLog ビューアー

[LabLog]({VIEWER_URL}) の iPhone・iPad 用の画面です。
PC の LabLog が OneDrive のアプリ専用フォルダに書き出した要約を、本人の Microsoft アカウントでサインインして表示します。
このリポジトリには画面のプログラムだけがあり、記録のデータは含みません。
"""
COMMIT_TRAILER = "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def git(site: Path, *args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(site), *args], check=check, capture_output=True, text=True,
                          encoding="utf-8")


def current_client_id(site: Path) -> str:
    try:
        text = (site / "config.js").read_text(encoding="utf-8")
        return json.loads(text[text.index("{"):text.rindex("}") + 1]).get("clientId", "")
    except (OSError, ValueError):
        return ""


def build(site: Path, client_id: str):
    site.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        (site / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(WEB_DIR / name, site / name)
    # build：どの版を表示しているか、ビューアーの一番下に出す（公開した日時）
    config = json.dumps({"clientId": client_id, "build": datetime.now().strftime("%m/%d %H:%M")})
    (site / "config.js").write_text(
        "// iPhone・iPad 用ビューアーの設定（tools/publish_viewer.py が作る）。clientId は公開して問題ない値。\n"
        f"window.LABLOG_CLOUD = {config};\n", encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    (site / "README.md").write_text(README, encoding="utf-8")


def push(site: Path, remote: str, message: str):
    if not (site / ".git").exists():
        git(site, "init", "-b", BRANCH)
        git(site, "remote", "add", "origin", remote)
    git(site, "add", "-A")
    if not git(site, "status", "--porcelain").stdout.strip():
        print("変更はありません")
        return
    git(site, "commit", "-m", message + COMMIT_TRAILER)
    result = git(site, "push", "-u", "origin", BRANCH, check=False)
    if result.returncode:
        sys.exit(f"GitHub に送れませんでした:\n{result.stderr}")
    print("GitHub に送りました")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", default=str(DEFAULT_SITE), help="公開用のフォルダ（git リポジトリ）")
    parser.add_argument("--client-id", help="Microsoft に登録したアプリの「アプリケーション (クライアント) ID」")
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--push", action="store_true", help="コミットして GitHub に送る")
    parser.add_argument("--message", default="ビューアーを更新")
    args = parser.parse_args()

    site = Path(args.site)
    client_id = args.client_id if args.client_id is not None else current_client_id(site)
    if client_id and not CLIENT_ID.match(client_id):
        sys.exit("アプリ ID の形式が正しくありません（xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx）")
    build(site, client_id)
    print(f"公開用のファイルを作りました: {site}（アプリ ID: {client_id or '未設定'}）")
    if args.push:
        push(site, args.remote, args.message)


if __name__ == "__main__":
    main()

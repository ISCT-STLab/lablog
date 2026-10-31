"""スマホ用ビューアーを GitHub Pages に公開する。

ダッシュボードの画面ファイル（lablog/web）だけを公開用のフォルダに写し、ビューアー用の config.js を作る。
記録のデータや設定は写さない。--push を付けると、コミットして GitHub に送る（gh-pages ブランチ）。
安定版は公開用フォルダの一番上（…/lablog/）、ベータ版は --beta で beta/ の下（…/lablog/beta/）に置く。

  python tools/publish_viewer.py --client-id <Microsoft のアプリ ID> --push
  python tools/publish_viewer.py --beta --push
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

from lablog import GITHUB_REPO, VIEWER_URL, __version__  # noqa: E402

WEB_DIR = ROOT / "lablog" / "web"
DEFAULT_SITE = ROOT.parent / "lablog-pages"  # 研究室の組織のリポジトリ（gh-pages）に送る公開用フォルダ
DEFAULT_REMOTE = f"https://github.com/{GITHUB_REPO}.git"
FILES = ["index.html", "style.css", "app.js", "setup.js", "cloud.js", "sw.js", "favicon.svg", "manifest.webmanifest",
         "icon-180.png", "icon-192.png", "icon-512.png", "fonts/KleeOne-Regular.woff2", "fonts/OFL.txt",
         "vendor/qrcode.js", "vendor/qrcode-LICENSE.txt", "vendor/qrcode-source.json"]
CLIENT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
BRANCH = "gh-pages"
README = f"""# LabLog ビューアー

[LabLog]({VIEWER_URL}) のスマホ用の画面です（ベータ版は beta/ の下）。
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


def build(site: Path, client_id: str, beta: bool = False):
    target = site / "beta" if beta else site
    target.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(WEB_DIR / name, target / name)
    if beta:  # ホーム画面に追加したとき、安定版と見分けられるように
        manifest = json.loads((target / "manifest.webmanifest").read_text(encoding="utf-8"))
        manifest |= {"name": "LabLog（ベータ版）", "short_name": "LabLog β"}
        (target / "manifest.webmanifest").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # version：ビューアーの一番下に出す。PC の LabLog がこれより古ければ、ビューアーが更新を勧める
    # build：公開した日時（一番下のバージョンにカーソルを当てると出る）
    config = json.dumps({"clientId": client_id, "version": __version__, "build": datetime.now().strftime("%m/%d %H:%M")})
    (target / "config.js").write_text(
        "// スマホ用ビューアーの設定（tools/publish_viewer.py が作る）。clientId は公開して問題ない値。\n"
        f"window.LABLOG_CLOUD = {config};\n", encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    (site / "README.md").write_text(README, encoding="utf-8")


def push(site: Path, remote: str, message: str):
    if not (site / ".git").exists():
        git(site, "init", "-b", BRANCH)
        git(site, "remote", "add", "origin", remote)
        # コミットの作者は本体のリポジトリと同じにする（PC 全体の設定にある個人のメールアドレスを公開しないため）
        for key in ("user.name", "user.email"):
            value = git(ROOT, "config", key, check=False).stdout.strip()
            if value:
                git(site, "config", key, value)
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
    parser.add_argument("--beta", action="store_true", help="ベータ版として beta/ の下に置く")
    parser.add_argument("--push", action="store_true", help="コミットして GitHub に送る")
    parser.add_argument("--message", help="コミットのメッセージ")
    args = parser.parse_args()

    site = Path(args.site)
    client_id = args.client_id if args.client_id is not None else current_client_id(site)
    if client_id and not CLIENT_ID.match(client_id):
        sys.exit("アプリ ID の形式が正しくありません（xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx）")
    build(site, client_id, args.beta)
    where = site / "beta" if args.beta else site
    print(f"公開用のファイルを作りました: {where}（{__version__}、アプリ ID: {client_id or '未設定'}）")
    if args.push:
        message = args.message or f"ビューアーを更新（{'ベータ版' if args.beta else '安定版'} {__version__}）"
        push(site, args.remote, message)


if __name__ == "__main__":
    main()

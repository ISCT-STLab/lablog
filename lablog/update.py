"""LabLog の更新：GitHub の Releases から新しいバージョンを探し、ダウンロードして切り替える（設定の画面の「更新を確認」）。

自動では確認しない。ボタンを押したときだけ GitHub に問い合わせ、送るのは「どのバージョンがあるか」を尋ねるリクエストだけ。
安定版（stable）は正式なリリースだけ、ベータ版（beta）はプレリリースも対象にする。
新しいバージョンは versions\\<バージョン> に展開してから切り替えるので、いま動いているバージョンのファイルは書き換えない（install.py）。
"""

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path

from . import GITHUB_REPO, __version__
from .install import cleanup_versions, install_base, switch_to

log = logging.getLogger(__name__)

# LABLOG_UPDATE_API：テストで、GitHub の代わりに PC 内の偽のサーバーを使うとき
API = os.environ.get("LABLOG_UPDATE_API") or f"https://api.github.com/repos/{GITHUB_REPO}/releases"
CHANNELS = ("stable", "beta")
ASSET = re.compile(r"^LabLog-(.+)\.zip$")
MAX_ZIP_BYTES = 100 * 1024 ** 2
MAX_UNZIPPED_BYTES = 300 * 1024 ** 2
TIMEOUT_SEC = 20

# バージョン番号：1.2.3（正式）、1.2.3a1・1.2.3b1・1.2.3rc1（ベータ版）。先頭の v は無視する
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$")
_STAGES = {"a": 0, "b": 1, "rc": 2, None: 3}


def parse_version(text: str) -> tuple | None:
    m = _VERSION.match(str(text).strip())
    if not m:
        return None
    major, minor, patch, stage, n = m.groups()
    return int(major), int(minor), int(patch), _STAGES[stage], int(n or 0)


def version_key(text: str) -> tuple:
    """並べ替え用（読めないバージョンは一番古いものとして扱う）。"""
    return parse_version(text) or (-1,)


def _get_json(url: str, opener=urllib.request.urlopen):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": f"LabLog/{__version__}"})
    with opener(req, timeout=TIMEOUT_SEC) as r:
        return json.load(r)


def _release_info(rel: dict) -> dict | None:
    """GitHub のリリースから、バージョンと ZIP の情報を取り出す（ZIP が無いものは使わない）。"""
    if rel.get("draft"):
        return None
    for asset in rel.get("assets") or []:
        m = ASSET.match(asset.get("name", ""))
        if m and parse_version(m.group(1)):
            return {"version": m.group(1), "name": rel.get("name") or rel.get("tag_name", ""),
                    "notes": rel.get("body") or "", "published": rel.get("published_at"),
                    "prerelease": bool(rel.get("prerelease")), "page": rel.get("html_url"),
                    "url": asset.get("browser_download_url"), "size": asset.get("size"),
                    "digest": asset.get("digest")}  # "sha256:…"（GitHub が付けていれば）
    return None


def latest_release(channel: str, api: str = API, opener=urllib.request.urlopen) -> dict | None:
    """いちばん新しいバージョン。安定版は正式なリリースだけ、ベータ版はプレリリースも含める。"""
    releases = _get_json(f"{api}?per_page=20", opener)
    found = [info for info in map(_release_info, releases)
             if info and (channel == "beta" or not info["prerelease"])]
    return max(found, key=lambda i: version_key(i["version"]), default=None)


def check(channel: str, api: str = API, opener=urllib.request.urlopen) -> dict:
    """「更新を確認」：現在のバージョンと、新しいバージョンがあればその情報。"""
    rel = latest_release(channel, api, opener)
    newer = bool(rel) and version_key(rel["version"]) > version_key(__version__)
    return {"current": __version__, "latest": rel, "newer": newer}


def download(url: str, dest: Path, size: int | None, digest: str | None, progress=lambda done: None,
             opener=urllib.request.urlopen) -> None:
    """ZIP をダウンロードし、大きさと（あれば）SHA-256 を確かめる。"""
    req = urllib.request.Request(url, headers={"User-Agent": f"LabLog/{__version__}"})
    sha = hashlib.sha256()
    done = 0
    with opener(req, timeout=TIMEOUT_SEC) as r, open(dest, "wb") as f:
        while chunk := r.read(256 * 1024):
            done += len(chunk)
            if done > MAX_ZIP_BYTES:
                raise RuntimeError("ダウンロードしたファイルが大きすぎます")
            sha.update(chunk)
            f.write(chunk)
            progress(done)
    if size and done != size:
        raise RuntimeError("ダウンロードが途中で切れました。もう一度お試しください")
    if digest and digest.startswith("sha256:") and digest.split(":", 1)[1].lower() != sha.hexdigest():
        raise RuntimeError("ダウンロードしたファイルが壊れています（SHA-256 が合いません）")


def extract_release(zip_path: Path, version: str, versions_dir: Path) -> Path:
    """ZIP の中の LabLog-<バージョン>/ を versions_dir/<バージョン> に展開する。中身がそのバージョンのものかも確かめる。"""
    top = f"LabLog-{version}/"
    target = versions_dir / version
    tmp = versions_dir / f"{version}.tmp"
    with zipfile.ZipFile(zip_path) as z:
        members = z.infolist()
        if sum(m.file_size for m in members) > MAX_UNZIPPED_BYTES:
            raise RuntimeError("ZIP の中身が大きすぎます")
        for m in members:  # ZIP の外にファイルを書かせない
            parts = Path(m.filename).parts
            if not m.filename.startswith(top) or m.filename.startswith("/") or ".." in parts or ":" in m.filename:
                raise RuntimeError(f"ZIP の中に想定外のファイルがあります：{m.filename}")
        names = {m.filename for m in members}
        if f"{top}LabLog.pyw" not in names or f"{top}lablog/__init__.py" not in names:
            raise RuntimeError("LabLog の ZIP ではありません")
        init = z.read(f"{top}lablog/__init__.py").decode("utf-8")
        if f'__version__ = "{version}"' not in init:
            raise RuntimeError("ZIP の中身のバージョンが、リリースのバージョンと合いません")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        for m in members:
            rel = m.filename[len(top):]
            if not rel or m.is_dir():
                continue
            out = tmp / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(m) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
    shutil.rmtree(target, ignore_errors=True)
    tmp.rename(target)
    return target


class Updater:
    """更新を裏のスレッドで進める（進み具合は status で見る）。restart：切り替えたあとに LabLog を起動し直す関数。"""

    def __init__(self, restart, base: Path | None = None, opener=urllib.request.urlopen):
        self.restart = restart
        self.base = base if base is not None else install_base()
        self.opener = opener
        self._lock = threading.Lock()
        # state：idle / running / restarting / error
        self._job: dict = {"state": "idle"}

    @property
    def installed(self) -> bool:
        """インストールした形で動いているか（開発用のフォルダから動かしているときは更新しない）。"""
        return self.base is not None

    def status(self) -> dict:
        with self._lock:
            return dict(self._job)

    def _update(self, **fields):
        with self._lock:
            self._job.update(fields)

    def start(self, release: dict):
        if not self.installed:
            raise RuntimeError("開発用のフォルダから動いているため、ここからは更新できません（git pull で更新してください）")
        if not parse_version(release.get("version", "")) or not release.get("url"):
            raise ValueError("更新するバージョンの情報が正しくありません")
        with self._lock:
            if self._job.get("state") in ("running", "restarting"):
                raise RuntimeError("いま更新を進めています")
            self._job = {"state": "running", "message": "ダウンロードしています…", "progress": 0.0,
                         "version": release["version"]}
        threading.Thread(target=self._run, args=(release,), name="update", daemon=True).start()

    def _run(self, rel: dict):
        version = rel["version"]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                zip_path = Path(tmp) / f"LabLog-{version}.zip"
                size = rel.get("size")
                download(rel["url"], zip_path, size, rel.get("digest"),
                         lambda done: self._update(progress=done / size if size else None), self.opener)
                self._update(message="新しいバージョンを展開しています…", progress=None)
                extract_release(zip_path, version, self.base / "versions")
            switch_to(self.base, version)
            cleanup_versions(self.base, version_key)
            log.info("LabLog %s に切り替えました。起動し直します", version)
            self._update(state="restarting", message="新しいバージョンで起動し直しています…")
            self.restart()
        except Exception as exc:
            log.exception("更新に失敗しました")
            self._update(state="error", message=str(exc))

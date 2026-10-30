"""日報用のローカル AI（Ollama）の準備：入っているかの確認、インストール、モデルのダウンロード。

インストールは winget（Windows 標準のパッケージ管理）で行う。モデルは Ollama が自分のサイトからダウンロードする。
LabLog から Ollama へは PC 内（127.0.0.1）にしか接続しない（report.py と同じ）。
"""

import json
import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from .config import is_local_url
from .report import _no_proxy, ollama_status
from .winapi import CREATE_NO_WINDOW, run_quiet, total_memory_gb

log = logging.getLogger(__name__)

# 選べるモデル。memory_gb：これより少ないメモリでは重すぎる（Windows が報告する量は表示より少し少ないので 1 割ゆるめて比べる）
MODELS = [
    {"name": "qwen3:8b", "size_gb": 5.2, "memory_gb": 16, "label": "標準"},
    {"name": "qwen3:4b", "size_gb": 2.5, "memory_gb": 8, "label": "軽量"},
]
WINGET_ID = "Ollama.Ollama"
# LabLog が入れたもの（Ollama・ダウンロードしたモデル）。設定と同じフォルダに置き、uninstall.ps1 が読んで、消すかを聞く
AI_RECORD = "ai.json"
INSTALL_TIMEOUT_SEC = 30 * 60
START_WAIT_SEC = 120


def recommended_model(memory_gb: float) -> str | None:
    """この PC で動かせるいちばん良いモデル。どれも重すぎるなら None。"""
    for m in MODELS:
        if memory_gb >= m["memory_gb"] * 0.9:
            return m["name"]
    return None


def _install_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama"


def ollama_exe() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    exe = _install_dir() / "ollama.exe"
    return str(exe) if exe.is_file() else None


def setup_info(cfg: dict) -> dict:
    """設定の画面に出す情報（Ollama の状態・PC のメモリ・おすすめのモデル）。"""
    memory = total_memory_gb()
    return ollama_status(cfg) | {
        "installed": ollama_exe() is not None, "winget": shutil.which("winget") is not None,
        "memory_gb": round(memory, 1), "recommended": recommended_model(memory), "catalog": MODELS,
    }


def start_ollama():
    """Ollama を起動する（通知領域のアプリがあればそれを、無ければサーバーだけを）。"""
    app = _install_dir() / "ollama app.exe"
    exe = ollama_exe()
    if app.is_file():
        subprocess.Popen([str(app)], creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif exe:
        subprocess.Popen([exe, "serve"], creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class OllamaSetup:
    """インストールとモデルのダウンロードを、裏のスレッドで 1 つずつ進める（進み具合は status で見る）。"""

    def __init__(self, settings):
        self.settings = settings
        self.record_path = Path(settings.path).parent / AI_RECORD
        self._lock = threading.Lock()
        # state：idle / running / done / error。step：install / start / pull。progress：0〜1（分からなければ None）
        self._job: dict = {"state": "idle"}

    def status(self) -> dict:
        with self._lock:
            return dict(self._job)

    def start(self, install: bool, model: str | None):
        if model is not None and model not in {m["name"] for m in MODELS}:
            raise ValueError("モデル名が正しくありません")
        with self._lock:
            if self._job.get("state") == "running":
                raise RuntimeError("いま準備を進めています。終わるまでお待ちください")
            self._job = {"state": "running", "step": "install" if install else "start",
                         "message": "準備しています…", "progress": None, "model": model}
        threading.Thread(target=self._run, args=(install, model), name="ollama-setup", daemon=True).start()

    def _update(self, **fields):
        with self._lock:
            self._job.update(fields)

    def _record(self, ollama: bool = False, model: str | None = None):
        """LabLog が入れたものを ai.json に書き足す（もとから入っていたものは書かない）。"""
        try:
            rec = json.loads(self.record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rec = {}
        models = [m for m in rec.get("models") or [] if isinstance(m, str)]
        if model and model not in models:
            models.append(model)
        rec = {"ollama": bool(rec.get("ollama")) or ollama, "models": models}
        self.record_path.write_text(json.dumps(rec), encoding="utf-8")

    def _run(self, install: bool, model: str | None):
        try:
            if install:
                self._install()
            self._ensure_running()
            if model:
                self._pull(model)
            self._update(state="done", message="準備ができました", progress=1.0)
            log.info("ローカル AI の準備ができました（%s）", model or "モデルなし")
        except Exception as exc:
            log.exception("ローカル AI の準備に失敗しました")
            self._update(state="error", message=str(exc))

    def _install(self):
        winget = shutil.which("winget")
        if not winget:
            raise RuntimeError("winget が見つかりません。https://ollama.com/download から Ollama を入れてください")
        self._update(step="install", message="Ollama をインストールしています（数分かかります）…")
        result = run_quiet([winget, "install", "--id", WINGET_ID, "--exact", "--silent",
                            "--accept-source-agreements", "--accept-package-agreements", "--disable-interactivity"],
                           timeout=INSTALL_TIMEOUT_SEC)
        log.info("winget install %s: 終了コード %s", WINGET_ID, result.returncode)
        if ollama_exe() is None:
            raise RuntimeError(f"Ollama をインストールできませんでした（winget の終了コード {result.returncode}）。"
                               "https://ollama.com/download から入れてください")
        self._record(ollama=True)

    def _ensure_running(self):
        url = self.settings.get("ollama")["url"]
        if ollama_status({"url": url}).get("running"):
            return
        self._update(step="start", message="Ollama を起動しています…")
        began = time.time()
        started = False
        while time.time() - began < START_WAIT_SEC:
            if ollama_status({"url": url}).get("running"):
                return
            if not started and time.time() - began > 6:  # インストール後に自分で起動しなければ、こちらで起動する
                start_ollama()
                started = True
            time.sleep(2)
        raise RuntimeError("Ollama が起動しませんでした。スタートメニューから Ollama を起動してから、もう一度お試しください")

    def _pull(self, model: str):
        cfg = self.settings.get("ollama")
        url = cfg["url"]
        if not is_local_url(url):
            raise RuntimeError("Ollama の URL が PC 内ではありません")
        had = ollama_status({"url": url, "model": model}).get("model_ready")
        self._update(step="pull", message=f"モデル {model} をダウンロードしています…", progress=0.0)
        body = json.dumps({"model": model, "name": model, "stream": True}).encode("utf-8")
        req = urllib.request.Request(url.rstrip("/") + "/api/pull", data=body,
                                     headers={"Content-Type": "application/json"})
        with _no_proxy.open(req, timeout=3600) as r:
            for line in r:
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("error"):
                    raise RuntimeError(f"モデルをダウンロードできませんでした（{event['error']}）")
                total, done = event.get("total"), event.get("completed")
                if total and done is not None:  # 大きなファイル（モデル本体）の進み具合
                    self._update(progress=min(1.0, done / total))
                if event.get("status") == "success":
                    break
        if not had:
            self._record(model=model)
        self.settings.update({"ollama": cfg | {"enabled": True, "model": model}})

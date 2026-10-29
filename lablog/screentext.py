"""ウィンドウ名から作業内容が分からないアプリ（Claude・ChatGPT など）の画面の文字を読み取る。

対象アプリのウィンドウが前面にあるときだけ、一定の間隔でそのウィンドウを画面から写し取り、
Windows 標準の文字認識（OCR）で文字にする。画像は保存せず（メモリ上で文字にしたらすぐ捨てる）、
文字はこの PC の中だけに置いて日報の材料にし、設定の日数を過ぎたら消す。OneDrive には書き出さない。
"""

import base64
import json
import logging
import re
import subprocess
from pathlib import Path

from .winapi import CREATE_NO_WINDOW, capture_window, foreground_handle, window_info

log = logging.getLogger(__name__)

OCR_SCRIPT = Path(__file__).with_name("ocr.ps1")
# 左右の一覧（過去の会話の題名・ファイル一覧など）を除き、中央の本文だけを使う
CENTER_BAND = (0.18, 0.82)
MAX_CHARS = 2000
MIN_CHARS = 20
IDLE_LIMIT_SEC = 120  # これより長く操作していなければ読み取らない（画面が変わっていない）
_CJK = "[　-ヿ㐀-鿿豈-﫿＀-￯]"
_CJK_SPACE = re.compile(f"(?<={_CJK}) +(?={_CJK})")  # Windows の OCR は日本語の 1 文字ごとに空白を入れる


def tidy_lines(lines: list[dict]) -> str:
    """OCR の行から本文らしい部分を取り出し、上から順に並べて整える。"""
    kept = [line for line in lines if isinstance(line, dict) and str(line.get("t", "")).strip()
            and CENTER_BAND[0] <= float(line.get("x", 0.5)) <= CENTER_BAND[1]]
    kept.sort(key=lambda line: float(line.get("y", 0)))
    return "\n".join(_CJK_SPACE.sub("", str(line["t"]).strip()) for line in kept)[:MAX_CHARS]


def ocr_image(width: int, height: int, pixels: bytes, timeout: float = 60) -> list[dict]:
    """画素を Windows の文字認識に渡して、読めた行を返す（画像はファイルに書かず、標準入力で渡す）。"""
    payload = f"{width} {height}\n".encode("ascii") + base64.b64encode(pixels)
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-File", str(OCR_SCRIPT)], input=payload, capture_output=True, timeout=timeout,
                            creationflags=CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip()[-300:])
    data = json.loads(result.stdout.decode("utf-8-sig").strip() or "[]")
    return data if isinstance(data, list) else [data]


class ScreenTextRecorder:
    def __init__(self, conn):
        self.conn = conn
        self._last_ts: dict[str, float] = {}
        self._last_text: dict[str, str] = {}

    def tick(self, now: float, cfg: dict, excluded: set[str], paused: bool, idle: float) -> bool:
        """読み取ったら True。"""
        if not cfg.get("enabled") or paused or idle >= IDLE_LIMIT_SEC:
            return False
        hwnd = foreground_handle()
        if not hwnd:
            return False
        exe, title = window_info(hwnd)
        key = exe.lower()
        if key not in set(cfg.get("apps", [])) or key in excluded:
            return False
        if now - self._last_ts.get(key, 0) < cfg.get("interval_min", 5) * 60:
            return False
        self._last_ts[key] = now
        shot = capture_window(hwnd)
        if shot is None:
            return False
        text = tidy_lines(ocr_image(*shot))
        del shot
        if len(text) < MIN_CHARS or text == self._last_text.get(key):
            return False
        self._last_text[key] = text
        self.conn.execute("INSERT INTO screen_texts (ts, exe, title, text) VALUES (?, ?, ?, ?)",
                          (now, exe, title[:200], text))
        return True

    def cleanup(self, now: float, keep_days: float):
        self.conn.execute("DELETE FROM screen_texts WHERE ts < ?", (now - keep_days * 86400,))

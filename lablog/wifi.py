"""接続中の Wi-Fi（SSID）を調べ、大学・自宅・判定に使わない・その他に分類する。"""

import re

from .winapi import run_quiet

# 「SSID : xxx」の行だけを拾う（「AP BSSID」や「BSSID:」の行は対象外）。
# 未接続のインターフェースには SSID 行が出ないので、拾えた分だけが接続中。
_SSID_RE = re.compile(r"^\s*SSID\s*:\s?(.*?)\s*$", re.M)
_PERMISSION_HINTS = ("location", "位置情報")

PERMISSION_MESSAGE = "Wi-Fi 名を読むには、Windows の位置情報をオンにしてください。"

UNIV, HOME, IGNORED, OTHER, OFFLINE = "univ", "home", "ignored", "other", "offline"
# 判定に使わない Wi-Fi（テザリングなど）の場所は、そのときの滞在の状態で決める（main.py）
LOCATION_LABELS = {UNIV: "大学", HOME: "自宅", OTHER: "その他", OFFLINE: "その他"}
WIFI_KINDS = ("univ", "home", "ignore")  # 設定の wifi に登録する種類


def current_ssids() -> tuple[list[str], str | None]:
    """(接続中の SSID 一覧, 取得できなかったときの説明)。"""
    try:
        result = run_quiet(["netsh", "wlan", "show", "interfaces"], timeout=15, encoding="oem")
    except Exception as exc:  # netsh が無い・応答しない
        return [], f"Wi-Fi の状態を取得できませんでした（{exc}）"
    text = (result.stdout or "") + (result.stderr or "")
    ssids = [s for s in _SSID_RE.findall(text) if s] if result.returncode == 0 else []
    # Windows 11 では、位置情報の許可が無いと Wi-Fi 名を読めない（終了コードが 0 のまま案内だけを出すこともある）
    if not ssids and any(hint in text.lower() for hint in _PERMISSION_HINTS):
        return [], PERMISSION_MESSAGE
    return ssids, None  # 読めなければ、Wi-Fi アダプターが無いなど。未接続として扱う


def classify(ssids: list[str], wifi_settings: dict) -> str:
    univ = set(wifi_settings.get("univ", []))
    home = set(wifi_settings.get("home", []))
    ignore = set(wifi_settings.get("ignore", []))
    if any(s in univ for s in ssids):
        return UNIV
    if any(s in home for s in ssids):
        return HOME
    if any(s in ignore for s in ssids):
        return IGNORED
    return OTHER if ssids else OFFLINE

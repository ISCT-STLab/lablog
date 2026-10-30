"""Wi-Fi 名の読み取り（netsh の出力の読み方）のテスト。"""

import subprocess
import unittest
from unittest import mock

from lablog import wifi

CONNECTED = """
インターフェイス:
    名前                   : Wi-Fi
    状態                   : 接続されました
    SSID                   : ScienceTokyo
    AP BSSID               : 00:11:22:33:44:55
"""
NEED_LOCATION = ("ネットワーク シェル コマンドには、WLAN 情報にアクセスするための位置情報のアクセス許可が必要です。"
                 "[プライバシーとセキュリティ] 設定の [位置情報] ページで位置情報サービスをオンにしてください。")


def run(stdout, code=0):
    return mock.patch.object(wifi, "run_quiet", return_value=subprocess.CompletedProcess([], code, stdout, ""))


class SsidTest(unittest.TestCase):
    def test_connected(self):
        with run(CONNECTED):
            self.assertEqual(wifi.current_ssids(), (["ScienceTokyo"], None))

    def test_location_permission_with_error_code(self):
        with run(NEED_LOCATION, code=1):
            self.assertEqual(wifi.current_ssids(), ([], wifi.PERMISSION_MESSAGE))

    def test_location_permission_with_success_code(self):
        with run(NEED_LOCATION, code=0):  # 終了コードが 0 のまま、案内だけを出すことがある
            self.assertEqual(wifi.current_ssids(), ([], wifi.PERMISSION_MESSAGE))

    def test_no_adapter(self):
        with run("システムにワイヤレス インターフェイスがありません。", code=1):
            self.assertEqual(wifi.current_ssids(), ([], None))


if __name__ == "__main__":
    unittest.main()

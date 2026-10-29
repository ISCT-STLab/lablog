"""ダッシュボードの合言葉（Cookie）のテスト。"""

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from lablog.collectors import _strip_dashboard_key
from lablog.server import make_handler

KEY = "k" * 43


class StubApi:
    def get_status(self, q):
        return {"ok": True}

    def post_pause(self, body):
        return {"paused": body.get("paused")}


class KeyTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = make_handler(StubApi(), KEY, self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, cookie=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": f"127.0.0.1:{self.port}"}
        if cookie:
            headers["Cookie"] = cookie
        if method == "POST":
            headers |= {"Content-Type": "application/json", "X-LabLog": "1"}
        conn.request(method, path, body=json.dumps(body or {}) if method == "POST" else None, headers=headers)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res, data

    def cookie(self, value=KEY):
        return f"lablog_key_{self.port}={value}"

    def test_static_files_open_without_key(self):
        res, _ = self.request("GET", "/")
        self.assertEqual(res.status, 200)

    def test_api_needs_key(self):
        res, data = self.request("GET", "/api/status")
        self.assertEqual(res.status, 401)
        self.assertEqual(json.loads(data)["code"], "key_required")
        res, _ = self.request("POST", "/api/pause", body={"paused": True})
        self.assertEqual(res.status, 401)
        res, _ = self.request("GET", "/api/export/stays.csv?month=2026-10")
        self.assertEqual(res.status, 401)

    def test_wrong_key_is_rejected(self):
        res, _ = self.request("GET", "/api/status", cookie=self.cookie("x" * 43))
        self.assertEqual(res.status, 401)
        res, _ = self.request("GET", "/?key=" + "x" * 43)
        self.assertEqual(res.status, 200)  # 画面は出るが Cookie は作らない
        self.assertIsNone(res.getheader("Set-Cookie"))

    def test_key_in_url_sets_cookie_and_is_removed(self):
        res, _ = self.request("GET", f"/?key={KEY}&level=4")
        self.assertEqual(res.status, 303)
        self.assertEqual(res.getheader("Location"), "/?level=4")
        cookie = res.getheader("Set-Cookie")
        self.assertIn(self.cookie(), cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_api_with_key(self):
        res, data = self.request("GET", "/api/status", cookie=self.cookie())
        self.assertEqual(res.status, 200)
        self.assertEqual(json.loads(data), {"ok": True})
        res, data = self.request("POST", "/api/pause", cookie=self.cookie(), body={"paused": True})
        self.assertEqual(json.loads(data), {"paused": True})

    def test_cookie_of_other_port_is_ignored(self):
        res, _ = self.request("GET", "/api/status", cookie=f"lablog_key_{self.port + 1}={KEY}")
        self.assertEqual(res.status, 401)


class StripKeyTest(unittest.TestCase):
    def strip(self, url):
        return _strip_dashboard_key(url, urlparse(url))

    def test_dashboard_url(self):
        self.assertEqual(self.strip("http://127.0.0.1:8765/?key=abc"), "http://127.0.0.1:8765/")
        self.assertEqual(self.strip("http://127.0.0.1:8765/?key=abc&level=4"), "http://127.0.0.1:8765/?level=4")

    def test_other_sites_are_kept(self):
        url = "https://example.com/?key=abc"
        self.assertEqual(self.strip(url), url)


if __name__ == "__main__":
    unittest.main()

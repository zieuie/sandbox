#!/usr/bin/env python3
"""Check routing, static file confinement, headers and compression."""

from __future__ import annotations

import gzip
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import fixture
import server
import snapshot


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = tempfile.TemporaryDirectory()
        deployments = Path(cls.directory.name)
        fixture.live_campaign(deployments)
        snapshots = snapshot.Snapshots(deployments, "live")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(snapshots))
        cls.httpd.quiet = True
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.directory.cleanup()

    def fetch(self, path, method="GET", headers=None):
        try:
            with urlopen(Request(self.base + path, method=method, headers=headers or {})) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    def test_index_and_security_headers(self) -> None:
        status, headers, body = self.fetch("/")
        self.assertEqual(status, 200)
        self.assertIn(b"/static/app.js", body)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_static_files_are_confined(self) -> None:
        self.assertEqual(self.fetch("/static/app.js")[0], 200)
        for path in ("/static/../server.py", "/static/%2e%2e/server.py", "/static/tests/fixture.py",
                     "/static/missing.js", "/server.py", "/static/APP.JS"):
            self.assertEqual(self.fetch(path)[0], 404, path)

    def test_snapshot_json_and_gzip(self) -> None:
        status, headers, body = self.fetch("/api/snapshot")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        plain = json.loads(body)
        self.assertEqual(plain["campaign"], "live")
        status, headers, body = self.fetch("/api/snapshot", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(headers["Content-Encoding"], "gzip")
        self.assertEqual(json.loads(gzip.decompress(body))["generated_at"], plain["generated_at"])

    def test_refresh_requires_post(self) -> None:
        self.assertEqual(self.fetch("/api/refresh", method="POST")[0], 200)
        self.assertEqual(self.fetch("/api/refresh")[0], 404)
        self.assertEqual(self.fetch("/api/snapshot", method="POST")[0], 404)

    def test_health(self) -> None:
        status, _, body = self.fetch("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])


if __name__ == "__main__":
    unittest.main()

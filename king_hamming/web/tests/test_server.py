#!/usr/bin/env python3
"""Check routing, static file confinement, headers and compression (signed in)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
import tempfile
import unittest

import fixture


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        fixture.live_campaign(root / "deployments")
        cls.httpd, cls.base, _ = fixture.serve(root / "deployments", root / "state")
        cls.client = fixture.Client(cls.base)
        assert cls.client.login("tester", fixture.PASSWORD)[0] == 200

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.directory.cleanup()

    def test_index_and_security_headers(self) -> None:
        status, headers, body = self.client.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"/static/app.js", body)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_static_files_are_confined(self) -> None:
        self.assertEqual(self.client.request("/static/app.js")[0], 200)
        for path in ("/static/../server.py", "/static/%2e%2e/server.py", "/static/tests/fixture.py",
                     "/static/missing.js", "/server.py", "/static/APP.JS"):
            self.assertEqual(self.client.request(path)[0], 404, path)

    def test_snapshot_json_and_gzip(self) -> None:
        status, headers, body = self.client.request("/api/snapshot")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        plain = json.loads(body)
        self.assertEqual(plain["campaign"], "live")
        status, headers, body = self.client.request("/api/snapshot", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(headers["Content-Encoding"], "gzip")
        self.assertEqual(json.loads(gzip.decompress(body))["generated_at"], plain["generated_at"])

    def test_refresh_requires_post(self) -> None:
        self.assertEqual(self.client.request("/api/refresh", "POST", {})[0], 200)
        self.assertEqual(self.client.request("/api/refresh")[0], 404)
        self.assertEqual(self.client.request("/api/snapshot", "POST", {})[0], 404)

    def test_health_is_public_and_minimal(self) -> None:
        status, _, body = fixture.Client(self.base).json("/api/health")
        self.assertEqual((status, body), (200, {"ok": True}))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Check accounts, sessions, CSRF, origin checks, throttling and auditing."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import fixture  # sets up the import path
import auth as auth_module  # noqa: E402
from auth import Auth, Throttle, hash_password, verify_password  # noqa: E402


class Clock:
    def __init__(self) -> None:
        self.value = 1_000_000.0

    def __call__(self) -> float:
        return self.value


class UnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.auth = Auth(Path(self.directory.name) / "state", clock=self.clock)
        self.auth.add_user("zooey", fixture.PASSWORD, "operator")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_hashes(self) -> None:
        encoded = hash_password("a long password")
        self.assertTrue(verify_password("a long password", encoded))
        self.assertFalse(verify_password("a long passwore", encoded))
        self.assertNotEqual(encoded, hash_password("a long password"))  # salted
        self.assertFalse(verify_password("x", "garbage"))

    def test_files_are_private_and_hold_no_plain_secrets(self) -> None:
        state = Path(self.directory.name) / "state"
        self.assertEqual(state.stat().st_mode & 0o777, 0o700)
        self.assertEqual((state / "users.json").stat().st_mode & 0o777, 0o600)
        self.assertNotIn(fixture.PASSWORD, (state / "users.json").read_text())
        token, _ = self.auth.login("zooey", fixture.PASSWORD, "127.0.0.1")
        stored = sqlite3.connect(state / "sessions.sqlite").execute("SELECT token_hash FROM sessions").fetchall()
        self.assertEqual(stored, [(auth_module.token_digest(token),)])

    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            self.auth.add_user("zooey", fixture.PASSWORD)  # exists
        with self.assertRaises(ValueError):
            self.auth.add_user("Bad Name", fixture.PASSWORD)
        with self.assertRaises(ValueError):
            self.auth.add_user("short", "tooshort")
        with self.assertRaises(ValueError):
            self.auth.add_user("samesame11", "samesame11")
        with self.assertRaises(ValueError):
            self.auth.add_user("someone", fixture.PASSWORD, "admin")

    def test_session_lifetimes(self) -> None:
        token, user = self.auth.login("zooey", fixture.PASSWORD, "127.0.0.1")
        self.assertEqual(user, {"name": "zooey", "role": "operator"})
        self.assertEqual(self.auth.session(token)["role"], "operator")
        self.assertIsNone(self.auth.login("zooey", "wrong password!", "127.0.0.1"))
        self.assertIsNone(self.auth.login("nobody", fixture.PASSWORD, "127.0.0.1"))
        # Idle expiry, refreshed by use.
        self.clock.value += auth_module.IDLE_SECONDS - 100
        self.assertIsNotNone(self.auth.session(token))
        self.clock.value += auth_module.IDLE_SECONDS - 100
        self.assertIsNotNone(self.auth.session(token))
        self.clock.value += auth_module.IDLE_SECONDS + 1
        self.assertIsNone(self.auth.session(token))
        # Absolute expiry even when active.
        token, _ = self.auth.login("zooey", fixture.PASSWORD, "127.0.0.1")
        for _ in range(8):
            self.clock.value += 23 * 3600
            self.auth.session(token)
        self.assertIsNone(self.auth.session(token))

    def test_role_changes_apply_and_removal_or_password_change_revokes(self) -> None:
        token, _ = self.auth.login("zooey", fixture.PASSWORD, "127.0.0.1")
        self.auth.set_role("zooey", "viewer")
        self.assertEqual(self.auth.session(token)["role"], "viewer")
        self.auth.set_password("zooey", fixture.PASSWORD + "2")
        self.assertIsNone(self.auth.session(token))
        self.assertEqual(self.auth.users()["zooey"]["role"], "viewer")  # role kept
        token, _ = self.auth.login("zooey", fixture.PASSWORD + "2", "127.0.0.1")
        self.auth.remove_user("zooey")
        self.assertIsNone(self.auth.session(token))

    def test_reauthentication_window(self) -> None:
        token, _ = self.auth.login("zooey", fixture.PASSWORD, "127.0.0.1")
        session = self.auth.session(token)
        self.assertTrue(self.auth.recently_authenticated(session))  # login counts
        self.clock.value += auth_module.REAUTH_SECONDS + 1
        session = self.auth.session(token)
        self.assertFalse(self.auth.recently_authenticated(session))
        self.assertFalse(self.auth.reauthenticate(session, "wrong password!"))
        self.assertTrue(self.auth.reauthenticate(session, fixture.PASSWORD))
        self.assertTrue(self.auth.recently_authenticated(self.auth.session(token)))

    def test_throttle(self) -> None:
        throttle = Throttle(self.clock)
        for _ in range(Throttle.FREE):
            self.assertEqual(throttle.wait("k"), 0)
            throttle.failed("k")
        self.assertEqual(throttle.wait("k"), 0)
        throttle.failed("k")
        self.assertEqual(throttle.wait("k"), 2)
        throttle.failed("k")
        self.assertEqual(throttle.wait("k"), 4)
        throttle.succeeded("k")
        self.assertEqual(throttle.wait("k"), 0)
        for _ in range(40):
            throttle.failed("j")
        self.assertEqual(throttle.wait("j"), Throttle.MAXIMUM)


class HttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        fixture.live_campaign(root / "deployments")
        self.state = root / "state"
        self.httpd, self.base, self.auth = fixture.serve(root / "deployments", self.state)

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.directory.cleanup()

    def audit(self) -> list[dict]:
        return [json.loads(line) for line in (self.state / "audit.jsonl").read_text().splitlines()]

    def test_signed_out_requests(self) -> None:
        client = fixture.Client(self.base)
        status, headers, _ = client.request("/")
        self.assertEqual((status, headers["Location"]), (303, "/login?next=%2F"))
        self.assertEqual(client.request("/api/snapshot")[0], 401)
        self.assertEqual(client.request("/static/app.js")[0], 401)
        for public in ("/login", "/static/login.js", "/static/style.css", "/static/icon.svg"):
            self.assertEqual(client.request(public)[0], 200, public)
        self.assertEqual(client.request("/api/refresh", "POST", {})[0], 401)

    def test_login_flow_and_cookie(self) -> None:
        client = fixture.Client(self.base)
        status, _, value = client.login("tester", "wrong password!")
        self.assertEqual((status, value["error"]), (401, "incorrect user name or password"))
        status, headers, value = client.json("/api/login", "POST",
                                             {"user": "Tester", "password": fixture.PASSWORD,
                                              "next": "//evil.example/"})
        self.assertEqual(status, 200)
        self.assertEqual((value["user"], value["role"], value["next"]), ("tester", "operator", "/"))
        cookie = headers["Set-Cookie"]
        for flag in ("HttpOnly", "SameSite=Lax", "Path=/"):
            self.assertIn(flag, cookie)
        self.assertNotIn("Secure", cookie)  # plain HTTP on the LAN
        client.cookie = cookie.split(";")[0]
        status, _, info = client.json("/api/session")
        self.assertEqual((status, info["user"], info["csrf"]), (200, "tester", value["csrf"]))
        self.assertEqual(client.request("/login")[0], 303)  # already signed in
        self.assertEqual([(e["action"], e["outcome"]) for e in self.audit()],
                         [("login", "failed"), ("login", "ok")])

    def test_csrf_and_origin(self) -> None:
        client = fixture.Client(self.base)
        client.login("tester", fixture.PASSWORD)
        self.assertEqual(client.request("/api/refresh", "POST", {}, csrf=False)[0], 403)
        self.assertEqual(client.request("/api/refresh", "POST", {},
                                        headers={"X-CSRF-Token": "forged"})[0], 403)
        self.assertEqual(client.request("/api/refresh", "POST", {},
                                        headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(client.request("/api/refresh", "POST", {},
                                        headers={"Origin": "", "Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(client.request("/api/refresh", "POST", {},
                                        headers={"Content-Type": "text/plain"})[0], 200)  # refresh has no body
        status, _, _ = client.request("/api/login", "POST", None,
                                      headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)  # simple cross-site forms cannot log in
        self.assertEqual(client.request("/api/refresh", "POST", {})[0], 200)

    def test_logout(self) -> None:
        client = fixture.Client(self.base)
        client.login("tester", fixture.PASSWORD)
        status, headers, _ = client.request("/api/logout", "POST", {})
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(client.request("/api/snapshot")[0], 401)

    def test_throttled_login(self) -> None:
        client = fixture.Client(self.base)
        for _ in range(5):
            client.login("tester", "wrong password!")
        status, headers, value = client.login("tester", fixture.PASSWORD)
        self.assertEqual(status, 429)
        self.assertIn("Retry-After", headers)
        self.assertGreater(value["retry_after"], 0)
        self.assertIn(("login", "throttled"), [(e["action"], e["outcome"]) for e in self.audit()])

    def test_reauth_and_password_change(self) -> None:
        client = fixture.Client(self.base)
        client.login("tester", fixture.PASSWORD)
        self.assertEqual(client.request("/api/reauth", "POST", {"password": "nope nope nope"})[0], 401)
        status, _, info = client.json("/api/reauth", "POST", {"password": fixture.PASSWORD})
        self.assertEqual(status, 200)
        self.assertGreater(info["reauth_until"], 0)
        self.assertEqual(client.request("/api/password", "POST",
                                        {"current": "nope nope nope", "new": "another long one"})[0], 401)
        self.assertEqual(client.request("/api/password", "POST",
                                        {"current": fixture.PASSWORD, "new": "short"})[0], 400)
        self.assertEqual(client.request("/api/password", "POST",
                                        {"current": fixture.PASSWORD, "new": "another long one"})[0], 200)
        self.assertEqual(client.request("/api/snapshot")[0], 401)  # signed out everywhere
        self.assertEqual(fixture.Client(self.base).login("tester", "another long one")[0], 200)

    def test_proxy_headers_only_when_trusted(self) -> None:
        client = fixture.Client(self.base)
        client.request("/api/login", "POST", {"user": "x", "password": "y"},
                       headers={"CF-Connecting-IP": "203.0.113.9"})
        self.assertEqual(self.audit()[-1]["address"], "127.0.0.1")
        self.httpd.shutdown()
        self.httpd.server_close()
        root = Path(self.directory.name)
        self.state = root / "state2"
        self.httpd, self.base, _ = fixture.serve(root / "deployments", self.state, trust_proxy=True)
        client = fixture.Client(self.base)
        status, headers, _ = client.json(
            "/api/login", "POST", {"user": "tester", "password": fixture.PASSWORD},
            headers={"CF-Connecting-IP": "203.0.113.9", "X-Forwarded-Proto": "https"})
        self.assertEqual(status, 200)
        self.assertIn("Secure", headers["Set-Cookie"])
        self.assertEqual(self.audit()[-1]["address"], "203.0.113.9")


class HardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        fixture.live_campaign(root / "deployments")
        self.root = root

    def tearDown(self) -> None:
        self.directory.cleanup()

    def serve(self, **options):
        httpd, base, auth = fixture.serve(self.root / "deployments", self.root / f"state{len(options)}", **options)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd, base

    def test_safe_next_rejects_disguised_external_targets(self) -> None:
        import server
        for good in ("/", "/#fleet", "/#tiles/abc/1,2", "/?x=1#results/2,29"):
            self.assertEqual(server.safe_next(good), good)
        for bad in ("//evil.example", "/\\evil.example", "/\t/evil.example", "/\n/evil.example",
                    "https://evil.example", "/ /evil", "", None, "/" + "a" * 600, "javascript:alert(1)"):
            self.assertEqual(server.safe_next(bad), "/", repr(bad))

    def test_forwarded_addresses(self) -> None:
        _, base = self.serve(trust_proxy=True)
        client = fixture.Client(base)
        state = self.root / "state1"
        def last_address():
            return json.loads((state / "audit.jsonl").read_text().splitlines()[-1])["address"]
        client.request("/api/login", "POST", {"user": "x", "password": "y"},
                       headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.7"})
        self.assertEqual(last_address(), "203.0.113.7")  # the hop our proxy added, not the claim
        client.request("/api/login", "POST", {"user": "x", "password": "y"},
                       headers={"X-Forwarded-For": "not-an-address"})
        self.assertEqual(last_address(), "127.0.0.1")

    def test_trusted_proxy_on_another_machine(self) -> None:
        # The test client connects from 127.0.0.1, which is not the named proxy.
        _, base = self.serve(trusted_proxies=("192.168.4.200",))
        client = fixture.Client(base)
        client.request("/api/login", "POST", {"user": "x", "password": "y"},
                       headers={"CF-Connecting-IP": "203.0.113.9"})
        line = (self.root / "state1" / "audit.jsonl").read_text().splitlines()[-1]
        self.assertEqual(json.loads(line)["address"], "127.0.0.1")

    def test_https_cookie_and_headers(self) -> None:
        _, base = self.serve(trust_proxy=True)
        client = fixture.Client(base)
        status, headers, _ = client.json("/api/login", "POST", {"user": "tester", "password": fixture.PASSWORD},
                                         headers={"X-Forwarded-Proto": "https"})
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"]
        self.assertTrue(cookie.startswith("__Host-kh_session="))
        for flag in ("Secure", "HttpOnly", "SameSite=Lax", "Path=/"):
            self.assertIn(flag, cookie)
        self.assertNotIn("Domain", cookie)
        self.assertEqual(headers["Strict-Transport-Security"], "max-age=31536000")
        client.cookie = cookie.split(";")[0]
        status, headers, _ = client.request("/api/session")
        self.assertEqual(status, 200)  # the __Host- cookie is accepted
        self.assertEqual(headers["Cross-Origin-Opener-Policy"], "same-origin")
        self.assertNotIn("Python", headers["Server"])

    def test_malformed_requests_and_audit_access(self) -> None:
        _, base = self.serve()
        guest = fixture.Client(base)
        guest.login("guest", fixture.PASSWORD + "!")
        self.assertEqual(guest.request("/api/audit")[0], 403)
        operator = fixture.Client(base)
        operator.login("tester", fixture.PASSWORD)
        self.assertEqual(operator.request("/api/audit")[0], 200)
        status = operator.request("/api/reauth", "POST", {"password": "x"},
                                  headers={"Content-Length": "abc"})[0]
        self.assertEqual(status, 400)

    def test_connection_cap(self) -> None:
        import socket
        import threading
        from http.server import ThreadingHTTPServer  # noqa: F401
        import server
        httpd = server.DashboardServer(("127.0.0.1", 0), server.BaseHTTPRequestHandler, max_connections=1)
        self.addCleanup(httpd.server_close)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        idle = socket.create_connection(("127.0.0.1", httpd.server_port))  # holds the only slot
        self.addCleanup(idle.close)
        import time
        time.sleep(0.2)
        second = socket.create_connection(("127.0.0.1", httpd.server_port))
        second.settimeout(5)
        second.sendall(b"GET / HTTP/1.0\r\n\r\n")
        self.assertEqual(second.recv(100), b"")  # refused: closed without a response
        second.close()


if __name__ == "__main__":
    unittest.main()

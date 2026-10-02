#!/usr/bin/env python3
"""Serve the King Hamming campaign dashboard behind a login.

Users and sessions live in a private state directory outside the repository.
Create the first account before serving:

  python3 king_hamming/web/server.py add-user NAME --role operator
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field as dataclass_field
import getpass
import hashlib
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import re
import socket
import sys
import threading
import time
from urllib.parse import parse_qs, quote, urlparse

from snapshot import ROOT, Snapshots  # first: sets up the import path
from audit import Audit  # noqa: E402
from auth import DEFAULT_STATE, ROLES, SESSION_SECONDS, Auth, check_password  # noqa: E402
from commands import CommandError, CommandService, Context  # noqa: E402
from jobs import Jobs, git_state  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
_CODE_VERSION: tuple[tuple, str] = ((), "")


def code_version() -> str:
    """Short hash of the static front end; open pages reload when it changes after a redeploy."""
    global _CODE_VERSION
    files = sorted(path for path in STATIC.iterdir() if path.is_file())
    key = tuple((path.name, path.stat().st_mtime_ns, path.stat().st_size) for path in files)
    if key != _CODE_VERSION[0]:
        digest = hashlib.sha256()
        for path in files:
            digest.update(path.name.encode() + b"\0" + path.read_bytes())
        _CODE_VERSION = (key, digest.hexdigest()[:16])
    return _CODE_VERSION[1]
STATIC_NAME = re.compile(r"^[a-z0-9_-]+\.(html|js|css|svg)$")
PUBLIC_STATIC = {"login.html", "login.js", "style.css", "icon.svg"}
CONTENT_TYPES = {
    "html": "text/html; charset=utf-8",
    "js": "text/javascript; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "svg": "image/svg+xml",
}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
COOKIE = "kh_session"
SECURE_COOKIE = "__Host-kh_session"  # browsers bind this name to HTTPS, this exact host, Path=/
MAX_BODY = 64 * 1024
MAX_CONNECTIONS = 64
REQUEST_TIMEOUT = 30          # seconds a connection may sit idle (defeats slow-drip connections)
PASSWORD_CHECKS = threading.BoundedSemaphore(4)  # each scrypt check costs ~80 ms and 32 MiB
SAFE_PATH = re.compile(r"/(?!/)[A-Za-z0-9._~!$&'()*+,;=:@%/#?-]*")


@dataclass
class Config:
    snapshots: Snapshots
    auth: Auth
    audit: Audit
    commands: CommandService | None = None
    trust_proxy: bool = False      # honour CF-Connecting-IP / X-Forwarded-* from a loopback proxy
    secure_cookies: bool = False   # always mark the cookie Secure (otherwise only on proxied HTTPS)
    trusted_proxies: tuple = dataclass_field(default_factory=tuple)  # further proxy addresses to trust


class Reject(Exception):
    def __init__(self, status: HTTPStatus, message: str, **extra) -> None:
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


def safe_next(value: str | None) -> str:
    """Only allow plain local paths as post-login redirects.

    Browsers strip tabs and newlines while parsing URLs, so "/\t/evil.example"
    would become "//evil.example"; only printable, unambiguous path characters
    are accepted, and never a leading "//" or a backslash.
    """
    if isinstance(value, str) and len(value) <= 512 and SAFE_PATH.fullmatch(value):
        return value
    return "/"


@contextmanager
def password_check():
    """Bound concurrent password hashing so a flood cannot exhaust memory."""
    if not PASSWORD_CHECKS.acquire(timeout=5):
        raise Reject(HTTPStatus.SERVICE_UNAVAILABLE, "the server is busy; try again", retry_after=2)
    try:
        yield
    finally:
        PASSWORD_CHECKS.release()


class DashboardServer(ThreadingHTTPServer):
    """A threading server that refuses connections beyond a fixed number."""

    daemon_threads = True

    def __init__(self, *arguments, max_connections: int = MAX_CONNECTIONS, **options) -> None:
        super().__init__(*arguments, **options)
        self.slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request, client_address) -> None:
        if not self.slots.acquire(blocking=False):
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def make_handler(config: Config) -> type[BaseHTTPRequestHandler]:
    snapshots, auth, audit = config.snapshots, config.auth, config.audit

    class Handler(BaseHTTPRequestHandler):
        server_version = "king-hamming-dashboard"
        sys_version = ""  # do not advertise the Python version
        timeout = REQUEST_TIMEOUT

        # ----- request facts ---------------------------------------------

        def proxied(self) -> bool:
            """True only for a connection from a proxy we were told to trust."""
            try:
                peer = ipaddress.ip_address(self.client_address[0])
            except ValueError:
                return False
            return ((config.trust_proxy and peer.is_loopback) or
                    any(peer == ipaddress.ip_address(address) for address in config.trusted_proxies))

        def address(self) -> str:
            if self.proxied():
                # Cloudflare sets CF-Connecting-IP itself, overwriting anything a visitor sends.
                # In X-Forwarded-For only the last hop was added by our proxy; earlier ones are
                # whatever the visitor claimed.
                forwarded = (self.headers.get("CF-Connecting-IP") or
                             (self.headers.get("X-Forwarded-For") or "").split(",")[-1]).strip()
                try:
                    return str(ipaddress.ip_address(forwarded))
                except ValueError:
                    pass
            return self.client_address[0]

        def https(self) -> bool:
            return config.secure_cookies or (
                self.proxied() and self.headers.get("X-Forwarded-Proto", "").lower() == "https")

        def host(self) -> str:
            if self.proxied() and self.headers.get("X-Forwarded-Host"):
                return self.headers["X-Forwarded-Host"]
            return self.headers.get("Host", "")

        def token(self) -> str | None:
            try:
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
            except CookieError:
                return None
            for name in (SECURE_COOKIE, COOKIE):
                if name in cookie and cookie[name].value:
                    return cookie[name].value
            return None

        def current_session(self) -> dict | None:
            return auth.session(self.token())

        # ----- responses -------------------------------------------------

        def send(self, status: HTTPStatus, body: bytes, content_type: str,
                 gzipped: bytes | None = None, cache: str = "no-cache",
                 headers: dict[str, str] | None = None) -> None:
            encoding = None
            if gzipped is not None and "gzip" in self.headers.get("Accept-Encoding", ""):
                body, encoding = gzipped, "gzip"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Dashboard-Version", code_version())
            if encoding:
                self.send_header("Content-Encoding", encoding)
                self.send_header("Vary", "Accept-Encoding")
            extra = {"Strict-Transport-Security": "max-age=31536000"} if self.https() else {}
            for name, value in {**SECURITY_HEADERS, **extra, **(headers or {})}.items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_json(self, status: HTTPStatus, value, headers: dict[str, str] | None = None) -> None:
            self.send(status, json.dumps(value).encode() + b"\n", "application/json",
                      cache="no-store", headers=headers)

        def redirect(self, location: str) -> None:
            self.send(HTTPStatus.SEE_OTHER, b"", "text/plain", headers={"Location": location})

        def static(self, name: str) -> None:
            path = STATIC / name
            if STATIC_NAME.match(name) and path.is_file():
                self.send(HTTPStatus.OK, path.read_bytes(), CONTENT_TYPES[name.rsplit(".", 1)[1]])
            else:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def cookie(self, token: str, max_age: int) -> dict[str, str]:
            if self.https():
                return {"Set-Cookie": f"{SECURE_COOKIE}={token}; Path=/; HttpOnly; Secure; "
                                      f"SameSite=Lax; Max-Age={max_age}"}
            return {"Set-Cookie": f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"}

        def session_info(self, session: dict) -> dict:
            return {"user": session["user"], "role": session["role"], "csrf": session["csrf"],
                    "reauth_until": session["reauth_until"]}

        # ----- POST checks -----------------------------------------------

        def body(self) -> dict:
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                raise Reject(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "expected application/json")
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                raise Reject(HTTPStatus.BAD_REQUEST, "invalid Content-Length") from None
            if not 0 <= length <= MAX_BODY:
                raise Reject(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")
            try:
                value = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                raise Reject(HTTPStatus.BAD_REQUEST, "invalid JSON") from None
            if not isinstance(value, dict):
                raise Reject(HTTPStatus.BAD_REQUEST, "expected a JSON object")
            return value

        def same_origin(self) -> None:
            origin = self.headers.get("Origin")
            if origin is None:
                # Browsers always send Origin on cross-site POSTs; fall back to Fetch metadata.
                if self.headers.get("Sec-Fetch-Site", "same-origin") not in {"same-origin", "none"}:
                    raise Reject(HTTPStatus.FORBIDDEN, "cross-site request refused")
                return
            if urlparse(origin).netloc != self.host():
                raise Reject(HTTPStatus.FORBIDDEN, "cross-site request refused")

        def authorized(self) -> dict:
            """Require a live session and a matching CSRF token."""
            session = self.current_session()
            if session is None:
                raise Reject(HTTPStatus.UNAUTHORIZED, "login required")
            if self.headers.get("X-CSRF-Token") != session["csrf"]:
                raise Reject(HTTPStatus.FORBIDDEN, "missing or invalid CSRF token")
            return session

        # ----- routes ----------------------------------------------------

        def do_GET(self) -> None:
            url = urlparse(self.path)
            route = url.path
            if route == "/api/health":
                self.send_json(HTTPStatus.OK, {"ok": True})
                return
            if route == "/login":
                if self.current_session():
                    self.redirect(safe_next(parse_qs(url.query).get("next", ["/"])[0]))
                else:
                    self.static("login.html")
                return
            if route.startswith("/static/") and route[len("/static/"):] in PUBLIC_STATIC:
                self.static(route[len("/static/"):])
                return

            session = self.current_session()
            if session is None:
                if route.startswith("/api/") or route.startswith("/static/"):
                    self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "login required"})
                else:
                    self.redirect("/login?next=" + quote(self.path, safe=""))
                return
            if route == "/":
                self.static("index.html")
            elif route.startswith("/static/"):
                self.static(route[len("/static/"):])
            elif route == "/api/session":
                self.send_json(HTTPStatus.OK, self.session_info(session))
            elif route == "/api/snapshot":
                self.send_snapshot(force=False)
            elif route == "/api/audit":
                if session["role"] != "operator":
                    self.send_json(HTTPStatus.FORBIDDEN, {"error": "the audit log is for operators"})
                else:
                    self.send_json(HTTPStatus.OK, {"entries": audit.recent(200)})
            elif route == "/api/jobs" and config.commands:
                context = config.commands.context
                rollout = context.state / "last_rollout.json"
                try:
                    rollout_value = json.loads(rollout.read_text()) if rollout.exists() else None
                except ValueError:
                    rollout_value = None
                self.send_json(HTTPStatus.OK, {"jobs": context.jobs.list(), "git": git_state(ROOT),
                                               "rollout": rollout_value})
            else:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        do_HEAD = do_GET

        def do_POST(self) -> None:
            route = urlparse(self.path).path
            try:
                self.same_origin()
                handler = {
                    "/api/login": self.post_login,
                    "/api/logout": self.post_logout,
                    "/api/refresh": self.post_refresh,
                    "/api/reauth": self.post_reauth,
                    "/api/password": self.post_password,
                    "/api/command/preview": self.post_command_preview,
                    "/api/command/run": self.post_command_run,
                }.get(route)
                if handler is None:
                    raise Reject(HTTPStatus.NOT_FOUND, "not found")
                handler()
            except Reject as rejection:
                headers = ({"Retry-After": str(int(rejection.extra["retry_after"]) + 1)}
                           if "retry_after" in rejection.extra else None)
                self.send_json(rejection.status, {"error": rejection.message, **rejection.extra}, headers)

        def post_login(self) -> None:
            request = self.body()
            name = str(request.get("user", "")).strip().lower()[:64]
            password = str(request.get("password", ""))
            keys = (f"address:{self.address()}", f"user:{name}")
            wait = auth.throttle.wait(*keys)
            if wait > 0:
                audit.record("login", name, self.address(), "throttled")
                raise Reject(HTTPStatus.TOO_MANY_REQUESTS, "too many attempts; try again later",
                             retry_after=round(wait, 1))
            with password_check():
                result = auth.login(name, password, self.address(), self.headers.get("User-Agent", ""))
            if result is None:
                auth.throttle.failed(*keys)
                audit.record("login", name, self.address(), "failed")
                raise Reject(HTTPStatus.UNAUTHORIZED, "incorrect user name or password")
            token, user = result
            auth.throttle.succeeded(*keys)
            audit.record("login", name, self.address(), "ok")
            session = auth.session(token)
            self.send_json(HTTPStatus.OK, {**self.session_info(session),
                                           "next": safe_next(request.get("next"))},
                           self.cookie(token, SESSION_SECONDS))

        def post_logout(self) -> None:
            session = self.authorized()
            auth.logout(session)
            audit.record("logout", session["user"], self.address(), "ok")
            self.send_json(HTTPStatus.OK, {"ok": True}, self.cookie("", 0))

        def post_refresh(self) -> None:
            self.authorized()
            self.send_snapshot(force=True)

        def post_reauth(self) -> None:
            session = self.authorized()
            key = f"user:{session['user']}"
            wait = auth.throttle.wait(key)
            if wait > 0:
                raise Reject(HTTPStatus.TOO_MANY_REQUESTS, "too many attempts; try again later",
                             retry_after=round(wait, 1))
            password = str(self.body().get("password", ""))
            with password_check():
                confirmed = auth.reauthenticate(session, password)
            if not confirmed:
                auth.throttle.failed(key)
                audit.record("reauth", session["user"], self.address(), "failed")
                raise Reject(HTTPStatus.UNAUTHORIZED, "incorrect password")
            auth.throttle.succeeded(key)
            audit.record("reauth", session["user"], self.address(), "ok")
            self.send_json(HTTPStatus.OK, self.session_info(auth.session(self.token())))

        def post_password(self) -> None:
            session = self.authorized()
            request = self.body()
            with password_check():
                current_ok = auth.verify(session["user"], str(request.get("current", ""))) is not None
            if not current_ok:
                auth.throttle.failed(f"user:{session['user']}")
                audit.record("password", session["user"], self.address(), "failed")
                raise Reject(HTTPStatus.UNAUTHORIZED, "the current password is incorrect")
            try:
                auth.set_password(session["user"], str(request.get("new", "")))
            except ValueError as error:
                raise Reject(HTTPStatus.BAD_REQUEST, str(error)) from None
            audit.record("password", session["user"], self.address(), "ok")
            # Every session for this user, including this one, has been revoked.
            self.send_json(HTTPStatus.OK, {"ok": True, "signed_out": True}, self.cookie("", 0))

        def operator(self) -> tuple[dict, CommandService]:
            session = self.authorized()
            if session["role"] != "operator":
                raise Reject(HTTPStatus.FORBIDDEN, "commands need the operator role")
            if config.commands is None:
                raise Reject(HTTPStatus.NOT_FOUND, "commands are not enabled")
            return session, config.commands

        def post_command_preview(self) -> None:
            session, commands = self.operator()
            request = self.body()
            try:
                preview = commands.preview(str(request.get("name", "")), request.get("params", {}))
            except CommandError as error:
                raise Reject(HTTPStatus(error.status), error.message, **error.extra) from None
            preview["reauth_ok"] = auth.recently_authenticated(session)
            self.send_json(HTTPStatus.OK, preview)

        def post_command_run(self) -> None:
            session, commands = self.operator()
            request = self.body()
            try:
                result = commands.run(str(request.get("name", "")), request.get("params", {}),
                                      str(request.get("fingerprint", "")), request.get("confirm"),
                                      session, self.address(), auth.recently_authenticated(session))
            except CommandError as error:
                raise Reject(HTTPStatus(error.status), error.message, **error.extra) from None
            self.send_json(HTTPStatus.OK, result)

        def send_snapshot(self, force: bool) -> None:
            try:
                _, body, gzipped = snapshots.get(force=force)
            except Exception as error:  # a broken build must not take the page down
                self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
                               {"error": f"{type(error).__name__}: {error}"})
                return
            self.send(HTTPStatus.OK, body, "application/json", gzipped, cache="no-store")

        def log_message(self, format_string: str, *arguments) -> None:
            if getattr(self.server, "quiet", False):
                return
            sys.stderr.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {self.address()} "
                             f"{format_string % arguments}\n")

    return Handler


# ----- command line ------------------------------------------------------

def parse_listen(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    if not host or not port.isdigit():
        raise argparse.ArgumentTypeError("expected HOST:PORT, for example 127.0.0.1:8070")
    return host, int(port)


def read_password(arguments: argparse.Namespace, name: str) -> str:
    if arguments.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass(f"New password for {name}: ")
    if first != getpass.getpass("Repeat it: "):
        raise ValueError("the passwords do not match")
    return first


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  python3 king_hamming/web/server.py add-user zooey --role operator\n"
               "  python3 king_hamming/web/server.py serve --listen 0.0.0.0:8070\n"
               "  python3 king_hamming/web/server.py serve --trust-proxy   # behind cloudflared")
    commands = parser.add_subparsers(dest="action")

    def command(name: str, text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=text)
        sub.add_argument("--state-dir", type=Path, default=DEFAULT_STATE,
                         help=f"private users/sessions/audit directory (default {DEFAULT_STATE})")
        return sub

    serve = command("serve", "run the dashboard")
    snapshot = command("snapshot", "build one snapshot and print a summary")
    for sub in (serve, snapshot):
        sub.add_argument("--deployments", type=Path, default=ROOT / "cluster" / "deployments",
                         help="directory of retained campaign state directories")
        sub.add_argument("--campaign", default="continuous-campaign",
                         help="the live deployment shown in the fleet and tile views")
    serve.add_argument("--listen", type=parse_listen, default=("127.0.0.1", 8070),
                       help="HOST:PORT (default 127.0.0.1:8070)")
    serve.add_argument("--ttl", type=float, default=15.0,
                       help="seconds a snapshot is reused before rebuilding (default 15)")
    serve.add_argument("--trust-proxy", action="store_true",
                       help="trust CF-Connecting-IP and X-Forwarded-* from a proxy on this machine")
    serve.add_argument("--trusted-proxy", action="append", default=[], metavar="ADDRESS",
                       help="also trust those headers from this address (a proxy on another machine; "
                            "repeatable)")
    serve.add_argument("--secure-cookies", action="store_true",
                       help="always mark the session cookie Secure (HTTPS only)")

    add = command("add-user", "create an account (prompts for the password)")
    add.add_argument("name")
    add.add_argument("--role", choices=ROLES, default="viewer")
    add.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    password = command("passwd", "set a user's password and sign out their sessions")
    password.add_argument("name")
    password.add_argument("--password-stdin", action="store_true")
    role = command("set-role", "change a user's role")
    role.add_argument("name")
    role.add_argument("role", choices=ROLES)
    remove = command("remove-user", "delete an account and its sessions")
    remove.add_argument("name")
    command("list-users", "show accounts and active sessions")
    revoke = command("revoke-sessions", "sign a user out everywhere")
    revoke.add_argument("name")
    return parser


def main() -> int:
    parser = build_parser()
    arguments = parser.parse_args()
    if arguments.action is None:
        parser.print_help()
        return 0
    try:
        auth = Auth(arguments.state_dir)
        if arguments.action == "add-user":
            password = read_password(arguments, arguments.name)
            check_password(arguments.name, password)
            auth.add_user(arguments.name, password, arguments.role)
            Audit(arguments.state_dir).record("add-user", arguments.name, "cli", "ok", role=arguments.role)
            print(f"added {arguments.name} ({arguments.role})")
            return 0
        if arguments.action == "passwd":
            auth.set_password(arguments.name, read_password(arguments, arguments.name))
            Audit(arguments.state_dir).record("password", arguments.name, "cli", "ok")
            print(f"password changed for {arguments.name}; their sessions were signed out")
            return 0
        if arguments.action == "set-role":
            auth.set_role(arguments.name, arguments.role)
            Audit(arguments.state_dir).record("set-role", arguments.name, "cli", "ok", role=arguments.role)
            print(f"{arguments.name} is now {arguments.role}")
            return 0
        if arguments.action == "remove-user":
            auth.remove_user(arguments.name)
            Audit(arguments.state_dir).record("remove-user", arguments.name, "cli", "ok")
            print(f"removed {arguments.name}")
            return 0
        if arguments.action == "revoke-sessions":
            count = auth.revoke(arguments.name)
            Audit(arguments.state_dir).record("revoke-sessions", arguments.name, "cli", "ok", count=count)
            print(f"signed out {count} session(s) for {arguments.name}")
            return 0
        if arguments.action == "list-users":
            for name, record in sorted(auth.users().items()):
                print(f"{name:20} {record['role']}")
            for session in auth.sessions():
                print(f"  session {session['user']} from {session['address']}, last seen "
                      f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(session['last_seen']))}")
            return 0
    except ValueError as error:
        print(f"server.py: {error}", file=sys.stderr)
        return 1

    if not (arguments.deployments / arguments.campaign / "leader.sqlite").exists():
        print(f"server.py: no leader.sqlite under {arguments.deployments / arguments.campaign}",
              file=sys.stderr)
        return 1
    if arguments.action == "snapshot":
        snapshot, body, gzipped = Snapshots(arguments.deployments, arguments.campaign).get()
        print(json.dumps({
            "build_seconds": snapshot["build_seconds"], "json_bytes": len(body),
            "gzip_bytes": len(gzipped), "warnings": snapshot["warnings"], "stale": snapshot["stale"],
            "status": snapshot["status"],
            "nodes": [f"{node['hostname']}: {node['idle_reason']}" for node in
                      (snapshot["fleet"] or {}).get("nodes", [])],
            "roots": [f"{root['p']}^{root['r']} {root['state']}" for root in snapshot["roots"] or []],
            "problems": (snapshot.get("problems") or {}).get("counts"),
        }, indent=2))
        return 0

    if not auth.users():
        print("server.py: no accounts yet. Create one first:\n"
              "  python3 king_hamming/web/server.py add-user NAME --role operator", file=sys.stderr)
        return 1
    host, port = arguments.listen
    snapshots = Snapshots(arguments.deployments, arguments.campaign, ttl=arguments.ttl)
    audit = Audit(arguments.state_dir)
    context = Context(arguments.deployments, arguments.campaign, snapshots,
                      Jobs(arguments.state_dir / "jobs"))
    config = Config(snapshots, auth, audit, CommandService(context, audit),
                    arguments.trust_proxy, arguments.secure_cookies,
                    tuple(str(ipaddress.ip_address(address)) for address in arguments.trusted_proxy))
    server = DashboardServer((host, port), make_handler(config))
    server.daemon_threads = True
    print(f"dashboard on http://{host}:{server.server_port}/ reading "
          f"{arguments.deployments / arguments.campaign}; accounts in {arguments.state_dir}",
          file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

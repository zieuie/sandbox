#!/usr/bin/env python3
"""Serve a read-only web dashboard for the continuous King Hamming campaign.

There is no login yet. Keep the default loopback address, or a LAN address,
until authentication from DESIGN.md is implemented; never route this to the
internet as it stands.
"""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import parse_qs, urlparse

from snapshot import ROOT, Snapshots

STATIC = Path(__file__).resolve().parent / "static"
STATIC_NAME = re.compile(r"^[a-z0-9_-]+\.(html|js|css|svg)$")
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
}


def make_handler(snapshots: Snapshots) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "king-hamming-dashboard/1"

        def send(self, status: HTTPStatus, body: bytes, content_type: str,
                 gzipped: bytes | None = None, cache: str = "no-cache") -> None:
            if gzipped is not None and "gzip" in self.headers.get("Accept-Encoding", ""):
                body, encoding = gzipped, "gzip"
            else:
                encoding = None
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            if encoding:
                self.send_header("Content-Encoding", encoding)
                self.send_header("Vary", "Accept-Encoding")
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_json(self, status: HTTPStatus, value) -> None:
            self.send(status, json.dumps(value).encode() + b"\n", "application/json", cache="no-store")

        def send_snapshot(self, force: bool) -> None:
            try:
                _, body, gzipped = snapshots.get(force=force)
            except Exception as error:  # a broken build must not take the page down
                self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
                               {"error": f"{type(error).__name__}: {error}"})
                return
            self.send(HTTPStatus.OK, body, "application/json", gzipped, cache="no-store")

        def do_GET(self) -> None:
            url = urlparse(self.path)
            route = url.path
            if route == "/":
                route = "/static/index.html"
            if route == "/api/snapshot":
                self.send_snapshot(force="refresh" in parse_qs(url.query))
                return
            if route == "/api/health":
                self.send_json(HTTPStatus.OK, {"ok": True, "snapshot_age": snapshots.age()})
                return
            if route.startswith("/static/"):
                name = route[len("/static/"):]
                path = STATIC / name
                if STATIC_NAME.match(name) and path.is_file():
                    self.send(HTTPStatus.OK, path.read_bytes(), CONTENT_TYPES[name.rsplit(".", 1)[1]])
                    return
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        do_HEAD = do_GET

        def do_POST(self) -> None:
            if urlparse(self.path).path == "/api/refresh":
                self.send_snapshot(force=True)
                return
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def log_message(self, format_string: str, *arguments) -> None:
            if getattr(self.server, "quiet", False):
                return
            sys.stderr.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {self.address_string()} "
                             f"{format_string % arguments}\n")

    return Handler


def parse_listen(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    if not host or not port.isdigit():
        raise argparse.ArgumentTypeError("expected HOST:PORT, for example 127.0.0.1:8070")
    return host, int(port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="example:\n  python3 king_hamming/web/server.py serve\n"
               "  python3 king_hamming/web/server.py serve --listen 0.0.0.0:8070")
    commands = parser.add_subparsers(dest="action")
    for name, text in (("serve", "run the dashboard"),
                       ("snapshot", "build one snapshot and print a summary")):
        command = commands.add_parser(name, help=text)
        command.add_argument("--deployments", type=Path, default=ROOT / "cluster" / "deployments",
                             help="directory of retained campaign state directories")
        command.add_argument("--campaign", default="continuous-campaign",
                             help="the live deployment shown in the fleet and tile views")
        if name == "serve":
            command.add_argument("--listen", type=parse_listen, default=("127.0.0.1", 8070),
                                 help="HOST:PORT (default 127.0.0.1:8070)")
            command.add_argument("--ttl", type=float, default=15.0,
                                 help="seconds a snapshot is reused before rebuilding (default 15)")
    return parser


def main() -> int:
    parser = build_parser()
    arguments = parser.parse_args()
    if arguments.action is None:
        parser.print_help()
        return 0
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
        }, indent=2))
        return 0

    host, port = arguments.listen
    snapshots = Snapshots(arguments.deployments, arguments.campaign, ttl=arguments.ttl)
    server = ThreadingHTTPServer((host, port), make_handler(snapshots))
    server.daemon_threads = True
    try:
        exposed = not ipaddress.ip_address(host).is_loopback
    except ValueError:
        exposed = host != "localhost"
    print(f"dashboard on http://{host}:{server.server_port}/ reading "
          f"{arguments.deployments / arguments.campaign}", file=sys.stderr)
    if exposed:
        print("note: no login yet; keep this on the home network only", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

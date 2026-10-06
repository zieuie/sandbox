#!/usr/bin/env python3
"""The leader listens with a backlog sized for the fleet, not socketserver's default of 5."""

from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import leader  # noqa: E402


class BacklogTests(unittest.TestCase):
    def test_leader_server_backlog(self) -> None:
        self.assertGreaterEqual(leader.LeaderHTTPServer.request_queue_size, 512)
        server = leader.LeaderHTTPServer(("127.0.0.1", 0), leader.BaseHTTPRequestHandler)
        try:
            # Many connections arriving at once all reach the accept queue without a reset.
            clients = [socket.create_connection(server.server_address, timeout=2) for _ in range(64)]
            for client in clients:
                client.close()
        finally:
            server.server_close()


if __name__ == "__main__":
    unittest.main()

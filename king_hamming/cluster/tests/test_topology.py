"""Private data-plane URLs must never strand Wi-Fi-only cluster members."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ["KH_ENABLE_TEST_FIXTURES"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import agent
import leader
import replication
import topology


class PrivateNetworkTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "leader.sqlite"
        leader.initialize(self.database, 1800)
        handler = object.__new__(leader.make_handler(self.database))
        for name, private in (("a", True), ("b", True), ("c", True),
                              ("wifi", False), ("wifi2", False)):
            payload = {"node_name": name, "address": f"http://192.168.4.{name}:9000"}
            if private:
                payload.update(private_address=f"http://10.203.0.{name}:9000",
                               private_group="switch")
            handler.dispatch_post("/v1/register", payload)
        self.digest = "a" * 64
        now = time.time()
        with leader.connect(self.database) as connection:
            connection.execute("UPDATE nodes SET storage_free_bytes=?", (100 * 2**30,))
            connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) "
                               "VALUES(?,?,?,?)", (self.digest, 3, now, 100))
            for name in ("a", "wifi"):
                connection.execute(
                    "INSERT INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                    (self.digest, name, f"http://192.168.4.{name}:9000/blobs/{self.digest}", now))

    def test_wired_peer_prefers_private_then_keeps_wifi_fallback(self) -> None:
        with leader.connect(self.database) as connection:
            urls = topology.locations(connection, self.digest, "b", time.time() - 60)
        self.assertEqual(urls[0], f"http://10.203.0.a:9000/blobs/{self.digest}")
        self.assertIn(f"http://192.168.4.a:9000/blobs/{self.digest}", urls)
        self.assertIn(f"http://192.168.4.wifi:9000/blobs/{self.digest}", urls)

    def test_wifi_peer_never_gets_private_address(self) -> None:
        with leader.connect(self.database) as connection:
            urls = topology.locations(connection, self.digest, "wifi", time.time() - 60)
        self.assertTrue(urls)
        self.assertTrue(all("10.203.0." not in url for url in urls))

    def test_bad_private_source_identifies_the_owner(self) -> None:
        events = []
        with mock.patch.object(agent, "request_json", side_effect=lambda _leader, route, value:
                               events.append((route, value))):
            report = agent.bad_blob_reporter(
                "http://leader", {"node_name": "b", "session_id": "session"},
                [{"node_name": "a", "address": "http://192.168.4.a:9000",
                  "private_address": "http://10.203.0.a:9000"}])
            report(f"http://10.203.0.a:9000/blobs/{self.digest}", self.digest)
        self.assertEqual(events[0][1]["source_node"], "a")

    def test_wired_capacity_keeps_new_copies_off_wifi(self) -> None:
        handler = object.__new__(leader.make_handler(self.database))
        request = {"node_name": "wifi2", "session_id": ""}
        self.assertIsNone(handler.dispatch_post("/v1/replication", request)["replication"])
        with leader.connect(self.database) as connection:
            connection.execute("UPDATE nodes SET storage_free_bytes=0 WHERE node_name='c'")
        # An empty scan holds the node's next scan for a while; this checks the scan itself.
        replication._idle_until.clear()
        replication._candidates.clear()
        task = handler.dispatch_post("/v1/replication", request)["replication"]
        self.assertEqual(task["artifact_hash"], self.digest)


if __name__ == "__main__":
    unittest.main()

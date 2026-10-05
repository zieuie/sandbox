#!/usr/bin/env python3
"""Leader writers wait in arrival order, so a busy leader stops failing requests with "database is locked"."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import leader


class WriterQueueTests(unittest.TestCase):
    def test_waiters_get_the_slot_in_arrival_order(self) -> None:
        queue = leader.WriterQueue()
        self.assertTrue(queue.acquire(1))
        order: list[int] = []

        def wait(index: int) -> None:
            self.assertTrue(queue.acquire(10))
            order.append(index)
            queue.release()

        threads = []
        for index in range(5):
            threads.append(threading.Thread(target=wait, args=(index,)))
            threads[-1].start()
            time.sleep(0.05)  # arrive one after another
        queue.release()
        for thread in threads:
            thread.join()
        self.assertEqual(order, [0, 1, 2, 3, 4])
        self.assertFalse(queue.held)

    def test_a_timed_out_waiter_leaves_the_queue(self) -> None:
        queue = leader.WriterQueue()
        self.assertTrue(queue.acquire(1))
        self.assertFalse(queue.acquire(0.1))
        self.assertEqual(len(queue.waiters), 0)
        queue.release()
        self.assertTrue(queue.acquire(0.1), "the slot is free again, not owed to the departed waiter")
        queue.release()

    def test_many_concurrent_writers_never_hit_a_lock_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            health = leader.SchedulerHealth(30.0)
            with leader.connect(database) as connection:
                connection.execute("CREATE TABLE counter(value INTEGER)")
                connection.execute("INSERT INTO counter VALUES(0)")
            errors: list[BaseException] = []

            def write() -> None:
                for _ in range(40):
                    try:
                        with leader.writer_session(database, "test", health, timeout=8.0) as connection:
                            value = connection.execute("SELECT value FROM counter").fetchone()[0]
                            time.sleep(0.002)  # hold the lock a little, like a real request
                            connection.execute("UPDATE counter SET value=?", (value + 1,))
                    except BaseException as error:  # noqa: BLE001 - collected and asserted below
                        errors.append(error)

            threads = [threading.Thread(target=write) for _ in range(24)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            with leader.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT value FROM counter").fetchone()[0], 24 * 40)
            self.assertEqual(health.snapshot()["transactions"]["test"]["lock_errors"], 0)


if __name__ == "__main__":
    unittest.main()

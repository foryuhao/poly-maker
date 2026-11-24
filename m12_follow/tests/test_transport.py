import json
import multiprocessing
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from m12_follow.transport import Producer, Reader, stable_id


SOURCE = dict(run_id="frozen-run", market="btc-updown-5m-123", condition_id="cond")


def intent(now):
    return dict(opportunity="opp", root_id="root", token="y", outcome="YES", price="0.76",
                quantity="6.5", version="v1", confirmed=now - 0.005, available=now,
                deadline=now + 2.9)


def crash_after_publish(directory, ready):
    producer = Producer(directory, **SOURCE)
    now = time.monotonic()
    producer.publish(1, [intent(now)], ["opp"], healthy=True, source_mono=now)
    ready.send(True)
    ready.recv()
    os._exit(17)


class TestTransport(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.read = Reader(self.directory, **SOURCE)

    def test_atomic_lifecycle_and_identity(self):
        with patch("m12_follow.transport.time.monotonic", return_value=100.0) as clock:
            r = intent(100.0)
            self.assertIsNone(self.read.read())
            self.assertFalse(any(self.directory.iterdir()))
            with Producer(self.directory, **SOURCE, tokens={"YES": "y"}) as p:
                self.assertIsNone(self.read.read())
                p.publish(1, [r], ["opp"], healthy=True, source_mono=100.0)
                got = self.read.read()
                self.assertEqual(got.active_ids, {"opp"})
                self.assertEqual(got.intents[0], r)
                self.assertIsNone(Reader(self.directory, **{**SOURCE, "condition_id": "other"}).read())
                with self.assertRaises(BlockingIOError):
                    Producer(self.directory, **SOURCE)
                self.assertEqual(self.read.read().generation, got.generation)
                clock.return_value = 100.001
                p.publish(2, [r], [], healthy=True, source_mono=100.001)
                ended = self.read.read()
                self.assertEqual(ended.active_ids, set())
                self.assertEqual(ended.intents, got.intents)
                with self.assertRaises(ValueError):
                    p.publish(3, [r], ["opp"], healthy=True, source_mono=100.001)
                self.assertIsNone(self.read.read())
            with Producer(self.directory, **SOURCE) as p:
                self.assertIsNone(self.read.read())
                p.publish(1, [], [], healthy=True, source_mono=100.001)
                self.assertNotEqual(self.read.read().generation, got.generation)
            self.assertIsNone(self.read.read())

    def test_expiry_health_and_watermarks(self):
        with patch("m12_follow.transport.time.monotonic", return_value=100.0) as clock:
            r = dict(intent(100.0), deadline=100.001)
            with Producer(self.directory, **SOURCE) as p:
                p.publish(1, [r], ["opp"], healthy=True, source_mono=100.0)
                self.assertEqual(self.read.read().active_ids, {"opp"})
                clock.return_value = 100.001
                self.assertEqual(self.read.read().active_ids, set())
                p.publish(2, [r], [], healthy=False, source_mono=100.001)
                self.assertIsNone(self.read.read())
                clock.return_value = 100.15
                p.publish(3, [r], [], healthy=True, source_mono=100.02)
                self.assertIsNone(self.read.read())
                clock.return_value = 100.18
                p.publish(4, [r], [], healthy=True, source_mono=100.18)
                self.assertEqual(self.read.read().input_seq, 4)
                clock.return_value = 100.30
                self.assertIsNone(self.read.read())

    def test_corrupt_copies_and_old_boot_fail_closed(self):
        with patch("m12_follow.transport.time.monotonic", return_value=100.0):
            with Producer(self.directory, **SOURCE) as p:
                p.publish(10, [], [], healthy=True, source_mono=100.0)
                self.assertEqual(self.read.read().input_seq, 10)
                valid = json.loads(p.path.read_bytes())
                for changes in ({"seq": 9}, {"boot_id": "wrong"}, {"active": ["unknown"]},
                                {"source_mono": 101.0}, {"identity": ["wrong"]}):
                    p.path.write_text(json.dumps({**valid, **changes}))
                    self.assertIsNone(self.read.read())
                p.path.write_text("{")
                self.assertIsNone(self.read.read())

    def test_killed_producer_lease(self):
        ctx = multiprocessing.get_context("fork")
        recv, send = ctx.Pipe()
        process = ctx.Process(target=crash_after_publish, args=(self.directory, send))
        process.start()
        self.addCleanup(send.close)
        self.addCleanup(recv.close)
        try:
            self.assertTrue(recv.poll(5))
            self.assertTrue(recv.recv())
            with self.assertRaises(BlockingIOError):
                Producer(self.directory, **SOURCE)
            recv.send(True)
            process.join(5)
            self.assertEqual(process.exitcode, 17)
            self.assertIsNone(self.read.read())
            old = self.read.path.read_bytes()
            with Producer(self.directory, **SOURCE) as new:
                now = time.monotonic()
                new.publish(1, [], [], healthy=True, source_mono=now)
                self.assertEqual(self.read.read().generation, new.generation)
                self.read.path.write_bytes(old)
                self.assertIsNone(self.read.read())
            self.assertEqual(sorted(p.name for p in self.directory.iterdir()), [".publishers.lock"])
        finally:
            if process.is_alive():
                process.kill()
                process.join()

    def test_key_ignores_consumer_and_producer_generation(self):
        self.assertEqual(stable_id("a", "b", "c", "d"), stable_id("a", "b", "c", "d"))
        self.assertNotEqual(stable_id("a:b", "c", "d", "e"), stable_id("a", "b:c", "d", "e"))

    def test_concurrent_markets_and_owner_permissions(self):
        self.directory.chmod(0o777)
        with self.assertRaises(PermissionError):
            Producer(self.directory, **SOURCE)
        self.directory.chmod(0o750)
        other = {**SOURCE, "market": "next-market", "condition_id": "cond-2"}
        with Producer(self.directory, **SOURCE) as p, Producer(self.directory, **other) as q:
            now = time.monotonic()
            p.publish(1, [], [], healthy=True, source_mono=now)
            q.publish(1, [], [], healthy=True, source_mono=now)
            self.assertEqual(self.read.read().generation, p.generation)
            p.path.chmod(0o660)
            self.assertIsNone(self.read.read())
            p.path.chmod(0o640)
            q.close()
            self.assertIsNone(Reader(self.directory, **other).read())
            self.assertEqual(self.read.read().generation, p.generation)
            self.assertEqual(p.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()), [".publishers.lock"])


if __name__ == "__main__":
    unittest.main()

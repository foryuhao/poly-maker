"""Offline tests for the baseline's native admission and lifecycle boundary."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from m12_follow.publisher import IntentPublication, publishing_capture


class TestPublisher(unittest.TestCase):
    def setUp(self):
        self.intent = {"opportunity": "pair", "signal_version": "v", "market": "m", "token": "t",
                       "outcome": "YES", "price": "0.6", "quantity": "8.3", "available": 9.9,
                       "deadline": 10.3, "order_type": "ASSUMED_FIXED_ANCHOR", "side": "BUY",
                       "strategy": "M12", "profile": "M12"}
        self.fact = {"id": "pair", "root_id": "root", "confirmed": 9.8, "anchor_price": "0.6",
                     "token": "t", "version": "v"}
        feed = SimpleNamespace(connected=True, core=SimpleNamespace(signals={"pair": self.fact}),
                               good={"t": True}, legal=lambda _: True)
        self.engine = SimpleNamespace(store=Mock(), seq=100, clock=SimpleNamespace(mono=10.0),
                                      sop_runtime=feed, signal_history={"pair": self.fact},
                                      meta=SimpleNamespace(slug="m"), validity={"status": "TRACKING"},
                                      ended=False, needs_reconnect=False)
        self.snapshots = []
        self.producer = Mock()
        self.producer.publish.side_effect = self.snapshot
        self.publisher = IntentPublication(self.engine, self.producer)

    def snapshot(self, seq, intents, active, *, healthy, source_mono):
        self.snapshots.append((list(intents), set(active), healthy))

    def input(self, kind, **event):
        self.engine.seq += 1
        self.publisher.after_input({"kind": kind, **event})

    def admit(self):
        self.engine.store.emit("INTENT", {"scenario": "sop/anchor", "intent": self.intent})

    def test_admission_only_and_early_end(self):
        self.engine.store.emit("SOP_EVIDENCE", {"event": "SIGNAL_CONFIRMED", "payload": self.fact})
        self.engine.store.emit("ASSUMED_FILL", {"intent": self.intent})
        self.engine.store.emit("INTENT", {"scenario": "some-other-scenario", "intent": self.intent})
        self.input("FRAME")
        self.assertEqual(self.snapshots, [([], set(), True)])
        self.admit()
        self.input("TIMER")
        self.engine.store.commit.assert_called_once()
        rows, active, healthy = self.snapshots[-1]
        self.assertEqual((active, healthy), ({"pair"}, True))
        self.assertEqual((rows[0]["root_id"], rows[0]["deadline"]), ("root", 10.3))
        self.engine.sop_runtime.core.signals.clear()
        self.input("FRAME")
        self.assertEqual(self.snapshots[-1], (rows, set(), True))

    def test_same_parent_ended_and_stale_pending(self):
        self.admit()
        self.engine.sop_runtime.core.signals.clear()
        self.input("TIMER", pending=[9.0])
        self.assertEqual(self.snapshots[-1][1:], (set(), False))

    def test_cannot_publish_an_uncommitted_intent(self):
        self.engine.store.commit.side_effect = OSError("disk full")
        self.admit()
        with self.assertRaises(OSError):
            self.input("TIMER")
        self.assertFalse(self.snapshots)

    def test_lost_capture_closes_publication_before_propagating_failure(self):
        import asyncio

        class Capture:
            def __init__(self, session, meta, ticks):
                self.group = session.group

        group = SimpleNamespace(ingest=AsyncMock(side_effect=ValueError("capture failed")))
        session = SimpleNamespace(group=group)
        capture = publishing_capture(Capture, "/unused", "run")(session, None, {})
        capture.publication = self.publisher
        with self.assertRaisesRegex(ValueError, "capture failed"):
            asyncio.run(group.ingest({"kind": "FRAME"}))
        self.producer.close.assert_called_once()
        self.assertTrue(capture.publication_disabled)

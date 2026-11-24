"""Publish admitted M12 intents from the authoritative baseline capture process."""

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

from .transport import HEARTBEAT_S, MAX_AGE_S, Producer


class IntentPublication:
    """Observe the native baseline audit writer; publish after a whole input succeeds."""

    def __init__(self, engine, producer, log=None):
        self.engine = engine
        self.producer = producer
        self.log = log
        self.intents = {}
        self.new_intents = []
        self.retired = set()
        self.last = None
        self.last_clock = float("-inf")
        original_emit = engine.store.emit

        def emit(kind, payload):
            original_emit(kind, payload)
            if kind == "INTENT" and payload.get("scenario") == "sop/anchor":
                self.new_intents.append(payload["intent"])

        self.original_emit = original_emit
        engine.store.emit = emit

    def close(self):
        self.engine.store.emit = self.original_emit
        self.producer.close()

    def after_input(self, event):
        engine = self.engine
        now = engine.clock.mono
        feed = engine.sop_runtime
        new = bool(self.new_intents)
        for intent in self.new_intents:
            key = intent["opportunity"]
            fact = engine.signal_history[key]
            if (intent["strategy"] != "M12" or intent["profile"] != "M12"
                    or intent["order_type"] != "ASSUMED_FIXED_ANCHOR"
                    or intent["side"] != "BUY" or intent["market"] != engine.meta.slug
                    or key in self.intents or intent["signal_version"] != fact["version"]
                    or intent["token"] != fact["token"] or intent["price"] != str(fact["anchor_price"])):
                raise ValueError("invalid authoritative M12 admission")
            self.intents[key] = {
                **{k: intent[k] for k in ("opportunity", "token", "outcome", "price", "quantity",
                                         "available", "deadline")},
                "version": fact["version"], "root_id": fact["root_id"], "confirmed": fact["confirmed"],
            }
        self.new_intents.clear()
        if new:
            # At most two admissions per market. No executable intent may precede
            # its own durable baseline evidence, including if this process crashes.
            engine.store.commit()

        active = set()
        for key, intent in self.intents.items():
            if (key not in self.retired and key in feed.core.signals
                    and now < intent["deadline"] and feed.good[intent["token"]]
                    and feed.legal(intent["token"])):
                active.add(key)
            else:
                self.retired.add(key)
        oldest = min(event.get("pending") or [now])
        received = event.get("received_mono", now)
        healthy = bool(feed.connected and not engine.ended and not engine.needs_reconnect
                       and engine.validity["status"] != "INVALID"
                       and now - min(oldest, received) <= MAX_AGE_S)
        state = (len(self.intents), active, healthy)
        if state != self.last or now - self.last_clock >= HEARTBEAT_S:
            self.producer.publish(engine.seq, self.intents.values(), active,
                                  healthy=healthy, source_mono=now)
            if self.log and self.intents and state != self.last:
                previous = self.last[1] if self.last else set()
                self.log("M12_FOLLOW_INTENT_STATE", market=engine.meta.slug, input_seq=engine.seq,
                         healthy=healthy, active_ids=sorted(active),
                         retired_ids=sorted(previous - active | self.retired),
                         known_ids=list(self.intents), source_mono=now)
            self.last, self.last_clock = state, now


def publishing_capture(base, channel, run_id, producer_factory=Producer):
    class PublishingCapture(base):
        def __init__(self, session, meta, ticks):
            super().__init__(session, meta, ticks)
            self.publication = None
            self.publication_disabled = False
            original_ingest = self.group.ingest

            async def ingest(event):
                try:
                    await original_ingest(event)
                except BaseException:
                    self.close_publication()
                    raise
                if self.publication_disabled:
                    return
                try:
                    engine = self.group.primary
                    if event["kind"] == "INIT":
                        producer = producer_factory(channel, run_id=run_id, market=meta.slug,
                                                    condition_id=meta.condition_id,
                                                    tokens={"YES": meta.yes.token_id,
                                                            "NO": meta.no.token_id})
                        try:
                            self.publication = IntentPublication(engine, producer, session.log)
                        except BaseException:
                            producer.close()
                            raise
                    if event["kind"] == "END" or session.stop.is_set():
                        self.close_publication()
                    elif self.publication:
                        self.publication.after_input(event)
                except Exception as exc:
                    self.close_publication()
                    session.log("M12_FOLLOW_PUBLICATION_DISABLED", market=meta.slug,
                                error=f"{type(exc).__name__}: {exc}")

            self.group.ingest = ingest

        def close_publication(self):
            self.publication_disabled = True
            if self.publication:
                self.publication.close()
                self.publication = None

        async def close(self):
            try:
                self.close_publication()
            finally:
                await super().close()

    return PublishingCapture


def frozen_baseline(runtime):
    """Use one verified baseline source tree for this process and replay workers."""
    manifest = json.loads((runtime / "manifest.json").read_bytes())
    for name, expected in manifest.items():
        path = (runtime / name).resolve(strict=True)
        if not path.is_relative_to(runtime) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"frozen baseline manifest mismatch: {name}")
    paths = [str(runtime / part) for part in ("ops", "research", "src")]
    sys.path[:0] = paths
    os.environ["PYTHONPATH"] = os.pathsep.join(paths)
    shared = importlib.import_module("m1223_shared72")
    if Path(shared.__file__).resolve() != runtime / "ops/m1223_shared72.py" or shared.runner.ROOT != runtime:
        raise ValueError("mixed baseline source trees")
    return shared


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "extend"), help="authoritative live capture only")
    parser.add_argument("--baseline-runtime", required=True, type=Path, help="frozen runtime with manifest.json")
    parser.add_argument("--channel-directory", required=True, type=Path, help="same-host handoff directory")
    parser.add_argument("--storage-root", required=True, type=Path, help="authoritative m122124_baseline root")
    parser.add_argument("--run-name", required=True, help="baseline run name and subscriber run_id")
    parser.add_argument("--two-markets", action="store_true", help="limit a new trial baseline to two consecutive markets")
    parser.add_argument("--project-root", type=Path, default=Path("/home/ubuntu/polymaker"))
    args = parser.parse_args(argv)
    if args.two_markets and args.action != "run":
        parser.error("--two-markets applies only to a new baseline run")
    shared = frozen_baseline(args.baseline_runtime.resolve(strict=True))
    capture = publishing_capture(shared.MarketCapture, args.channel_directory.resolve(), args.run_name)
    native_argv = [shared.__file__, args.action, "--run-name", args.run_name,
                   "--storage-root", str(args.storage_root), "--project-root", str(args.project_root)]
    # Scope the hook to the authoritative online entry point. Its separate replay
    # subprocess imports the original coordinator and has no publish callback.
    original_capture, original_argv, original_markets = shared.MarketCapture, sys.argv, shared.MARKETS
    try:
        if args.two_markets:
            shared.MARKETS = 2
        shared.MarketCapture, sys.argv = capture, native_argv
        shared.main()
    finally:
        shared.MarketCapture, sys.argv, shared.MARKETS = original_capture, original_argv, original_markets


if __name__ == "__main__":
    main()

"""Artificial raw public book/trade paths; never counted as recorded markets."""

from m510_trade_through import SCENARIO_SETTINGS
from m511_sim import definition

from m1234_fixtures import fixture_events
from test_m511_feed import Tape, play_m10_book_path


def partial_unsettled_events():
    fixture = fixture_events()
    init = fixture[0]
    cfg = init["definition"]["settings"]
    cfg.update(profiles={}, scenarios=SCENARIO_SETTINGS, m5_m11=definition(only_m5_m10=True))
    meta = init["definition"]["market"]
    tokens = dict(zip(("up", "down"), (t["token_id"] for t in meta["tokens"]), strict=True))
    events = [init, fixture[1]]
    wall_zero = init["available_wall"]

    def append(at, kind, **payload):
        events.append({**fixture[1], "seq": len(events)+1, "kind": kind,
            "available_mono": at, "available_wall": wall_zero+at, **payload})

    class RecordedTape(Tape):
        def __init__(self):
            self.seq = 0

        def apply(self, at, rows, available=None):
            self.seq += 1
            at += 16
            raw = [{**r, "asset_id": tokens[r["asset_id"]], "market": meta["condition_id"],
                    "timestamp": str((wall_zero+at)*1000)}
                   for r in (rows if isinstance(rows, list) else [rows])]
            append(at, "FRAME", raw=raw, received_mono=at, received_wall=wall_zero+at)

        def tick(self, at, pending=()):
            self.seq += 1
            append(at+16, "TIMER")

    tape = RecordedTape()
    play_m10_book_path(tape)
    tape.trade(2.34, "SELL", 1, price=".39")
    append(18.4, "GAP", reason="ARTIFICIAL_MISSING_INPUT_AFTER_PARTIAL_FILL")
    append(240, "TIMER")
    append(300.1, "END", reason="T300_OBSERVED")
    return events

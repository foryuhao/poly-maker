"""Materialize independent ledgers from a closed shared baseline recording."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from m1223_shared72 import MarketEngines, configuration
from m1223_shared_storage import BASELINE_STRATEGIES, PAIR, read_tape


async def observe(directory, tape, owner, *, pair=False, original_plan=None):
    participants = PAIR if pair else BASELINE_STRATEGIES
    settings, parameters = configuration(participants)
    if original_plan is not None:
        if not pair:
            raise ValueError("HISTORICAL_PAIR_REPLAY_REQUIRED")
        plan = json.loads(original_plan.read_text())
        epoch = int(directory.name.rsplit("-", 1)[1])
        settings = plan["continuation_settings"] if epoch >= plan["continuation_start_epoch"] else plan["settings"]
    group = MarketEngines(directory, settings, {k: parameters[k] for k in participants[1:]}, owner)
    seq = 0
    try:
        for event in read_tape(tape):
            await group.ingest(event)
            seq = event["seq"]
            if seq == 1 or seq % 4096 == 0:
                print(json.dumps({"input_seq": seq}), flush=True)
    finally:
        await group.close()
    print(json.dumps({"input_seq": seq, "states": group.ledger_states()}, separators=(",", ":")),
          flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("tape", type=Path)
    parser.add_argument("owner", type=json.loads)
    parser.add_argument("--pair", action="store_true")
    parser.add_argument("--original-plan", type=Path)
    args = parser.parse_args()
    os.nice(5)
    asyncio.run(observe(args.directory, args.tape, args.owner, pair=args.pair,
                        original_plan=args.original_plan))


if __name__ == "__main__":
    main()

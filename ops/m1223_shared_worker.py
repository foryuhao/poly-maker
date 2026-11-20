"""Materialize independent ledgers from a closed shared baseline recording."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from m1223_shared72 import MarketEngines, configuration
from m1223_shared_storage import read_tape


async def observe(directory, tape, owner):
    settings, parameters = configuration()
    group = MarketEngines(directory, settings, {k: parameters[k] for k in ("M21", "M24")}, owner)
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
    args = parser.parse_args()
    os.nice(5)
    asyncio.run(observe(args.directory, args.tape, args.owner))


if __name__ == "__main__":
    main()

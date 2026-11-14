"""Stop only a simulation whose exact output directory matches the supplied run."""

import argparse
import json
import os
import signal
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    directory = Path(args.run_dir).resolve()
    plan = json.loads((directory / "plan.json").read_text())
    if plan.get("mode") != "SIMULATION_ONLY":
        raise SystemExit("Not a declared simulation directory")
    matched = []
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            words = [w.decode() for w in (process / "cmdline").read_bytes().split(b"\0") if w]
            if "polymaker.m1234_sim" not in words:
                continue
            module = words.index("polymaker.m1234_sim")
            if words[module + 1] != "run":
                continue
            argument = Path(words[words.index("--output") + 1])
            if not argument.is_absolute():
                argument = (process / "cwd").resolve() / argument
            if argument.resolve() != directory:
                continue
            os.kill(int(process.name), signal.SIGINT)
            matched.append(int(process.name))
        except (OSError, ValueError, IndexError, UnicodeError):
            continue
    print(
        json.dumps(
            {
                "simulation_run": str(directory),
                "stopped_pids": matched,
                "status": "SIGINT_SENT" if matched else "ALREADY_STOPPED",
            }
        )
    )


if __name__ == "__main__":
    main()

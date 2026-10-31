"""One bounded successor capture after the existing public-context process exits."""

import argparse
import subprocess
import sys
import time
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--parent-pid", type=int, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seconds", type=int, default=7200)
    a = p.parse_args()
    proc = Path(f"/proc/{a.parent_pid}/cmdline")
    while proc.exists():
        try:
            args = proc.read_bytes()
        except FileNotFoundError:
            break
        if b"research/microstructure_discovery/context.py" not in args:
            break
        time.sleep(20)
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("context.py")),
            "--output",
            str(a.output),
            "--seconds",
            str(a.seconds),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()

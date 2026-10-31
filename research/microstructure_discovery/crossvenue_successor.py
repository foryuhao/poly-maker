"""One bounded successor for overnight sports books, with automatic fill audit."""

import argparse
import subprocess
import sys
import time
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--parent-pid", type=int, required=True)
    p.add_argument("--events", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seconds", type=int, default=43200)
    a = p.parse_args()
    proc = Path(f"/proc/{a.parent_pid}/cmdline")
    while proc.exists():
        try:
            if b"crossvenue_scan.py" not in proc.read_bytes():
                break
        except FileNotFoundError:
            break
        time.sleep(20)
    folder = Path(__file__).parent
    with (
        Path("/tmp/microstructure-crossvenue-overnight.log").open("w") as log,
        Path("/tmp/microstructure-crossvenue-overnight-confirm.log").open("w") as auditlog,
    ):
        capture = subprocess.Popen(
            [
                sys.executable,
                str(folder / "crossvenue_scan.py"),
                "--events",
                str(a.events),
                "--output",
                str(a.output),
                "--seconds",
                str(a.seconds),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        while capture.poll() is None and not (a.output / "positive.jsonl").exists():
            time.sleep(0.2)
        if capture.poll() is not None:
            raise RuntimeError(f"capture failed before audit startup: {capture.returncode}")
        audit = subprocess.Popen(
            [
                sys.executable,
                str(folder / "crossvenue_confirm.py"),
                str(a.output),
                "--seconds",
                str(a.seconds),
            ],
            stdout=auditlog,
            stderr=subprocess.STDOUT,
        )
        capture.wait()
        audit.wait()
        if capture.returncode or audit.returncode:
            raise RuntimeError(f"capture={capture.returncode},audit={audit.returncode}")


if __name__ == "__main__":
    main()

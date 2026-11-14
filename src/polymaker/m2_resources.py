"""Measure actual CPU headroom separately from scheduler pressure."""

import time
from collections import deque
from pathlib import Path


def cpu_counters():
    values = tuple(int(v) for v in Path("/proc/stat").read_text().splitlines()[0].split()[1:9])
    if len(values) != 8 or any(v < 0 for v in values):
        raise ValueError("CPU_COUNTERS_UNAVAILABLE")
    return values


def live_cpu_pressure():
    relative = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    group = Path("/sys/fs/cgroup")/relative.lstrip("/")
    target = group.parent/"polymaker-m1-live.service"/"cpu.pressure"
    if not target.parent.exists():
        return None
    for row in target.read_text().splitlines():
        if row.startswith("full "):
            value = float(dict(v.split("=", 1) for v in row.split()[1:])["avg10"])
            if not 0 <= value <= 100:
                raise ValueError("M1_CPU_PRESSURE_INVALID")
            return value
    raise ValueError("M1_CPU_PRESSURE_UNAVAILABLE")


class CPUHeadroom:
    def __init__(self, *, reader=cpu_counters, protected_reader=live_cpu_pressure,
                 clock=time.monotonic, window_sec=10):
        self.reader, self.protected_reader, self.clock = reader, protected_reader, clock
        self.window_sec = window_sec
        self.samples = deque(maxlen=64)
        self.live_pressure_pct = None
        self.busy_pct = 100.

    def __call__(self):
        try:
            now, counters = self.clock(), self.reader()
            self.live_pressure_pct = self.protected_reader()
            self.samples.append((now, counters))
            while len(self.samples) > 2 and self.samples[1][0] <= now-self.window_sec:
                self.samples.popleft()
            start, previous = self.samples[0]
            delta = [b-a for a, b in zip(previous, counters, strict=True)]
            total = sum(delta)
            if now-start < .25 or total <= 0 or any(v < 0 for v in delta):
                self.busy_pct = 100.
            else:
                # iowait consumes no CPU; steal is unavailable and counts as busy.
                self.busy_pct = 100*(total-delta[3]-delta[4])/total
        except (OSError, ValueError, KeyError, IndexError):
            self.busy_pct, self.live_pressure_pct = 100., 100.
        return self.busy_pct

"""Idle-window reclamation for the public M234 study process."""

import ctypes
import gc
import time
from pathlib import Path


def memory_snapshot():
    """Keep resident and swapped memory separate when measuring reclamation."""
    result = {"rss_bytes": None, "swap_bytes": None}
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                result["rss_bytes"] = int(line.split()[1])*1024
            elif line.startswith("VmSwap:"):
                result["swap_bytes"] = int(line.split()[1])*1024
    except (OSError, ValueError, IndexError):
        pass
    return result


def recycle_idle_memory():
    """Collect unreachable cycles, then return free libc pages when supported.

    The caller owns the quiet-window gate. This must run after feed shutdown
    and evidence persistence, while no current-market frames can arrive.
    Live objects and Python arenas containing live objects remain allocated.
    """
    before = memory_snapshot()
    started = time.monotonic()
    collected = gc.collect()
    trimmed = None
    try:
        trim = ctypes.CDLL(None).malloc_trim
    except (AttributeError, OSError):
        trim = None
    if trim is not None:
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
        trimmed = bool(trim(0))
    return {"duration_ms": (time.monotonic()-started)*1000,
            "collected_objects": collected, "allocator_trimmed": trimmed,
            "before": before, "after": memory_snapshot()}

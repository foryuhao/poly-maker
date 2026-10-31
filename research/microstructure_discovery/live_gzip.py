"""Read complete JSONL records from a bounded snapshot of a growing gzip file."""

import zlib


def complete_lines(path):
    """No invented footer or record: an unfinished final JSON line is omitted."""
    decoder = zlib.decompressobj(31)
    pending = b""
    with path.open("rb") as source:
        remaining = path.stat().st_size
        while remaining:
            chunk = source.read(min(2**16, remaining))
            if not chunk:
                raise OSError("source shrank while reading")
            remaining -= len(chunk)
            pending += decoder.decompress(chunk)
            parts = pending.split(b"\n")
            pending = parts.pop()
            for line in parts:
                yield (line + b"\n").decode()
            if decoder.eof:
                if decoder.unused_data or remaining:
                    raise ValueError("unexpected concatenated gzip members or trailing bytes")
                if pending:
                    raise ValueError("closed gzip ended with an incomplete JSONL record")
                break

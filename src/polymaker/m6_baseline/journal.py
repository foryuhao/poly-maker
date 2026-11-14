"""Resume one M6 market's existing risk journal without an account-wide lease."""

import json
import os
import re
from pathlib import Path

from polymaker.rdp_sm_execution import RiskJournal


class M6RiskJournal(RiskJournal):
    def write_batch(self, records):
        """Append every FIFO record, acknowledging only after one durable sync."""
        if self._fh is None:
            raise RuntimeError("SM_JOURNAL_CLOSED")
        lines = []
        for kind, payload, ts in records:
            if payload["session_id"] != self.session_id:
                raise RuntimeError("SESSION_JOURNAL_IDENTITY_CONFLICT")
            lines.append(json.dumps({"ts": ts, "kind": kind, "data": payload}, default=str) + "\n")
        if lines:
            self._fh.write("".join(lines))
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self.sequence += len(lines)

    def __init__(self, directory, funder, *, session_id, resume=False):
        if (re.fullmatch(r"m6_(?:live|virtual)_[0-9a-f]{32}", session_id) is None
                or re.fullmatch(r"0x[0-9a-fA-F]{40}", funder) is None):
            raise ValueError("M6_JOURNAL_IDENTITY_INVALID")
        directory = Path(directory).absolute()
        if directory.resolve() != directory or directory.is_symlink():
            raise ValueError('M6_JOURNAL_SYMLINK_FORBIDDEN')
        path = directory/f"rdp_sm_{funder.lower()}_{session_id}.jsonl"
        if path.is_symlink():
            raise ValueError('M6_JOURNAL_SYMLINK_FORBIDDEN')
        self.last_writer_sequence = 0
        if not resume or not path.exists():
            super().__init__(directory, funder, session_id=session_id)
            return
        self.session_id, self.path, self.sequence = session_id, str(path), 0
        self.enabled = True
        # Keep every complete record. Critical money/identity/transaction facts
        # are independently committed in M6Ledger before external mutations.
        # A partial final diagnostic cannot be appended to as if it were JSON.
        with path.open("rb+") as source:
            end = 0
            for line in source:
                if not line.endswith(b"\n"):
                    break
                row = json.loads(line)
                if row["data"]["session_id"] != session_id:
                    raise ValueError("M6_JOURNAL_SESSION_CONFLICT")
                sequence = row["data"].get("writer_sequence", 0)
                if sequence:
                    if sequence <= self.last_writer_sequence:
                        raise ValueError("M6_JOURNAL_SEQUENCE_CONFLICT")
                    self.last_writer_sequence = sequence
                self.sequence += 1
                end += len(line)
            source.truncate(end)
            source.flush()
            os.fsync(source.fileno())
        self._fh = path.open("a", buffering=1, encoding="utf-8")

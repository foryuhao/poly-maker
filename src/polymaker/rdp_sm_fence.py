"""Market/account restart fence and recovery of possibly sent prepared IDs.

The market fence survives a clean exit: a same-market restart cannot infer a
new signal episode. Another market never reads a prior Session's journal.
Same-market recovery only cancels durable signed identities; it never resubmits.
"""

import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path

from polymaker.rdp_sm import _require


class AccountLease:
    """One OBI writer per account; historical status is not cross-market admission."""

    def __init__(self, cfg, funder):
        identity = hashlib.sha256(funder.lower().encode()).hexdigest()
        directory = Path(cfg.paths.journal_dir).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"rdp_obi_account_{identity}.json"
        # Preserve the locked inode, but never use O_APPEND: seek(0) must
        # overwrite the prior state rather than concatenate JSON documents.
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        self.file = os.fdopen(fd, "r+", encoding="utf-8")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.file.seek(0)
            raw = self.file.read()
            self.state = json.loads(raw) if raw else {}
        except BaseException:
            self.file.close()
            raise

    def write(self, state):
        # A torn overwrite is a parse failure, never permission to trade.
        self.file.seek(0)
        json.dump(state, self.file)
        self.file.truncate()
        self.file.flush()
        os.fsync(self.file.fileno())
        self.state = state

    def activate(self, fence, session_id):
        self.write({"fence": str(fence), "session_id": session_id, "status": "UNRESOLVED"})

    def verified_closed(self):
        if self.state:
            self.write({**self.state, "status": "CLOSED"})

    def close(self):
        if not self.file.closed:
            self.file.close()


def fence_path(cfg, funder, market_id):
    identity = hashlib.sha256(f"{funder.lower()}:{market_id}".encode()).hexdigest()
    return Path(cfg.paths.journal_dir).resolve() / f"rdp_obi_market_{identity}.json"


def create_fence(cfg, funder, meta, session_id, journal_path, lease=None):
    path = fence_path(cfg, funder, meta.condition_id)
    with path.open("x", encoding="utf-8") as file:
        json.dump(
            {
                "session_id": session_id,
                "market_id": meta.condition_id,
                "slug": meta.slug,
                "journal_path": journal_path,
                "funder": funder.lower(),
            },
            file,
        )
        file.flush()
        os.fsync(file.fileno())
    fd = os.open(path.parent, os.O_DIRECTORY | os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    if lease is not None:
        lease.activate(path, session_id)


async def recover_fence(reads, meta):
    lease = getattr(reads, "lease", None)
    if lease is None or lease.file.closed:
        reads.lease = lease = await asyncio.to_thread(AccountLease, reads.cfg, reads.gateway.funder)
    path = fence_path(reads.cfg, reads.gateway.funder, meta.condition_id)
    if not path.exists():
        _require(
            lease.state.get("status") == "CLOSED" or lease.state.get("fence") != str(path),
            "OBI_RECOVERY_FENCE_MISSING",
        )
        # Session independence: old journal/market availability is not a new
        # market's prerequisite. Keep the exclusive account lock, then let the
        # normal preflight inspect current official account and market facts.
        return

    def load():
        fence = json.loads(path.read_text())
        _require(
            fence.get("funder") == reads.gateway.funder.lower()
            and path == fence_path(reads.cfg, reads.gateway.funder, fence["market_id"]),
            "OBI_RECOVERY_FENCE_IDENTITY",
        )
        journal = Path(fence["journal_path"]).resolve()
        _require(
            journal.parent == path.parent and journal.name.startswith("rdp_sm_"),
            "OBI_RECOVERY_JOURNAL_SCOPE",
        )
        candidates = {}
        # A truncated trailing record cannot grant permission or erase any
        # earlier prepared risk. PREPARED itself was fsync'ed before activation.
        for line in journal.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                break
            _require(
                row["data"]["session_id"] == fence["session_id"], "OBI_RECOVERY_SESSION_CONFLICT"
            )
            if row["kind"] != "sm_prepared":
                continue
            for candidate in row["data"]["payload"]["candidates"]:
                for oid, quote in zip(candidate["order_ids"], candidate["quotes"], strict=True):
                    _require(
                        isinstance(oid, str) and len(oid) == 66 and oid.startswith("0x"),
                        "OBI_RECOVERY_ID_INVALID",
                    )
                    candidates[oid] = quote
        return fence, candidates

    fence, candidates = await asyncio.to_thread(load)
    reads.gateway._client.known_ids.update(candidates)
    report = []
    # Closing is allowed even when an order is not yet visible. not-found is
    # retained as uncertainty, and a later explicit run repeats cleanup.
    for offset in range(0, len(candidates), 50):
        ids = list(candidates)[offset : offset + 50]
        await reads.gateway.cancel_detailed(ids)
        for oid in ids:
            try:
                row = await reads.gateway.get_order(oid)
                report.append({"order_id": oid, "official": row, "prepared": candidates[oid]})
            except Exception as exc:
                report.append({"order_id": oid, "unresolved": type(exc).__name__})
    await reads.gateway.get_trades(market=fence["market_id"])
    from polymaker.rdp_sm import SessionStatus, _Issue

    raise _Issue(
        SessionStatus.BLOCKED,
        "OBI_RESTART_NORMAL_DISABLED"
        if fence["market_id"] == meta.condition_id
        else "OBI_PRIOR_SESSION_UNRESOLVED",
        context={
            "fence": str(path),
            "recovery": report,
            "reason": "same-market episode cannot be reconstructed; no new POST",
        },
    )

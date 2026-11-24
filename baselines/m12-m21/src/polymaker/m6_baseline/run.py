"""Independent M6 baseline runner. No live mode and no wallet configuration."""
import argparse
import asyncio
import fcntl
import hashlib
import json
import signal
import time
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from polymaker.config import RDPObiConfig
from polymaker.maker_public import VirtualPublicAPI
from polymaker.rdp_sm_writer import AsyncRiskWriter

from .config import STRATEGY, M6Config, signal_sources
from .control import StopControl, atomic_json
from .execution import M6Executor, M6Market
from .fill_model import POLICY
from .journal import M6RiskJournal
from .settlement import resolutions
from .signals import M6Feed
from .signing import M6Signer
from .state import M6Ledger
from .virtual import VirtualReads

VIRTUAL_IDENTITY = "0x"+"0"*39+"6"


def paths(project):
    root = Path(project).resolve()/"research_outputs/m6_virtual"
    if root.is_symlink() or root.resolve() != root:
        raise ValueError("M6_DATA_ROOT_SYMLINK_FORBIDDEN")
    return root


def sources():
    root = Path(__file__).parent
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.suffix in {".py", ".json"}}


class Executor(M6Executor):
    def __init__(self, *args, control, **kwargs):
        super().__init__(*args, **kwargs)
        self.control = control

    def inhibited(self):
        return self.control.inhibited()

    async def balance_observed(self, account):
        return None

    async def account_snapshot(self):
        return {"official_balance_usd": "10", "applicable_allowance_usd": "10",
                "source": "MODELED_GROSS_BUDGET_10_USDC_PER_MARKET"}

    def drain_network_events(self):
        return None


async def wait(stop, seconds):
    with suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


async def run(project, *, once=False, stop=None):
    project = Path(project).resolve()  # noqa: ASYNC240 -- Local setup before network.
    configured, control = M6Config.load(project), StopControl(project)
    process_sources = sources()
    if control.inhibited():
        return {"status": "STOP_CONFIRMATION_REQUIRED", "real_orders": 0}
    root = paths(project)
    root.mkdir(parents=True, exist_ok=True)
    stop = stop or asyncio.Event()
    loop, handlers = asyncio.get_running_loop(), []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
            handlers.append(signum)
        except (ValueError, NotImplementedError):
            pass
    api, ledger, feed, executor, resolver = None, None, None, None, None
    reads, journal = None, None
    signer = None
    resolver_stop = asyncio.Event()
    try:
        with (root/"runner.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Warm once before market admission/WS capture, not on a signal.
            signer = M6Signer()
            await signer.warmup()
            api = VirtualPublicAPI()
            resolver = asyncio.create_task(resolutions(root, api, resolver_stop,
                active_path=lambda: ledger.path if ledger else None), name="m6-public-settlement")
            while not stop.is_set() and not control.inhibited():
                try:
                    server, sent, received = await api.clock()
                    epoch = int(server//300)*300
                    slug = f"btc-updown-5m-{epoch}"
                    path = root/"ledgers"/(slug+".sqlite3")
                    if not path.exists() and server+1+received-sent-epoch >= configured.start_sec:
                        atomic_json(root/"startup-status.json", {"status": "WAITING_FULL_MARKET", "observed_at": time.time(), "slug": slug})
                        await wait(stop, min(5, max(.01, epoch+300-server)))
                        continue
                    meta = await api.market(slug)
                    ticks = await api.ticks(t.token_id for t in meta.tokens)
                    # Startup GET latency must not create a partial first market.
                    server, sent, received = await api.clock()
                    if not path.exists() and (int(server//300)*300 != epoch or server+1+received-sent-epoch >= configured.start_sec):
                        continue
                except Exception as error:
                    atomic_json(root/"startup-status.json", {"status": "PUBLIC_READ_RETRY", "observed_at": time.time(), "error_type": type(error).__name__})
                    await wait(stop, 1)
                    continue
                settings = configured
                saved = M6Ledger.metadata(path)
                identity = {"strategy": STRATEGY, "mode": "VIRTUAL", "funder": VIRTUAL_IDENTITY,
                            "market_id": meta.condition_id, "settings_hash": settings.fingerprint}
                if saved.get("session_id"):
                    previous = saved["identity"]
                    expected_market = json.loads(json.dumps(asdict(meta), default=str))
                    if (any(previous.get(k) != identity[k] for k in ("strategy", "mode", "funder", "market_id"))
                            or any(saved["market"][k] != expected_market[k] for k in ("slug", "condition_id", "tokens"))):
                        raise ValueError("M6_RECOVERY_SESSION_IDENTITY_CHANGED")
                    settings = M6Config.model_validate(saved["settings"])
                    if previous["settings_hash"] != settings.fingerprint:
                        raise ValueError("M6_RECOVERY_SETTINGS_INVALID")
                    identity["settings_hash"] = settings.fingerprint
                ledger = M6Ledger(path, identity)
                if ledger.get("complete"):
                    ledger.close()
                    ledger = None
                    if once:
                        return {"status": "MARKET_ALREADY_COMPLETE", "real_orders": 0}
                    await wait(stop, min(5, max(.01, epoch+300-server)))
                    continue
                sid = ledger.get("session_id")
                recovering = sid is not None
                sid = sid or "m6_virtual_"+uuid4().hex
                # Preserve the original JSON-normalized market snapshot on recovery.
                # Token identity was checked above; public descriptive fields may
                # change, and asdict(meta) contains tuples unlike persisted JSON.
                session_market = saved["market"] if recovering else asdict(meta)
                ledger.initialize_session(sid, settings.model_dump(mode="json"), session_market, baseline_fill_model=POLICY)
                scope = root/"statistics-scope.json"
                if not scope.exists():
                    atomic_json(scope, {"strategy": STRATEGY, "mode": "VIRTUAL", "model": POLICY["model"],
                        "first_market_epoch": epoch, "initial_pnl_usdc": "0", "settings_hash": settings.fingerprint})
                feed = M6Feed(meta, RDPObiConfig())
                feed.verify_ticks(ticks)
                reads = VirtualReads(feed, ledger, api, signer=signer)
                backing = M6RiskJournal(root/"journal", VIRTUAL_IDENTITY, session_id=sid, resume=recovering)
                journal = AsyncRiskWriter(backing, batch_size=128)
                journal.accepted = journal.written = backing.last_writer_sequence
                executor = Executor(None, slug, sid, reads, journal, ledger=ledger, control=control)
                executor.stop_event, executor.meta = stop, meta
                executor.official_time, executor.time_anchor = server, received
                executor.official_time_uncertainty_s = 1+received-sent
                def record_signal(payload, owner=executor):
                    owner.record("sm_m6_signal", payload)
                    return owner.journal.ready
                feed.m1.record = record_signal
                executor.record("sm_session", {"strategy": STRATEGY, "mode": "VIRTUAL", "slug": slug,
                    "settings": settings.model_dump(mode="json"), "settings_hash": settings.fingerprint,
                    "signal_sources": signal_sources(), "baseline_fill_model": POLICY,
                    "source_sha256": process_sources, "recovery": recovering, "real_orders": 0})
                atomic_json(root/"startup-status.json", {"status": "READY", "observed_at": time.time(), "slug": slug})
                feed.task = asyncio.create_task(feed.run(), name="m6-public-feed")
                market = M6Market(executor, settings, stop, public_api=api,
                    display=lambda value: atomic_json(root/"latest.json", value))
                try:
                    clean = await market.run()
                finally:
                    await feed.close()
                    executor.close()
                    executor, feed, reads, journal = None, None, None, None
                ledger.close()
                ledger = None
                if once or not clean:
                    return {"status": "COMPLETE" if clean else "RETAINED", "real_orders": 0}
            return {"status": "STOPPED", "real_orders": 0}
    finally:
        resolver_stop.set()
        if resolver:
            await resolver
        if feed:
            await feed.close()
        if executor:
            executor.close()
        elif reads:
            reads.close()
        if executor is None and journal is not None:
            journal.close()
        if ledger:
            ledger.close()
        if signer:
            signer.close()
        if api:
            await api.close()
        for signum in handlers:
            loop.remove_signal_handler(signum)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        settings = M6Config.load(args.project_dir)
        result = {"strategy": STRATEGY, "settings": settings.model_dump(mode="json"),
                  "settings_hash": settings.fingerprint, "data_root": str(paths(args.project_dir)),
                  "fill_policy": POLICY, "signal_source_sha256": signal_sources(),
                  "inhibited": StopControl(args.project_dir).inhibited(), "real_orders": 0, "network_requests": 0}
    else:
        result = asyncio.run(run(args.project_dir, once=args.once))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

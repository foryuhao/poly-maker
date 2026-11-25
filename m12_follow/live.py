"""Independent strategy-baseline follower. Import/check never contacts the venue."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import re
import sqlite3
import sys
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

FRAMEWORK = Path(__file__).resolve().parent / "framework"
sys.path.insert(0, str(FRAMEWORK / "src"))


def wallet_lock_directory():
    return Path.home() / ".local/state/polymaker/follower-wallet-locks"


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value) or int(value, 16) == 0:
        raise ValueError("invalid wallet address")
    return value.lower()


@dataclass(frozen=True)
class FollowConfig:
    source_dir: Path
    run_id: str
    funder: str
    existing_project: Path
    existing_funder: str
    trial_window: object | None = None
    existing_wallet_file: Path | None = None
    baseline_db_run: Path | None = None
    max_overpay_ticks: int = 5
    strategy: str = "M12"
    instance_id: str | None = None
    state_dir: Path | None = None

    def reader(self, meta):
        if self.baseline_db_run:
            from m12_follow.sqlite_source import SQLiteIntents
            reader = SQLiteIntents(self.baseline_db_run, meta, self.max_overpay_ticks, strategy=self.strategy)
        else:
            from m12_follow.transport import Reader
            reader = Reader(self.source_dir, run_id=self.run_id, market=meta.slug, condition_id=meta.condition_id)
        return self.trial_window.scoped_reader(reader, meta.slug) if self.trial_window else reader

    def bind_instance(self, ledger_root):
        """Called with the execution directory's runner lock held."""
        if self.state_dir is None:
            return
        from polymaker.m4_control import atomic_json

        path = self.state_dir / "instance.json"
        identity = {
            "instance_id": self.instance_id, "strategy": self.strategy, "funder": self.funder,
            "baseline_db_run": str(self.baseline_db_run), "run_id": self.run_id,
        }
        if path.is_symlink():
            raise ValueError("follower instance identity cannot be a symlink")
        if path.exists():
            if json.loads(path.read_bytes()) != identity:
                raise ValueError("follower state directory belongs to a different instance or baseline")
            return
        if any(item.name != "runner.lock" for item in ledger_root.iterdir()):
            raise ValueError("follower state directory already contains unbound execution state")
        atomic_json(path, identity)

    def stop_control(self, own):
        from polymaker.m4_control import StopControl
        legacy = StopControl(self.existing_project, live=True)

        class FollowControl:
            def inhibited(inner):
                return own.inhibited() or legacy.inhibited()

            def __getattr__(inner, name):
                return getattr(own, name)

        return FollowControl()

    def secrets(self):
        if self.existing_wallet_file is not None:
            from m12_follow.existing_wallet import existing_secrets
            if self.funder != self.existing_funder:
                raise ValueError("existing wallet reuse requires the verified original funder")
            return existing_secrets(self.existing_project, self.existing_wallet_file, self.funder)
        from polymaker.config import Secrets
        key = os.environ.get("M12_FOLLOW_PRIVATE_KEY")
        wallet = os.environ.get("M12_FOLLOW_BROWSER_ADDRESS")
        if not key or not wallet or address(wallet) != self.funder:
            raise ValueError("dedicated M12_FOLLOW_PRIVATE_KEY and matching M12_FOLLOW_BROWSER_ADDRESS required")
        return Secrets(_env_file=None, PK=key, BROWSER_ADDRESS=wallet)

    def previous_roots(self):
        root = self.existing_project / "research_outputs"
        owners = [root / "m4_live"]
        if (root / "m4_independent_live").exists():
            owners.append(root / "m4_independent_live")
        return owners

    def verify_existing_wallet(self):
        """Require persistent local evidence before trusting a distinct-wallet claim."""
        for root in self.previous_roots():
            if root != root.resolve() or not (root / "runner.lock").is_file():
                raise ValueError("existing live deployment or runner lock unavailable")
            lanes = [p for p in root.iterdir() if p.name == "ledgers" or p.name.endswith("-ledgers")]
            verified = set()
            for lane in lanes:
                if lane != lane.resolve():
                    raise ValueError("existing live ledger directory redirected")
                path = max(lane.glob("btc-updown-5m-*.sqlite3"), default=None)
                if path is None:
                    continue
                if path != path.resolve():
                    raise ValueError("existing live ledger redirected")
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.5)) as db:
                    db.execute("PRAGMA query_only=ON")
                    row = db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
                if not row:
                    raise ValueError("existing live ledger not yet identified")
                ident = json.loads(row[0])
                if ident.get("mode") != "LIVE":
                    raise ValueError("existing live ledger identity invalid")
                verified.add(address(ident["funder"]))
            if verified != {self.existing_funder}:
                raise ValueError("existing funder does not match local live ledger evidence")

    def market_entry_block(self, slug):
        """Consult only this market of the old writer, under the same-wallet lease."""
        if not re.fullmatch(r"btc-updown-5m-[0-9]+", slug):
            raise ValueError("invalid market slug")
        if self.funder != self.existing_funder:
            return None
        from polymaker.m4_live_halt import MarketHalt
        for root in self.previous_roots():
            for lane in root.iterdir():
                if not (lane.name == "ledgers" or lane.name.endswith("-ledgers")):
                    continue
                path = lane / f"{slug}.sqlite3"
                if path.resolve() != path:
                    return "OLD_MARKET_LEDGER_REDIRECTED"
                if MarketHalt(path, slug).reason:
                    return "OLD_MARKET_HALT"
                if not path.exists():
                    continue
                try:
                    reason = self._old_ledger_entry_block(path)
                except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError):
                    return "OLD_MARKET_LEDGER_UNREADABLE"
                if reason:
                    return reason
        return None

    def _old_ledger_entry_block(self, path):
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.5)) as db:
            db.execute("PRAGMA query_only=ON")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            identity = db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
            if not identity or address(json.loads(identity[0])["funder"]) != self.funder:
                return "OLD_MARKET_LEDGER_IDENTITY_CONFLICT"
            if "opportunities" not in tables:
                return "OLD_MARKET_LEDGER_UNREADABLE"
            if "taker_attempts" in tables:
                # A journaled native start can be vetoed locally before HTTP.
                # Only the original guard's immutable proof establishes UNSENT.
                unresolved = False
                for state, oid, proof in db.execute(
                    "SELECT state,order_id,proof FROM taker_attempts WHERE started!=0 AND state!='TERMINAL'"
                ):
                    evidence = json.loads(proof) if proof else {}
                    if not (state == "UNSENT" and evidence.get("kind") == "LOCAL_NATIVE_POST_BLOCKED"
                            and evidence.get("order_id") == oid and evidence.get("native_post_sent") is False):
                        unresolved = True
                        break
                sent = "SELECT 1 FROM taker_attempts WHERE started!=0 AND state!='UNSENT' LIMIT 1"
            else:
                unresolved = db.execute("SELECT 1 FROM opportunities WHERE started!=0 "
                                        "AND closed=0 AND order_id IS NOT NULL LIMIT 1").fetchone()
                sent = "SELECT 1 FROM opportunities WHERE posts>0 OR started!=0 LIMIT 1"
            uncertain = ("uncertain_posts" in tables and
                         db.execute("SELECT 1 FROM uncertain_posts LIMIT 1").fetchone())
            tracking = db.execute("SELECT value FROM metadata "
                                  "WHERE key='owned_orders_need_reconciliation'").fetchone()
            if unresolved or uncertain or (tracking and json.loads(tracking[0]) is True):
                return "OLD_MARKET_ORDER_UNRESOLVED"
            halted = db.execute("SELECT value FROM metadata WHERE key='market_halt_reason'").fetchone()
            if halted and json.loads(halted[0]):
                return "OLD_MARKET_HALT"
            if db.execute(sent).fetchone():
                return "OLD_MARKET_ALREADY_TRADED"
        return None

    @contextmanager
    def writer_ownership(self):
        # Old processes use the same advisory locks. Never create a lock in their state.
        self.verify_existing_wallet()
        with ExitStack() as held:
            directory = wallet_lock_directory()
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if directory.is_symlink() or directory.stat().st_uid != os.getuid():
                raise ValueError("follower wallet lock directory has invalid ownership")
            path = directory / f"137-{address(self.funder)}.lock"
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            lock = held.enter_context(os.fdopen(fd, "r+"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("another follower still owns the funding wallet lock") from exc
            if self.existing_funder == self.funder:
                for root in self.previous_roots():
                    path = root / "runner.lock"
                    if path != path.resolve():
                        raise ValueError("cannot verify old same-wallet runner lock")
                    lock = held.enter_context(path.open("rb"))
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError as exc:
                        raise ValueError("old same-wallet live runner still owns its lock") from exc
                self.verify_existing_wallet()
            yield


def verify_strategy(strategy="M12"):
    import polymaker.m4_run
    from polymaker.m4_live_config import M4LiveSettings
    from polymaker.m4_types import M421_LEDGER
    from polymaker.taker_pool import configured_profiles
    from polymaker.taker_runner import strategy_settings
    if Path(polymaker.m4_run.__file__).resolve().parents[2] != FRAMEWORK:
        raise ValueError("follower requires its private execution framework")
    if configured_profiles(FRAMEWORK) != {M421_LEDGER: "M42112"}:
        raise ValueError("M12 follower requires standalone M42112 pool")
    settings = strategy_settings(FRAMEWORK, M421_LEDGER, M4LiveSettings.load(FRAMEWORK),
                                 signal_profile="M42112", follow_strategy=strategy)
    if tuple(settings.signal_output_sources) != (strategy,):
        raise ValueError("follower requires a single configured signal source")
    return settings


def main(argv=None):
    from m12_follow.sqlite_source import verify_run
    from polymaker.m4_types import BASELINE_FOLLOW_STRATEGIES

    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="offline code/configuration check")
    mode.add_argument("--live", action="store_true", help="start live follower with dedicated credentials")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--source-dir", type=Path)
    sources.add_argument("--baseline-db-run", type=Path, help="authoritative baseline run with live SQLite ledgers")
    parser.add_argument("--instance-id", help="name for an isolated follower")
    parser.add_argument("--strategy", choices=BASELINE_FOLLOW_STRATEGIES, default="M12")
    parser.add_argument("--state-dir", type=Path, help="override the named instance's execution state directory")
    parser.add_argument("--max-overpay-ticks", type=int, default=os.environ.get("M12_FOLLOW_MAX_OVERPAY_TICKS", "5"))
    parser.add_argument("--run-id")
    parser.add_argument("--funder", help="expected follower funding address")
    parser.add_argument("--existing-project", type=Path, help="old live deployment; read-only controls")
    parser.add_argument("--existing-funder", help="existing live account's funding address")
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument("--once", action="store_true")
    duration.add_argument("--two-markets-from-epoch", type=int, metavar="EPOCH",
                          default=os.environ.get("M12_FOLLOW_TWO_MARKETS_FROM_EPOCH"),
                          help="only the specified five-minute market and its immediate successor")
    args = parser.parse_args(argv)
    state_dir = None
    if args.instance_id is not None:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", args.instance_id):
            parser.error("invalid follower instance ID")
        state_dir = (args.state_dir or FRAMEWORK / "runtime/follow-instances" / args.instance_id).resolve()
        protected = (FRAMEWORK / "research_outputs", FRAMEWORK / "runtime/trading-control")
        if state_dir == FRAMEWORK or any(
                state_dir.is_relative_to(p) or p.is_relative_to(state_dir) for p in protected):
            parser.error("instance state directory overlaps the legacy follower state")
    elif args.state_dir is not None or args.strategy != "M12":
        parser.error("--state-dir and non-M12 strategies require --instance-id")
    if args.strategy != "M12" and args.baseline_db_run is None:
        parser.error("non-M12 strategies require --baseline-db-run")
    if args.max_overpay_ticks < 0:
        parser.error("max-overpay-ticks must be nonnegative")
    db_run = None
    if args.baseline_db_run:
        try:
            db_run = verify_run(args.baseline_db_run, strategy=args.strategy)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            parser.error(str(exc))
        if state_dir is not None and (state_dir.is_relative_to(db_run) or db_run.is_relative_to(state_dir)):
            parser.error("instance state directory overlaps the read-only baseline")
        if args.run_id and args.run_id != db_run.name:
            parser.error("--run-id must match the authoritative baseline database directory")
        args.run_id = db_run.name
    window = None
    if args.two_markets_from_epoch is not None:
        from m12_follow.trial import TwoMarketWindow
        if args.once:
            parser.error("--once cannot be combined with a two-market trial")
        try:
            window = TwoMarketWindow(args.two_markets_from_epoch)
        except ValueError as exc:
            parser.error(str(exc))
    settings = verify_strategy(args.strategy)
    if args.check:
        risk = settings.signal_price_risk(args.strategy)
        print(json.dumps({"mode": "OFFLINE_CHECK", "source": f"{args.strategy}_BASELINE_INTENT",
                          "strategy": args.strategy, "instance_id": args.instance_id,
                          "state_dir": str(state_dir or FRAMEWORK),
                          "framework": str(FRAMEWORK), "price_risk": str(risk),
                          "price_band_applies_to": "INTENT.price" if db_run else "signed_limit_price",
                          "baseline_db_run": str(db_run) if db_run else None,
                          "max_overpay_ticks": args.max_overpay_ticks,
                          "two_market_trial": window.slugs() if window else None,
                          "order_budget": str(settings.order_budget_usdc),
                          "market_budget": str(settings.session_budget_usdc)}))
        return
    if not all((args.source_dir or db_run, args.run_id, args.existing_project)):
        parser.error("--live requires a source, run ID and --existing-project")
    project = args.existing_project.resolve(strict=True)
    if project == FRAMEWORK or not (project / "config/m4-live/balance-guard.toml").is_file():
        parser.error("--existing-project must identify the existing live deployment")
    if state_dir is not None and any(
            state_dir.is_relative_to(p) or p.is_relative_to(state_dir)
            for p in (project / "research_outputs/m4_live", project / "research_outputs/m4_independent_live",
                      project / "runtime/trading-control")):
        parser.error("instance state directory overlaps the existing live deployment")
    # The publisher can be offline at restart. Reader treats a missing channel as
    # ineligible while the executor continues to reconcile its own pending orders.
    source = db_run or args.source_dir.resolve()
    if source.exists() and not source.is_dir():
        parser.error("invalid source directory")
    wallet_file = os.environ.get("M12_FOLLOW_REUSE_EXISTING_WALLET_FILE")
    if wallet_file:
        from m12_follow.existing_wallet import existing_funder
        owner = existing_funder(project)
        if any(address(value) != owner for value in (args.funder, args.existing_funder) if value):
            parser.error("funding address conflicts with the existing M4/M21 ledgers")
        follow = FollowConfig(source, args.run_id, owner, project, owner, window, Path(wallet_file),
                              db_run, args.max_overpay_ticks, args.strategy, args.instance_id, state_dir)
    else:
        if not args.funder or not args.existing_funder:
            parser.error("dedicated wallet mode requires --funder and --existing-funder")
        follow = FollowConfig(source, args.run_id, address(args.funder), project, address(args.existing_funder), window,
                              baseline_db_run=db_run, max_overpay_ticks=args.max_overpay_ticks,
                              strategy=args.strategy, instance_id=args.instance_id, state_dir=state_dir)
    from polymaker.m4_run import run
    os.chdir(FRAMEWORK)
    with follow.writer_ownership():
        result = asyncio.run(run(FRAMEWORK, live=True, once=args.once, follow=follow))
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()

"""A fixed pair of BTC five-minute markets, with no rolling allowance after restart."""
import asyncio
import json
import time
from contextlib import closing, suppress
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
import sqlite3

from polymaker.m4_control import atomic_json
from polymaker.m4_live_budget import BudgetLedger
from polymaker.m4_live_budget_reconcile import reconcile_owned
from polymaker.m4_live_config import M4LiveSettings
from polymaker.m4_types import M421_LEDGER
from polymaker.market_protocol import market_from_snapshot, market_protocol


@dataclass(frozen=True)
class TwoMarketWindow:
    first_epoch: int

    def __post_init__(self):
        if type(self.first_epoch) is not int or self.first_epoch <= 0 or self.first_epoch % 300:
            raise ValueError("trial first epoch must be a positive five-minute boundary")

    @property
    def end(self):
        return self.first_epoch + 600

    def slugs(self):
        return tuple(f"btc-updown-5m-{epoch}" for epoch in (self.first_epoch, self.first_epoch + 300))

    def contains(self, slug):
        return slug in self.slugs()

    def scoped_reader(self, reader, slug):
        class TrialReader:
            def __getattr__(inner, name):
                return getattr(reader, name)

            def read(inner, **kwargs):
                now = time.time()
                if self.contains(slug) and self.first_epoch <= now < self.end and slug == f"btc-updown-5m-{int(now // 300) * 300}":
                    return reader.read(**kwargs)
                return None

        return TrialReader()

    def market_results(self, root):
        results = {}
        pending = {}
        for slug in self.slugs():
            path = root / "m421-ledgers" / f"{slug}.sqlite3"
            if path != path.resolve():
                raise ValueError("redirected trial ledger")
            metrics = results[slug] = dict.fromkeys(("intent_count", "post_count", "unknown_post_count",
                "confirmed_order_count", "confirmed_fill_count", "confirmed_shares",
                "confirmed_cost_with_fees_usdc", "unconfirmed_order_count"))
            metrics["ledger_status"] = "MISSING"
            if not path.is_file():
                continue
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")  # Report all stages from one consistent ledger snapshot.
                intent_count, = db.execute("SELECT COUNT(*) FROM opportunities").fetchone()
                posts = db.execute("SELECT order_id,state FROM taker_attempts "
                                   "WHERE started!=0 AND state!='UNSENT' ORDER BY seq").fetchall()
                # The evidence table is keyed by trade_id. A CONFIRMED leg can be
                # counted while a different leg of the same FAK is still settling.
                confirmed = db.execute("""
                    SELECT t.order_id,t.economics FROM taker_trade_evidence t
                    JOIN taker_attempts a ON a.order_id=t.order_id
                    WHERE t.status='CONFIRMED' AND a.started!=0 AND a.state!='UNSENT'
                """).fetchall()
            shares = cost = Decimal(0)
            for _, data in confirmed:
                fill = json.loads(data)
                shares += Decimal(str(fill["size"]))
                cost += Decimal(str(fill["principal_usdc"])) + Decimal(str(fill["fee_usdc"]))
            outstanding = [oid for oid, state in posts if state != "TERMINAL"]
            if outstanding:
                pending[path] = outstanding
            metrics.update(ledger_status="PRESENT", intent_count=intent_count, post_count=len(posts),
                unknown_post_count=sum(state == "UNKNOWN" for _, state in posts),
                confirmed_order_count=len({oid for oid, _ in confirmed}), confirmed_fill_count=len(confirmed),
                confirmed_shares=format(shares, "f"), confirmed_cost_with_fees_usdc=format(cost, "f"),
                unconfirmed_order_count=len(outstanding))
        return results, pending

    async def finish(self, root, funder, reads_type, config, stop, control):
        """Reconcile only exact order IDs from these two ledgers, without opening another market."""
        report_path = root / "two-market-trials" / f"{self.first_epoch}.json"
        report = {"first_epoch": self.first_epoch, "end_epoch_exclusive": self.end,
                  "markets": self.slugs(), "live": True}
        report["market_results"], pending = self.market_results(root)
        while True:
            report["remaining_orders"] = {path.stem: orders for path, orders in pending.items()}
            if not pending:
                report["status"] = "TWO_MARKET_WINDOW_CLOSED"
            elif stop.is_set() or control.inhibited():
                report["status"] = "TWO_MARKET_WINDOW_STOPPED_WITH_PENDING"
            else:
                report["status"] = "RECONCILING_OWNED_TRIAL_ORDERS"
            atomic_json(report_path, report)
            if report["status"] != "RECONCILING_OWNED_TRIAL_ORDERS":
                return report
            reads = reads_type(config)
            try:
                await reads.connect_account()
                if reads.gateway.funder.lower() != funder:
                    raise ValueError("trial reconciliation wallet mismatch")
                client = reads.gateway._client
                client.m4_read_only = True
                for path, orders in pending.items():
                    saved = BudgetLedger.metadata(path, strategy=M421_LEDGER)
                    identity = saved["identity"]
                    meta = market_from_snapshot(saved["market"])
                    if identity["funder"].lower() != funder or meta.slug != path.stem:
                        raise ValueError("trial ledger identity mismatch")
                    settings = M4LiveSettings.model_validate(saved["settings"])
                    fields = ("strategy", "mode", "funder", "market_id", "settings_hash")
                    with closing(BudgetLedger(path, {k: identity[k] for k in fields},
                                              settings=settings, fees=saved["taker_fees"])) as ledger:
                        ledger.set("buy_closed", True)
                        reads.bind_ledger(ledger)
                        client.session_market_id = meta.condition_id
                        client.session_market_protocol = market_protocol(meta)
                        client.m4_execution = settings.execution

                        def record(event, payload):
                            print(json.dumps({"trial_reconciliation": event, "market": meta.slug,
                                              "order_id": payload.get("order_id")}), flush=True)

                        executor = SimpleNamespace(gateway=reads.gateway, meta=meta, record=record, fatal=None)
                        for oid in orders:
                            if stop.is_set() or control.inhibited():
                                break
                            await reconcile_owned(SimpleNamespace(e=executor, ledger=ledger), oid)
                        outstanding = any(a["started"] and a["state"] not in {"TERMINAL", "UNSENT"}
                                          for a in ledger.attempts())
                        ledger.set("owned_orders_need_reconciliation", outstanding)
                        if not outstanding:
                            ledger.set("complete", True)
                report.pop("last_read_error", None)
            except Exception as exc:
                report["last_read_error"] = type(exc).__name__
            finally:
                reads.close()
            report["market_results"], pending = self.market_results(root)
            if pending:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=5)

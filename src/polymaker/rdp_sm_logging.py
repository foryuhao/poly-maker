"""RDP-SM CLI presentation only. No API, strategy or economic state writes.

The loop hands off completed windows. A single daemon writes through the
existing logging handler; a slow terminal never makes the executor await it.
"""
from __future__ import annotations

import logging
import math
import re
import traceback
from copy import copy
from datetime import datetime
from enum import Enum
from logging.handlers import QueueHandler
from queue import SimpleQueue
from threading import Thread

from polymaker.rdp_sm import TEST_CAPITAL_LIMIT_USDC

NA = "NOT_AVAILABLE"


def value(item):
    if item is None:
        return NA
    if isinstance(item, Enum):
        return value(item.value)
    if isinstance(item, float):
        return f"{item:.12g}" if math.isfinite(item) else NA
    return str(item)


def shortened(item):
    if not item:
        return NA
    item = str(item)
    return item if len(item) <= 16 else f"{item[:8]}…{item[-6:]}"


def local_time(stamp=None):
    return (datetime.now().astimezone() if stamp is None else
            datetime.fromtimestamp(stamp).astimezone()).isoformat(timespec="seconds")


def redact_credentials(data, secrets=(), *, mask_wallets=False):
    """Preserve the JSON schema while scrubbing credential fields at CLI exit."""
    if isinstance(data, dict):
        return {key: "[REDACTED]" if re.sub(r"[^a-z]", "", key.lower()) in {
            "pk", "privatekey", "apikey", "apisecret", "apipassphrase", "secret", "passphrase",
            "builderkey", "buildersecret", "builderpassphrase",
        } else redact_credentials(item, secrets, mask_wallets=mask_wallets) for key, item in data.items()}
    if isinstance(data, (list, tuple)):
        items = [redact_credentials(item, secrets, mask_wallets=mask_wallets) for item in data]
        return tuple(items) if isinstance(data, tuple) else items
    if isinstance(data, str):
        for secret in secrets:
            if isinstance(secret, str) and secret:
                data = data.replace(secret, "[REDACTED]")
        data = re.sub(r"(?i)(api[_ -]?(?:secret|key|passphrase)|private[_ -]?key|passphrase)"
                      r"([\"']?\s*[:=]\s*[\"']?)([^,\s\"']+)", r"\1\2[REDACTED]", data)
        if mask_wallets:
            data = re.sub(r"0x[0-9a-fA-F]{40}(?![0-9a-fA-F])", lambda m: shortened(m[0]), data)
    return data


def missing_book_context(raw, tick, token, meta, missing):
    """O(1) capture of this rejected snapshot; price scans belong to display."""
    return {
        "slug": meta.slug, "condition_id": meta.condition_id,
        "outcome": "YES / UP" if token == meta.yes.token_id else "NO / DOWN",
        "missing_side": "Bid" if missing == "bids" else "Ask",
        "bid_levels_count": len(raw["bids"]) if isinstance(raw.get("bids"), list) else None,
        "ask_levels_count": len(raw["asks"]) if isinstance(raw.get("asks"), list) else None,
        "tick": tick, "_raw_book": raw,
        "validation_failure": f"BOOK_SIDE_MISSING: {missing} must be a non-empty list",
    }


def resolve_book_context(context):
    """Resolve display prices off the execution path, retaining no raw JSON."""
    raw = context.get("_raw_book")
    if raw is None:
        return context

    def best(side):
        levels = raw.get(side)
        if isinstance(levels, list) and not levels:
            return "MISSING"
        if not isinstance(levels, list):
            return NA
        try:
            prices = [float(level["price"]) for level in levels]
            if not prices or not all(math.isfinite(p) and 0 < p < 1 for p in prices):
                return NA
            return max(prices) if side == "bids" else min(prices)
        except (KeyError, TypeError, ValueError, OverflowError):
            return NA

    return {**{k: v for k, v in context.items() if k != "_raw_book"},
            "best_bid": best("bids"), "best_ask": best("asks")}


class _ConsoleQueue(QueueHandler):
    def prepare(self, record):
        # Keep structlog's event dict intact for the existing console formatter.
        # Formatting and the terminal handler lock belong to the writer thread.
        return copy(record)


class HumanRunLog:
    """One CLI invocation, one FIFO writer; emit is an O(1) memory handoff."""

    def __init__(self, *, signature_type=None, secrets=(), console_handlers=None):
        self.signature_type = signature_type
        self.secrets = tuple(s for s in secrets if isinstance(s, str) and s)
        self.queue = SimpleQueue()
        self.console_handlers = console_handlers
        self.console_queue = None
        if console_handlers is not None:
            self.console_queue = _ConsoleQueue(self.queue)
            # CLI configure(console=True) installs one console handler. Filter
            # noisy INFO before enqueueing, retaining all WARNING/ERROR records.
            if len(console_handlers) == 1:
                self.console_queue.filters[:] = console_handlers[0].filters
            logging.getLogger().handlers[:] = [self.console_queue]
        self.discovery = {}
        self.account = {}
        self.started_at = local_time()
        self.window_number = 0
        self.submitted = 0
        self.fills = {}
        self.merged_cost = 0
        self.maximum_committed = None
        self.planned = None
        self.decision = {}
        self.cancel_events = []
        self.reconciliations = []
        self.thread = Thread(target=self._write, name="rdp-sm-cli-output", daemon=True)
        self.thread.start()

    def emit(self, kind, payload):
        self.queue.put((kind, payload))

    def close(self):
        # Called only after run_current_market has closed all execution resources.
        if self.console_queue is not None:
            logging.getLogger().handlers[:] = self.console_handlers
        self.queue.put(None)
        self.thread.join(timeout=5)
        return not self.thread.is_alive()

    def _safe(self, text):
        return redact_credentials(text, self.secrets, mask_wallets=True)

    def _write(self):
        logger = logging.getLogger("polymaker.rdp_sm.cli")
        while (event := self.queue.get()) is not None:
            try:
                if isinstance(event, logging.LogRecord):
                    self._output(event)
                else:
                    lines = self.render(*event)
                    if lines:
                        self._message(logger, logging.INFO, self._safe("\n".join(lines)))
            except Exception:
                # Presentation can never change trading/risk status. Never dump
                # a payload, exception message or credentials on writer failure.
                self._message(logger, logging.ERROR,
                              "RDP-SM CLI presentation failed; consult JSON/Journal")

    def _output(self, record):
        record.msg = redact_credentials(record.msg, self.secrets, mask_wallets=True)
        record.args = redact_credentials(record.args, self.secrets, mask_wallets=True)
        if record.exc_info:
            trace = self._safe("".join(traceback.format_exception(*record.exc_info)))
            record.msg, record.args = f"{record.getMessage()}\n{trace}", ()
            record.exc_info, record.exc_text = None, None
        for handler in self.console_handlers:
            if record.levelno >= handler.level:
                handler.handle(record)

    def _message(self, logger, level, message):
        if self.console_handlers is None:
            logger.log(level, message)
        else:
            self._output(logger.makeRecord(logger.name, level, __file__, 0, message, (), None))

    def _market_lines(self, market):
        elapsed = market.get("elapsed_s")
        remaining = max(0, 300 - elapsed) if isinstance(elapsed, (int, float)) else None
        return [
            "BTC Up/Down 5m Market",
            f"Market Slug: {value(market.get('slug'))}",
            f"Market ID: {shortened(market.get('condition_id'))}",
            f"Market Time: {local_time(market['start']) if market.get('start') is not None else NA} -> "
            f"{local_time(market['end']) if market.get('end') is not None else NA}",
            f"Market Elapsed Seconds: {value(elapsed)} | Trading Window: "
            "NORMAL 15 <= T < 180s | REBALANCE 15 <= T < 300s",
            "Completion: T >= 300s | NORMAL waits for market end after T >= 180s",
            f"Remaining Trading Time: {value(remaining)}s",
        ]

    def _preflight(self, p):
        market, account = p.get("market") or self.discovery, p.get("account") or {}
        self.account = account
        status = value(p.get("status"))
        lines = [f"Local Timestamp: {local_time()}", *self._market_lines(market),
                 f"Account Type: signature_type={value(account.get('signature_type', self.signature_type))}",
                 f"Wallet: {shortened(account.get('funder'))}",
                 f"Available Capital: {value(account.get('available_balance_usd'))} USDC | "
                 f"Spendable Budget (cap {TEST_CAPITAL_LIMIT_USDC:g}): {value(account.get('basic_spendable_budget_usd'))} USDC",
                 f"Current Inventory: YES={value(account.get('yes_inventory'))} "
                 f"NO={value(account.get('no_inventory'))}", f"Preflight Status: {status}"]
        if account.get("capital_basis") == "OFFICIAL_BALANCE_AT_SESSION_START":
            lines.append("Starting Capital: official account balance | Pre-existing commitments: operator managed")
        failures = [c for c in p.get("checks", []) if c.get("outcome") != "PASS"]
        for c in failures:
            lines.append(f"Failed Preflight Check: {c['name']} | {c['outcome']} | Failure Reason: {c['detail']}")
        if failures:
            lines.append("Trading Was Attempted: NO (preflight did not open this executor)")
        for diagnostic in p.get("diagnostics", []):
            lines.extend(self._tick_failure(diagnostic) if diagnostic.get("tick_check")
                         else self._book_failure(diagnostic))
        return lines

    def _tick_failure(self, d):
        return ["Tick Check: BLOCKED | DYNAMIC_TICK_UNKNOWN_OR_CHANGED",
                f"Exact Market: {value(d.get('slug'))} | Market ID: {value(d.get('condition_id'))}",
                f"Outcome: {value(d.get('outcome'))} | Token ID: {value(d.get('token_id'))}",
                f"Official Tick={value(d.get('official_tick'))} | Book Tick={value(d.get('book_tick'))} | "
                f"Supported Ticks={value(d.get('supported_ticks'))}"]

    def _book_failure(self, d):
        d = resolve_book_context(d)
        if not self.discovery and d.get("slug"):
            self.discovery = {"slug": d["slug"], "condition_id": d.get("condition_id")}
        return ["BOOK_SIDE_MISSING | BLOCKED",
                f"Exact Market: {value(d.get('slug'))} | Market ID: {value(d.get('condition_id'))}",
                f"Outcome: {value(d.get('outcome'))} | Missing Side: {value(d.get('missing_side'))}",
                f"Observed Bid Levels Count: {value(d.get('bid_levels_count'))} | "
                f"Ask Levels Count: {value(d.get('ask_levels_count'))}",
                f"Best Bid: {value(d.get('best_bid'))} | Best Ask: {value(d.get('best_ask'))} | "
                f"Dynamic Tick: {value(d.get('tick'))}",
                f"Actual Validation Failure: {value(d.get('validation_failure'))}"]

    def _window(self, w):
        self.window_number += 1
        plan = self.planned or {}
        lines = [f"Window {self.window_number} (closed) | Market Elapsed Seconds: {value(plan.get('elapsed_s'))} | "
                 f"Trading State: {value(plan.get('state'))}",
                 f"Decision Inventory: YES={value(plan.get('inventory_yes'))} NO={value(plan.get('inventory_no'))} | "
                 f"Delta: {value(plan.get('delta'))}"]
        for outcome, label in (("YES", "YES / UP"), ("NO", "NO / DOWN")):
            book = plan.get("books", {}).get(outcome, {})
            lines.append(f"{label} Decision Book: Best Bid={value(book.get('best_bid'))} "
                         f"Best Ask={value(book.get('best_ask'))} | Bid Levels Count={value(book.get('bid_levels_count'))} "
                         f"Ask Levels Count={value(book.get('ask_levels_count'))} | Dynamic Tick={value(book.get('tick'))} "
                         f"Snapshot Timestamp={value(book.get('timestamp'))}")
        for q in plan.get("quotes", []):
            lines.append(f"Planned Quote: {q['outcome']} | Side={value(q['side'])} Price={value(q['price'])} "
                         f"Size={value(q['size'])} Maker/Post-only=YES")
        attempts = w.get("placement_results", [])
        posted = [a for a in attempts if a.get("post_started") is True]
        if (len(posted) == 2 and posted[0].get("request_hash")
                and posted[0]["request_hash"] == posted[1].get("request_hash")):
            lines.append("Order Submission: YES + NO together (one batch request)")
        elif len(posted) == 1:
            lines.append(f"Order Submission: {posted[0]['quote']['outcome']} (single order request)")
        for a in attempts:
            q, posted = a["quote"], a.get("post_started") is True
            self.submitted += posted
            label = "Actually Submitted Order" if posted else "NOT SUBMITTED"
            lines.append(f"{label}: {q['outcome']} | Side={value(q['side'])} Price={value(q['price'])} Size={value(q['size'])} "
                         f"Maker/Post-only=YES | Order Submission Result={a['outcome']} | Order ID={shortened(a.get('order_id'))}")
            if a["outcome"] == "UNKNOWN":
                lines.append("UNKNOWN order result | BLOCKED | retained capital; no confirmed-fill inference")
        if not attempts:
            lines.append("Order Submission Result: NOT SUBMITTED")
        fresh = []
        for f in w.get("confirmed_fills", []):
            if f.get("status") != "CONFIRMED":
                continue
            key = (f["trade_id"], f["maker_order_id"])
            if key not in self.fills:
                fresh.append(f)
            self.fills[key] = f
        deadline, t0 = w.get("fill_deadline_monotonic"), w.get("t0_monotonic")
        duration = deadline - t0 if deadline is not None and t0 is not None else None
        used = sum(f["price"] * f["size"] for f in self.fills.values()) - self.merged_cost
        barrier = w.get("close_barrier", "BLOCKED")
        reason_label = ("Quote Skip Reason" if barrier == "SAFE_TO_CONTINUE" and not attempts
                        and w.get("block_reason") in {"QUOTE_WOULD_TAKE", "QUOTE_PRICE_TICK_INVALID", "QUOTE_PRICE_TICK_OR_SIZE_INVALID",
                                                     "NORMAL_LEG_NOTIONAL_BELOW_MINIMUM", "MAKER_BUY_NOTIONAL_BELOW_MINIMUM"}
                        else "Block Reason")
        cancel = ", ".join("ACK" if c.get("ack") is True else "NOT_ACKNOWLEDGED" for c in self.cancel_events) or NA
        latest = {r.get("order_id"): r.get("filltruth") for r in self.reconciliations}
        reconciliation = ", ".join(f"{shortened(oid)}={value(status)}" for oid, status in latest.items()) or NA
        lines.extend([
            f"Known Order IDs (run): {', '.join(shortened(i) for i in w.get('known_order_ids', [])) or NA}",
            f"FillWindow Duration (deadline - T0): {value(duration)}s",
            f"Confirmed Fill Quantity (this window, newly observed): {sum(f['size'] for f in fresh):g} | "
            f"YES={sum(f['size'] for f in fresh if f['token_id'] == plan.get('books', {}).get('YES', {}).get('token_id')):g} "
            f"NO={sum(f['size'] for f in fresh if f['token_id'] == plan.get('books', {}).get('NO', {}).get('token_id')):g}",
            f"Cancel Status: {cancel} | Reconciliation Status: {reconciliation}",
            f"Updated Inventory: YES={value(w.get('yes_inventory'))} NO={value(w.get('no_inventory'))} | Updated Delta={value(w.get('delta'))}",
            f"Capital Used (confirmed cost): {used:g} USDC | Capital Committed (M2): {value(w.get('capital_committed'))} USDC",
            f"Close Barrier Result: {barrier} | {reason_label}: {value(w.get('block_reason'))}",
            f"Next Trading State: {'BLOCKED' if barrier != 'SAFE_TO_CONTINUE' or w.get('block_reason') == 'ACCOUNT_INVENTORY_NOT_EXPLAINED_BY_SM' else 'NORMAL' if abs(w.get('delta', 0)) < 5 else 'REBALANCE'} "
            "(inventory state; next market/time check still required)",
        ])
        self.cancel_events, self.reconciliations, self.planned, self.decision = [], [], None, {}
        return lines

    def render(self, kind, payload):
        if kind == "discovery":
            self.discovery = payload
            return [f"Local Timestamp: {self.started_at}", *self._market_lines(payload),
                    f"Account Type: signature_type={value(self.signature_type)} | Wallet: {NA}",
                    f"Available Capital: {NA} | Current Inventory: YES={NA} NO={NA}",
                    f"Preflight Status: {NA} (awaiting M1)"]
        if kind == "preflight":
            return self._preflight(payload)
        if kind == "phase":
            return [f"Trading Phase: {value(payload.get('state'))} | "
                    f"Market Elapsed Seconds: {value(payload.get('elapsed_s'))}"]
        if kind == "inventory_check":
            return self._inventory_check(payload)
        if kind == "recovery":
            action = {"PAUSED": "No new BUY; retry reads and close verification",
                      "RESUMED": "Continue with fresh inventory, account and book checks",
                      "MERGE_DISABLED": "Keep inventory cost reserved; continue safe trading"}.get(
                          payload.get("status"), "Consult failure reason")
            lines = [f"Trading Recovery: {value(payload.get('status'))} | "
                    f"T+{value(payload.get('elapsed_s'))}s | Stage={value(payload.get('stage'))} | "
                    f"Reason={value(payload.get('reason'))} | "
                    f"{action} | Order and fill records retained"]
            context = payload.get("context") or {}
            if context.get("tick_check"):
                lines.extend(self._tick_failure(context))
            return lines
        if kind == "diagnostic":
            context = payload.get("context") or {}
            lines = [f"Stop Diagnostic: {value(payload.get('reason'))} | BLOCKED"]
            if "preflight" in context:
                lines.extend(self._preflight(context["preflight"]))
            elif "missing_side" in context:
                lines.extend(self._book_failure(context))
            elif context.get("tick_check"):
                lines.extend(self._tick_failure(context))
            else:
                lines.append(f"Actual Failed Check / Failure Detail: {NA}")
            return lines
        if kind == "events":
            lines = []
            for name, p in payload or []:
                if name == "sm_decision_book":
                    self.decision = p
                elif name == "sm_strategy_window":
                    self.planned = {**p, **self.decision}
                elif name == "sm_quote_refreshed":
                    self.planned = {**(self.planned or {}), "quotes": p["quotes"], "books": p["books"]}
                elif name == "sm_cancel_ack":
                    self.cancel_events.append(p)
                elif name == "sm_official_reconciliation":
                    self.reconciliations.append(p)
                elif name == "sm_placement_intent":
                    committed = float(p["capital_committed_usd"])
                    self.maximum_committed = max(self.maximum_committed or 0, committed)
                elif name == "sm_diagnostic":
                    lines.extend(self._inventory_check(p) if "inventory" in p else
                                 self._tick_failure(p) if p.get("tick_check") else self._book_failure(p))
                elif name.startswith("sm_merge_"):
                    lines.append(f"Merge: {name.removeprefix('sm_merge_').upper()} | "
                                 f"Pairs={value(p.get('amount_raw', 0) / 1_000_000)} | "
                                 f"Status={value(p.get('status'))} | Transaction={shortened(p.get('tx_hash') or p.get('tx_id'))}")
                    if name == "sm_merge_result" and p.get("status") == "CONFIRMED":
                        self.merged_cost += float(p["released_cost_usdc"])
                        lines.append(f"Merge Recovered: {p['amount_raw'] / 1_000_000:g} USDC (verified)")
                elif name == "sm_quote_skipped":
                    lines.append(f"Quote Skipped: T+{value(p.get('elapsed_s'))}s | {value(p.get('reason'))} | NOT SUBMITTED")
                elif name == "sm_window_result":
                    lines.extend(self._window(p))
            return lines
        if kind == "summary":
            r = payload
            observed = r.get("total_windows", 0) > 0
            yes = r.get("confirmed_yes") if observed else self.account.get("yes_inventory")
            no = r.get("confirmed_no") if observed else self.account.get("no_inventory")
            delta = yes - no if yes is not None and no is not None else None
            maximum = self.maximum_committed
            market = {**self.discovery, "elapsed_s": r.get("stop_elapsed_s")}
            return ["RDP-SM Exit Summary", f"Local Timestamp: {local_time()}", *self._market_lines(market),
                    f"Start Time: {local_time(r['start_time'])} | Stop Time: {local_time(r['stop_time']) if r.get('stop_time') is not None else NA}",
                    f"Stop Reason: {value(r.get('stop_reason'))} | Execution Status: {value(r.get('execution_status'))}",
                    f"Total Windows: {r['total_windows']} | NORMAL Windows: {r['normal_windows']} | REBALANCE Windows: {r['rebalance_windows']}",
                    f"ActiveCycles: {r['active_cycles']} | Submitted Orders: {self.submitted} | Confirmed Fills: {r['confirmed_fills']} (records)",
                    f"YES Inventory: {value(yes)} | NO Inventory: {value(no)} | Delta: {value(delta)}",
                    f"Completed Pairs: {value(r.get('completed_pairs') if delta is not None else None)} | Residual Shares: {value(abs(delta) if delta is not None else None)}",
                    f"Merged Pairs: {value(r.get('merged_pairs', 0))} | Recovered Collateral: {value(r.get('recovered_collateral_usdc', 0))} USDC",
                    f"Maximum Capital Utilization (observed M2 commitment / {TEST_CAPITAL_LIMIT_USDC:g} USDC cap): {value(maximum)} USDC | "
                    f"{value(maximum / TEST_CAPITAL_LIMIT_USDC * 100 if maximum is not None else None)}%",
                    f"Maximum Residual Exposure: {NA} | Exposure Duration: {NA}",
                    f"Close Barrier Result: {r['final_close_barrier']}"]
        return []

    def _inventory_check(self, payload):
        lines = [f"Inventory Check: {payload.get('status', 'BLOCKED_WAIT_UNTIL_300')} | "
                 f"T+{value(payload.get('elapsed_s'))}s | Source=DATA_API | "
                 f"Tolerance={value(payload.get('tolerance_shares'))} shares"]
        for side, values in payload.get("inventory", {}).items():
            lines.append(f"{side}: Expected={value(values.get('expected'))} "
                         f"Actual={value(values.get('actual'))} "
                         f"Difference={value(values.get('difference'))} shares")
        return lines

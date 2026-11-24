"""Manual live trial: sequential signals/markets until one confirmed buy.

No transport or sizing policy changes. A partially filled signal retains the
normal cash-target/T240 workflow. Unresolved orders at market end stop the trial.
"""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from polymaker.m4_control import atomic_json
from polymaker.m4_live_budget_market import BudgetMarket
from polymaker.m4_live_task import TERMINAL_TASKS
from polymaker.m4_state import M4Ledger, decimal

POLICY = "FIRST_CONFIRMED_SIGNAL_CASH_TARGET_V1"


def inspect_ledger(path):
    """Read exact local owned-order records; ACK and SETTLING are not fills."""
    saved = M4Ledger.metadata(path)
    if not saved:
        return {"complete": False, "fills": [], "unresolved": []}
    if saved.get("identity", {}).get("mode") != "LIVE":
        raise ValueError("M4_TRIAL_FOREIGN_LEDGER")
    with closing(sqlite3.connect(Path(path).absolute().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = [dict(r) for r in db.execute("SELECT * FROM opportunities")]
        attempts = ([dict(r) for r in db.execute("SELECT * FROM taker_attempts")]
                    if "taker_attempts" in tables else [])
    unresolved = [a["order_id"] for a in attempts
                  if a["started"] and a["state"] not in {"TERMINAL", "UNSENT"}]
    # Historical Maker ACKs had no owned fill tracking. Preserve those records;
    # they are neither confirmed fills nor this Taker trial's unresolved attempts.
    fills = [{k: r[k] for k in ("id", "filled", "spent", "state", "reason")}
             for r in rows if decimal(r.get("filled", "0")) > 0]
    return {"complete": saved.get("complete") is True, "fills": fills,
            "unresolved": unresolved, "session_id": saved.get("session_id"),
            "trial_id": saved.get("first_fill_trial_id")}


class FirstFillTrial:
    def __init__(self, state_path, root):
        self.path, self.root = Path(state_path).absolute(), Path(root).resolve()
        if self.path.resolve() != self.path or not self.path.is_relative_to(self.root / "manual-runs"):
            raise ValueError("M4_TRIAL_STATE_MUST_BE_IN_MANUAL_RUNS")
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            if (self.data.get("policy") != POLICY or self.data.get("root") != str(self.root)
                    or not isinstance(self.data.get("markets"), list)
                    or not isinstance(self.data.get("trial_id"), str)):
                raise ValueError("M4_TRIAL_STATE_INVALID")
        else:
            self.data = {"policy": POLICY, "root": str(self.root), "trial_id": uuid4().hex,
                         "markets": [], "status": "WAITING_MARKET"}
            self.save()

    def save(self):
        atomic_json(self.path, self.data)

    def ledger_path(self, slug):
        # Never accept arbitrary filesystem paths from a saved manifest.
        prefix = "btc-updown-5m-"
        if not isinstance(slug, str) or not slug.startswith(prefix) or not slug[len(prefix):].isdigit():
            raise ValueError("M4_TRIAL_MARKET_INVALID")
        return self.root / "ledgers" / (slug + ".sqlite3")

    def result(self, status, **fields):
        self.data.update(status=status, **fields)
        self.save()
        return {"status": status, "live": True, "trial_state": str(self.path), **fields}

    def resume(self):
        """Called under the runner lock, before any network/entry is enabled."""
        owned = set(self.data["markets"])
        recovery = None
        for slug in self.data["markets"]:
            state = inspect_ledger(self.ledger_path(slug))
            if state.get("trial_id") != self.data["trial_id"]:
                raise ValueError("M4_TRIAL_LEDGER_IDENTITY_CHANGED")
            if state["complete"]:
                if state["unresolved"]:
                    return self.result("FIRST_FILL_UNRESOLVED", market=slug, **state), None
                if state["fills"]:
                    return self.result("FIRST_FILL_COMPLETED", market=slug, **state), None
            elif recovery is not None:
                raise ValueError("M4_TRIAL_MULTIPLE_INCOMPLETE_MARKETS")
            else:
                recovery = slug
        for path in sorted((self.root / "ledgers").glob("*.sqlite3")):
            if path.stem in owned:
                continue
            if inspect_ledger(path)["unresolved"]:
                return self.result("PRIOR_OWNED_ORDERS_UNRESOLVED", market=path.stem), None
        return None, recovery

    def bind(self, ledger, slug):
        if ledger.path != self.ledger_path(slug):
            raise ValueError("M4_TRIAL_LEDGER_PATH_CONFLICT")
        owner = ledger.get("first_fill_trial_id")
        if owner not in (None, self.data["trial_id"]) or (owner is None and ledger.rows()):
            raise ValueError("M4_TRIAL_CANNOT_ADOPT_EXISTING_SIGNALS")
        ledger.set("first_fill_trial_id", self.data["trial_id"])
        if slug not in self.data["markets"]:
            self.data["markets"].append(slug)
        self.result("RUNNING_MARKET", market=slug)

    def finish_market(self, ledger, clean):
        slug = ledger.get("market")["slug"]
        state = inspect_ledger(ledger.path)
        if not clean:
            return self.result("RETAINED", market=slug, **state)
        if state["unresolved"]:
            return self.result("FIRST_FILL_UNRESOLVED", market=slug, **state)
        if state["fills"]:
            return self.result("FIRST_FILL_COMPLETED", market=slug, **state)
        self.result("WAITING_MARKET", market=slug, **state)
        return None


class FirstFillMarket(BudgetMarket):
    """Only one signal at a time, including during delayed fill confirmation."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.e.record("sm_m4_first_fill_trial", {
            "policy": POLICY, "trial_id": self.ledger.get("first_fill_trial_id"),
            "max_active_signals": 1, "partial_fill": "CONTINUE_CASH_TARGET_UNTIL_T240",
            "stop_after": "FIRST_FILLED_SIGNAL_MARKET_END",
        })

    def admission_allowed(self):
        rows = self.ledger.rows()
        if any(decimal(r["filled"]) > 0 for r in rows):
            return False
        if any(a["started"] and a["state"] not in {"TERMINAL", "UNSENT"}
               for a in self.ledger.attempts()):
            return False
        return all(r["state"] in TERMINAL_TASKS | {"ABORTED"} for r in rows)

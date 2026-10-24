# poly-maker

A maker-only market-making bot for **Polymarket CLOB V2**, focused on political
markets. Single async process, local-file config, typed and
tested.

> [!WARNING]
> Market making on Polymarket is competitive and can lose money. This is a
> reference implementation and a research harness, not a guaranteed-profitable
> product. Test in `--paper` mode first; go live with small size.

## What it does

- Discovers political markets via the **Gamma API** (seconds) and ranks them by
  reward + rebate income vs. volatility/spread risk.
- Maintains a live order book per token from the **market WebSocket**.
- Quotes **maker-only** — every order is post-only. Fair-value + inventory-skew
  strategy that posts BUY-YES and BUY-NO as a two-sided quote, with live
  volatility/toxicity estimation and a regime machine that pulls quotes during
  news events (see [Strategy](#strategy)).
- Reconciles a target quote set against live orders with churn tolerances; runs
  the exchange **heartbeat** dead-man switch; enforces risk caps and a daily-loss
  kill switch.
- Config, market selection, and state are **local files + SQLite**. An operator
  with the repo, a `.env`, and a funded wallet is a complete deployment.

## Install

Uses [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
uv sync --extra dev          # install deps + dev tools
uv run polymaker --help
```

## Configure

```bash
cp .env.example .env         # then edit two values:
```

- `PK` — the private key of your signer wallet
- `BROWSER_ADDRESS` — your Polymarket address (shown on the profile / developer page)

Everything else is TOML under [`config/`](config/):

- `config.toml` — wallet/engine/risk/execution settings
- `strategy.toml` — named parameter profiles (`political-longdated`, `political-hot`)
- `markets.toml` — the trade list (populated via the CLI below)

### RDP opposite BA exit

`config/config.toml` selects `rdp_sm.repair.policy = "opposite_ba_v1"`.
`livecfg/config.toml` retains `hd_tp_sl_v1`; both files contain settings for both
policies. Select one policy when starting a new session; no hot reload applies.
All existing HD/TP/SL parameters are preserved for switching back.

With `opposite_ba_v1`, net YES waits for a new NO BA, and net NO waits for a new
YES BA. The exit detector uses `[rdp_sm.repair.opposite_ba]` and its `.network`
section, independently of NORMAL's `[rdp_sm.ba]`. Both consume the same ordered
market stream using the existing SELL → Bid depletion → same-price refill logic.
Only an unconsumed, currently valid opposite BA whose first SELL is at or after
the opening fill can submit a BUY at that BA's anchor Bid. The BA deadline starts
at its first SELL and is never extended by preparation, submission or retries.

Invalid/expired BA orders are cancelled and reconciled before another independent
opposite BA can submit. BA and the price policy share repair sizing: round up to
the legal quantity/minimum notional, require smaller absolute net exposure after
the proposed BUY, and enforce the same capital, Delta and maximum-order limits.
This can produce a smaller opposite-side residual. Residuals of **at most 2.5
shares**, inclusive, end the round once the existing order/fill/account close
barrier is safe; their exact quantities and costs remain in the ledger. Larger
reverse residuals use the existing FIFO direction handoff and wait for a fresh
opposite BA. Existing cooldown and NORMAL time/admission rules then apply.

There is no holding timeout, TP/SL price signal, sigma requirement, T180 forced
exit, or high-entry-price abandonment. The existing T240 market submission
cutoff remains: cancel/reconcile old orders, retain any residual for settlement.
No opposite BA means no exit order. Maker submission does not guarantee a fill.
See [the implementation contract](docs/development/OPPOSITE_BA_EXIT_V1.md).

### RDP TP / SL comparison switches

The `hd_tp_sl_v1` policy has independent exit switches in both
`config/config.toml` and `livecfg/config.toml`:

```toml
[rdp_sm.repair.path]
tp_enabled = true
sl_enabled = true
```

Both default to `true`, including when the fields are omitted. Change the file
used by the intended run; configuration is read when a new session starts,
not hot reloaded into an active trading session.

| `tp_enabled` | `sl_enabled` | Exit behavior |
|---|---|---|
| `true` | `true` | Existing TP, SL and T+180 forced SL behavior. |
| `false` | `true` | Skip TP; retain SL signals, Delta breach repair and T+180 forced SL. |
| `true` | `false` | TP may operate before T+180; then cancel its remaining order and hold residual Delta. |
| `false` | `false` | Submit no TP / SL repair; hold residual Delta through market end. |

Disabling SL also disables the adverse shock, drawdown, T+180 and Delta breach
**SL orders**. Existing NORMAL admission and capital/Delta limits still apply.
Order cancellation, fill reconciliation, ownership proofs and merging of
already paired positions continue normally. Previously filled shares remain
in inventory; changing a switch does not undo fills.

The switches apply to `hd_tp_sl_v1`; disabling either with `ev_legacy` is a
configuration error. Existing parameter and replay readiness checks still
apply. Session configuration, repair decisions and policy snapshots record
the switches for later comparison.

With repair disabled, an unpaired episode continues to block new NORMAL
entry under the existing coordinator. Live comparisons therefore will not
reproduce the offline experiment's fixed set of 148 historical NORMAL fills.

### RDP dynamic cooldown

After a completed REPAIR ownership barrier, the OBI coordinator consumes this
round's newly completed FIFO YES/NO pairs once. Only actual Maker BUY fill
prices enter `PairPnL = shares - YES cost - NO cost`; existing position and
collateral reconciliation remains the authority for delayed native statuses.
Unpaired residuals, rebates and pairs consumed in earlier rounds are excluded.
TP, SL and TP-to-SL use the same economic test.

REPAIR completes only after order/fill reconciliation is safe and the exact
session residual is at most **2.5 shares**, inclusive. The residual stays in
inventory, actual acquisition cost, capital commitment and Delta admission;
it is neither rounded away nor treated as paired PnL. NORMAL may resume with
that residual, using its existing quoting, sizing, risk and time checks.
Unresolved orders/fills still block completion even below the threshold;
the small-residual wait uses a bounded timer rather than socket events.

```toml
[rdp_sm.cooldown]
max_sec = 20.0
loss_sec_per_dollar = 100.0
```

`cooldown_sec = min(max_sec, max(0, -PairPnL / shares * loss_sec_per_dollar))`;
zero pairs produce zero seconds. Positive durations enter `COOLDOWN`, blocking
all Maker submissions while market feeds, volatility samples, reconciliation,
cancellation and emergency handling continue. The monotonic deadline never
resets on repeated proofs. Expiry requires the original NORMAL admission
conditions, including its T+120 cutoff. Unresolved execution or invalid PnL
evidence stays blocked. An expired timer logs the reason if NORMAL cannot
resume. Account rechecks use a bounded timer rather than each book update;
after T+120 they retain the original one-second cutoff waiting cadence.
Settings apply when a new session starts; zero
`max_sec` disables the wait without changing FIFO accounting.

High-entry REPAIR abandonment (v1.2) is configurable:

```toml
[rdp_sm.repair.abandon_high_entry]
enabled = true
min_elapsed_sec = 120.0
entry_price_threshold = "0.80"
```

When a round is still in REPAIR at or after T+120 and its original NORMAL
first-leg actual VWAP is at least 0.80 (inclusive, either side), the market
permanently enters `HOLD_TO_SETTLEMENT`. The NORMAL order/fill barrier must
first establish its actual fills. Reverse repair prices and current Mid are
not entry prices. Existing controllers cancel and reconcile all outstanding
orders; final POST guards also stop already prepared orders. Earlier partial
repair fills and fills racing cancellation remain in the ledger and risk.
No further NORMAL, REPAIR, cooldown resumption or merge is performed in this
market; feeds, volatility, account reconciliation and safety handling continue.
`sm_repair_abandoned` records the trigger and reconciled FIFO pair PnL plus
retained residual acquisition costs. Residual settlement PnL stays pending
until actual settlement; rebates and estimated terminal values are excluded.
Disabling this rule preserves the other state transitions.

Offline 100-market validation and its simulation assumptions are recorded in
[v1.2 validation](docs/development/reviews/20261022_high_entry_abandon_v12/README.md).

## Use

```bash
# 1. discover + rank political markets (writes to state.db)
uv run polymaker scan
uv run polymaker markets

# 2. add markets to the trade list
uv run polymaker markets-add <slug> --profile political-longdated

# 3. dry run: full pipeline against the live feed, no orders posted
uv run polymaker run --paper

# 4. preflight the wallet before going live
uv run polymaker doctor

# 5. self-tests: a deep post-only order (free), then a real fill round-trip (~cents)
uv run polymaker livetest      # place a deep post-only order + cancel (no fill)
uv run polymaker moneydoctor   # limit rest + market buy + market sell, auto-flattens

# 6. go live
uv run polymaker run

# ops
uv run polymaker status        # positions / open orders
uv run polymaker cancel-all    # panic button
```

## Architecture

```
market WS ─▶ OrderBook ─▶ (wake) ─▶ Quoter ─▶ strategy (pure) ─▶ reconcile ─▶ ExecutionGateway
user WS   ─▶ StateStore                                         RiskManager ┘   (post-only, heartbeat)
Gamma     ─▶ Catalog/scanner ─▶ SQLite            periodic REST reconcile ┘
```

One async event loop. The strategy layer is a pure function `(book, inventory,
params, clock) → TargetQuotes` — deterministic and unit-tested. The engine owns
all I/O and state around it; the `ExecutionGateway` wraps `py-clob-client-v2`
(which handles the V2 EIP-712 signing) and offloads its blocking calls to a
thread pool so the hot path never stalls. State (positions, orders, PnL, catalog)
lives in one SQLite file; raw WS/order events are journaled to `journal/` for
replay.

Runtime data stays inside this project: `journal/`, `logs/`,
`research_outputs/`, `research_data/`, `research_audits/`, and `backups/`.
The `livecfg/` configuration uses its own `livecfg/journal/` and `livecfg/logs/`.
These are real directories, without links to sibling directories. Run commands
from the project root. Log retention operations are documented in
[`ops/README.md`](ops/README.md).

## Strategy

Maker-only, quoting both sides of each market as USDC-collateralized bids:

- **Fair value** — depth-weighted microprice off the live book, nudged by an
  EWMA of signed trade flow.
- **Quote construction** — reservation price `r = FV − skew(inventory)`;
  half-spread `δ = base + c_vol·σ + c_tox·toxicity`. Post **BUY-YES at `r − δ`**
  and **BUY-NO at `(1 − r) − δ`**. Because both legs are bids that sum below 1,
  a filled pair merges back to USDC at locked edge `1 − p − q` — a maker-only
  exit that never crosses the spread.
- **Inventory skew** — net position leans both quotes: long YES → bid YES lower,
  bid NO higher (acquire the offsetting leg). Size tapers as inventory approaches
  a soft cap, then the adding side is pulled entirely.
- **Volatility / toxicity** — realized-vol and per-fill markout (adverse
  selection) EWMAs widen the spread and shrink size in markets that pick us off.
- **Regime machine** — per market: `QUIET` (farm rewards in-band), `TRENDING`
  (lean + widen + half size), `EVENT` (sweep/jump detected → pull quotes, cool
  off), `REDUCE_ONLY` (inventory cap / near end date → exits only), `HALTED`
  (stale data / resolved / kill switch → cancel all).
- **Rewards + rebates** — quotes stay inside the liquidity-rewards band in QUIET;
  the market selector also scores the new maker-rebate program (a share of taker
  fees rebated to makers).
- **Risk** — per-market notional cap, neg-risk event-group worst-case cap, total
  exposure cap, daily-loss kill switch, WS-staleness halt.

Tune it all via profiles in `config/strategy.toml`.

## Develop

```bash
uv run pytest                 # unit suite (offline)
POLYMAKER_LIVE=1 uv run pytest tests/test_live_marketdata.py   # live WS integration
uv run ruff check src tests   # lint
uv run mypy src               # types (strict)
```

## Status

Implemented and live-verified end to end (auth → book → strategy → sign → post →
cancel): config, catalog/scanner, order book + analytics, strategy (FV,
vol/toxicity, regime, quoting), state store + lifecycle, execution gateway +
reconciler + heartbeat, market/user websockets, risk manager, merger (EOA path),
engine, CLI, paper mode, journal capture. 83 tests; ruff + mypy strict clean.

Not yet built: a replay backtester over the captured journals, and external data
feeds (polls / news / cross-venue). Merging through a Safe/proxy wallet routes a
tx via the relayer and isn't wired yet — until then inventory exits via limit
sells rather than merging.

## License

MIT

### FavoriteFirst run review

A completed `favorite-first-live --once` market writes `raw.log`, `events.jsonl`,
`run.json`, and `chat_report.md` under `logs/runs/<run_id>/` (or the configured
log directory). Upload `chat_report.md` for routine strategy review. Inspect
`raw.log` for engineering anomalies. Run `uv run polymaker favorite-first-report
--last 10` for a structured multi-market summary. Settlement and rebates stay
`unknown` until an authoritative source supplies them.

# Cross-market microstructure discovery

Read-only research using public Polymarket data. Does not import the trading executor, read `.env`, send orders, change strategy configuration, or install a service.

Run from the project root:

```bash
.venv/bin/python research/microstructure_discovery/study.py \
  --output research_outputs/microstructure_discovery/my_run --seconds 1800 --markets 24
.venv/bin/python research/microstructure_discovery/audit.py research_outputs/microstructure_discovery/my_run
.venv/bin/python research/microstructure_discovery/report.py research_outputs/microstructure_discovery/my_run
.venv/bin/pytest -q research/microstructure_discovery/test_study.py
```

Use a new output directory for each run: collection deliberately opens output files in write mode. A bounded run exits after the requested duration. Discovery errors fail visibly. WebSocket disconnects are recorded, books are reset on reconnect, and markouts never bridge connection generations. Raw messages and metadata remain reproducible. Re-analysis without network access:

```bash
.venv/bin/python research/microstructure_discovery/study.py \
  --output research_outputs/microstructure_discovery/my_run --analyze-only
```

## Research question and measurement

Can current, observable order-flow/book features distinguish subsequent persistent price impact from temporary liquidity shocks across different markets?

Discovery combines 300 highest-24h-volume and 300 newest active markets. Requires binary outcomes, accepting orders, enabled orderbook, and first-outcome price 0.03–0.97. A keyword-based category cap diversifies the pilot. It is neither exhaustive nor a representative random sample. Save all discovery rows and the exact selected token/outcome mapping.

The collector stores raw messages, receipt-time L2 books sampled approximately every second, and trade messages with the book visible at receipt. It reconstructs absolute-size price changes, removes zero-size levels, and excludes empty/crossed books from normalized quotes. Transport ping plus application PING keeps the connection alive.

`impact_h = trade_sign * (mid_h - mid_at_message_receipt)` at h=5,30,120 seconds. BUY sign +1; SELL sign -1. These are markouts from receipt, not estimates of trade-caused price impact. Trade and book updates may be delivered in either order, so the baseline may already incorporate the execution. No trade-time prebook is asserted.

The first sample at/after each target is eligible only within 2 seconds and within the same connection generation. Reject trade source/receipt lags outside [-0.25,2] seconds. Conservatively deduplicate by asset, source timestamp, side, price, size, and transaction hash. Identical genuine fills may be merged because this feed lacks unique execution IDs.

Near-touch depth sums sizes within 0.02 of each side's best price. Future/current depth ratios are NOT pure replenishment measurements because the price band moves. L2 cannot identify trader identity, intent, hidden liquidity, or distinguish canceled and executed quantities from depth changes alone.

Descriptive persistent path: signed impact at 5s >= half a tick and at 120s >= one tick. Descriptive reverting path: first condition holds but 120s impact <= 0. These are ex-post outcome labels, NOT real-time predictors. Example selection deliberately highlights large changes and must not be used as a performance estimate.

Immediate-fade gross PnL uses opposing executable quotes: after a BUY shock, sell at initial bid and buy at future ask; after a SELL shock, buy at initial ask and sell at future bid. This assumes immediate fills and sufficient capacity; excludes fees, latency, rebates, inventory/collateral constraints and settlement risk. It is an optimistic price-only diagnostic, not a backtest.

## Evidence and stopping rules

- Raw source messages: `raw.jsonl`; trade receipts with L2 baseline: `trades.jsonl`.
- Per-second quotes: `quotes.jsonl`; exact forward calculations: `events.jsonl`.
- Metadata/discovery inputs, collection diagnostics, maturity/rejection counts and SHA-256 hashes are saved alongside results.
- `replay_audit.json` independently compares reconstructed BBO to price-change-reported BBO; mismatch/regression requires investigation before accepting affected cases.
- `REPORT.md` includes market-level observations and at most three examples per path type, linked to raw trade line numbers.
- Multiple trades, opposite outcomes, and related markets are correlated. Nonoverlap counts apply a 120s separation per condition across both tokens; they still do not guarantee independence.

No candidate is a validated edge from a short pilot. If the pilot is quiet or has no qualifying paths, retain that result instead of widening thresholds retrospectively. For inference, freeze sampling/definitions, collect across dates, cluster by event/market, and use chronological holdout. Train classification only on past data; compare with unconditional and spread-only baselines. Align public news/event feeds before interpreting information arrival. Validate executable costs, capacity and latency separately before considering live trading.

Official source: https://docs.polymarket.com/market-data/realtime-data

## Current independent information-lead study (V8)

The pilot above and V2–V6 did not establish an executable edge. Current work
tests whether a matched public Kalshi proposition reprices before Polymarket
withdraws its old liquidity. The fixed universe and future decision rule are
in `PROTOCOL_V8_INFORMATION_LEAD.md`; `RESEARCH_STATE.md` records chronology,
negative results, process identities and data limitations. Entry parameters
and running capture sources are frozen. Research remains read-only.

`information_lead.py` reads the bounded bulk capture. Its Polymarket BUY model
spends a USD FOK budget, deducts fees in shares and enforces minimum notional
and depth. Kalshi books expose bids: opposite-outcome bids imply the ask.
Partial execution, observation errors and unconfirmed payouts remain in the
accounting. Ordinary completed-game outcome matching does not erase different
postponement/cancellation provisions.

The independently captured Chelsea–Manchester United observation at
2026-10-31 13:38:40 UTC has delayed book evidence, actual public executions at
the old prices and verified final payouts. Its specific historical price
advantage does not prove repeatable strategy returns. Reproduce that case:

```bash
.venv/bin/python research/microstructure_discovery/verify_crossvenue_case.py \
  research_outputs/microstructure_discovery/crossvenue_focused_20261031 \
  --case research_outputs/microstructure_discovery/crossvenue_focused_20261031/automatic_confirmation/1793453920449_KXEPLGAME-26OCT31CFCMUN-MUN \
  --ws-root research_outputs/microstructure_discovery/crossvenue_mun_ws_20261031
```

Evaluate all V8 decisions, including losses and pending exposure, without
placing orders:

```bash
.venv/bin/python research/microstructure_discovery/information_lead_settlement.py \
  research_outputs/microstructure_discovery/information_lead_v8_20261031
.venv/bin/pytest -q research/microstructure_discovery
```

Terminal accounting cross-checks native Polymarket resolution data against
CTF payouts at a recorded Polygon block and the exact finalized Kalshi ticker.
Unknown or exceptional payouts retain full cost exposure. All numbers are
conditional public-book paper bounds, never personal trading PnL.
`live_gzip.py` and `seal_crossvenue_ws.py` preserve and replay complete raw
records from ongoing compressed captures without requiring a fabricated EOF.

V11 (`information_lead_ws.py`, `ws_gate.py`) adds a current full-depth WS
check before any simulated exposure. A later V8 signal had an already
obsolete REST ask and produced a single-leg unwind loss bound. V8 keeps that
loss; V11 is a separately registered future study. Its protocol, dependency
hashes, warmup coverage and raw feed are recorded under
`information_lead_ws_v11_20261031`. Use the same terminal-accounting command
with that output directory. Neither ongoing study has established strategy
profitability.

The V9/V10 weather studies retain 910 city-date observations each and all
selected losses. They compare actual cheap-NO taker prices with the opposing
economic exposure to favorite-YES taker fills. The latter is a maker exposure,
not an executable NO ask or an assumed place in the queue. Their narrow local
09:00–09:05 window produced only one and six eligible observations, too few
for inferential confidence intervals. Selected payouts were checked directly
against Polygon CTF getters. The source paper and replication questions are
saved with V9; a published calibration return is not a trading-edge proof.

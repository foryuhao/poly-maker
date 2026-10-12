# RDP-SM CLI Logging — Worker Result

## 1. Baseline

- Baseline: `rdp-sm-microlive-candidate-v0.1.0`, commit `f9376be5a90c7ac910fff29e445bdfdc4ef85231`.
- Frozen SOP: RDP-SM v1.8. No SOP, strategy, trading-window, FillWindowSec, capital-policy, market-selection, execution-permission or Close Barrier rule was changed.
- Before this task, the candidate's RDP-SM files were already present as untracked files and `cli.py` was modified relative to HEAD (`c9d52ad`). Their contents matched the candidate tag. Existing work was preserved; no Git staging, commit or push was performed.
- Development and verification were offline. No real orders, network calls, workspace credentials, Merge/Redeem or scheduling.

## 2. Actual Logging Changes

`uv run polymaker rdp-sm-run` defaults to human logs on **stderr**. The original single machine-readable RunResult JSON remains on **stdout**, with the same exit-code rule. `--json-only` disables the human presentation; `--http-info` restores HTTP/component INFO diagnostics through the existing logging configuration.

Startup prints a local timestamp with timezone, exact slug, shortened condition ID, market start/end times, official elapsed seconds, the existing `[15, 240)` trading window, remaining trading time, configured account signature type, masked wallet, account capital/inventory, and actual M1 preflight status. Before an authenticated account snapshot exists, these account fields explicitly say `NOT_AVAILABLE`.

Completed-window output shows:

- Window number, decision elapsed time/state, initial YES/NO inventory and delta.
- Both decision books: best bid/ask, observed bid/ask level counts, dynamic tick and exact snapshot timestamp. These are the snapshots used by `make_quotes`; they are labelled **Decision Book**, not a new/current post-window fetch or the later M2 pre-submit book.
- NORMAL's two planned quotes or REBALANCE's one quote, with outcome, BUY side, price, size and Maker/Post-only status.
- Separately, each placement with `post_started=True` as **Actually Submitted Order**, including ACK/REJECTED/UNKNOWN and a shortened known ID. ACK is not a fill. A planned second leg that never reaches POST is never displayed as submitted.
- Existing deadline minus T0, newly observed **CONFIRMED** quantity, cancellation request acknowledgments, latest observed FillTruth reconciliation outcome per order, updated confirmed inventory/delta, confirmed acquisition cost and M2 committed capital, actual Close Barrier and the inventory-derived next state. The next window still requires the existing market/time/risk checks.

CONFIRMED legs are deduplicated by `(trade_id, maker_order_id)`. MATCHED/MINED do not enter the human confirmed-fill totals or inventory. Submitted-order counts include POST attempts with explicit rejection or UNKNOWN; they are not ACK-only counts. Known IDs and confirmed fill records in WindowResult are cumulative and are labelled accordingly.

Exit shows market/start/stop/stop reason/status, all requested window/cycle/order/fill/inventory/pair/residual fields, observed maximum M2 commitment in USDC and as a percentage of the existing **30 USDC test cap**, and the final barrier. Confirmed fill count is a **record count**, while the window quantity is shares. Capital used and committed are separate views, not quantities to add together. No realized PnL or profit is inferred.

The existing `httpx`/`httpcore` repetitive INFO is filtered only for this command's console. Default human mode also hides routine `execution.filltruth`, `state.tracker` and `state.store` INFO, whose relevant outcomes appear in window summaries. All WARNING/ERROR and exception diagnostics remain; other loggers retain their existing behavior. `--http-info` restores the original INFO records.

Human wallet addresses are masked, including addresses in component console messages. Human presentation uses an allowlist of fields and never dumps SDK raw responses, session credentials or account objects. Configured secrets, credential-named fields and labelled credentials in messages/exception traces are redacted at the output boundary. Machine JSON retains its structure and economics; credential values, if unexpectedly present in an SDK response, become `[REDACTED]`. Existing JSON Journal references retain their original paths for analysis-tool compatibility. Journal contents and persistence behavior are unchanged.

## 3. Example CLI Output

The following is an excerpt captured by `test_l01_l04_l08_l09_l11_normal_rebalance_confirmed_summary`, using the actual CLI, M1/M2/M3, SDK/Gateway/Tracker/FillTruth/Journal, and public dummy-account HTTP/time fixtures. These are **offline simulated fills**, not trading evidence or profit. Local wall time and the fixture's official market time deliberately differ.

```text
Local Timestamp: 2026-10-12T15:54:19+08:00
BTC Up/Down 5m Market
Market Slug: btc-updown-5m-1800000000
Market ID: 0xababab…ababab
Market Time: 2027-01-15T16:00:00+08:00 -> 2027-01-15T16:05:00+08:00
Market Elapsed Seconds: 15 | Trading Window: 15 <= T < 240s
Remaining Trading Time: 225s
Account Type: signature_type=0
Wallet: 0x1a642f…0914F1
Available Capital: 100 USDC | Spendable Budget (cap 30): 30 USDC
Current Inventory: YES=0 NO=0
Preflight Status: SESSION_READY
Window 1 (closed) | Market Elapsed Seconds: 15 | Trading State: NORMAL
Decision Inventory: YES=0 NO=0 | Delta: 0
YES / UP Decision Book: Best Bid=0.49 Best Ask=0.51 | Bid Levels Count=1 Ask Levels Count=1 | Dynamic Tick=0.01 Snapshot Timestamp=1800000015
NO / DOWN Decision Book: Best Bid=0.48 Best Ask=0.52 | Bid Levels Count=1 Ask Levels Count=1 | Dynamic Tick=0.001 Snapshot Timestamp=1800000015
Planned Quote: YES | Side=BUY Price=0.48 Size=5 Maker/Post-only=YES
Planned Quote: NO | Side=BUY Price=0.479 Size=5 Maker/Post-only=YES
Actually Submitted Order: YES | Side=BUY Price=0.48 Size=5 Maker/Post-only=YES | Order Submission Result=ACK | Order ID=o-1
Actually Submitted Order: NO | Side=BUY Price=0.479 Size=5 Maker/Post-only=YES | Order Submission Result=ACK | Order ID=o-2
Known Order IDs (run): o-1, o-2
FillWindow Duration (deadline - T0): 3s
Confirmed Fill Quantity (this window, newly observed): 5 | YES=5 NO=0
Cancel Status: ACK | Reconciliation Status: o-1=COMPLETE, o-2=COMPLETE
Updated Inventory: YES=5 NO=0 | Updated Delta=5
Capital Used (confirmed cost): 2.4 USDC | Capital Committed (M2): 2.4 USDC
Close Barrier Result: SAFE_TO_CONTINUE | Block Reason: NOT_AVAILABLE
Next Trading State: REBALANCE (inventory state; next market/time check still required)
Window 2 (closed) | Market Elapsed Seconds: 18 | Trading State: REBALANCE
Decision Inventory: YES=5.0 NO=0 | Delta: 5.0
YES / UP Decision Book: Best Bid=0.49 Best Ask=0.51 | Bid Levels Count=1 Ask Levels Count=1 | Dynamic Tick=0.01 Snapshot Timestamp=1800000018
NO / DOWN Decision Book: Best Bid=0.48 Best Ask=0.52 | Bid Levels Count=1 Ask Levels Count=1 | Dynamic Tick=0.001 Snapshot Timestamp=1800000018
Planned Quote: NO | Side=BUY Price=0.48 Size=5 Maker/Post-only=YES
Actually Submitted Order: NO | Side=BUY Price=0.48 Size=5 Maker/Post-only=YES | Order Submission Result=ACK | Order ID=o-3
Known Order IDs (run): o-1, o-2, o-3
FillWindow Duration (deadline - T0): 3s
Confirmed Fill Quantity (this window, newly observed): 5 | YES=0 NO=5
Cancel Status: ACK | Reconciliation Status: o-1=COMPLETE, o-2=COMPLETE, o-3=COMPLETE
Updated Inventory: YES=5 NO=5 | Updated Delta=0
Capital Used (confirmed cost): 4.8 USDC | Capital Committed (M2): 4.8 USDC
Close Barrier Result: SAFE_TO_CONTINUE | Block Reason: NOT_AVAILABLE
Next Trading State: NORMAL (inventory state; next market/time check still required)
RDP-SM Exit Summary
Local Timestamp: 2026-10-12T15:54:19+08:00
BTC Up/Down 5m Market
Market Slug: btc-updown-5m-1800000000
Market ID: 0xababab…ababab
Market Time: 2027-01-15T16:00:00+08:00 -> 2027-01-15T16:05:00+08:00
Market Elapsed Seconds: 240 | Trading Window: 15 <= T < 240s
Remaining Trading Time: 0s
Start Time: 2026-10-12T15:54:19+08:00 | Stop Time: 2027-01-15T16:04:00+08:00
Stop Reason: MARKET_CUTOFF | Execution Status: COMPLETED
Total Windows: 3 | NORMAL Windows: 2 | REBALANCE Windows: 1
ActiveCycles: 2 | Submitted Orders: 5 | Confirmed Fills: 2 (records)
YES Inventory: 5 | NO Inventory: 5 | Delta: 0
Completed Pairs: 5 | Residual Shares: 0
Maximum Capital Utilization (observed M2 commitment / 30 USDC cap): 9.595 USDC | 31.9833333333%
Maximum Residual Exposure: NOT_AVAILABLE | Exposure Duration: NOT_AVAILABLE
Close Barrier Result: SAFE_TO_CONTINUE
```

## 4. Error Diagnostics

`BOOK_SIDE_MISSING` retains the original validation condition, validation order and BLOCKED failure code. A small context object is built only on that failure from **the exact rejected raw book**, with slug/full condition ID, mapped YES/UP or NO/DOWN outcome, failing Bid or Ask, observed counts, observable best prices, validated tick and the actual failed non-empty-list check. An empty list's price is `MISSING`; an absent/non-list/unparseable price is `NOT_AVAILABLE`. A missing level list is not reported as zero levels. No extra book fetch or cached fallback is used.

Example from the offline NO missing-ask case:

```text
BOOK_SIDE_MISSING | BLOCKED
Exact Market: btc-updown-5m-1800000000 | Market ID: 0xabababababababababababababababababababababababababababababababab
Outcome: NO / DOWN | Missing Side: Ask
Observed Bid Levels Count: 1 | Ask Levels Count: 0
Best Bid: 0.48 | Best Ask: MISSING | Dynamic Tick: 0.001
Actual Validation Failure: BOOK_SIDE_MISSING: asks must be a non-empty list
```

`M1_SESSION_NOT_READY` retains the actual PreflightResult, including non-PASS checks, detail/status, available account/market context and book-failure context. Both M3's startup preflight and M2.open's independent second preflight are covered. The display explicitly says that this failed preflight did not open an executor or attempt trading; it does not promote REJECTED/BLOCKED to PASS.

For example, an EOA identity mismatch reports `Failed Preflight Check: account_identity | BLOCKED | Failure Reason: EOA_FUNDER_IDENTITY_MISMATCH`. Clean-market inventory and insufficient-capital rejections report their original check details. When a check never produced account or official elapsed-time evidence, those fields say `NOT_AVAILABLE`.

A missing M2 second-leg book is shown separately from the earlier **Decision Book**: the first ACK order remains visible, the second plan is not shown as a POST, existing cancellation/cleanup still runs, and the original blocking reason remains. UNKNOWN clearly says BLOCKED and retains the existing M2 committed capital; neither an ACK nor a cancel ACK establishes confirmed fill or a safe barrier.

## 5. Performance Impact

- One command-specific daemon and a `SimpleQueue`, using the existing stdlib/structlog console formatter and handlers. No new logging dependency, network read, database, disk output, fsync or reporting framework.
- All default-mode console records, including component WARNING/ERROR, use the same writer. This avoids a terminal-held console-handler lock blocking Gateway/Tracker/FillTruth on the trading thread. Standard `QueueHandler.prepare` is minimally overridden to shallow-copy the LogRecord and leave structlog formatting to the writer.
- Inside M2, the only added successful-path work is an optional list append of the already-created Journal payload reference. Its failure is isolated from Journal/risk handling. Book-level counts are two constant-time list lengths. No formatter, human callback or queue output runs between the first/second POST and window cleanup.
- Decision-book presentation data are handed off **after** `execute_window` returns and inventory is updated. No additional synchronous Journal serialization or flush was introduced. Existing strategy/window/run Journal payloads retain their schema and contents. The displayed window uses the actual returned WindowResult, including a barrier changed by a failed final Journal write.
- Startup/preflight/discovery enqueue existing observations without awaiting terminal output. Failure-only M2 diagnostics capture the rejected snapshot in constant time; best-price scans run on the output thread after execution closes. Failed M1 preflights resolve compact public diagnostics with no active executor. Raw book levels are never added to preflight JSON or Journal. No read/cancel/reconciliation task is added or moved.
- The writer drains for at most five seconds **after execution resources have closed**. Stdout JSON serialization and credential scrubbing also happen after execution. A permanently stalled terminal may prevent all queued console lines from being shown before exit; it cannot hold the executor's deadline/cancellation/barrier path.
- L10 compares the same real production run with logging disabled and enabled, resetting only the external HTTP/time fixture and temporary Journal location. With the writer stalled while holding the actual StreamHandler lock, both POSTs remain at T+15, deadline and close remain T0+3, cancel duration remains zero in the fake time boundary, and the entire recorded HTTP request sequence, inventory, M2 capital and barrier match. Both runs close execution before the terminal is released.

The timing result is **UNCHANGED in offline semantic/timing checks**. The small in-memory operations have nonzero CPU overhead; this report does not assert zero wall-clock cost or verified live-exchange latency. No real orders were authorized or sent.

## 6. Tests

All executions use the existing credential/network guard, with fixtures replacing external HTTP/SDK transport, public dummy credential sources and time boundaries. New tests use production strategy/session/execution logic, not FakeGateway/FakeTracker/FakeFillTruth or fabricated RunResult outcomes. The stalled-terminal test instruments only the output boundary.

| Requirement | Production-path evidence | Result |
|---|---|---|
| L01 | Actual CLI startup: market/timezone/slug/elapsed/window/capital/preflight | PASS |
| L02 | NORMAL YES/NO books, levels, ticks and planned/submitted quotes; next-window changed book/tick | PASS |
| L03 | Inventory-driven REBALANCE shows only planned NO after YES confirmed fill | PASS |
| L04 | Confirmed fills update inventory; MATCHED/MINED remain zero confirmed | PASS |
| L05 | YES missing bid, NO missing ask, absent levels and M2 second-leg rejected fresh snapshot | PASS |
| L06 | Identity/clean-market/capital/book checks; M2.open re-preflight rejection after initial READY | PASS |
| L07 | UNKNOWN first POST blocks; planned second quote never appears as submitted | PASS |
| L08 | Cutoff summary and out-of-window account/inventory NOT_AVAILABLE | PASS |
| L09 | Key/builder/API credentials, raw responses, wallet masking, warning/exception redaction | PASS |
| L10 | Disabled/enabled request-sequence and timing comparison under a held terminal-handler lock | PASS |
| L11 | JSON parsed from stdout; final Journal RunResult equals JSON; original strategy-event keys; CLI regressions | PASS |
| L12 | Existing M1/M2/Type2/M3/handoff and selected FL suites below | PASS |

Final verification batches:

| Batch | Executed | Passed | Failed | Skipped | Notes |
|---|---:|---:|---:|---:|---|
| CLI logging + existing R11/R15 CLI compatibility | 22 | 22 | 0 | 0 | 20 new tests, 2 repeated existing tests; 23 entry cases excluded from this targeted repeat are covered below |
| Existing Entry/M3/M3-H01 | 58 | 58 | 0 | 0 | Full selected files; 173.83s |
| Existing M1/M2/Type2 + selected FL | 445 | 445 | 0 | 0 | 16.33s; one pre-existing credential-guard deselection |
| Final diagnostic-path recheck | 20 | 20 | 0 | 0 | 2.80s; same new tests after moving rejected-book price scans off the M2 path |
| Final batch invocations | 545 | 545 | 0 | 0 | Includes 22 deliberate repeats |
| **Distinct tests** | **523** | **523** | **0** | **0** | One unique deselected test, documented below |

Every final process reports `M0 offline guard: 0 network/DNS attempts; workspace credentials denied`. The pre-existing `test_cli_conflict_stops_before_settlement_preflight` remains deselected: it discovers workspace `.env` before its expected configuration-conflict exception under this guard. Credentials were not enabled to run it. This is not counted as a skipped or passing test.

Bootstrap (run from the project root, without reading workspace credentials):

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c "import tempfile,runpy,sys,os; tempfile.tempdir='/dev/shm'; sys.path.insert(0,os.path.abspath('tests')); runpy.run_path('tests/rdp_sm_m0_offline.py',run_name='__main__')" <test arguments>
```

Batch arguments:

```text
tests/test_rdp_sm_cli_logging.py tests/test_rdp_sm_entry.py
-k 'logging or test_r11_r12_full_cli_single_market_and_exit or test_r15_help_import_preflight_and_startup_error_never_place' -q --tb=short

tests/test_rdp_sm_entry.py tests/test_rdp_sm_m3_loop.py tests/test_rdp_sm_m3_h01_handoff.py -q --tb=short

tests/test_rdp_sm_type2_account.py tests/test_rdp_sm_m2_h01_pagination.py tests/test_rdp_sm_m2_h01_commitment.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_execution.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py
-k 'not test_cli_conflict_stops_before_settlement_preflight' -q --tb=short
```

Ruff on all six changed/new source files and the new test file passes. `git diff --check` passes. `uv run --no-sync --offline polymaker rdp-sm-run --help` exits zero and shows the default human mode and diagnostic options; this help-only call does not execute trading.

Initial test-authoring failures (fixture expectation mismatches, an enum display issue, and an incorrectly shaped injected SDK test response) were corrected. Only completed final passing batches above contribute to the result; earlier runs are not counted again.

## 7. FL Regression

**PASS** for the selected baseline production regressions: Gateway execution/positions, FillTruth, StateStore/Tracker, userstream parsing, BTC5m discovery, catalog, book/marketdata, favorite-live and configuration aliases. Existing UNKNOWN, post-only, pagination, commitment, cancellation, deadline, previous-proof and M3 handoff regressions pass.

All baseline `src/` files were compared byte-for-byte against the candidate tag. The only existing source differences are the five RDP-SM/CLI files listed below. Shared Gateway, Tracker, FillTruth, Journal, StateStore, config, Engine, FL strategy, catalog and BTC5m source are unchanged. AST comparison also confirms the account policy, quote construction, inventory, snapshot, committed capital, quote validation, cancellation, reconciliation and barrier definitions match the baseline exactly (11 definitions). M2 execute_window matches after removing only its new diagnostic-context append branch. The command-specific logging filter/queue does not alter other CLI entry points. This is the selected baseline regression suite, not a claim that every unrelated repository test was run. M0's historical CLOSED_WITH_FINDINGS evidence is not relabelled PASS.

## 8. Change Manifest

| File | Change |
|---|---|
| `src/polymaker/cli.py` | Default human presentation, JSON-only/raw-INFO options, command console filtering/queue lifecycle, final JSON credential redaction |
| `src/polymaker/rdp_sm_logging.py` | New command-specific renderer, minimal stdlib console queue/writer, snapshot failure context, redaction and summaries |
| `src/polymaker/rdp_sm.py` | Two observed book-level counts; additive PreflightResult diagnostics; exception context for the unchanged missing-side rejection |
| `src/polymaker/rdp_sm_entry.py` | Optional observer; enqueue already-known discovered market and entry failure context |
| `src/polymaker/rdp_sm_loop.py` | Optional observation handoff after completed windows; retain real preflight failure context; original Journal payloads unchanged |
| `src/polymaker/rdp_sm_execution.py` | Optional reference capture of existing Journal events; preflight and rejected-book context only; no new execution/risk decision |
| `tests/test_rdp_sm_cli_logging.py` | 20 guarded tests using actual production components, plus temporary captured example output |
| `docs/development/RDP_SM_CLI_LOGGING.md` | This report |

No dependencies, configuration, DBs, shared FL/core sources, frozen SOP documents or other existing tests were changed. There are no added HTTP calls. `make_quotes`, M2 `_snapshot`, `committed`, capital gates, deadline scheduling, cancellation, reconciliation and Close Barrier rules are unchanged. Diagnostic fields in preflight JSON are additive; the RunResult and WindowResult dataclass schemas are unchanged.

Because the baseline RDP-SM files are untracked in this workspace, plain `git diff` against the candidate tag does not represent their working-file changes correctly. Review each manifest file against `git show rdp-sm-microlive-candidate-v0.1.0:<path>` and compare to the current file; treat the new renderer/test/report as additions. No staging was used to alter the existing workspace state.

## 9. Remaining Limitations

- Maximum residual exposure and exposure duration are explicitly `NOT_AVAILABLE`. They have no existing run-level metric with agreed units/interval semantics; this logging task does not create a new exposure-reporting framework or reconstruct an unverified estimate from pending trades.
- Maximum capital utilization is the maximum **observed M2 owned commitment** from existing placement-intent payloads divided by the 30 USDC cap. It is not account-wide utilization or a continuous exchange-side measurement. If no such event exists, it is `NOT_AVAILABLE`.
- Book display is deferred until the window closes. It identifies the decision snapshot, not a guarantee that the book still has those prices when it is printed. M2 continues its existing fresh reads before each POST.
- FillWindow Duration is the existing deadline interval, not an invented measured exchange fill lifetime. Cancel ACK and FillTruth COMPLETE are shown separately from the actual Close Barrier.
- If startup stops before M1 obtains account/time data, fields are `NOT_AVAILABLE`; default zero-valued RunResult inventory is not presented as an observed account balance. The machine JSON/Journal reference paths remain intact and can contain their existing wallet-derived path names; human console wallet fields are masked.
- A permanently stalled output stream may leave queued logs undisplayed after the bounded post-execution drain. No live timing or exchange acceptance has been tested or authorized.

Final result:

```text
CLI LOGGING: CODE_READY
MARKET DISPLAY: PASS
ORDER BOOK DISPLAY: PASS
QUOTE DISPLAY: PASS
EXECUTION DISPLAY: PASS
ERROR DIAGNOSTICS: PASS
EXIT SUMMARY: PASS
HTTP NOISE: REDUCED
EXECUTION TIMING: UNCHANGED (offline timing/semantics)
JSON/JOURNAL: COMPATIBLE
TESTS: Executed 523 / Passed 523 / Failed 0 / Skipped 0 (distinct; 1 deselected)
FL REGRESSION: PASS (selected baseline suite)
SHARED CORE CHANGES: NO
REPORT: docs/development/RDP_SM_CLI_LOGGING.md
NEXT ACTION: Independent Targeted Review
```

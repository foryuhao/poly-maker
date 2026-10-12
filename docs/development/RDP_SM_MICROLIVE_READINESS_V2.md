# RDP-SM MicroLive Readiness v2

## 1. Baseline

- Required/current source branch: `fl-restored-97ab0f9`.
- HEAD / original FL baseline: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`.
- Date: 2026-10-12, Asia/Shanghai. SOP: RDP-SM v1.8 FROZEN.
- Entry status: tracked CLI modification; untracked accepted SM source, tests and development reports; index empty. Origin: `https://github.com/foryuhao/poly-maker.git`; upstream unchanged.
- Architecture: FL-first / Thin SM Orchestrator; MicroLive First. This Worker has NO live trading authorization.
- Prior Readiness, development and independent reports are preserved. Their earlier generic live-gate/Type2 restrictions describe historical scope; this task explicitly authorizes a dedicated production CLI without an additional authorization gate and accepts live Safe authority as NOT_VERIFIED_FOR_LIVE.
- No historical RDP development branch was read, restored or copied. No unrelated files were cleaned.

## 2. Accepted Milestones

M1 Safe Session, M2 Execution Integrity, M3 Trading Loop: ACCEPTED per Architect/user. M2-H01/H02 and M3-H01 CLOSED. M0-H01/H02/H03 CONTAINED by existing M2 execution/close/UNKNOWN protections. Type 2 offline compatibility PASS. No milestone redeveloped or frozen NORMAL/REBALANCE formula changed.

## 3. Production CLI

Actual operator command:

```bash
uv run polymaker rdp-sm-run
```

Optional existing `--config-dir`; no market ID, `--live`, authorization parameter or second confirmation. Only this explicitly invoked new command calls the SM production composition. Existing separate FL commands are retained.

```text
Typer rdp-sm-run
  -> run_current_market
  -> official current BTC5m discovery + immediate startup time rule
  -> SMTradingLoop.run_production
  -> actual M1 run_preflight with retained account evidence
  -> actual SMWindowExecutor.open (M1 recheck + Type2 identity + durable Journal)
  -> actual M3 NORMAL/REBALANCE loop
  -> actual M2.execute_window
  -> existing FL Gateway / official SDK / Tracker / FillTruth / StateStore
  -> M2 cancel / terminal proof / confirmed reconciliation / close barrier
  -> Journal + final RunResult + process exit
```

The CLI contains no Gateway.place/cancel call and no executor replacement. `SMTradingLoop.run()` still defaults to BLOCKED and keeps its offline MockTransport boundary. Dedicated `run_production` reuses the same runner and M2; no separate execution engine, Gate, Ledger or state machine.

## 4. Current Market Discovery

Public official CLOB `/time` selects `current_epoch(now)` and existing `slug_for_epoch`. Existing Gamma transport `/markets?slug=<exact>&limit=2` requires exactly one raw market. `_market` validates BTC Up/Down slug, 300 seconds, aligned slug start, official endDate and supplied eventStartTime, two correct positive uint256 token IDs and tradability. Actual CLOB `/book` and `/tick-size` validate dynamic ticks, identities and fresh books against a second official time read. Clock reversal, interval crossing, missing/invalid/ambiguous response fails closed.

Production M1 **and every M2 snapshot** use the same strict SM market-read hook. Shared Gamma remains unchanged. Discovery CID/tokens are bound to M3; later metadata change or conflicting response blocks new orders. Independent Review found an initial post-discovery uniqueness gap; the Worker repaired this hook and added conflict-at-M1, initial-M2-snapshot and second-BUY tests.

Startup `15 <= Now - MarketStart < 240`. Before15 and at/after240 exits immediately, with zero account initialization/placements and no waiting. At239 M1 can run, but existing M2/M3 close margin denies a new window. Single slug only; no market retry, next-market selection, scheduler, background trading task or automatic restart. Cutoff closes and exits.

Official live diagnostic selected the current market and checked distinct tokens, 300 seconds and fresh valid official dynamic ticks. Only read helpers/preflight were invoked; the production trading function/CLI was not run against the account.

## 5. Account Preflight

Current official GET-only observation at **2026-10-12 07:06:27–07:06:33 UTC**:

| Item | Evidence/result |
|---|---|
| Signature type | 2 |
| Signer/Funder | Different; configured key-derived signer, Gateway/SDK signer, configured/Gateway/SDK Funder agree |
| Safe derivation | MATCHED using existing installed official SDK local derivation, no RPC |
| Live signing authority | NOT_VERIFIED_FOR_LIVE, allowed MicroLive validation item |
| Official collateral balance | 36.064169 USDC |
| Applicable exchange allowance | Sufficient; official spender/version resolution reused |
| Current account open orders | 0, complete SDK pagination |
| Positive account positions | 0, existing official Data API v2 reader targets Funder |
| Current-market inventory/orders | Zero, actual current tokens checked |
| After retained historical pending reserve | 25.914169 USDC basic spendable budget |
| Production-aware read-only M1 | SESSION_READY; official time, identity, account, capital, market, freshness and window checks PASS |

All configuration identity mismatches fail closed. Shared signing and Gateway unchanged. Actual key, API secret/passphrase, full signer/funder/order/trade identities were never printed or written to deliverables. Exact account amounts in this local report are redacted in the release export.

Diagnostics enforced GET-only, approved exact official hosts/paths, no redirects and bounded timeouts. The successful combined diagnostic issued **33 GET, 0 HTTP writes**, including targeted old-order reads. Earlier bounded diagnostics were also GET-only; one sampled market at T+275 correctly failed book eligibility and was not treated as clearance. No RPC/third-party interface, SDK credential creation, trading CLI, order POST or cancellation request was invoked.

## 6. Historical MATCHED

Fresh authenticated trade history: 230 rows; 224 CONFIRMED, 3 FAILED, **3 MATCHED**. Three pending records reference unrelated March2026 BTC5m markets. Official `/markets` with `condition_ids` **and `closed=true`** returned one matching closed, resolved, not-accepting-orders market per record. Without `closed=true` these archived markets were absent; absence was not treated as resolution evidence. Trade match times precede official market end, which precedes the current market start. Official historical tokens match each relevant account leg and differ from current tokens.

Related order reads remain unavailable. No terminal order proof or Redeem claim is made. Two MAKER records have current-Funder BUY legs; the TAKER record is an authenticated account BUY. Exact related identifiers and raw responses are not published.

Known historical BUY costs: **3.25 + 3.50 + 3.40 = 10.15 USDC**. The minimal SM account adapter retains this entire known pending cost and subtracts it from the existing M1/M2 collateral budget. This is a conservative additional reserve, even if official balance already reflects the historical spend. It is never counted as SM inventory or rewritten to CONFIRMED/REDEEM_VERIFIED. Later empty trade snapshots and terminal labels do not silently release the retained cost.

Decision: **HISTORICAL_MATCHED_NONBLOCKING**, with bounded historical cost **still reserved**. Actual current funds after reserve support first Maker clips. Nonblocking is based on reliable unrelated resolved market identity, complete current account reads and retained financial cost, not label/age/OpenOrders=0 alone.

Current-market unowned pending, unrelated unclosed/unresolved market pending, ambiguous attribution/identity/economics, historical token contradiction, related current/retained observed order or related positive position: BLOCKED. Owned current pending goes to existing M2 confirmed-only close verification. M1→M2 carries the same policy evidence as well as existing observed order/trade/page-failure facts. Genuine observed commitments are neither deleted nor released by the historical exception.

## 7. Trading Safety

- Frozen BUY-only / Maker-only / post-only / clip5 / $30 / three-second FillWindow unchanged; no SELL/Taker/Merge/Redeem.
- M2 capital admission, cumulative confirmed inventory cost, unresolved full intent/ACK/UNKNOWN and retained external commitments remain authoritative. Historical cost is deducted in both M1 and M2's existing collateral calculation.
- M2 still rechecks time, tick, market/account identity, fresh capital and evidence before **each** POST. `elapsed + remaining window + CANCEL_BUFFER_S < 240`; no new BUY at/after240.
- Two NORMAL orders share one three-second quote window. Cancellation and reconciliation may take longer. No new authorization work is inserted into the window.
- M2 barrier cannot be bypassed; unknown placement, pending fill, failed pagination or unreliable terminal/economic evidence stop new orders. Failed final closure reports INCOMPLETE.
- Capital Exhausted retains `TEST_CAPITAL_LIMIT_EXCEEDED` or `OFFICIAL_CAPITAL_INSUFFICIENT` and safe final barrier when verified; it is not relabelled as UNKNOWN exposure or profitable completion. No retry to evade the cap.
- Process exceptions do not start another run. Existing Journal lock/nonempty-history refusal prevents automatic reuse of unresolved session state.

## 8. MicroLive Metrics

Existing Journal and RunResult support minimum audit; no reporting framework/database/dashboard/M4 was added. Minimal additions to existing SM records:

| Record | New necessary fields |
|---|---|
| sm_session | Starting spendable and official balance, $30 limit, historical retained reserve and classification |
| sm_placement_intent | Current cumulative M2 committed capital, before the POST |
| sm_cancel_ack | Cancellation request wall-clock time and monotonic duration |
| sm_window_result | Close monotonic timestamp, alongside existing quote-window start/deadline |

Available reconstruction:

- **Capital:** starting spendable, fixed limit, pending/committed capital, confirmed fill cost, maximum committed/30; session/run timestamps and exact capital stop reason yield time-to-exhaustion, otherwise NOT_REACHED. Submitted orders are never confirmed inventory.
- **Maker execution:** actual windows/NORMAL/REBALANCE/ActiveCycles, ACK/submitted orders, deduplicated confirmed economic fills. Fill Rate = distinct orders with confirmed fills / submitted POST attempts (`post_started`, including UNKNOWN; record denominator explicitly). The offline R14 sample has five POSTs and five ACKs, so both observed counts coincide. Quote lifetime is observed ACK→first relevant cancellation request; fill/terminal records permit earlier termination when reliable. Cancel duration and close_monotonic−fill_deadline give cancellation/reconciliation duration; no unsupported exchange latency claim.
- **Directional exposure:** deduplicated verified confirmed fills, token/outcome/price/size and official fill timestamps in existing `sm_trade`/raw official reconciliation reconstruct Delta, max residual shares/cost and exposure intervals across multiple windows. Raw official `match_time` can supplement absent normalized timestamp; missing/unreliable times must be reported INCOMPLETE, never fabricated. Final RunResult supplies final residual. Group simultaneous fills by reliable timestamp to avoid artificial exposure ordering.
- **Pairs:** existing confirmed YES/NO, quantity-weighted average costs, min(YES,NO) pairs and theoretical pair PnL. This is not realized/settlement PnL; residual shares are not profit.

R14 uses actual offline records: three windows, five ACK orders, three confirmed fills/ActiveCycles, fill rate0.6; maximum residual5 shares/$2.40; one directional exposure lasting **6 seconds across three windows**, then final YES5/NO5/residual0 and theoretical PairPnL0.202. All values are public dummy-account simulation. Exact real exchange acceptance/cancellation and actual fill performance remain MicroLive targets.

## 9. Test Results

Final verification uses three guarded process batches. A combined process was externally terminated (exit137) after partial progress; it has no complete verdict and is not counted as a pass. The batches avoid overlapping memory-heavy full-market processes. No failing assertion was observed in the interrupted run.

| Final suite | Executed / Passed / Failed / Skipped | Deselected | Runtime |
|---|---|---:|---|
| Entry R01–R15 + dynamic ambiguity | 25 /25 /0 /0 | 0 | 41.91s |
| M3 + M3-H01 handoff | 33 /33 /0 /0 | 0 | 61.93s |
| M1/M2/Type2 + selected FL | 445 /445 /0 /0 | 1 | 16.46s |
| Distinct final total | 503 /503 /0 /0 | 1 | |

The complete test argument set is shown below; final results above were obtained in three process batches (entry; M3/handoff; remaining M1/M2/Type2/FL) using the same bootstrap. The combined invocation itself was interrupted, as recorded above:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c "import tempfile,runpy,sys,os; tempfile.tempdir='/dev/shm'; sys.path.insert(0,os.path.abspath('tests')); runpy.run_path('tests/rdp_sm_m0_offline.py',run_name='__main__')" tests/test_rdp_sm_entry.py tests/test_rdp_sm_m3_loop.py tests/test_rdp_sm_m3_h01_handoff.py tests/test_rdp_sm_type2_account.py tests/test_rdp_sm_m2_h01_pagination.py tests/test_rdp_sm_m2_h01_commitment.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_execution.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -k 'not test_cli_conflict_stops_before_settlement_preflight' -q --tb=short
```

Prior worker runs: entry25 PASS, M3/handoff33 PASS, M1/M2/Type2/selectedFL445 PASS. One preserved deselection: `test_cli_conflict_stops_before_settlement_preflight` discovers workspace.env before its expected exception under the credential guard; credentials were not enabled to run it. Selected new CLI tests exercise actual Config loading using a public dummy external .env source. All offline runs deny sockets/DNS and workspace credentials, fake only HTTP/time/dummy configuration and delegate instrumentation to actual production components.

R01–R15 covered by new entry tests plus accepted safety regressions. Both root/command help, imports, ordinary preflight and unexpected startup error submit no orders. `uv run --no-sync --offline polymaker rdp-sm-run --help` exit0; no market/live/authorization argument. Ruff no-cache / git diff --check / shared core quiet diff PASS.

## 10. FL Regression

Actual selected FL execution, FillTruth, state/tracker, userstream parsing, BTC5m discovery, catalog, book/marketdata, official positionsv2, favorite-live and config-alias regressions passed in the guarded production-component suite. No shared Gateway/Tracker/FillTruth/Journal/config/Engine/FL strategy/catalog/btc5m source changed. Existing M2-H01/H02/M3-H01/Type2 assertions pass; existing UNKNOWN/cancel/post-only/deadline tests contain M0 findings. Original M0 feasibility report remains CLOSED_WITH_FINDINGS; it is not retrospectively rewritten PASS.

## 11. MicroLive Blockers

No remaining demonstrated production/account safety blocker after strict-read repair and conservative historical reserve. Final regression batches and independent targeted Review PASS; no remaining release safety blocker. Live Safe signing authority, actual acceptance/fill/cancel latency/profit are explicitly not pre-live blockers.

## 12. Deferred

Actual Type2 live authority/order acceptance, real Maker fill rate/cancellation latency/rebate/profitability: validate only in the first human-started controlled MicroLive. Fine exchange latency and timestamps absent from a particular live response must be reported as unverified. Settlement PnL, Merge/Redeem, fullM4/database/dashboard, recovery and multi-market scheduling remain out of scope. Historical order terminal/Redeem proof not claimed; retained conservative cost remains in the session budget.

## 13. Readiness Decision

**MICROLIVE_READY = YES**. Production CLI/offline integration, actual current official market/account and conservative historical policy verified. No real order/cancel executed and no unauthorized shared-core change. All final batch checks complete:503 distinct cases PASS,0 failed/skipped,1 preserved deselection. Independent targeted Review PASS.

## 14. Independent Review

**INDEPENDENT_REVIEW = PASS**. Separate `/root/microlive_entry_review` independently passed157 distinct targeted tests, reproduced the initial uniqueness defect, then re-executed its original counterexample and final43-case suite to verify the Worker fix. It created only its review report. Current reviewed source/test hashes exactly match this workspace. Worker checks are not substituted for this independent verdict.

Report: `docs/development/RDP_SM_MICROLIVE_ENTRY_INDEPENDENT_REVIEW.md`.

## 15. GitHub Release

**GITHUB_RELEASE = PUSHED**. Atomic push succeeded; independent subsequent remote-ref query verified branch, annotated tag object and peeled commit.

- Branch: `rdp-sm-microlive-candidate`.
- Annotated tag: `rdp-sm-microlive-candidate-v0.1.0`.
- Commit: `454403c1d0bb866dfe1c2650bbb9e316665143a7`.
- Annotated tag object: `f9376be5a90c7ac910fff29e445bdfdc4ef85231`.
- Remote Branch Commit = Remote Tag peeled Commit = Local Release Commit = `454403c1d0bb866dfe1c2650bbb9e316665143a7`.
- Origin checked and unchanged: `https://github.com/foryuhao/poly-maker.git`.
- Candidate branch/tag were absent before creation; both were created as new remote refs. No force/overwrite/remote deletion. Original source branch/HEAD preserved.
- GitHub URL: [candidate branch](https://github.com/foryuhao/poly-maker/tree/rdp-sm-microlive-candidate), [annotated tag](https://github.com/foryuhao/poly-maker/releases/tag/rdp-sm-microlive-candidate-v0.1.0). These URLs derive from the verified origin/remote refs; no separate GitHub Release object was requested or created.
- Release export used isolated worktree `/home/ubuntu/poly-maker-microlive-candidate` and an explicit39-file allowlist. The candidate worktree is clean and the release has exactly39 selected changed paths (12052 added lines, mostly preserved accepted milestone artifacts). Sources/tests byte-match the reviewed workspace. Five account-bearing reports were redacted in the export; two other old reports had trailing blank-line formatting normalized only in the export. Original source branch, local reports and unrelated untracked development directories remain preserved. Reports exported for GitHub omit account-sensitive data; no credentials/runtime logs/research data included. Final local report records the verified release identity after push; committed release report is a redacted pre-push snapshot and does not claim an operation before it happens.

## 16. Change Manifest

Created this task:

- `src/polymaker/rdp_sm_entry.py`.
- `tests/test_rdp_sm_entry.py`.
- `docs/development/RDP_SM_MICROLIVE_READINESS_V2.md`.
- Reviewer-owned `docs/development/RDP_SM_MICROLIVE_ENTRY_INDEPENDENT_REVIEW.md` when completed.

Modified existing accepted SM sources:

- `src/polymaker/cli.py`: additive dedicated entry (prior M1 entry preserved).
- `src/polymaker/rdp_sm_loop.py`: dedicated production composition/default offline preservation, exact discovery binding, immediate production startup rejection.
- `src/polymaker/rdp_sm.py`: reusable SM market-read hook and optional production account-risk contribution to existing collateral reserve.
- `src/polymaker/rdp_sm_execution.py`: strict production market reads, policy evidence handoff/reserve preservation and four small Journal field additions.

Frozen quote strategy code is unchanged. Accepted safety checks, signing core, Gateway, Tracker, FillTruth, config and shared Journal unchanged. **SHARED CORE CHANGES = NO** (CLI adapter addition is the requested entry). Existing reports/tests preserved; unrelated ITER development folders excluded from release.

## 17. Next Action

Ready for the first human-initiated single-market MicroLive using `uv run polymaker rdp-sm-run`. Readiness YES, Independent Review PASS and GitHub PUSHED. Worker authorization remains NO; zero real orders/cancels were executed. At operator launch, fresh official market/account reads and all M2 gates must pass again.

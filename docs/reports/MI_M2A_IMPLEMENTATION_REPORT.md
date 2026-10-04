# MI M2A Live Wiring / NO-SEND Implementation Report

Status: FINAL NO-SEND MARKET OBSERVED — READY FOR GIT CHECKPOINT; no MI execution authority
Commit / working tree base: `5befd9ee6eb27293bb6f6d885fbe458ea44cabe4` on `m4-dynamic-ev`; this M2A tree is uncommitted.
Reviewed predecessor baseline: MI M1 checkpoint `5befd9ee6eb27293bb6f6d885fbe458ea44cabe4` (M1 CLOSED / PASS).
Architecture reference: `docs/architecture/MI_ARCHITECTURE.md`, M2A NO-SEND supplement, plus the explicit user M2A scope.

## Scope implemented

- One exact BTC5m market per `inventory-live --once --research --no-send` invocation. Official Gamma supplies market identity; Up/Down labels, token distinctness and end time are checked before observation.
- Existing official market WS service supplies both token books. Its connection-generation snapshot readiness rule is shared with Engine without changing the rule. The adapter supplies current best bid/ask, actual tick, SDK-derived quantity step, venue minimum, cash, flat MI-session inventory and elapsed time to frozen M1 `evaluate()`.
- An official account-only adapter uses CLOB GET authentication, paginated open-order GET, collateral balance/allowance GET and official Data API positions GET. Target-market non-flat positions and open orders fail closed. Other-market positions are never adopted as MI inventory.
- Each evaluation writes a normalized `INVENTORY_DECISION` row under `livecfg/logs/runs/<session>/inventory_decisions.jsonl`; `run.json` records identity, counts, completion and zero execution/cancel/fill counters. Desired PLACE/CANCEL actions are recorded as `SUPPRESSED_NO_SEND`.
- The runner receives no execution gateway, has no order submit/cancel method, and does not instantiate Engine or user-stream/trading state. Required CLI flags prevent an accidental send-enabled mode; no such mode exists.

## Files added

- `src/polymaker/mi_live.py`
- `src/polymaker/mi_readonly_account.py`
- `tests/test_mi_live.py`
- `docs/reports/MI_M2A_IMPLEMENTATION_REPORT.md`

## Files modified

- `src/polymaker/cli.py`: independent, NO-SEND-only MI command.
- `src/polymaker/marketdata/service.py` and `src/polymaker/engine.py`: move the existing generic two-book connection/snapshot readiness predicate into the market data service; Engine delegates to the same predicate.
- `src/polymaker/btc5m.py`: optional cache bypass and strict metadata requirement so MI discovery avoids local catalog mutation and rejects a Gamma fallback for venue tick/minimum; other callers retain defaults.
- `src/polymaker/config.py`: address checksum validation uses `eth_utils` rather than importing Web3 during MI configuration loading; no config parameter changed.
- `docs/architecture/MI_ARCHITECTURE.md`: explicitly record the user-authorized M2A NO-SEND supplement to the older reconstructed draft.
- `docs/CURRENT_STATUS.md`: M2A handoff status.

## Important implementation decisions

- SDK `ROUNDING_CONFIG` for the official tick determines the size floor step. An unknown tick fails closed; no strategy precision is invented.
- `cash_available_for_new_order` is the smaller of official collateral balance and required exchange allowance, both converted from 6-decimal units. This conservatively caps desired BUY decisions and is never increased by anticipated Merge, rebate or settlement.
- Startup and every actual evaluation repeat official account clean-state checks. Account read failure or new target-market position/order stops the observation; there is no adoption or cancellation.
- Every actual evaluate input/output is persisted together. Events are driven by top-of-book/connection changes and the one-shot T−15 cutoff, without a periodic decision timer. The final 15 seconds produce one cutoff decision before passive wait to market end.
- M4A research schema has FavoriteFirst-specific FIRST/PAIR fields, so MI uses an MI-specific normalized JSONL event in the existing run-artifact directory instead of falsifying FavoriteFirst roles.

## Deviations from architecture

- The older reconstructed MI architecture planned trial risk, Merge and settlement work under broader M2. The user-authorized M2A supplement narrows this iteration to NO-SEND observation. No broader M2 feature is implemented.
- The first observed session used the discovery helper's then-default local catalog cache and recorded repeated invalid-book WAIT evaluations in the final 15 seconds. Subsequent source revision bypasses cache for M2A and records one cutoff evaluation; strategy decisions and NO-SEND behavior are unchanged.

## Tests added

- Official Up/Down identity, target-market flat/position/order startup gates, other-market isolation, valid/invalid books, connection-generation invalidation, venue tick/minimum/size step, cash mapping, desired YES/NO/WAIT/data-invalid/T−15/CANCEL event records, CLI required flags, official host restriction and gateway write-call spies.

## Tests executed and results

- `uv run pytest tests/test_maker_inventory.py -q`: **57 passed**.
- `uv run pytest tests/test_mi_live.py tests/test_marketdata.py tests/test_engine.py tests/test_btc5m.py tests/test_config_aliases.py -q`: **66 passed**.
- `uv run pytest -q`: **763 passed, 2 skipped**.
- Ruff on all files changed by M2A: passed.
- Mypy on the two MI integration modules: passed. Repository-wide mypy still reports 38 errors in 10 pre-existing modules, including FavoriteFirst, state, gateway and settlement; none points to the two new MI modules. Repository-wide Ruff also includes many errors from existing untracked `research_outputs/` and unrelated historical files; targeted M2A Ruff passes.

## Real-market observation

Session `20261004T121039406404Z_mi_1791115800` observed `btc-updown-5m-1791115800` from 12:10:39 UTC through market end. Official Gamma identity: condition `0x89e96fd5fd121bb3df2bd797f5b4925ed22435166f42cd006eb6c3ecb1f1d3b1`; YES token `9137883886407970907209535677321803495252900796844318552793640799917506690528` = Up, NO token `33624288044206211181069182807896081564867469892973705930441122402969519602027` = Down. 156 evaluations; 114 had valid two-sided books. Event-interval estimate of valid-book duration: 130.75 seconds. Two WS disconnects reconnected; affected inputs were marked invalid. Later one-sided books were also invalid.

`p` range 0.015–0.885; target range −24.25 to +19.25; desired YES 56, NO 38, WAIT 62, CANCEL 0. Maximum desired quantity 24.25 shares, peak theoretical cash requirement $23.765, observed available cash $36.064169. Zero candidate potential-inventory violations, zero order submissions, zero cancel calls and zero MI fills. An independent recomputation of all 114 valid persisted rows found zero `p`/target mismatches.

Five reconstructable examples (each has `qY=qN=N_actual=0` in the event):

| UTC | YES bid/ask | NO bid/ask | p | N_target = delta | Desired action |
|---|---|---|---:|---:|---|
| 12:10:39.972 | .71/.72 | .28/.29 | .715 | +10.75 | BUY YES 10.75 |
| 12:11:12.673 | .87/.88 | .12/.13 | .875 | +18.75 | BUY YES 18.75 |
| 12:11:34.471 | .70/.71 | .29/.30 | .705 | +10.25 | BUY YES 10.25 |
| 12:11:57.924 | .41/.42 | .58/.59 | .415 | −4.25 | WAIT |
| 12:12:22.960 | .30/.31 | .69/.70 | .305 | −9.75 | BUY NO 9.75 |

Artifact: `livecfg/logs/runs/20261004T121039406404Z_mi_1791115800/`. Generated artifact is intentionally outside Git.

Session `20261004T121559554933Z_mi_1791116100` then observed `btc-updown-5m-1791116100` from 12:15:59 UTC through market end. Official Gamma identity: condition `0x6c055008e1f2061089c94e71e8e7aa7aa7f3271b8d6d6f052c387f6d28df4e75`; YES token `53940662691734789977182805924129654196007494109025276794731904045432090611308` = Up, NO token `76822952782402501023453867156757596801533146193594060833142096039908861688844` = Down. 191 evaluations, 187 valid; event-interval estimated valid-book duration 198.95 seconds. One WS drop recovered. `p` range 0.015–0.795; target range −24.25 to +14.75. Desired YES 22, NO 145, WAIT 24, CANCEL 0. Maximum desired quantity 24.25 shares, peak theoretical cash requirement $23.765, observed available cash $36.064169. Zero formula mismatches across valid rows, zero candidate potential-inventory violations, zero submissions/cancels/MI fills. Exactly one decision was recorded at T−15: WAIT / DATA_INVALID because the book was invalid at that instant. No later evaluation or PLACE was recorded. Artifact: `livecfg/logs/runs/20261004T121559554933Z_mi_1791116100/`.

The second observation exercised the code with no-cache discovery and the one-shot cutoff evaluation. The final strict metadata and zero-valid-book fail-closed checks, plus explicit Up/Down labels in future event/run records, were added after that process started; they are covered by deterministic tests and did not alter the read/evaluate/record decision path used during the observation. Both historical artifacts record token IDs and the validated mapping is preserved in this report.

## Known limitations / unresolved issues

- This is observation, not evidence of maker fillability or profit. Desired orders are never sent. No MI Fill Truth, order ACK, partial fill, Merge or settlement state is active.
- The account reader uses the official Data API position snapshot as a *startup/cleanliness check* only. MI-session confirmed inventory remains zero in M2A.
- A `COMPLETED` report certifies that the observation loop ended and events were written, not that the official WS was valid for the whole market. Validity is recorded per decision.
- The older reconstructed architecture's future trial/merge/settlement slices remain unaccepted and outside this iteration.

## Safety / behavior impact

Existing live trading semantics changed: NO. Engine uses the same book-readiness predicate after extracting it to the market-data service; the full regression suite passes. MI has zero order authority. `src/polymaker/strategy/maker_inventory.py` and FavoriteFirst strategy code are untouched.

## Git diff summary

M2A source/test/docs only, with generic readiness and configuration dependency changes noted above. Pre-existing `livecfg/config.toml` and `research_outputs/` remain untouched and uncommitted. No commit, tag or push was created.

## Handoff to Reviewer

Independently inspect the account GET-only boundary, the extracted book-readiness rule, exact Up/Down identity gate, venue size metadata source, persisted event math, startup fail-closed checks, CLI flags and absence of any MI path to execution gateway writes. Compare the observed run artifact with its `run.json` summary. Do not infer send-enabled M2B approval from M2A observation.

## M2A.1 repair — time gate and NO_VALID_BOOK regression

The independent M2A Architect Review found one MAJOR: the runner captured decision time before awaiting the account snapshot, so account latency crossing T−15 could give M1 a stale elapsed time and produce a desired PLACE. It also identified a TEST GAP for the existing end-of-market `NO_VALID_BOOK` lifecycle. M2A Review verdict was FAIL; no M2A checkpoint or M2B authorization resulted.

The runner now obtains one `evaluation_now` after the account snapshot and clean-state check. That same value feeds `build_snapshot()`, `evaluate_and_record()` and the final-window lifecycle check. No M1 formula, sizing, cash, working-order or BUY-only behavior changed. A runner-level fake-clock test advances time from T−15−0.1s before the account read to T−15+0.2s after it; the persisted event timestamp is the latter, and M1 records WAIT rather than PLACE. A second runner-level test simulates invalid books from market start through end and verifies `INCOMPLETE`, `MIReadinessError: NO_VALID_BOOK`, zero valid evaluations, WAIT/DATA_INVALID events and zero execution/cancel calls. Neither test uses a network client or execution gateway.

Repair files: `src/polymaker/mi_live.py`, `tests/test_mi_live.py`, this canonical report and `docs/CURRENT_STATUS.md`. Discovery, venue metadata, account semantics, WS reconnect and M1 files are unchanged by M2A.1. No third real-market observation was run; the Architect Review requested that separately after repair Re-review. `livecfg/config.toml` and `research_outputs/` remain untouched.

Repair verification: `uv run pytest tests/test_mi_live.py -q` — 19 passed; `uv run pytest tests/test_maker_inventory.py -q` — 57 passed; related MI/marketdata/engine/BTC5m/config tests — 68 passed; `uv run pytest -q` — 765 passed, 2 skipped; Ruff on the two touched code/test files — passed; Mypy on `src/polymaker/mi_live.py` — passed. `git diff 5befd9e -- src/polymaker/strategy/maker_inventory.py tests/test_maker_inventory.py` remains empty.

M2A.1 status: IMPLEMENTED — AWAITING INDEPENDENT RE-REVIEW. No commit, push, real-market run, order submission or cancellation occurred in this repair iteration. Next action is independent Re-review of the timing fix and the NO_VALID_BOOK test; M2B remains unauthorized.

## Final third NO-SEND market observation — 2026-10-04

M2A.1 independent Architect Re-review concluded PASS and authorized exactly one final NO-SEND market. The final code was run once via `uv run polymaker inventory-live --once --research --no-send`; no code, config or strategy changes were made for the run. The command exited 0 after the selected market ended. This observation is the final M2A live validation, not a send-enabled test.

Market `btc-updown-5m-1791118200`, condition `0x10bd597b3aa9a301d05e95bf5e75502cca347090628196f99e603cfbf4e5fedf`, ran from 2026-10-04 12:50:00 to 12:55:00 UTC. YES token `79859292475081012986331743866827168481097818448844909121479916236250909873862` = Up; NO token `60749902803390728020690130378170470287924047450518565982180454902811799455938` = Down. A read-only post-run Gamma lookup matched exact slug, condition, both token IDs and Up/Down outcomes. MI discovery passed the code's `cache=False, strict_metadata=True` path; no local catalog fallback is reachable. The event stream recorded live WS tick `0.01`, quantity step `0.01`, venue minimum `5.0`. A post-close Gamma lookup reported tick `0.001`; this is a later metadata observation, so it does not establish the discovery-time tick. The run artifact records the WS tick used for each decision.

Artifact: `livecfg/logs/runs/20261004T125000999631Z_mi_1791118200/` (generated, outside Git). `run.json` status `COMPLETED`, error `null`; `inventory_decisions.jsonl` has 265 events, of which 262 have valid two-sided books and 3 are invalid one-sided books. Actions: desired BUY YES 59, BUY NO 104, WAIT 102, CANCEL 0. The official market WS logged one subscription with two tokens; no disconnect, reconnect, duplicate subscription or queue overflow was observed. Session creation was approximately 12:50:01 UTC; first/last decision elapsed were 2.024s/285.403s. The run completed after market end.

Across all 262 valid events, independent Decimal recomputation found zero mismatches for `midY`, `midN`, `p`, `N_target`, `N_actual` and `delta`; independent action, best-bid price, desired quantity, cash-cover, identity and potential-inventory checks also found zero mismatches. `p` ranged 0.015–0.775, `N_target` ranged −24.25–+13.75, `qY=qN=0` throughout. Observed available cash was $36.064169; maximum desired quantity was 24.25 shares and peak theoretical cash requirement was $23.7650. Every event has `execution_status=NO_SEND` and `execution=SUPPRESSED_NO_SEND`.

Five reconstructable examples (all have `N_actual=0`, so `delta=N_target`):

| UTC | YES bid/ask | NO bid/ask | p | N_target / delta | Desired decision |
|---|---|---|---:|---:|---|
| 12:50:33.379 | .73/.74 | .26/.27 | .735 | +11.75 | BUY YES 11.75 @ .73 |
| 12:50:27.673 | .71/.72 | .28/.29 | .715 | +10.75 | BUY YES 10.75 @ .71 |
| 12:54:06.883 | .04/.05 | .95/.96 | .045 | −22.75 | BUY NO 22.75 @ .95 |
| 12:53:26.203 | .18/.19 | .81/.82 | .185 | −15.75 | BUY NO 15.75 @ .81 |
| 12:51:52.930 | .57/.58 | .42/.43 | .575 | +3.75 | WAIT / BELOW_ADJUSTMENT_THRESHOLD |

The final pre-cutoff event was WAIT/DATA_INVALID at elapsed 256.593s; the first and only event at or after T−15 was WAIT/DATA_INVALID at elapsed 285.403s, with an incomplete one-sided book. Desired PLACE after cutoff: zero. The M2A.1 deterministic runner test independently covers the valid-book account-read crossing case at elapsed 285.2s. No natural WS disconnect occurred in this market; earlier M2A observations and code tests cover reconnect behavior.

NO-SEND evidence: MI runner contains no execution gateway or order mutator, the CLI requires `--no-send`, the run emitted no order ID, submit ACK, cancel ACK or MI fill, and the run report records zero execution/cancel/fill calls. These counters are fixed by the NO-SEND architecture rather than independently instrumented gateway calls. The source path and observed process logs were checked in addition to `run.json`. Existing `livecfg/config.toml` and `research_outputs/` were preserved; only this generated run directory was created.

Final observation result: no material wiring or safety finding. M2A is ready for a complete reviewed Git checkpoint. M2B and any real MI order/cancel remain unauthorized until a separate milestone authorizes them. No commit, tag or push was made during this observation.

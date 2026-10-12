# RDP-SM M0 — Execution Feasibility

## 1. Baseline

- Date: 2026-10-11 (Asia/Shanghai).
- Working directory: `/home/ubuntu/poly-maker`.
- Branch: `fl-restored-97ab0f9`.
- HEAD: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`.
- Preflight working tree: clean (`git status --short` produced no output).
- Frozen strategy: RDP-SM SOP v1.8, frozen by the human Architect on 2026-10-11, as specified in the task. No trading rules were changed or inferred.
- Conventions: Python >=3.12; pytest + pytest-asyncio (`asyncio_mode = "auto"`); Ruff. Existing `.venv` contains the V2 SDK and test dependencies. No installation was performed.
- No applicable `AGENTS.md` or repository-specific Reviewer definition was found in this workspace/ancestor directories. A separate review agent was run under the user's explicit review instruction; its result is recorded in section 10.

## 2. Objective

Can the existing Gateway, Tracker/StateStore and FillTruth safely support two non-atomic Maker submissions, partial placement failure, cancellation and confirmed-fill reconciliation without shared production changes?

Scope: inspect only current FL-restored source, test the actual production components against a fake external SDK, and demonstrate the minimum Thin SM safety composition. The test-only `Probe` models attempt accounting and the close predicate; it is neither a strategy nor a runnable session service.

Exclusions: live orders, authenticated network requests, external HTTP/WS, RPC, market data, previous RDP development branches, full RDP runtime, NORMAL/REBALANCE, CapitalGate, MERGE/REDEEM, FL/Engine refactoring and shared production edits. No prior RDP implementation was accessed. M1 has not been started.

## 3. Existing Component Audit

Line references below refer to unchanged baseline production files.

| Component | Actual Interface | Current Capability | Evidence | Reuse Decision |
|---|---|---|---|---|
| ExecutionGateway placement | `await place(quotes, meta, *, submit_guard=None, force_post_only=False, strict_placement=False) -> list[OpenOrder]` | Real V2 OrderArgs/opts/GTC; forced post-only independent of config; guard before signing and immediately before POST. Strict mode propagates `PlacementUnknown`. | `src/polymaker/execution/gateway.py:172`, `:207`, `:248`; S1–S3 and final guard test | Reuse with one quote per call, both strict flags enabled; future orchestrator serializes attempts. |
| Placement parsing | `_parse_place_response(resp, quotes)` | Dict or singleton-list dict only, exactly one input quote; canonical nonblank string `orderID`; conflicting aliases or unusable shape => UNKNOWN. Literal `success is False` => empty list. | `gateway.py:263`; existing `tests/test_execution.py:75` onward; S2/S3 | Reuse. Do not submit a two-quote batch. `[]` alone does not distinguish rejection from local abort/signing failure; classify NO_ACK conservatively. |
| Cancellation | `cancel(ids) -> bool`; `cancel_market_asset(market, asset_id) -> bool` | Calls SDK cancellation; no exception => True; exception => False. SDK response body is discarded, including `not_canceled`. Scoped primitive sends real `OrderMarketCancelParams`. | `gateway.py:305`, `:347`; S3, S4 and cancel-reply tests | Reuse only as a request/transport result. Mandatory subsequent official state reads. |
| Official reads | `get_order(id) -> dict`; `get_trades(*, market=None, asset_id=None) -> list[dict]`; `open_orders() -> list[OpenOrder]` | Order read checks identity and normalized cumulative fields; malformed/failed snapshots raise `GatewayReadError`. Trades use V2 `TradeParams`; open orders map original minus matched to remaining size. | `gateway.py:595`, `:711`, `:733`; S1/S4, read-failure tests and baseline read tests | Reuse. No placement-request correlation/unknown-attempt lookup interface is exposed. Empty open-orders is not historical closure proof. |
| Tracker ownership/pending capture | `UserEventProcessor.arm_pending(token, side)`, `register_order(...)`, `owned_record(id)`, `on_trade(...)`, `on_order(...)` | Exact owned maker IDs; bounded pre-ACK trade buffer; persisted registry; late fills remain attributable after order removal; economics mismatches/overflow quarantine. | `src/polymaker/state/tracker.py:87`, `:171`, `:193`, `:328`; S3/S4 and baseline FillTruth tests | Reuse. Check ID uniqueness before registration: StateStore uses INSERT OR REPLACE, so conflicts are not automatically rejected. Pending capture cannot resolve an identity-free POST. |
| Tracker fill lifecycle | `fill_records`, `applied_fill_keys`, `economic_fills_for_orders(ids)` | Key `(trade_id, maker_order_id)`; replay dedupe; MATCHED is optimistic; lifecycle status progresses through MINED/CONFIRMED; FAILED quarantines. REST can recover missed confirmed fills. Status-only records can exist before MATCHED without an applied fill key. | `tracker.py:110`, `:137`, `:254`, `:269`, `:297`; S4/S5 and status-before-MATCHED regressions | Reuse records and filter exact CONFIRMED applied states for inventory. For pending safety inspect **all** owned fill_records, including unapplied/quarantined keys; economic_fills alone is insufficient. |
| StateStore | `register_owned_order`, `upsert_order`, `position`, `apply_fill`, `mark_owned_order_unresolved`, `owned_orders_for_recovery` | SQLite ownership/fill dedupe; order maps; persistent unresolved-ID registry; optimistic position updates at MATCHED; in-flight guards may expire. | `src/polymaker/state/store.py:78`, `:95`, `:135`, `:192`, `:205`, `:225`; S1/S4/S5 and `tests/test_state.py` | Reuse ownership and dedupe. `position()` is FL optimistic inventory, not SM Confirmed Inventory. No persisted identity-free attempt or confirmed lifecycle journal. |
| FillTruth | `reconcile_owned_order_once(...)`; `reconcile_owned_order(..., retry_delays=..., deadline=None, stopping=None)` | Official order + market-filtered trades; exact maker attribution, cumulative checksum, associate-trade coverage, economics validation; bounded retries only for proven order lag; failed/contradictory/malformed evidence remains unresolved. | `src/polymaker/execution/filltruth.py:69`, `:113`, `:143`, `:169`, `:211`; S4/S5, gap/FAILED/read tests and baseline suite | Reuse checksum/recovery outcome. COMPLETE does **not** verify terminal status or require CONFIRMED; it can clear the registry's unresolved bit for a LIVE order or a MATCHED trade. |
| Official parsers | `normalize_trade(msg, our_address, other_token)`, `normalize_order(msg)` | Trade statuses remain distinct; normalized maker economics; explicit canceled statuses; original/matched/remaining quantities. Order status itself need not be recognized for successful parsing. | `src/polymaker/userstream/parse.py:17`, `:126`, `:171`, `:200`, `:221`; S4/S5 and unknown-status test | Reuse; terminal status must be separately allowlisted by the caller. |
| Existing unknown cleanup | `_unknown_cleanup(gw, market, token, exc) -> bool` | Scoped cancellation + open-order check, always returns False; never converts an unidentified attempt into success. | `src/polymaker/filltest.py:497`; S3 calls this actual helper | Safety precedent/directly exercised; no automatic resumption proof. |
| Existing FL finalization | `Engine._favorite_finalize_orders(cid, *, first)` | Waits for disappearance from refreshed open-order map, then FillTruth COMPLETE. | `src/polymaker/engine.py:711` | Inspected, not reused as SM close barrier: its completion semantics do not enforce confirmed-only inventory. FL unchanged. |

## 4. Proposed Minimal Composition

The future Thin SM Orchestrator sits above the existing Gateway and processor; it owns the current attempt ledger, unique order-ID set, unresolved latch and close decision. It adds no SDK, transport or execution service.

```text
Within one serialized session:
  record attempt BEFORE await; arm_pending(yes_token, BUY)
  Gateway.place([yes_quote], meta, force_post_only=True, strict_placement=True)
    -> real rate limiter -> real executor -> SDK.create_order/post_orders
    -> real placement parser -> ACK / NO_ACK / PlacementUnknown
  on ACK: reject duplicate registry identity; register_order(...); upsert_order(...)
  repeat the same single-quote path for no_quote within this window

Close/recovery:
  known IDs -> Gateway.cancel(ids)
  unknown attempt -> latch Fail-Closed; cancel_market_asset(market, token)
  known IDs -> reconcile_owned_order(id, real_gateway, real_processor)
               -> real Gateway.get_order/get_trades -> maker-leg validation/dedupe
            -> final Gateway.get_order(id) -> normalize_order -> latest terminal evidence
            -> Tracker.on_order(normalized_snapshot, condition_id)
  Confirmed Inventory delta = sum unique owned economic fills with status CONFIRMED
  pending/unresolved fills = every observed owned fill record that is not CONFIRMED,
                             not applied, or quarantined (including status-before-MATCHED)
  next-window submissions -> real Gateway.submit_guard(close predicate)
```

The test-only predicate at `tests/test_rdp_sm_m0_execution.py:243` requires accounted attempts, unique registered ACKs, every known ID explicitly terminal, FillTruth COMPLETE, no quarantine/unresolved ownership and no pending or unapplied observed fills. UNKNOWN/PENDING/identity conflict cannot pass it. Fresh verification first invalidates prior evidence; terminal evidence is fetched **after** FillTruth's reads and processed through Tracker's cumulative/economics checks. A later LIVE snapshot therefore overrides an earlier canceled observation and blocks. Gateway's second guard check blocks a safety change during signing. Because that check runs in the SDK executor thread (`gateway.py:224`), the predicate reads main-loop-cached ownership evidence and in-memory records, never SQLite. A positive guarded POST after verified closure is tested as well as blocked POSTs.

For these cancellation scenarios, explicit `CANCELED`/`CANCELLED` is terminal. The probe also conservatively recognizes full-size `MATCHED` with zero remainder; this branch is not exercised by the M0 scenarios and is not live-venue certification. Any other status blocks continuation. No trading or frozen-SOP rule is introduced.

The test projection starts from zero and covers only the two submitted BUY orders. A future nonzero opening inventory and durable session ledger are outside this M0 proof; do not seed Confirmed Inventory from optimistic StateStore positions without authoritative recovery.

## 5. Test Scenarios

Evidence paths in this table are `tests/test_rdp_sm_m0_execution.py`.

| Scenario | Expected | Observed | PASS/FAIL/BLOCKED | Evidence |
|---|---|---|---|---|
| 1. Both ACK | Distinct owned Maker BUYs; verified closure/reconciliation; no premature next window | Dict and singleton envelope ACKs produce two IDs/registry entries. Cancel ACK leaves ownership intact. Both final canceled snapshots + two confirmed fills permit close. | PASS | `:321`, `test_s1_both_acknowledged_and_confirmed_closure` |
| 2. First ACK / second reject | Preserve first commitment/fills; block replacement until close | Explicit SDK success=False yields NO_ACK, not UNKNOWN. First remains tracked; replacement guard blocks. REST recovers first's 3 confirmed shares before continuation eligibility. | PASS | `:339`, `test_s2_first_ack_second_explicit_rejection` |
| 3. First ACK / second unknown | No blind retry/new window; Fail-Closed; investigate official resolution | Three cases: post TimeoutError, success without ID, None. Fake venue retains a hidden order. Real unknown cleanup returns False. Even empty open-orders/trades and reconciled first order do not clear UNKNOWN; pending capture remains. | PASS (Fail-Closed; unresolved submission) | `:358`, `test_s3_unknown_submission_stays_fail_closed_after_scoped_cancel` (3 cases) |
| 4. Partial fill / cancel race | Count every confirmed fill once; cancel ACK not terminal; correct commitment | 2-share MATCHED reduces remainder to 8 ($1.60); another 3 arrives while real Gateway executor waits in SDK cancel, remainder becomes 5 ($1.00). REST recovers an additional missed 1 share. Duplicate REST/WS replay leaves exactly 3 fill keys and 6 confirmed shares. Both orders final canceled. | PASS | `:386`, `test_s4_partial_fill_cancel_race_reconciles_and_deduplicates` |
| 5. MATCHED, not confirmed | Pending explicit; no confirmed inventory/false SM COMPLETE; no next window | MATCHED/MINED/RETRYING remain pending after terminal canceled reads, despite raw FillTruth COMPLETE. Optimistic StateStore=2, SM confirmed delta=0. Guard blocks until official CONFIRMED, then confirmed delta=2. | PASS | `:431`, `test_s5_terminal_order_with_unconfirmed_trade_blocks_continuation` (3 cases) |
| 6. No fill / cancel | Unchanged confirmed inventory; verified barrier; eligibility established | Cancel ACK + LIVE snapshots + FillTruth COMPLETE still block. Explicit final canceled snapshots release barrier; positions/confirmed deltas stay zero. | PASS | `:453`, `test_s6_no_fill_cancel_and_verified_final_states` |

Additional safeguards: duplicate ACK identity preserves original ownership (`:472`); body-level cancellation failure and cancellation timeout cannot imply terminality (`:486`, 2 cases); order/trade read failures, missing associated trade, FAILED trade and unknown order status block (`:498`, 5 cases); final pre-POST guard recheck (`:528`); successful guarded next-window POST and tracked reclosure (`:540`); MINED/RETRYING/CONFIRMED observed before MATCHED cannot disappear behind an empty official snapshot (`:574`, 3 cases); a latest LIVE official snapshot cannot be overridden by earlier canceled evidence (`:594`). Total: 24 new cases.

## 6. Close Barrier Findings

**Placement ACK:** an ACK establishes a usable exchange ID, not atomic pairing or absence of fills. Register each ID before proceeding. An empty return is NO_ACK; in the explicit-rejection scenario the fake response proves rejection, but the public return value does not expose that reason. With strict mode, post exceptions/unusable identities propagate UNKNOWN instead of returning an empty list.

**Cancellation:** True only means the SDK method returned without an exception. Gateway ignores the cancellation body. Do not erase orders, zero commitment or open the next window based on that boolean. Order state must be fetched and checked independently. Tracker's remaining size is original minus cumulative matched; after verified terminality the resting commitment is zero, while matched but unconfirmed shares remain pending economic exposure.

**Confirmation:** Tracker's exact-key records preserve MATCHED/MINED/RETRYING/CONFIRMED. StateStore applies inventory at MATCHED. Only confirmed applied records contribute to SM Confirmed Inventory; all observed owned unresolved/unapplied records remain visible and prevent continuation. A CONFIRMED event before MATCHED is still unresolved economically until its exact key has been applied/recovered. The per-token in-flight counter/expiry is insufficient as a close decision because it is neither a per-trade confirmation ledger nor durable lifecycle evidence.

**Three separate statements:**

- ORDER_TERMINAL: official order snapshot establishes a recognized terminal state.
- TRADE_CONFIRMED: each relevant economic fill has exact CONFIRMED status and valid owned maker economics.
- SM RECONCILIATION_COMPLETE: all attempts accounted, all known orders terminal, official quantity/associate checks complete, no unknown/quarantine/pending fill. Raw `ReconciliationOutcome.COMPLETE` alone means a matched-quantity checksum is consistent.

**SAFE_TO_CONTINUE:** all seven task contract clauses hold, including an explicit no-unknown latch, confirmed-only inventory and an actual submit guard. The S1/S2/S4/S6 tests establish eligibility in this single-process fake-venue composition; S5 only establishes it after confirmation.

**FAIL_CLOSED_WITH_UNRESOLVED_STATE:** UNKNOWN submission, contradictory identity/economics, unrecognized order status, failed official reads or unresolved fills forbid a new window. S3 intentionally remains in this state even after cleanup and empty official snapshots. Its PASS is proof of safe refusal, **not** proof of successfully closed/reconciled unidentified orders.

**Unknown resolution limit:** open-orders can expose candidates, and trade history can expose maker IDs, but no inspected public interface correlates a missing POST ACK to a unique submission request/order. A fully filled/canceled hidden order may be absent from open-orders. Market/asset cancellation mitigates resting exposure but cannot prove identity, historical fills or completeness. No safe automatic resolution/resume is claimed. Human-led reconciliation is required to release such a session; automatic future-window continuation is not authorized by this evidence.

## 7. Required Production Changes

**NONE to shared production components for the demonstrated fail-closed composition.** Gateway, Tracker, StateStore, FillTruth, FL strategy/runtime and Engine are unchanged. Thin session attempt bookkeeping, uniqueness checks, terminal checks, CONFIRMED projection and submission gating are caller responsibilities demonstrated in test code, not a new production implementation.

Supported limitations and architecture boundaries:

| Finding | Existing-interface limitation | Minimal proposal / decision | Why caller composition is sufficient or insufficient | FL regression exposure |
|---|---|---|---|---|
| Raw COMPLETE and optimistic positions differ from SM closure/confirmed inventory | No combined terminal-plus-confirmed completion method | NONE shared: compose explicit status checks and filtered owned fill records | Actual records/read results expose the required distinctions | None from M0; changing MATCHED accounting or COMPLETE semantics globally would affect FL and is neither needed nor approved |
| Duplicate ID registration can overwrite ownership | StateStore INSERT OR REPLACE | NONE shared: precheck existing ID and fail closed on conflict | `owned_record` exposes original ownership; test preserves it | None from M0; a global registration behavior change would require FL regression review |
| Identity-free UNKNOWN cannot be automatically resolved | No request-to-order authoritative lookup/correlation | NONE for permanent Fail-Closed. If automatic recovery is required, human Architect must approve an authoritative correlation mechanism before design/implementation | Empty snapshots and scoped cancel cannot prove historical attribution; the safe composition refuses continuation | A Gateway request/identity protocol change would need explicit approval and FL regression tests; no speculative API capability is proposed as existing |
| Restart can lose attempt latch and trade statuses | Registry persists only known IDs; fill lifecycle records and probe ledger are in memory | M1 prerequisite: specify durable Thin SM session state and mandatory recovery before any resume | M0 proves only same-process behavior; current store alone cannot restore an identity-free unknown or confirmed lifecycle | Keep future session metadata scoped to SM; changing shared SQLite/accounting semantics needs human approval |

No unapproved shared change was implemented. If the Architect requires automatic unknown recovery or crash-safe production execution within M0, this evidence does not satisfy that expanded requirement.

## 8. Test Evidence

Created:

- `tests/test_rdp_sm_m0_execution.py`: real Gateway in `paper=False`, real executor/rate limiter, real V2 SDK argument types and parser path; real memory SQLite StateStore/UserEventProcessor/FillTruth; SDK boundary alone faked. `Probe` is test-only orchestration bookkeeping, not a fake production component.
- `tests/rdp_sm_m0_offline.py`: clears inherited settings before imports, retains only PATH/LANG, disables pytest plugin autoload, explicitly loads pytest-asyncio, uses an empty temporary working directory, denies socket connections/datagrams/DNS and protects workspace `.env`/`.aws` reads. Any denied network attempt fails the run even if tested code swallows its exception. No Gateway.connect, RPC, market-data or live runtime entrypoint is called by new tests.

Exact successful commands from repository root:

```bash
.venv/bin/python tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m0_execution.py -q
# 19 passed in 0.40s
# M0 offline guard: 0 network/DNS attempts; workspace credentials denied

.venv/bin/python tests/rdp_sm_m0_offline.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_gateway_positions_v2.py tests/test_favorite_first.py tests/test_favorite_first_review.py -q
# 230 passed in 4.74s (before the runner's final guard-output enhancement)

.venv/bin/python tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m0_execution.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_gateway_positions_v2.py tests/test_favorite_first.py tests/test_favorite_first_review.py -q
# 254 passed in 4.22s (Worker final run)
# M0 offline guard: 0 network/DNS attempts; workspace credentials denied

.venv/bin/ruff check tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m0_execution.py
# All checks passed!
```

Initial harness collection had one import error: an assumed `trade_events` symbol did not exist. Source inspection established the actual `normalize_trade(msg, our_address, other_token)` interface; the import/call were corrected before the successful runs. Initial Ruff import-order findings were also corrected. No failing production test was hidden, skipped or xfailed.

During review, a positive next-window path exposed the need to avoid SQLite in the worker-thread submit guard. The test-only guard now uses cached ownership evidence. The independent Reviewer proved two additional counterexamples: a status-before-MATCHED owned record could be missed by an economic-fills-only pending filter, and a terminal snapshot read before FillTruth could become stale relative to its subsequent order read. The filter now covers all owned records (three added regressions), and terminal verification now uses a final official order read after FillTruth (one added conflicting-status regression). No shared production changes were needed for these corrections.

Fake SDK limits: these tests prove current client-boundary semantics, real component composition and deterministic event ordering. They do not verify exchange cancellation finality/consistency, real signing/authentication, live pagination completeness, on-chain confirmation, venue latency or recovery from an actual lost placement. The SDK itself/signing is simulated; official response shapes are synthetic. No external source was consulted because the task forbids network access.

The fake SDK cancellation deliberately leaves orders LIVE on a success reply. The cancel race uses `threading.Event` to hold the SDK call inside the real Gateway executor while a normalized trade reaches the real processor on the asyncio thread; the test then releases cancellation. This is more than merely changing return values.

Final evidence totals: **254 executed / 254 passed / 0 failed**, comprising **24 M0 cases + 230 existing cases**. The separate Reviewer reran the exact combined command above: **254 passed in 4.19s**, zero network/DNS attempts; the same Ruff command passed. Test durations are observations, not timing requirements. Earlier 19-case output is retained above as development history, not the final M0 count.

## 9. Risks and Open Questions

| Classification | Supported issue | Evidence / consequence |
|---|---|---|
| PROVEN | UNKNOWN can coexist with a real unowned venue order and empty post-cleanup open-orders | S3 preserves UNKNOWN and prevents resubmission; automatic closure is not proven |
| PROVEN | FillTruth COMPLETE can occur for LIVE orders and unconfirmed trades | S5/S6; caller must distinguish checksum completion, terminality and confirmation |
| PROVEN | Status-before-MATCHED records can exist outside applied economic fills and survive a COMPLETE zero-size reconciliation | Independent Reviewer counterexample; three added regressions inspect all owned records and block until exact-key recovery |
| PROVEN | Final submit_guard executes in Gateway's worker thread | `gateway.py:224`; successful guarded next-window test verifies a DB-free predicate in both contexts |
| PROVEN | Separate official reads can give conflicting order status; an earlier terminal observation must not override a later LIVE snapshot | Independent Reviewer counterexample and final-read regression |
| PROVEN | Cancellation True can coexist with body-level `not_canceled` | Cancel-reply test; final state read is mandatory |
| PROVEN | Registry registration itself overwrites existing ID metadata | StateStore source; candidate precheck prevents it in duplicate-ACK test |
| PROVEN | Ownership and fill dedupe persist, but lifecycle records/unknown attempts do not | Store schema vs processor fields; existing state persistence tests do not provide a durable SM session |
| INFERRED | Serialized thin orchestration can reuse these interfaces without shared changes | Test-only composition exercises all six required cases; a production scheduler is not implemented |
| NOT_VERIFIED | Crash/restart, external coroutine cancellation while a POST thread continues, WS pre-ACK timing under a real venue, full-size MATCHED terminal branch and nonzero opening inventory | Beyond required six-case M0 evidence; mandate fail-closed recovery design before M1 resumes anything |
| NOT_VERIFIED | Actual venue read completeness/consistency, supported terminal statuses and live CONFIRMED authority | No network/RPC permitted; Fake SDK evidence cannot certify venue behavior |

## 10. Final Decision

FEASIBILITY_PASS

All six required scenarios have executable evidence using the real production Gateway/Tracker/StateStore/FillTruth without shared production edits. The minimum caller composition distinguishes order terminality, confirmation and reconciliation completion, and the actual Gateway guard prevents next-window POSTs while unresolved.

This decision includes permanent Fail-Closed for unidentified submissions. It does not claim automatic unknown resolution, crash-safe sessions, an implemented SM runtime or live-trading readiness. Such claims are unsupported by this M0.

### Independent review

**Reviewer conclusion: PASS.** Separate agent `/root/m0_reviewer` inspected current baseline source, the test harness and this report, independently executed the combined 254-test command plus Ruff, and verified branch/HEAD and Git changes. The Reviewer made no file changes. This is a separate review, not a Worker self-review.

| Required review check | Reviewer finding |
|---|---|
| Real Gateway/Tracker/FillTruth rather than fake replacements | PASS: production classes/functions, real executor/rate limiter/SQLite/parser/reconciliation; only SDK boundary and credential-source isolation are simulated |
| Partial placement failures and cancel/fill race | PASS: explicit rejection, three UNKNOWN replies, executor-held cancel while main-loop fill arrives, REST recovery and duplicate replay |
| UNKNOWN distinguished from rejection | PASS: strict exceptions/latch vs NO_ACK; unknown cleanup cannot permit retry/resume |
| MATCHED and CONFIRMED separated | PASS: confirmed-only projection; all observed unapplied lifecycle records also block |
| Cancel ACK treated as terminal proof | PASS: ACK alone never terminal; final official order read required |
| New window with unresolved state | PASS: real Gateway guarded POSTs are blocked; positive guarded POST works only after verified closure |
| Shared components unchanged | PASS: tracked diff empty; only three intended new files |
| Offline testing | PASS: independent run reports zero network/DNS attempts; workspace credentials denied |
| Report accurately describes evidence | PASS: includes same-process scope, UNKNOWN limitation, all corrections and unsupported live/restart claims |

Reviewer-discovered findings and dispositions:

1. **Owned status before MATCHED:** original test predicate inspected only applied economic fills. The Reviewer executed an offline counterexample showing MINED could remain in `fill_records` while pending appeared empty and the barrier passed; CONFIRMED before MATCHED had the same unapplied-key risk. Corrected to inspect all observed owned records and require applied, nonquarantined exact CONFIRMED keys. Three regressions now pass; Reviewer independently verified official recovery releases the barrier.
2. **Stale terminal observation:** original explicit order read preceded FillTruth's order read. The Reviewer scripted canceled then LIVE and proved the old predicate could accept stale terminality. Corrected to fetch terminal evidence after FillTruth and process the latest snapshot through Tracker. The conflicting-status regression passes; Reviewer independently verified blocking.
3. **Worker's separate guard-thread correction:** the original test guard queried SQLite, but Gateway's final check runs in its executor. Corrected to use main-loop-cached ownership evidence. Reviewer verified both the positive guarded POST and guard invalidation during signing.

All corrections were test-only; no shared production fix was needed. Reviewer PASS covers the documented same-process composition with permanent UNKNOWN Fail-Closed, not automatic unknown recovery, restart safety or live readiness.

## 11. Next Minimal Iteration

Before M1 Safe Session, the human Architect should approve a minimal session safety-state contract covering persistent attempt/UNKNOWN bookkeeping, confirmed-only inventory recovery and mandatory Fail-Closed on uncertain restart. Reuse the audited interfaces with no shared changes; do not add strategy behavior. M1 has not been started.

## 12. Change Manifest

Created (only these files):

1. `docs/development/RDP_SM_M0_EXECUTION_FEASIBILITY.md`
2. `tests/rdp_sm_m0_offline.py`
3. `tests/test_rdp_sm_m0_execution.py`

Modified baseline tracked files: NONE. Frozen SOP and shared production: unchanged. No commit or branch change was made.

Final `git status --short --untracked-files=all` after review:

```text
?? docs/development/RDP_SM_M0_EXECUTION_FEASIBILITY.md
?? tests/rdp_sm_m0_offline.py
?? tests/test_rdp_sm_m0_execution.py
```

`git diff --name-only` is empty; `git diff --exit-code c9d52ad8efc5a49a7d36dd05958bf250cef802ce -- src pyproject.toml uv.lock` exits 0. Final branch/HEAD remain the required baseline.

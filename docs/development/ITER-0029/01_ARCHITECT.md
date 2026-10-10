# ITER-0029 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

This contract selects exactly one delivery behavior: **DG-004 — Operating REBALANCE and Return to NORMAL**. It targets complete DG-004 acceptance, rather than another factual-admission helper. Approval applies only after the Orchestrator persists this original report and freezes it. It grants no real-money execution authority.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`; branch: `rdp-phase0-dev`.
- Current initialization HEAD: `2531e6af6db333fa3cb6d612c16a49160ae140cf`, reported by the Orchestrator as exactly matching origin and independently observed locally. Initialization changes are documents only.
- Previous archived iteration: ITER-0028; implementation `9f3ad272b7447219d18ae50b2fbb03b3095ebe4e`; archive/receipt HEAD `6b74bca9c6ccb600d4427ea936a0bd8e67cc2ac7`.
- Actual working tree has no tracked or staged changes. Pre-existing untracked `research_outputs/` remains preserved and excluded.
- Authorities inspected: `AGENTS.md`, `docs/strategy/RDP_PHASE0_FROZEN.md`, `docs/development/BASELINE.md`, `ITERATION_INDEX.md`, `DELIVERY_GAPS.md`, ITER-0029 initialization, and relevant ITER-0028 frozen Architect, final Worker fix, independent Reviewer, RESULT and archive receipt.
- ITER-0028 final Reviewer09 PASS closes DG-003. Its recorded full suite is 1703 passed / 2 skipped, with additional independent probes and zero network. These are predecessor evidence, not a substitute for testing this implementation.
- DG-001/003/006 are CLOSED. DG-002/004/005/007–012 remain OPEN. MICRO_LIVE_READY is NO. Real-money authorization and main merge authorization are NOT_GRANTED.

## Problem

The retained runtime can execute consecutive NORMAL cycles and produce a reconciled REBALANCE handoff, but cannot operate that repair or actually return to dual NORMAL quoting. A descriptive mode and an eligible candidate cannot repair inventory.

Implement one authorized retained-runtime operation which consumes a genuinely closed REBALANCE handoff, operates the Frozen deficient-side repair loop, closes remaining repair orders on recovery, and resumes the existing NORMAL continuation in the same invocation. Preserve the current NORMAL-only entry point and its established handoff behavior.

Observable start: the current retained attempt has a complete, released, reconciled close; cumulative confirmed inventory has `abs(Delta) >= 5`; current scoped official evidence supports a legal REBALANCE candidate; explicit execution authorization is true.

Observable successful end: repair fills are in the same authoritative inventory; remaining repair orders are actually cancelled and reconciled; current cumulative confirmed inventory remains NORMAL after that barrier; existing NORMAL composition submits a correctly skewed dual maker pair. The operation may subsequently continue through the existing NORMAL loop until its ordinary handoff or terminal result. A required unsafe exit instead performs scoped cancel/reconcile and returns an honest STOP/cleanup result.

## Evidence / Current Behavior

Actual implementation and tests were inspected before selecting the change:

- `src/polymaker/rdp_runtime.py`: `continue_normal` and `_run_normal_continuation` retain an authorized operation, repeatedly obtain fresh post-cycle admission, submit through the gateway, complete NORMAL lifecycle, and return `REBALANCE_HANDOFF`. Admission and completion use cumulative processor inventory and prior attempt close proofs. There is no operating repair loop.
- `post_cycle_next_action_admission` already constructs a factual REBALANCE candidate with current metadata, books, account evidence and capital gates. Its receipt explicitly has no execution authorization.
- `src/polymaker/strategy/rdp_quotes.py`: `build_rebalance_quote` already supplies deficient-side BUY, five-share clip, BestAsk minus current tick and post-only requirements. NORMAL and REBALANCE have distinct gate requirements.
- `src/polymaker/execution/rdp_placement.py`: retained ownership, pending capture, typed uncertain outcomes and close-proof history exist, but continuation arming, complete acknowledgement and close validation assume a two-leg NORMAL pair. Simply relaxing a length check would remove a safety invariant.
- `src/polymaker/risk/rdp_capital.py`: the projector already handles cumulative unique close proofs plus current attributable commitments and retains acquisition cost. Current-account backing currently rejects any run order in the official open-order view as a closed-order resurrection. Active repair monitoring must distinguish a proved current resting repair from a resurrected historical order.
- `src/polymaker/execution/gateway.py`: one-quote post-only placement, per-token tick checks, SDK signing, final guards and POST drain exist. The legacy single-response parser accepts an ID without strictly requiring boolean success, and returns empty on `success=False` before checking contradictory IDs. That parser cannot safely certify a new RDP repair attempt. The opted-in strict pair parser provides an existing pattern for authoritative acknowledgement versus observed unknown IDs.
- Existing tests cover cumulative NORMAL cycles, strict pair responses, late fills, cancellation drain, proof contradictions, selected-position backing and REBALANCE spending its reserve. Those components must remain intact.

Frozen §§23–27 require an operating loop, two repair exits, current CanSafelyContinue, and no duration/no-fill stop. §28 requires preserved repair economic evidence. This behavior can be implemented without a new strategy, ledger or execution service.

## Existing Components Reused

- The retained RDP input session, bootstrap, exact-market official Gamma/account reads, market WebSocket books and validated dynamic-tick source.
- Existing live authorization checks, positive operational budgets, absolute deadlines, event-loop/thread guard bridge, pending-before-POST capture and SDK-task drain.
- `build_rebalance_quote`, `build_normal_quotes`, existing mode-specific CapitalGate and NORMAL BookFilter.
- The placement owner, attempt history, user-event processor, registered-order inventory, local order store and existing scoped close/cancel/reconciliation barrier.
- Cumulative close-evidence projector and current official-account validator, including cash, allowance and account-wide BUY commitments.
- The established NORMAL submission, pre-fill handling, FillWindow, close coordinator and continuation loop after recovery.
- Existing immutable safe diagnostics and terminal cleanup machinery, including retained unknown/observed order evidence.

## Existing Components Modified

1. `src/polymaker/rdp_runtime.py`: add the integrated authorized REBALANCE-to-NORMAL composition and narrowly share existing intent/guard/submission/close mechanics. Add mode-specific repair monitoring rather than running NORMAL FillWindow for repairs.
2. `src/polymaker/execution/rdp_placement.py`: extend the retained attempt with explicit mode-specific shape/ownership validation so a proved one-leg REBALANCE attempt can be armed, resolved, scoped, closed and retained without weakening existing two-leg checks.
3. `src/polymaker/execution/gateway.py`: add a narrowly opted-in strict RDP single-result acknowledgement branch within the existing placement path. Preserve legacy behavior when this option is absent and preserve the existing strict-pair option.
4. `src/polymaker/risk/rdp_capital.py`: narrowly extend current backing validation to recognize exactly proved current active owned order evidence, while still rejecting reappearing closed IDs and contradictory account/local scope. Default closed-run admission behavior remains unchanged.
5. Relevant existing tests: `tests/test_rdp_placement.py`, `tests/test_execution.py`, `tests/test_rdp_capital.py`, and runtime tests only where needed for entry-point compatibility.

The Worker may choose names/private factoring locally. Changes outside these production files require return to Architect if necessary; do not expand scope silently.

## New Components Required

No new subsystem, ledger, gateway, external interface or parallel runtime path.

A small mode-specific repair operation/result within the existing runtime is necessary: there is currently no callable operation which owns the repair from closed handoff through live fills, close and real NORMAL resumption. A strict-single opt-in is necessary because legacy single acknowledgement semantics cannot establish RDP ownership safely. Explicit mode/cardinality on retained attempts is necessary to preserve the existing pair invariant while accepting a single repair. These are compatible extensions of current components, not speculative interfaces.

## Desired Behavior

1. **Admission and ownership.** Accept only explicit authorization and a current complete/released close with fresh cumulative inventory and current scoped evidence. A denied, concurrent, closed, stale or terminal invocation sends nothing. Reuse the retained operation exclusion and teardown discipline. Factual candidates never confer authority.
2. **Repair submission.** For positive Delta submit only NO; for negative Delta only YES. Every newly submitted repair is BUY five shares at the existing builder's highest legal non-crossing maker price, forced post-only. Carry only that submitted token's validated current tick into the existing gateway. Capture ownership before POST. Revalidate mode, quotes, metadata, actual time, live gates, capital, account backing and evidence generations before signing and immediately before POST.
3. **Strict acknowledgement.** The opted-in RDP single branch requires exactly one official positional batch result. Boolean true with a unique canonical `orderID` and no contradictory ID evidence establishes one attributable acknowledgement. Boolean false without IDs is a definitive rejection. Malformed/multiple results, missing/nonboolean success, success-false with an ID, conflicting IDs or other contradictions remain typed unknown. Preserve observed IDs separately; never register an ambiguous ID as acknowledged. Use the existing safety close path for uncertain exposure.
4. **Operating loop.** After placement, consume existing authoritative fill/order processing and fresh market/account evidence. Confirmed fills update the same cumulative inventory and cost. If the existing resting repair is still the highest legal maker price and safe, keep it resting while awaiting/polling existing inputs. No fill and elapsed repair duration alone neither stop nor cause repeated replacement.
5. **Reprice ordering.** A changed legal target/tick requires actual cancel and reconcile of current remaining quantity before a fresh replacement. Recompute inventory, deficiency, capital and current market evidence after the barrier. Replacements are new five-share clips, not unapproved resizing of an old fractional remainder. Do not post a replacement while any old remainder or uncertain cancel/fill evidence remains unresolved.
6. **Recovery priority.** Evaluate confirmed inventory recovery first. At `abs(Delta) < 5`, cancel remaining repair orders and reconcile. Recheck cumulative inventory after late/racing fills. Only a current released proof and still-NORMAL confirmed inventory permit the existing NORMAL continuation to place a dual pair. If the barrier leaves REBALANCE inventory, continue the repair loop after fresh admission. A reported NORMAL label alone is insufficient.
7. **Current safety.** CanSafelyContinue uses actual time, FreeCash/current commitments, legal maker tradability and reliable market/order/fill evidence. Check sufficient remaining approved operational budget for Order → Cancel → Reconcile before new risk and during repair monitoring. Reuse positive caller-supplied order/close budgets; introduce no numeric strategy cutoff. Current FreeCash excludes actual resting reservations. Do not credit hypothetical cancellations or add a protected NORMAL reserve in REBALANCE. Preserve the existing pure gate's ability to admit a repair at exact available cash; if subsequent current FreeCash cannot fund a minimum legal repair while still REBALANCE, the Frozen current capital predicate fails and requires scoped close/STOP. No additional pre-send headroom rule is authorized.
8. **Unsafe exit.** Failure of one of the four predicates yields the corresponding `TIME_EXHAUSTED`, `CAPITAL_INSUFFICIENT`, `MARKET_NOT_TRADABLE` or `STATE_UNRELIABLE` StopReason, retained separately from safe detailed diagnostics. Cancel/reconcile remaining attributable orders, preserve unresolved observed IDs, and prevent all subsequent new-risk sends. An incomplete close remains incomplete/blocked; no proof or inventory may be fabricated.

## Minimal Scope

Complete this one delivery transition and its necessary live-order safety checks. Provide an explicit retained-session REBALANCE entry point which can start from an initial-cycle or later NORMAL closed handoff and invokes the existing NORMAL continuation after proven recovery. Do not change the established default `continue_normal` handoff API or initial once-only API semantics.

Share current placement and close mechanics where practical. Do not implement a generic strategy engine, migrate NORMAL onto a new framework, add a CLI execution command, replace the close coordinator, or introduce additional shared stores. Tests must demonstrate real fake-SDK POST/cancel/read operations through the existing gateway, not just mocked admission or helper return values.

DG-004 closure requires all acceptance below. DG-002/005/007/009 and other gap closures are not automatically implied by this iteration; their complete register conditions still require explicit independent evidence. Essential repair-path capital, uncertainty, time and cleanup behavior cannot be deferred merely because another gap also references them.

## Invariants

- Frozen SOP, approved $30 budget and strategy semantics remain unchanged. `Delta = QY - QN`; only confirmed fills contribute authoritative acquisition quantities/cost.
- No inventory/cost reset across NORMAL → REBALANCE → NORMAL. No adoption of foreign selected inventory, orders or fills.
- Cumulative close-proof union is unique, nonoverlapping, current and scope-complete. Each proof matches its real attempt/order identities. Current processor/local evidence is checked; an old result cannot launder a new scope or late fill.
- NORMAL keeps existing skew, BookFilter, protected reserve, FillWindow and pair semantics. REBALANCE uses its own Frozen gates and never inherits NORMAL BookFilter/price gates or a three-second FillWindow as a repair stopping/repricing rule.
- New repair clip is exactly five shares. Minimum-order metadata incompatible with five shares is a tradability failure, not permission to resize or round up.
- No hard MaxRebalanceSec, no no-fill timeout/quota, and no loss/MatchedCost/price-concession stop.
- Remaining commitments are removed only after authoritative close evidence. Actual model commitment and physical cash/allowance backing remain distinct; account-wide commitments are subtracted once.
- UNKNOWN, partial unsafe acknowledgement, registration/pending anomalies, interruptions and timeout after POST retain ownership and use existing fail-closed drain/terminal cleanup. Authoritative single acknowledgement is COMPLETE only for an explicit valid REBALANCE attempt.
- No new external runtime dependency, live order/cancel during development, main merge, Frozen edit, or unrelated FL/IM change.

## Acceptance Criteria

Independent Reviewer must verify all eleven criteria through actual code/diff and deterministic integrated evidence:

1. A single authorized call consumes a genuine closed initial or later NORMAL REBALANCE handoff, operates repairs and makes an actual subsequent NORMAL dual-pair POST after proven recovery. Both starting ownership histories are covered. Default authorization denial and established APIs remain compatible.
2. Delta `+5` and `-5`, larger magnitudes and the strict `<5` recovery boundary choose exactly the deficient token, five shares, BestAsk-currentTick and post-only. No opposite-side repair or overlapping NORMAL quote exists.
3. Strict opted-in single acknowledgement distinguishes authoritative success/rejection from uncertainty, preserves observed versus acknowledged identities, and leaves legacy single and strict pair placement unchanged.
4. Partial confirmed fills, fractional quantity/cost, multiple repair attempts and later NORMAL fills use one cumulative inventory, confirmed acquisition cost and current commitment scope. A replay is not double counted; late prior evidence invalidates stale certification.
5. Changed ask/tick reprices only after actual cancel/reconcile; accepted repair fills racing with cancel are consumed before deficiency/recovery or replacement. No new POST occurs while close, SDK drain or state reliability is unresolved.
6. Recovery cancels a partial remainder, confirms the barrier and rechecks inventory before NORMAL. A race which changes the recovered inventory cannot bypass the recheck. The resumed NORMAL pair uses current cumulative skew and capital, not the initial 5/5 state.
7. Current model budget, physical cash, spender allowance and account-wide commitments are validated on repair admission, final guards and monitoring. A proved current resting repair is not falsely classified as historical resurrection. Closed-ID resurrection, foreign selected positions, contradictory active evidence, overlapping proofs and false cost resets fail closed.
8. All four CanSafelyContinue predicates use actual evidence; each permitted StopReason has an integrated scenario with scoped cleanup, no subsequent new risk and honest complete/incomplete output. Exact available-cash pure admission remains compatible; active reserved FreeCash is neither ignored nor hypothetically released.
9. Official-format dynamic tick, stale/crossed/unreliable book, malformed/freshness-failed account/order/fill evidence and incompatible minimum size fail closed at the relevant repair stages, including final pre-POST mutation.
10. Many unchanged no-fill wakeups with sufficient current safety evidence keep the same order resting; safe repricing and continuing repair have no elapsed-duration/attempt-count/no-fill stop. Only actual safety failure or confirmed recovery exits REBALANCE.
11. Safe structured evidence records entry, attempts, repricing, recovery/STOP, economic endpoints and current close/capital scope; mandatory relevant regression/full checks pass; protected files, external interfaces and unrelated strategies remain unchanged.

## Required Tests

- Add integrated fake-official-SDK scenarios in the existing placement tests using retained inputs, real owner, processor, store, cumulative projector, gateway and close barrier. Cover both deficiency signs, initial and later handoff, repeat repair, recovery and actual NORMAL send; a builder-only or forced receipt test is insufficient.
- Exercise CONFIRMED partials, replay, late fills, cancel/fill races, sign change after barrier, buffered capture anomaly, pending/unreliable fill evidence, uncertain POST, registration failure, cancellation interruption, held SDK POST/close drain and concurrent/unauthorized entry.
- Strict-single parser matrix: canonical success, clean rejection, missing/nonboolean success, false plus ID, conflicting aliases, wrong envelope cardinality, malformed result and observed-ID preservation. Run legacy single/pair gateway regression.
- Pure/current capital tests: model budget preserved across signs/multiple attempts; exact cash admission retained; active reservations/current order matching; closed resurrection; foreign commitment counted once; allowance shortage; false/missing/overlapping proof and late inventory mismatch.
- Tick/minimum/market/time tests: official WS tick provenance and changing target; pre-sign and pre-POST mutation; minimum above five; accepted five-share minimum; exact remaining-budget boundary, invalid/backward clock, market close and stale inputs. No introduced hard numeric rebalance horizon.
- Separate prolonged safe no-fill from natural market time exhaustion; demonstrate no repeated POST while target is unchanged and no no-fill-specific StopReason.
- Run narrow new tests, affected `test_rdp_placement.py`, `test_rdp_capital.py`, `test_execution.py`, `test_rdp_runtime.py`, then relevant lifecycle/account/market-data/user-stream/quote/accounting/BookFilter and favorite-live regression, then the practical full suite.
- Use the existing isolated runner, for example `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_placement.py tests/test_rdp_capital.py tests/test_execution.py tests/test_rdp_runtime.py`; full suite uses the same prefix and `-q` without a file restriction. Record exact commands/results/failure names. Tests must perform zero external/network trading operations.
- Before delivery inspect `git status --short`, `git ls-files --others --exclude-standard`, `git diff --stat` and `git diff --check`. Explicitly identify new files and preserve excluded research outputs. Independent Reviewer must inspect the actual tree and run appropriate additional probes.

## Observability Requirements

Retain safe structured receipts/events in the existing runtime evidence path with selected market/attempt identifiers, explicit mode, authorization denial or accepted operation, timestamps, current tick/quote, acknowledged and observed IDs, current inventory/cost/commitments, gate reason, proof scope and cleanup completeness. Do not log secrets or raw account/SDK payloads.

Record confirmed REBALANCE entry and post-close recovered NORMAL timestamps; pre/post `LockedPnL` and `WorstSettlementPnL` snapshots and their arithmetic differences. Preserve actual repair fill prices/sizes and contemporaneous BestBid/BestAsk/tick/posted target inputs for later PriceConcession reporting. No price-quality value controls execution. DG-009 still owns complete aggregate metric units, denominators and report assembly; missing observations remain explicitly missing, never fabricated zero.

Completion reports describe current cumulative evidence. Reporting is not an extra official read, owner release, execution grant or alternative state machine. A stale historical proof may not certify the latest processor state.

## Live Validation

This iteration uses deterministic fake official inputs only. No agent may send or cancel a real order.

After every mandatory gap is closed, final independent readiness PASS is archived and human live authorization is granted, supervised 1–3 BTC5m markets can verify whether the retained official path actually repairs either sign, maintains post-only highest maker placement, consumes partial fills, reprices through cancel/reconcile, and returns to a correctly skewed NORMAL pair. Preserve market/code/config identity, order and fill timelines, confirmed inventory/cost, mode transitions, current safety reasons and repair economics. Synthetic tests do not certify actual venue fills or economic performance.

## Explicit Non-Goals

- Reimplementing ITER-0025/0026/0027/0028 or changing their reviewed initial/NORMAL behavior.
- New CLI/live execution command, deployment, readiness declaration, final PR, real trading/cancellation or main merge.
- Full DG-005 terminal policy across every phase, complete DG-007 termination lifecycle, aggregate DG-009 metrics or DG-010 command integration.
- Strategy optimization, aggressive/taker orders, alternative clip sizes, hard repair duration, fill quota, PnL/cost stop or extra protected REBALANCE reserve.
- New gateway, reconciliation engine, inventory store, shared-data copy, generic framework, external runtime interface or unrelated FL/IM refactor.

## Risks

Mode-sensitive ownership is a necessary safety change: every arm, acknowledgement, registry, close and cumulative proof check must agree on explicit mode, not infer a single order is safe merely from length. Current account/order evidence may lag confirmed fill or cancellation; mismatch must fail closed rather than manufacture synchronized state. Physical and model commitments must not be double subtracted or released early.

Recovery and repricing can race with accepted fills, and POST cancellation can outlive the caller. Retained drain and close ordering are mandatory. Literal current FreeCash may become insufficient while a funded repair rests; preserve the Frozen current capital predicate without adding hypothetical release or changing existing exact-cash admission. Full integration is more substantial than a helper, but each extension is necessary for the single required delivery behavior.

If actual implementation needs different strategy interpretation, new external interfaces or changes beyond this necessary scope, return WORKER_BLOCKED with the exact conflict. Ordinary code defects remain Worker repair/re-review work.

## Deferred Findings

Other OPEN gaps remain the finite delivery register. Complete cross-mode DG-002 closure requires explicit independent review of all its conditions; passing this repair composition alone does not silently close it. Complete STOP/end-of-market lifecycle, reporting/metrics, command integration and final readiness remain later required iterations under their existing IDs.

PriceConcession aggregate reporting and its durable units/denominators are left with DG-009; this iteration retains the actual underlying market/price/fill observations and economic endpoints. No unrelated historical FL/IM behavior is changed. No optional future subsystem or speculative roadmap is introduced.

## Human Approval Required

No new human strategy or architecture decision is required for this compatible implementation of Frozen §§23–27. Existing explicit development continuation authorizes the serial Worker, independent Reviewer, ordinary repairs and archival after this contract is persisted. Strategy semantic changes and new external runtime interfaces remain outside this approval.

Real-money order/cancel actions and main merge still require separate human authorization. Reaching MICRO_LIVE_READY requires the remaining mandatory gaps and final independent readiness review; this contract does not declare readiness.

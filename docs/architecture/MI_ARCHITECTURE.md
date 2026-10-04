# MakerInventory (MI) Architecture

Status: RECONSTRUCTED DESIGN — AWAITING ARCHITECTURE ACCEPTANCE; no live authority
Revision: 2026-10-04 (Asia/Shanghai)
Git inspection base: `19c322a` on `m4-dynamic-ev`, with uncommitted MI M1 source, tests, report, and status update
Reviewed live predecessor: `m4a-a1-pass` (`97ab0f9`); `19c322a` is an archival checkpoint, not a Review PASS
Requirement source: user-attached `BTC5m_Maker_Inventory_SOP_v0.2.md` (2026-10-04). This document treats its strategy rules as requirements, not as repository instructions or proof of a working implementation.
Related evidence: `docs/reports/MI_M1_IMPLEMENTATION_REPORT.md`, `docs/research/P0_MAKER_ONLY_20261004.md`, `docs/CURRENT_STATUS.md`

## 1. Objective and authority

Build a BTC 5-minute Up/Down, BUY-only, GTC post-only strategy that tracks a probability-derived **net** YES/NO inventory, merges confirmed pairs, and carries residual inventory to official settlement. Produce a causal, reproducible 30-market maker replay before any live proposal. Keep strategy decisions pure; state, authenticated reads, orders, chain actions, and accounting belong to explicit adapters and ledgers.

This is an architecture reconstruction, not an implementation acceptance, research GO decision, or authorization for live orders. The attached SOP's risk-reduction rule has not been validated by the older C-M replay. M1 already exists as an unreviewed working-tree implementation; its code and tests are evidence of current behavior, not accepted architecture authority.

## 2. Scope and non-goals

**Fixed strategy contract:** YES = Up, NO = Down after metadata identity verification; starting trial cash $100; no borrowing or anticipated rebate; `p = ((bidY+askY)/2 + 1 - (bidN+askN)/2)/2`; `N_target = clamp(50(p-0.5), -25, 25)`; `N_actual = qY-qN`; place a new BUY only when `abs(N_target-N_actual) >= 5`, subject to actual venue minimum, quantity step, cash and risk; price at the chosen token's valid best bid; no new buys in final 15 seconds. A market has at most one MI adjustment-order lifecycle, including uncertain submit and pending cancel. No fixed 5-share order, no 25-share lifetime buy cap, and no profitability threshold for a pair.

**Out of scope:** changing FavoriteFirst or M4 research semantics; selling, taker fallback, position sizing research, BTC directional prediction, or automatic live enablement. The existing `settlement-sweep` is a separate operator path until a later MI-specific integration has been reviewed. Architecture slices below may prepare adapters or shadow paths; authenticated MI order placement, merge, redemption, and a continuous live trial each require a separate explicit authorization after their safety gates.

## 3. Repository assessment and conflicts

| Current artifact | Actual state | Architectural consequence |
|---|---|---|
| `src/polymaker/strategy/maker_inventory.py` | Untracked pure single-market controller, with 50 deterministic tests and no Engine import. It computes probability, target, confirmed net/pair/residual, cash/quantity/inventory caps, and order intent. | Retain as the M1 starting point; independently review against this design and repair before a checkpoint. |
| MI M1 risk behavior | Controller receives `cash_available_for_new_order`, not a trial ledger. It does **not** compute `W_market`, `W_trial`, known costs, merge proceeds, or latched trial stop. Its constrained flip is driven by cash/inventory/precision, not the full SOP risk rule. | Do not treat M1's `REDUCE_OLD_EXPOSURE` flag as proof that the v0.2 risk reduction requirement is met. Extend through an explicit risk input/result in a later slice. |
| `src/polymaker/engine.py` and `src/polymaker/cli.py` | Engine routes to FavoriteFirst or general quoting; CLI has no MI mode. `_maybe_merge` returns immediately. | Add a separately selected MI runner/adapter; preserve other strategies' paths and keep live MI disarmed by default. |
| `src/polymaker/execution/gateway.py`, `src/polymaker/state/{store,tracker}.py` | Gateway supports GTC with `force_post_only` and `strict_placement`; unknown POST outcomes are expressible. Store deduplicates fills and records owned orders, but position quantities and risk cash use floats. Cancel API success does not by itself prove no later fill. | Reuse gateway/fill-truth plumbing with MI-specific lifecycle and Decimal ledger. Unknown submits and cancel races remain reserved until authoritative reconciliation. |
| `src/polymaker/risk/manager.py` | Existing risk uses marked equity, daily PnL, and notional caps; it does not compute the SOP's two-settlement-outcome lower bound. | MI requires a separate trial risk calculation. Existing global safety gates may still veto orders; they cannot silently redefine MI risk. |
| `src/polymaker/merge.py`, `src/polymaker/settlement_sweep.py` | Merge transaction code and a separate fail-closed settlement sweep exist. Live automatic merge is disabled, and no MI cash-release ledger is wired. | Design explicit merge/redeem state, token reservation and confirmation before counting cash. Do not re-enable `_maybe_merge` as a shortcut. |
| `research_tools/p0_maker_only.py` and local `research_outputs/` | Earlier 30-market conditional visible-queue replay exists, with assumptions and unresolved review. | Freeze sample identity and compare v0.2 paths without overwriting prior outputs or claiming actual executable PnL. |

The dirty `livecfg/config.toml` signature-type change and local `research_outputs/` predate this design; neither is a MI architecture decision. The M1 report says “NONE” for deviations from a user-supplied M1 specification, yet no canonical MI architecture existed then. This document does not retroactively approve that claim.

## 4. Module boundaries and exact expected files

| Boundary | Responsibility | Expected files |
|---|---|---|
| Pure single-market policy | Probability, target, normal adjustment, order keep/cancel, quantity quantization; no I/O | Existing `src/polymaker/strategy/maker_inventory.py`, `tests/test_maker_inventory.py` (M1 review/repair) |
| Pure trial economics and admission | Decimal scenario PnL, cross-market lower bound, cash reservations, stop latch, full and reduce-only candidate sizing | New `src/polymaker/strategy/mi_risk.py`, `tests/test_mi_risk.py` |
| MI authoritative projection | Identity-checked market metadata, confirmed fill/position projection, owned order lifecycle, order and merge reservations, restart recovery | New `src/polymaker/mi/state.py`, `src/polymaker/mi/ledger.py`, `tests/test_mi_state.py`; minimal additive changes to `src/polymaker/state/store.py` and `src/polymaker/state/tracker.py` only where needed |
| Read-only integration | Causal book and user-event snapshots, explicit MI selection, no order authority | New `src/polymaker/mi/runner.py`; additive `src/polymaker/engine.py`, `src/polymaker/cli.py`, `src/polymaker/config.py`, and integration tests |
| Execution boundary, later authorization | Map one approved intent to one order, persistent submit/cancel correlation, strict unknown-outcome reconciliation, final pre-submit guard | Reuse `src/polymaker/execution/gateway.py` and `src/polymaker/execution/filltruth.py`; new MI adapter in `src/polymaker/mi/execution.py`, integration tests |
| Pair and settlement operations, later authorization | Per-market merge lock, token reserve, confirmation and cash release; official resolution and redemption verification | New `src/polymaker/mi/settlement.py`, tests; reuse `src/polymaker/merge.py` and `src/polymaker/settlement_sweep.py` through reviewed adapters, without changing their existing callers by default |
| Research replay and audit | Causal maker queue/cancel simulation, 30-market immutable cohort, full ledger and attribution | New `research_tools/mi_v02_replay.py`, `tests/test_mi_v02_replay.py`, `docs/research/MI_V02_REPLAY_20261004.md`; generated outputs under ignored `research_outputs/mi_v02/` |

The file map is the intended boundary, not permission to edit all files in one slice. If current gateway or chain APIs cannot satisfy a boundary without changing unrelated live behavior, stop that slice and revise this architecture.

## 5. Data and control flow

```text
market metadata + local-received YES/NO books + owned user/order events
       -> identity/freshness/reconciliation gates
       -> immutable MI market snapshot + durable trial ledger projection
       -> pure single-market policy and pure trial risk admission
       -> WAIT / KEEP / CANCEL / PLACE intent with reason and risk evidence
       -> (shadow recorder; only a separately authorized executor may mutate)
       -> strict gateway submit/cancel + fill truth + confirmed balances
       -> persisted order/fill/merge/settlement transitions -> next snapshot
```

Each event records exchange timestamp, local receipt timestamp, source, market and token IDs, sequence/correlation ID, and ledger revision. A decision uses only events received by its local decision time; a late event changes the next projection and never rewrites an earlier decision. Serialized per-market evaluation and a trial-wide atomic reservation/stop update prevent two markets from spending the same cash. External I/O occurs outside the pure functions; after any await, recheck generation, funds, stop and order lifecycle before submit.

**Snapshot:** verified market start/end and Up/Down identity, tradability, two valid books, actual tick and quantity metadata, confirmed `qY/qN`, spendable collateral, all MI owned submit/working/cancel-pending orders, pending merges/redemptions, cumulative cost `C`, confirmed merge income `M`, settled economic PnL, and latched trial stop. Never infer `qY/qN` from an unfilled order. Do not treat flat prices alone as a stale feed. Missing identity, book, order, balance or metadata authority blocks new orders and drives reconciliation/quarantine.

**Order lifecycle:** persist intent and reservation before POST; `SUBMIT_PENDING -> WORKING/PARTIAL -> CANCEL_REQUESTED/CANCEL_PENDING -> TERMINAL`, with `UNKNOWN` held as a reserved, reconciliation-required state. Only authoritative terminal evidence releases the order reservation. An ACK or cancel request alone does not. A partial fill updates confirmed inventory and cost exactly once while retaining remaining reservation. On restart, reconcile all possibly live MI orders and fills before another placement. If ownership or remaining quantity is ambiguous, cancel or quarantine through the reviewed execution path and wait; never layer a second MI order.

## 6. Economics, cash and risk contract

Use Decimal quantities and cash with explicit units and versioned fee/metadata assumptions. Let `C_m` be cumulative confirmed BUY cash spend plus separately known costs, `M_m` confirmed Merge proceeds, and `qY_m/qN_m` confirmed *currently held* unmerged quantities. Then `P_yes = M_m + qY_m - C_m`, `P_no = M_m + qN_m - C_m`, `W_m = min(P_yes,P_no)`. For an officially settled market use its known economic PnL even if cash redemption is pending. `W_trial = sum(known settled market PnL) + sum(W_m for unresolved markets)`. Sum per-market minima; do not select one hypothetical winner across unrelated markets. Pending Merge changes neither `M_m` nor spendable cash; reserve its token quantities against a second Merge. Confirmed Merge atomically reduces both token holdings, adds `M_m`, and adds spendable cash only after the collateral receipt is verified.

For a candidate BUY of size `x` at legal bid with worst known total cost `a(x)`, test every `x` from zero to full possible fill: updated net inventory, available collateral after other reservations, and `W_trial(x)`. With one BUY order and affine known costs, the risk minimum occurs at an interval endpoint or at a YES/NO-scenario crossover; evaluate those exact points or a mathematically equivalent bound. Unknown fees/costs cannot be silently set to zero for live admission. Do not count rebates. Quantize size downward to actual venue step, never upward to reach a minimum.

Normal admission requires all possible fills within `[-25,+25]`, covered cash, and no state at/below the `-$20` trial stop line after newly added risk. If a full flip fails, separately test `q_reduce = min(abs(N_actual),abs(delta))` on the opposite token, clipped to avoid crossing zero; a legal pure-reduction order must be at least venue/strategy minimum, funded, and must satisfy `W_trial(x) >= W_trial(0)` for every possible partial fill. A pure reduction is available even after stop has latched, but it never clears the latch. If a full order is quantized or cash clipped, its executable size is retested; do not call a direction-increasing clipped order “pure reduction.” Record each rejected full and reduction candidate and the limiting reason.

At `W_trial <= -$20`, latch trial STOP across restarts and future markets. Stop scheduling new markets and cancel orders that can increase directional risk. Existing markets target zero and may attempt only legal, funded, non-worsening maker reductions before T−15. Existing orders are kept only if their entire remaining-fill range still meets the current stop/risk rule; otherwise request cancel and preserve reserves through the race. The existing general `RiskManager` can impose stricter account-wide vetoes; any conflict that prevents a safe MI reduction must be recorded rather than bypassed.

The $100 trial allocation is a real reserved capital envelope, not a simulated balance copied into live execution. Available cash excludes all order/chain reservations and unconfirmed merge or redemption proceeds. Trial accounting and account-wide balance must reconcile; unexplained differences halt new buys. Runtime allocation across simultaneous markets is serialized by the trial ledger.

## 7. Timing, book, merge and failure behavior

- Validate both sides' bid/ask, order, tick, source freshness and market identity. Post-only crossing rejects are recorded; refresh and recompute, with no taker fallback. A best-bid move by at least one **current actual** tick requests cancel. A small target-size change alone does not resize a valid working order.
- On invalid/stale books, missing user-stream/fill truth, uncertain order status, balance mismatch or metadata conflict: block new buys, request appropriate cancellation, preserve potential inventory and cash reserve, and resume only after authoritative reconciliation. A failed cancel or unknown submit is not terminal evidence.
- At market end minus 15 seconds: request all MI cancellations and never submit a new BUY. Continue processing fills during cancellation and after market expiry. Expiry is not official settlement.
- Merge confirmed available `min(qY,qN)` without a pair-profit gate, subject to actual token step/dust and one in-flight Merge per market. Pre-submit balance verification, token reservation, transaction identity, confirmation, and collateral receipt are required. Any failure leaves the pair locked for retry/reconcile; it cannot credit cash. The currently disabled `_maybe_merge` remains disabled until a separate approved integration.
- Residual follows verified official resolution/redeem status; loser value zero and winner proceeds enter spendable cash only after actual receipt. The separate settlement sweep cannot be counted as an MI confirmation without reconciliation. The SOP's 60-second Merge and 15-minute redemption delays, and 500 ms submit/cancel delays, are replay assumptions only.
- Record total `Merge proceeds + residual settlement proceeds - BUY spend - other costs`. Average cost can split Pair/Residual for explanation, but that split must reconcile to total and cannot change it. Track unknown chain/venue cost as an explicit uncertainty, not hidden profit.

## 8. Milestone slices and gates

1. **MI M1 — pure policy review/repair.** Independently review the existing untracked controller/tests against this architecture. In particular, check full-flip clipping, stale/cancel precedence, precision, order-pending contract and the absence of risk claims. Update the same `docs/reports/MI_M1_IMPLEMENTATION_REPORT.md`, create `docs/reviews/MI_M1_REVIEW.md`, and checkpoint only after PASS and full untracked-file audit. No live wiring.
2. **MI M2 — pure trial risk and ledger.** Add scenario PnL, durable cost/reservation/stop semantics and property/boundary tests. Prove zero/partial/full fills, scenario crossovers, multiple markets, and risk-limited reduction; preserve M1 policy isolation. No authenticated actions.
3. **MI M3 — causal replay and evidence.** Freeze the existing 30-market cohort; replay SOP v0.2 with actual local event arrival order, conditional queue model, partial fills, cancel races, post-only rejects, metadata uncertainty and cash release assumptions. Compare against the frozen C-M output without overwriting it. Require independent methodology/code review. Result may be negative or inconclusive.
4. **MI M4 — read-only integration and restart recovery.** Verify token identity, fill truth, open orders, balance, metadata and event timestamps in an MI-scoped shadow runner. Persist all decisions and invariants; do not call order/merge/redeem mutators. Prove FavoriteFirst/general quoting unchanged.
5. **MI M5 — execution/settlement integration proposal.** Only after separate authorization, implement a disarmed adapter and dry-run end-to-end tests for unknown POST, cancel races, partial fills, merge/redeem confirmation and stop recovery. Another explicit review/authorization is required before any live trial or arming.

Each completed implementation slice needs a canonical Implementation Report, independent Review, verified complete checkpoint, and `docs/CURRENT_STATUS.md` update under `AGENTS.md`. No later slice begins before its predecessor's review/checkpoint. This architecture does not itself authorize a Worker to start M2–M5.

## 9. Acceptance criteria

- Pure M1 and risk functions emit only BUY/WAIT/KEEP/CANCEL intent and make no I/O; tests cover `p=0.65 -> +7.5`, 4.99/5 threshold, existing net inventory, two-way flip, step flooring and minimum size.
- Trial risk tests prove the two-outcome `W_m`, independently summed unresolved-market minima, known settled PnL, correct cash distinction, permanent stop, and full versus pure-reduction decisions over zero/partial/full fill ranges.
- State tests prove one order per market during submit/cancel uncertainty, idempotent fills, restart reconstruction, late fill during cancel, immutable historical decisions, and no cash credit before confirmed Merge/redeem.
- Integration tests prove invalid/stale book and identity mismatch fail closed, final-15-second cancellation, post-only GTC at best bid, current tick comparison, and no FavoriteFirst/general quoting regression. No test may rely on a paper gateway's immediate ACK as proof of real fill behavior.
- Replay retains all fixed 30 markets, including zero-fill/invalid-book/loss paths. Publish total, Pair, Residual PnL; worst market; target/actual inventory path; fill and reduction-fill rates; tracking error; full-flip versus reduction outcomes; reject/cancel/missing-data counts; stop path; capital occupancy/release; explicit queue and fee/metadata sensitivity. Reconcile per-market and total ledgers. Report GO / NO-GO / INCONCLUSIVE only with independent review and stated uncertainty; historical visible-queue fills are conditional.

## 10. Open issues requiring an explicit decision before execution

1. Verify the actual venue quantity step, minimum size, tick and fee snapshots for each market/time. Ten C-M sample markets lacked comparable historical metadata; results using assumptions must remain labeled conditional.
2. Specify the exact authoritative spendable-collateral and token-balance source, fee upper bound, and chain transaction costs for the account/wallet type before live admission. The existing signature-type config change has no durable rationale and is excluded.
3. Define a reviewed MI process mode and account ownership boundary so FavoriteFirst, general quoting, MI, manual orders and settlement sweep cannot mutate the same market inventory concurrently.
4. Confirm whether the available merge/redeem SDK and wallet path can meet one-in-flight, idempotent receipt verification without reactivating the disabled automatic Merge route. If not, keep MI in replay/shadow mode and revise the architecture.
5. Establish replay source completeness for queue position, cancel timing and complementary-token fills. If unobservable, report conditional bounds and INCONCLUSIVE when those bounds change the conclusion.

## 11. Handoff

This session reconstructed the missing MI architecture from the attached v0.2 SOP and inspected the current repository. It changed documentation only. No code/test, live configuration, order, chain operation, commit, tag or push was performed. The next authorized action from the current repository status is independent MI M1 review against this architecture, including untracked artifacts; any M1 repair should be documented in the existing report. M2 and later stages remain unstarted and unauthorized. Preserve `19c322a` as the MI archival base and `m4a-a1-pass` as the last reviewed live baseline; neither certifies the uncommitted MI implementation.

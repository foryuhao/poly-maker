# ITER-0015 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`, Frozen SOP and durable project state.
- ITER-0015 `01_ARCHITECT.md` and `02_WORKER.md`.
- Actual five-file source/test change and relevant existing queue, parser, order-book, Book Filter and retained-owner implementation.
- Current tests, including network-blocked independent probes.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `70c3c0f8f12cad28c82791c523985344dcf4bdf6`

The authorized shared-core change is limited to ephemeral processing receipt context. No filter formulas, execution code or configuration changed. Audit/index changes were distinguished from implementation. Historical `research_outputs/` remains preserved.

Reviewed SHA-256 hashes:

| File | SHA-256 |
|---|---|
| `src/polymaker/marketdata/service.py` | `d225102b063ee85e970da7fd34d6cbb2bca2579d70efe49fcef5ab69f51796eb` |
| `src/polymaker/rdp_marketdata.py` | `e4de180787698f846d05e84124d7f5be509307cc737c06bb108b62f866601744` |
| `src/polymaker/rdp_runtime.py` | `63ffdd12f707ec51ce322ba5275ec7d17105f8c3b8ae7da9794700d26572fc85` |
| `tests/test_rdp_marketdata.py` | `5caf3a37133e710263b5a72884774ab1db691ebe9fc1941b649c1458e279e679` |
| `tests/test_rdp_runtime.py` | `47efe9090c1974e049fcf7dbb457d23f320eb012107b67c5e8cf7d92b4ffb25d` |

## Acceptance Criteria Review

| Criteria | Review |
|---|---|
| AC1: authoritative callback wiring | Implemented using existing filter and callbacks. |
| AC2: socket receipt versus processing time | Source uses receipt context correctly; required regression is ineffective, MF-01. |
| AC3: ordering and aggregation | Existing per-message aggregate behavior preserved; distinct multi-message reversal evidence missing, MF-02. |
| AC4–5: selected trades and atomic rejection | Validation precedes shared mutation; selected/foreign separation and malformed-frame tests pass. |
| AC6: Frozen predicates through wiring | Warmup, unchanged snapshots, exact-five variation, Depth Extreme and Fast Move are covered. |
| AC7: tick/generation invalidation | Tick reset covered; warmed production-history generation reset remains uncovered, MF-02. |
| AC8–9: actual owner reliability | Session derives reliability internally; failure, expiry and closure tests pass. |
| AC10–11: compatibility and reuse | Existing APIs remain intact; no duplicate parser/history/controller or execution action found. |
| AC12: required tests | Current full suite passes with zero network attempts, but required coverage is incomplete. |

## Existing Codebase Compatibility

The shared service adds one context field, sets it during current-generation processing and clears it in the existing `finally`. Transport behavior, queue ordering, callbacks and research receipt fields remain unchanged.

RDP reuses the existing parser and Book Filter. No unnecessary subsystem replacement, FL strategy change or IM dependency was found. Full predecessor and research regressions pass.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — Delayed-processing regression does not distinguish receipt time from processing time.**

Location: `test_queue_uses_socket_receipt_not_delayed_trade_processing_time`.

The test puts the trade into the fake socket queue at clock1, then changes the clock to9 without allowing the socket reader to consume it. Its actual socket receipt is therefore9. Evaluation at11.2 also expires a trade timestamped at processing time9.

Independent reproduction of the fixture’s scheduling records:

```text
ACTUAL_TRADE_RECEIPTS=[9.0]
RECENT_TRADE_AT_9_1=True
RECENT_TRADE_AT_11_2=False
NETWORK_ATTEMPTS=0
```

The current test would pass if processing time were substituted for receipt time, defeating AC2.

Required repair: wait for a deterministic reader acknowledgment that the trade was actually received/enqueued at1 while processing remains blocked. Advance processing to9, release it and evaluate at9.1. Assert the recorded receipt is1 and activity is false. A processing-time timestamp of9 would then produce activity true and fail the regression.

No production change is currently indicated.

**MF-02 — Required production-wiring regression cases remain missing.**

Add the following focused cases using existing fixtures:

1. **Missing/invalid receipt context after valid warmup.** Begin with ready evidence, deliver a selected event with missing or invalid context, and assert unready/rejected evidence without substituting processing time. Independent probes show the current source behaves correctly for `None`, `True` and NaN; preserve that behavior in tests.
2. **Warmed-history generation reset.** Warm generation1 through actual queue consumption, advance to generation2, reject old-generation backlog and show that old history cannot certify generation2. New baselines must require the existing warmup. Current stale-generation test begins with empty histories and does not establish this reset.
3. **Multiple-message frame ordering.** Put separate reversing depth-update messages in one frame and assert cumulative variation12. Retain the existing contrasting test where two updates inside one `price_change` message expose only its final state and variation0.
4. **Complete retained-session evaluation path.** Exercise queued frames through the retained service and call `session.book_filter_result()`. The current runtime fixture manually seeds filter history, while queue fixtures evaluate the filter directly. A small composition of those existing fixtures closes the contract’s required frame→queue→filter→session validation.
5. **Both outcome trade callbacks.** Parameterize selected-trade activation for each side and preserve foreign-event isolation.

These are tests explicitly required by the approved contract. They require no new subsystem or strategy change.

### NOTE

Whole-frame validation correctly precedes mutation, including when a valid book appears before an invalid selected trade.

Independent missing-context probes produced:

```text
Before: ready=True
After None/True/NaN selected book receipt: ready=False
Reason: NOT_READY:INVALID_BOOK_EVIDENCE
```

## Frozen SOP Compliance

Filter formulas, thresholds, windows and approved aggregate-update semantics remain unchanged.

The implementation does not compose Book Filter evidence into NORMAL execution or apply it to REBALANCE.

## External Interface Review

No new runtime interface or dependency was introduced. Independent tests and probes blocked socket connections and DNS resolution, recording zero attempts.

## Trading Safety Review

No submission, signing, cancellation, inventory mutation or account action was added.

The returned result is Book Filter evidence, not execution authorization. No real-money trading occurred.

## Inventory / Capital Accounting Review

Inventory and capital code remain unchanged. Public trades only update filter activity and do not become confirmed inventory.

## State Machine Review

Current session evaluation checks retained owner health, pinned public generation, market expiry and closure. Unreliable evidence fails closed.

Book Filter rejection remains distinct from public-feed startup failure. No trading controller or additional state machine was introduced.

## Test Review

Independent current-tree full suite ran through `uv run python` with socket connections and DNS blocked, invoking:

```python
pytest.main(["-q"])
```

Result:

```text
1205 passed, 2 skipped in 15.70s
NETWORK_ATTEMPTS=0
```

Additional independent checks:

- Delayed-trade scheduling reproduction: completed, zero attempts.
- Missing/invalid receipt-context probes: completed, zero attempts.
- Scoped Ruff across all five changed source/test files: passed.
- `git diff --check`: passed.

Passing tests do not close MF-01/MF-02.

## Scope Review

Implementation remains within the approved contract. The remaining findings are ordinary test defects/gaps. No architecture revision or human decision is needed.

## Residual Risks

Book Filter evidence does not establish authoritative account/order state, execution readiness or strategy edge.

NORMAL admission composition, account refresh, lifecycle integration and complete metrics remain deferred. MICRO_LIVE_READY remains unestablished.

## Final Decision

RETURN_TO_WORKER

BLOCKER: 0  
MUST_FIX: 2

Repair the delayed-processing regression and close the specified production-wiring test gaps, then return for independent re-review.

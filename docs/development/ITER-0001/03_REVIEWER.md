# ITER-0001 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`
- `docs/strategy/RDP_PHASE0_FROZEN.md`, particularly sections 2–12 and 35
- `docs/development/BASELINE.md`
- `docs/development/ITERATION_INDEX.md`
- `docs/development/ITER-0000/RESULT.md`
- `docs/development/ITER-0001/01_ARCHITECT.md`
- `docs/development/ITER-0001/02_WORKER.md`
- Actual new accounting implementation and tests
- Relevant existing domain types, StateStore, UserEventProcessor, REST fill recovery, FL economics, and state/filltruth tests

The review inspected implementation and independently executed tests; the Worker report was not treated as proof.

## Repository / Diff Reviewed

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `29cdc603cebf305457f665b1aee2d0eb705406a3`
- Framework checkpoint `fd51429` is in current ancestry.
- Architect contract is committed at HEAD.
- Implementation files reviewed directly because they are untracked:
  - `src/polymaker/strategy/rdp_accounting.py`
  - `tests/test_rdp_accounting.py`
- Other iteration changes:
  - Modified `docs/development/ITERATION_INDEX.md`
  - Untracked `docs/development/ITER-0001/02_WORKER.md`
- Preserved unrelated untracked `research_outputs/` remains outside implementation scope.

Ordinary Git diff excludes the new source/test files; both were read in full. No existing source, test, configuration, dependency, or dataset changes appeared in the inspected Git state.

`git diff --check` passed.

Reviewer performed no source edits, Git mutations, authenticated network operations, or trading.

## Acceptance Criteria Review

| Criterion | Assessment |
|---|---|
| 1. Immutable snapshot with specified fields | Implemented using a frozen, slotted dataclass. |
| 2. Existing processor and explicit owned-order scope | Implemented; no additional ledger or ownership registry. |
| 3. Unknown, foreign-condition, foreign-token and SELL rejection | Implemented and tested. |
| 4. Nonconfirmed evidence and resting orders excluded | Implementation is correct; MATCHED/resting exclusion tested. Explicit MINED/RETRYING tests are missing. |
| 5. Confirmation and replay count once | Implemented and tested through the existing tracker. |
| 6. Distinct maker orders sharing a trade ID | Implemented and tested. |
| 7. Quarantine and invalid economics rejected | Implementation includes the required checks. Existing invalid-economics tests are intercepted by tracker quarantine and do not demonstrate the projection’s independent validation. |
| 8. Required formula cases | Incomplete: profitable balanced inventory and mirrored two-sided residual NO fixtures are absent. |
| 9. Negative locked economics | Implemented and tested without clamping. |
| 10. Explicit winner calculations | Implemented and tested for both outcomes and foreign-token rejection. |
| 11. Repeated reads have no state/callback effects | Source inspection supports this; tests verify snapshot equality, order equality and one callback only. Required authoritative-state assertions remain incomplete. |
| 12. Required regression suites | Independently passed, including the full suite. |
| 13. No unrelated/shared-core/interface changes | Satisfied in the reviewed tree. |

Acceptance is incomplete because required test evidence is missing.

## Existing Codebase Compatibility

The implementation correctly reuses:

- Existing market and outcome-token identities.
- Existing owned-order registry reads.
- Existing accepted, deduplicated maker-fill evidence.
- Existing quarantine and failed-fill information.
- Existing StateStore and REST recovery in integration tests.

The new component is an RDP-specific mathematical projection. It does not duplicate or replace the existing ownership registry, fill ledger, reconciliation subsystem, execution gateway, or order state machine.

Existing optimistic MATCHED positions remain unchanged. FL’s first-leg/pair-leg economics remain unchanged. No IM implementation or shared-core change was introduced.

No existing safety control was bypassed.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — Complete the contract-required accounting and lifecycle test cases.**

Locations: `tests/test_rdp_accounting.py:41`, `:58`, `:82`, and `:110`.

The contract explicitly requires profitable balanced inventory and residual YES plus mirrored residual NO formula cases. The current tests contain:

- A profitable residual YES fixture.
- One-sided YES/NO cases.
- A balanced negative-PnL fixture.

They do not contain a profitable balanced fixture or a two-sided residual NO fixture. A one-sided NO test does not exercise balanced NO cost attribution or winner reversal with nonzero inventory on both sides.

Additionally, only MATCHED exclusion is exercised directly. MINED and RETRYING exclusion remain untested despite acceptance criterion 4.

Required repair:

- Add a profitable balanced fixture with independent expected quantities, costs, balanced/residual economics, settlement bounds and both winner calculations.
- Mirror the required residual fixture so NO has five shares and YES has three; assert negative Delta, residual NO attribution, economic fields and both winner calculations.
- Exercise accepted MATCHED→MINED and MATCHED→RETRYING evidence through the real processor. Assert zero confirmed inventory/cost until CONFIRMED, then one contribution.

No strategy or architecture change is required.

**MF-02 — Exercise the projection’s independent invalid-economics checks.**

Location: `tests/test_rdp_accounting.py:203`.

Every current parameter is rejected by the tracker’s registered-order consistency checks:

- NaN quantity fails the tracker’s quantity-range comparison.
- Infinite price exceeds the submitted BUY price.
- Price `1.0` exceeds the fixture’s submitted price `0.9`.

The test therefore passes through scoped quarantine rejection. It does not demonstrate the new projection’s independent finite-number or binary-price validation, despite its comment implying admitted evidence.

Required repair:

- Add cases where the existing processor admits malformed evidence and the accounting projection rejects it.
- For example, a positive quantity with price `0.0` or a negative price passes the tracker’s current BUY price ceiling but must fail the projection’s strict binary-price validation.
- Assert the evidence is applied and not quarantined before requesting the snapshot, so the test proves which validation layer rejects it.
- Include an admitted case exercising a numeric-validation branch where practical, using existing components without modifying shared core.

Preserve current quarantine tests as separate evidence.

**MF-03 — Complete the required read-only state assertions.**

Locations: `tests/test_rdp_accounting.py:110` and `:157`.

The contract requires that snapshot reads leave positions, orders, fill evidence and callbacks unchanged. Current tests assert repeated snapshot equality, unchanged open orders, and unchanged `_on_change` count. They do not assert that reads preserve:

- Positions.
- Registered owned-order records.
- Fill records and applied/quarantined fill keys.
- Other relevant callbacks.

Changing positions between snapshot reads proves those positions are excluded from accounting; it does not prove the projection leaves them untouched.

Required repair:

- Capture independent copies of relevant authoritative state after arranging the fixture.
- Perform repeated snapshot reads.
- Assert positions, orders, registry records, fill records and evidence sets remain unchanged.
- Capture the existing callback interfaces and assert reads invoke none.
- Verify that a rejected snapshot also preserves authoritative state.

This can be implemented entirely within the approved test file.

### NOTE

The Worker report states that residual YES/NO formula coverage and read-only behavior are covered. That description is broader than the assertions in the actual test file. Preserve the report unchanged and record completed repair coverage in the new Worker fix artifact.

## Frozen SOP Compliance

The inspected implementation correctly derives:

- QY/QN exclusively from accepted confirmed BUY fills.
- CY/CN from actual fill prices.
- Delta and inventory exposure.
- Weighted side averages, with unavailable averages represented by `None`.
- Balanced and residual quantities and attributed acquisition costs.
- LockedPnL without clamping negative values.
- Residual worst loss and best PnL.
- Worst and best settlement PnL.
- Winner-specific MarketPnL from explicitly supplied market token identity.

The implementation distinguishes acquisition cost, locked economics and settlement economics. It does not introduce obsolete `PnL_floor`, Pair State, predictive logic or trading decisions.

No Frozen SOP modification or identified formula deviation occurred.

## External Interface Review

The added module performs local calculations and existing registry/evidence reads only.

It introduces no HTTP client, WebSocket client, blockchain RPC call, third-party service, or runtime dependency.

REST recovery tests use a local fake gateway through the existing recovery implementation. No new external runtime interface was introduced.

## Trading Safety Review

The module has no order placement, cancellation, signing, execution authority, or live-send path.

Scoped unknown, foreign-market, foreign-token, SELL, failed and quarantined evidence fails explicitly. Unadmitted CONFIRMED-only evidence is excluded rather than synthesized into a new fill.

Existing live-send gates, reconciliation, stale-data protection and CapitalGate behavior were not modified.

This projection cannot certify reconciliation completeness or pending-fill safety. Its successful construction must not be used alone to authorize new orders.

No real-money trading occurred.

## Inventory / Capital Accounting Review

All inspected formulas match the approved contract and Frozen accounting definitions.

Resting orders and account positions do not supply acquisition cost. The snapshot uses the explicit run order scope and actual accepted confirmed maker fills.

InventoryCost equals C_total. FreeCash, ReservedCash, reservation policy and CapitalGate are appropriately outside this iteration.

No independent mutable inventory or capital state was introduced.

Required test repairs concern evidence completeness; no production formula defect was identified in this review.

## State Machine Review

The projection uses lifecycle status exactly equal to `TradeState.CONFIRMED`.

Deduplication and lifecycle advancement remain owned by UserEventProcessor. Separate maker orders sharing a trade ID retain distinct fill identities.

REST admission and recovery remain owned by existing filltruth code. The projection does not reconstruct unadmitted fills.

NORMAL, REBALANCE, STOP, quote timing, cancel/fill orchestration and market termination are explicitly outside this contract. Their absence does not block this prerequisite and does not imply project readiness.

## Test Review

Independent commands and results:

| Command | Result |
|---|---|
| `uv run pytest -q tests/test_rdp_accounting.py` | 15 passed in 0.11s |
| `uv run pytest -q tests/test_state.py tests/test_filltruth.py tests/test_favorite_first.py tests/test_favorite_first_review.py` | 118 passed in 0.88s |
| `uv run pytest -q` | 671 passed, 2 skipped in 9.35s |
| `git diff --check` | Passed |

No tests were excluded. No failure names or errors were produced.

Existing coverage supports compatibility, exact maker ownership, REST recovery, duplicate handling, late fills and lifecycle replay. Passing those tests does not substitute for the missing RDP-specific contract cases identified above.

## Scope Review

Production implementation remains within the two permitted files.

No existing subsystem was unnecessarily replaced. No FL/IM behavior, shared core, configuration, dependency, dataset, Frozen SOP or runtime interface was changed.

Requested repairs fit the existing approved scope and require no Architect revision or human decision.

## Residual Risks

- Callers must supply the complete owned-order set for the intended RDP run.
- Confirmed accounting can differ temporarily from existing optimistic positions.
- Pending execution, reconciliation reliability and restart recovery require separate future runtime gates.
- MarketPnL requires an externally established resolved winner; the snapshot does not establish resolution.
- This iteration remains an accounting prerequisite, not an integrated single-market runtime or MICRO_LIVE_READY certification.

## Final Decision

RETURN_TO_WORKER

BLOCKER: 0  
MUST_FIX: 3

Complete the required tests in the existing approved scope, run the prescribed checks, and produce a new immutable Worker fix artifact. Then return the actual repaired tree for independent re-review.

No human decision is required.

# ITER-0005 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md` and Frozen SOP
- `03_HUMAN_DECISION.md`
- `04_ARCHITECT_REVISION_01.md`
- `05_WORKER.md`
- Actual complete capital implementation and tests
- Existing accounting, quote, close-barrier and processor interfaces
- Current Git state and relevant untracked files

## Repository / Diff Reviewed

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `57bee8ffb8b83c7e81c6999773c4d11edabec351`
- Approved additions:
  - `src/polymaker/risk/rdp_capital.py`
  - `tests/test_rdp_capital.py`
- Other changes: Worker evidence and Orchestrator index maintenance.
- Preserved `research_outputs/` remains outside scope.

Reviewed SHA-256:

```text
rdp_capital.py:
dd85b2e54cad94ed0621fc0a7849767a538da56a2303dc71f823ed01812782cf

test_rdp_capital.py:
4d1c58b827895aff8909c336c2fce17eadd810643a856e5167bf5362a7c117af
```

Reviewer performed no edits, Git mutations or trading.

## Acceptance Criteria Review

The implementation supports confirmed acquisition cost, posted remaining reservations, strict money comparisons, pending/unadmitted evidence rejection, ownership checks and immutable results.

The human-approved policy is implemented correctly: NORMAL protects exactly $5; REBALANCE has no additional floor and may exhaust available cash.

Acceptance criteria 4–5 remain incomplete because contradictory current local order state does not invalidate prior close evidence.

Required partial-outcome guarantee tests also need correction. Other reviewed criteria and regression checks pass.

## Existing Codebase Compatibility

Existing ownership, fill evidence, accounting, candidates and complete close results are reused.

Decimal monetary accumulation reads the same confirmed fills without introducing mutable capital state or changing accounting/processor behavior.

No working subsystem was unnecessarily replaced or duplicated, and no existing safety control was bypassed.

No shared-core or FL/IM behavior changed.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — Prior close evidence releases capital despite a newer local LIVE observation.**

Location: `rdp_capital.py:310`.

Close validation checks supplied open-order overlap and current fill accounting. It does not check whether newer processor order evidence contradicts the recorded closure.

Independent reproduction:

1. Complete a real fake-gateway zero-fill close for registered order `closed`.
2. Deliver a valid LIVE `OrderEvent` through `processor.on_order()`, restoring its five-share local order.
3. Project capital with the prior close evidence and an empty supplied open-order list.

Observed:

```text
Local open IDs: ["closed"]
Projection: certified=True, FreeCash=30.00
NORMAL CapitalGate: admitted=True
```

The order evidence is contradictory, yet its reservation is released. Caller reliability flags must not certify away this known conflict.

Required repair:

- Reject close-covered IDs that remain/reappear in current local order state.
- Preserve the contradictory evidence; do not remove orders or clear quarantine.
- Add the late-LIVE reproduction, assert no certified snapshot/admission, then obtain renewed valid close evidence and verify recovery.
- Apply only read-only validation within the approved module.

No strategy or architecture decision is required.

**MF-02 — Test the approved admission guarantee with the actual NORMAL pair.**

Location: `tests/test_rdp_capital.py:399`.

The purported partial-outcome tests register one `0.375`-share resting order, then evaluate a new single-side `0.375`-share candidate while its existing reservation is already counted. This does not test the required guarantee for an admitted five-share NORMAL dual candidate at the exact $5 boundary.

Required test-only repair:

- Start from InventoryCost `20.60`, FreeCash `9.40`, with no current resting orders.
- Admit five YES at `.29` plus five NO at `.59`, leaving exactly `$5`.
- Register that pair and parameterize YES-only and NO-only confirmed fills, including fractional partial quantities.
- Project authoritative remaining orders and assert actual fill cost replaces the corresponding posted reservation without reducing FreeCash below `$5`.
- Include both full one-sided outcomes and verified cancellation release. The existing full-NO terminal fixture can be reused; add the missing YES counterpart.
- Use concrete expected cost/reservation/cash amounts and existing processor/close APIs.

Do not change gate policy, quote quantities or candidate-builder behavior.

### NOTE

None.

## Frozen SOP Compliance

Capital Survival and the approved `$5 FUTURE REPAIR RESERVE` are implemented without a current repair-price discount.

Money admission comparisons do not add a broad tolerance. Existing exchange checksum tolerances are confined to order evidence.

MF-01 weakens evidence required before reservation release. Repair fits the Frozen SOP and approved revision.

## External Interface Review

Projection and admission perform local reads/calculations only.

No external runtime interface, network client, dependency, RPC or settlement path was added.

## Trading Safety Review

No placement, cancellation or authoritative-state mutation occurs in this module.

Pending, failed, quarantined and unadmitted fills reject even when reliability flags are true. Unknown/manual market-token orders reject.

Current contradictory local order evidence must additionally block closed-reservation release under MF-01.

No real-money trading or cancellation occurred.

## Inventory / Capital Accounting Review

Confirmed actual fill prices determine InventoryCost. Remaining quantity times registered submitted price determines ReservedCash.

The capital identity is maintained without clamping negative cash. Overcommitment rejects.

NORMAL exact `$5` remaining passes; just below rejects. REBALANCE exact cash exhaustion passes. The `$23` InventoryCost / `$7` FreeCash fixture rejects NORMAL as required.

Fractional float-produced accounting interoperates with Decimal money calculation.

## State Machine Review

Mode checks preserve Frozen Delta boundaries.

Capital eligibility grants no execution or transition authority. Lifecycle, reliability and live authorization remain separate gates.

Prior close evidence freshness needs the order-state repair above.

## Test Review

Independent current-tree results:

| Command | Result |
|---|---|
| `uv run pytest -q tests/test_rdp_capital.py` | 37 passed in 0.14s |
| `uv run pytest -q tests/test_rdp_accounting.py tests/test_rdp_quotes.py tests/test_rdp_lifecycle.py tests/test_state.py tests/test_filltruth.py tests/test_risk.py` | 220 passed in 1.02s |
| `uv run pytest -q` | 832 passed, 2 skipped in 9.95s |
| `git diff --check` | Passed |

No exclusions or failures occurred. Separate local reproduction demonstrated MF-01.

## Scope Review

Implementation remains within the two approved additions.

Both repairs are ordinary source/test corrections within current authority. No shared-core, strategy, FL/IM or external-interface change is required.

## Residual Risks

Fresh official snapshots, collateral-read integration and complete runtime ownership remain caller/runtime responsibilities.

This gate certifies capital eligibility only. Integrated controller, metrics, Book Filter and final readiness remain outstanding.

## Final Decision

RETURN_TO_WORKER

BLOCKER: 0  
MUST_FIX: 2

Repair stale local-order release and the specified NORMAL partial-outcome tests, preserve existing artifacts, and return the actual repaired tree for re-review.

The approved $5 policy is settled and must not be reopened.

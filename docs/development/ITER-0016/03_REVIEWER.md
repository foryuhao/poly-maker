# ITER-0016 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`, Frozen SOP and durable project state.
- ITER-0016 `01_ARCHITECT.md` and `02_WORKER.md`.
- Actual runtime/test diff.
- Reused account capture, strict/exact order producer, backing evaluator, capital projection/gate, quote builder, Book Filter and retained-owner cleanup.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `e5e3ef46238a8df8ea235aeaf300db7a50d99ad0`

Implementation changes are confined to `rdp_runtime.py` and its tests. Audit/index changes were distinguished from implementation. Historical `research_outputs/` remains preserved.

Reviewed SHA-256:

| File | SHA-256 |
|---|---|
| `src/polymaker/rdp_runtime.py` | `19728306af1fdf7d3b5f9601304df5f02c410736d588d37e9b4befb8696a7866` |
| `tests/test_rdp_runtime.py` | `f07e4aef37a6205fdcecef0abfa93f07d83b666daf0c692dc33568936ec2c995` |

Reviewer made no source, test, audit or Git edits.

## Acceptance Criteria Review

| Criteria | Review |
|---|---|
| AC1–3: initial composition and refreshed retained account reads | Implemented; current evidence controls admission. |
| AC4: commitments/backing/selected state | Existing components correctly reused; partial-fill producer regression missing, MF-02. |
| AC5: empty local scope | Ownership, pending capture, fill and durable owned records are checked before and after reads. |
| AC6–8: Frozen gates, ticks and minima | Existing builders/filter retained; integration scenarios cover predicate rejection, unequal ticks and CLOB/Gamma disagreement. |
| AC9–10: health, expiry, closure and backlog | Synchronous final checks and relevant integration scenarios are present. |
| AC11: capital invariants | Fixed $30 projection and $5 NORMAL reserve preserved. |
| AC12: immutable scoped evidence without authority | Evidence is deeply immutable and never authorizes execution, but early rejection lacks market identity, MF-01. |
| AC13: sanitized reads and draining | Cleanup/cancellation implemented and tested; direct admission read-failure regression missing, MF-02. |
| AC14: compatibility | Full regressions pass; no shared-core/FL change. |
| AC15: required tests | Current suite passes with zero network attempts; specified coverage remains incomplete. |

## Existing Codebase Compatibility

The implementation reuses existing components without duplicating a gateway, ledger, inventory state or controller. No shared-core, FL or IM behavior changed.

The derived market copy uses validated token minima without mutating bootstrap metadata or changing Frozen clip sizes.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — Early rejected admission evidence does not identify its selected market.**

Locations: `RDPInitialNormalAdmission` and `_admission_rejected()`.

The result contains no condition ID or slug. Early rejections such as `SESSION_CLOSED`, `LOCAL_SCOPE_NOT_EMPTY` and overlap rejection also contain no candidate, projection or token metadata from which market scope could be recovered.

A pure in-memory reproduction called the actual rejection helper for two distinct market identities with the same generations:

```text
DISTINCT_MARKET_REJECTIONS_COMPARE_EQUAL=True
HAS_CONDITION_ID=False
HAS_SLUG=False
CANDIDATE=None
TOKEN_TICKS=()
```

This fails AC12’s requirement that returned evidence identify its scope.

Required repair: include stable selected-market identity in every result, including early rejection. Populate it directly from the session’s immutable bootstrap. Add assertions for successful and early rejected results, including distinguishable results from different market scopes. Preserve deep immutability and `execution_authorized=false`.

**MF-02 — Required account-composition regressions remain incomplete.**

Two focused tests are required:

1. **Actual partially filled foreign BUY commitment.** The strict/exact producer fixture currently uses `original_size="1"` and `size_matched="0"`. Its high-precision price tests fractional cost, but it does not exercise cumulative subtraction for a partially filled foreign order through parser→account capture→backing→initial admission.

   Use an official-shaped row such as original1.1, matched0.2, price0.5. Assert exact remaining0.9 and commitment0.45, counted once. A cash/allowance backing boundary of30.45 is a useful assertion. Preserve the existing high-precision fixture.

2. **Initial-admission read failure and sanitization.** Current new scenarios exercise successful reads and cancellation, but do not inject a read failure into `initial_normal_admission()`. Existing observer/account tests do not verify this method’s rejection and ownership cleanup.

   Inject an SDK/gateway exception containing credential sentinels. Assert a categorized rejected result, no sentinel in returned evidence/logs/output, cleared admission/read ownership, no execution calls and successful subsequent cleanup.

These are approved required-test cases. No production accounting defect is currently indicated.

### NOTE

The interrupted unguarded Worker run is correctly disclosed as incomplete. The completed independently guarded full suite provides current regression evidence.

## Frozen SOP Compliance

No strategy-semantic deviation was found.

Initial inventory comes from the existing processor with empty run scope. Quotes remain five shares per side at each token’s BestBid minus its current tick. PriceGate, Book Filter and CapitalGate are reused unchanged.

## External Interface Review

No runtime interface or dependency was added. Current account reads use the existing retained gateway and official paths.

Independent full-suite validation blocked DNS and socket connections, recording zero attempts.

## Trading Safety Review

No signing, placement, cancellation, reservation mutation or execution authority was introduced.

Outstanding execution gates explicitly remain listed, and the authorization field is fixed false. Eligibility remains historical capture evidence requiring future submit-time revalidation.

No real-money trading occurred.

## Inventory / Capital Accounting Review

Processor ownership, pending capture, fill records, admitted/quarantined evidence and durable owned records prevent initial-only scope reuse.

The existing projection preserves:

```text
FreeCash + ReservedCash + InventoryCost = $30
```

The existing NORMAL protected reserve remains $5. Account positions are not adopted. Foreign commitments are handled by the existing exact backing evaluator; MF-02 concerns integration proof of partial remaining quantity.

## State Machine Review

Account reads occur asynchronously through the retained gateway. Subsequent local-state, health/generation, expiry, backlog, book/filter, candidate and capital checks execute without intervening awaits.

Overlap is rejected. Active reads remain owned through first/repeated cancellation and session closure, including tested executor draining.

No subsequent-cycle or REBALANCE controller is added.

## Test Review

Independent current-tree full suite ran through `uv run python`, blocking `getaddrinfo`, `create_connection`, socket `connect` and `connect_ex`, invoking:

```python
pytest.main(["-q"])
```

Result:

```text
1238 passed, 2 skipped in 69.39s
NETWORK_ATTEMPTS=0
```

Additional independent checks:

- Pure in-memory rejection-scope reproduction: confirmed MF-01.
- Scoped Ruff: passed.
- `git diff --check`: passed.

Passing tests do not close MF-01/MF-02.

## Scope Review

Changes remain within the approved initial empty-run admission contract. No unnecessary subsystem replacement was found.

Both repairs are ordinary implementation/test work within existing authority. No architecture revision or human decision is needed.

## Residual Risks

Admission can become obsolete after return. It does not certify current tradability, operational time sufficiency, subsequent-cycle reconciliation or deliberate live authorization.

Later NORMAL cycles, REBALANCE, execution lifecycle and complete metrics remain deferred. MICRO_LIVE_READY remains unestablished.

## Final Decision

RETURN_TO_WORKER

BLOCKER: 0  
MUST_FIX: 2

Add market scope to all admission evidence and close the two account-composition regressions, then return for independent re-review.

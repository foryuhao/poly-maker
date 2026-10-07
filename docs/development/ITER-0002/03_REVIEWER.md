# ITER-0002 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`
- Frozen SOP, particularly sections 14, 15 and 17
- Current iteration index
- `docs/development/ITER-0002/01_ARCHITECT.md`
- `docs/development/ITER-0002/02_WORKER.md`
- Actual `rdp_quotes.py` and complete quote tests
- Reviewed accounting implementation
- Relevant existing domain, orderbook, market-data tick dispatch and generic quoting implementation
- Actual Git state, including untracked implementation files

## Repository / Diff Reviewed

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `15a87320f26eafc640c383f2d0455f3c081da713`
- New untracked implementation:
  - `src/polymaker/strategy/rdp_quotes.py`
  - `tests/test_rdp_quotes.py`
- Other current changes: Orchestrator index maintenance and Worker artifact.
- Preserved unrelated `research_outputs/` remains outside iteration scope.
- No existing production, configuration, dependency or dataset change appeared.

Reviewed SHA-256:

```text
rdp_quotes.py:
e7afcff48ade24c3db2b1fdccfe6617876a33ad5e041eb445cb091075362925b

test_rdp_quotes.py:
c51adce5384aa9236b12109e56acf0397d3b443d5939047293371900e3cfa179
```

`git diff --check` passed. Reviewer performed no edits, Git mutations, external network operations or trading.

## Acceptance Criteria Review

Criteria 1, 2, 5, 6, 8, 10, 11 and the regression/scope portion of criterion 12 are supported by implementation and passing tests.

Criteria 3 and 9 are incomplete: valid fractional accounting snapshots can be rejected as malformed.

Criteria 4 and 7 are incomplete at numerical boundaries: a finite positive tick can cause an uncaught Decimal exception or produce a quote that no longer preserves the one-tick offset after conversion.

The existing integration test uses an integer one-sided fill and does not expose the fractional accounting incompatibility.

## Existing Codebase Compatibility

The implementation reuses existing immutable `Quote`, `BookView`, `MarketMeta` and RDP accounting types.

The separate RDP price policy is justified because existing generic quoting adds fair-value, clamping and sizing behavior incompatible with the Frozen formulas.

No working infrastructure was replaced or duplicated. No existing execution safety control was bypassed. FL and IM remain unchanged.

Compatibility with the reviewed accounting snapshot needs the ordinary repair below.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — Valid fractional confirmed inventory is incorrectly rejected.**

Location: `src/polymaker/strategy/rdp_quotes.py:72`.

The accounting component computes Delta using float subtraction. The quote builder independently converts QY, QN and Delta to Decimal and demands exact Decimal equality.

Independent reproduction using registered orders and actual confirmed fills:

```text
YES quantity: 0.3
NO quantity:  0.1

snapshot.qy:    0.3
snapshot.qn:    0.1
snapshot.delta: 0.19999999999999998

build_normal_quotes(...):
quotes=(), reason="INVALID_INVENTORY"
```

These are valid NORMAL inputs. Decimal conversion makes `QY - QN` exactly `0.2`, which differs from the string representation of the valid accounting Delta.

Required repair:

- Validate consistency using semantics compatible with the existing accounting producer.
- Preserve malformed-inventory rejection.
- Preserve strict NORMAL boundaries and decimal-safe price arithmetic; do not introduce a broad trading tolerance.
- Add integration coverage from real confirmed fractional fills on both sides, including this reproduction.

No accounting/shared-core modification or strategy decision is required.

**MF-02 — Decimal arithmetic and output conversion can violate the rejection/price contract.**

Locations: `src/polymaker/strategy/rdp_quotes.py:98` and `:113`.

Two independent flat-inventory probes with otherwise valid books demonstrated:

```text
YES bid=0.56, ask=0.60; NO bid=0.42, ask=0.48

YES tick=1e-30:
raises decimal.InvalidOperation / DivisionImpossible at modulo

YES tick=1e-20:
returns YES Quote(price=0.56, size=5.0)
```

The first escapes the required explicit no-candidate result. In the second, float conversion erases the computed one-tick discount and returns BestBid itself.

These ticks are outside normal venue values, but the builder currently accepts them as finite positive input and must preserve its fail-closed contract.

Required repair:

- Handle unsupported arithmetic/precision safely with an explicit rejection.
- Validate that returned float quote prices preserve the intended Decimal price and tick-grid semantics.
- Reject the entire pair when its exact required price cannot be represented; do not clamp or substitute a different price.
- Add tests for both reproductions while retaining ordinary per-token tick and PriceGate boundary coverage.

This is a numerical implementation correction within approved scope.

### NOTE

None.

## Frozen SOP Compliance

For ordinary valid inputs, the implementation correctly uses:

- Strict `abs(Delta) < 5`.
- PriceGate on current best bids.
- BestBid minus each explicit token tick.
- Five-share base clip with inventory quantity skew.
- Deterministic YES/NO BUY pair.
- Post-only requirement.

It does not introduce predictive filters, Pair State or execution policy.

MF-01 prevents valid Frozen NORMAL behavior. MF-02 can erase the required price offset. No Frozen SOP change is needed to repair either defect.

## External Interface Review

The module performs local calculation only. No runtime interface, network client or dependency was added.

Synthetic tick-change tests reuse the existing market-data dispatcher.

## Trading Safety Review

The builder has no placement, cancellation or live-send authority.

Outstanding Book Filter, CapitalGate, reliability, reconciliation and authorization gates are explicit. The result does not claim readiness to send.

Most invalid inputs reject both quotes. MF-02 must complete numerical fail-closed handling.

No real-money trading occurred.

## Inventory / Capital Accounting Review

The builder does not mutate accounting or introduce a capital ledger.

MF-01 concerns interpretation of the existing valid accounting output. CapitalGate remains an explicitly outstanding control, appropriately outside this contract.

## State Machine Review

NORMAL availability and both threshold signs are covered.

The module creates candidates only. Cycle lifecycle, REBALANCE, STOP, FillWindow and reconciliation remain outside this iteration and are not claimed as implemented.

## Test Review

Independent commands:

| Command | Result |
|---|---|
| `uv run pytest -q tests/test_rdp_quotes.py` | 37 passed in 0.11s |
| `uv run pytest -q tests/test_rdp_accounting.py tests/test_quoting.py tests/test_orderbook.py tests/test_marketdata.py tests/test_favorite_first.py tests/test_favorite_first_review.py` | 100 passed in 0.63s |
| `uv run pytest -q` | 716 passed, 2 skipped in 9.35s |
| `git diff --check` | Passed |

No exclusions or test failures occurred.

Separate local probes reproduced both findings. Passing tests currently omit those numerical cases.

## Scope Review

Changes remain within the two permitted new files.

No shared-core, FL/IM, accounting, gateway, CLI, configuration, dependency, dataset or runtime-interface change occurred.

Both repairs fit the current Architect Contract.

## Residual Risks

Book freshness and correct token/book association remain caller responsibilities.

Candidates still require Book Filter, CapitalGate, reliable order state, completed cycle reconciliation and explicit live authorization before execution.

This iteration does not establish integrated runtime readiness.

## Final Decision

RETURN_TO_WORKER

BLOCKER: 0  
MUST_FIX: 2

Repair the two numerical defects within the approved files, preserve existing artifacts, add regression cases and return the actual repaired tree for independent re-review.

No human or Architect decision is required.

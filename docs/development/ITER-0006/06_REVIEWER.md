# ITER-0006 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`, Frozen SOP, baseline and iteration index.
- `ITER-0006/03_HUMAN_DECISION.md`, approved revision `04_ARCHITECT_REVISION_01.md`, and `05_WORKER.md`.
- Actual new filter and tests.
- Existing `OrderBook`, parsed `TradePrint`, market-data callback/generation infrastructure and relevant tests.

## Repository / Diff Reviewed

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `10e94b5f6e519eca3c2618efeaaa524d3a333711`.
- Implementation consists of two untracked files. Tracked changes are confined to the Orchestrator’s iteration index.
- Historical `research_outputs/` remains untracked and untouched.
- `git diff --check`: passed.

Reviewed SHA256:

```text
e63359d5bc8756f837ed772207ee7c8de8f3477ce0cef6559ca248f5266490b1  src/polymaker/strategy/rdp_book_filter.py
236e509a4b07ddaac0709835baa72e612a046918c8d71c5c6c028057dab16e9a  tests/test_rdp_book_filter.py
```

## Acceptance Criteria Review

| AC | Assessment |
|---|---|
| 1 | Satisfied: reversing depth produces variations 12 and 6. |
| 2 | Incomplete: unsupported precision can change the strict Depth Extreme result. |
| 3 | Incomplete: an exactly expired trade can remain active. |
| 4 | Satisfied for supported fixtures: exact adjacent level and missing neighbor. |
| 5 | Incomplete: float cutoff arithmetic violates exact expiration. |
| 6 | Incomplete: unavailable evidence is not marked rejected; decreasing evaluation times can remain ready. |
| 7 | Satisfied: existing synthetic callbacks populate evidence. |
| 8 | Immutable evidence exists, but rejection/readiness semantics require repair. |
| 9 | Satisfied: no authoritative input mutation or I/O. |
| 10 | Specified suites pass; scope is preserved. Required boundary coverage needs strengthening. |

## Existing Codebase Compatibility

The implementation reuses existing market identity, books, trade objects and callbacks. It does not duplicate or replace the market-data subsystem, modify shared core, alter FL behavior, import IM code, or bypass execution controls.

## Findings

### BLOCKER

NONE.

### MUST_FIX

**MF-01 — Unavailable evidence reports `rejected=False`.**

`rdp_book_filter.py:330` derives rejection as `ready and bool(reasons)`. A baseline at receipt time zero evaluated at one second returns:

```text
ready=False
rejected=False
reasons=('NOT_READY:WARMUP',)
```

The approved contract requires insufficient/disconnected/unreliable history to return an explicit rejection. Make unavailable evidence reject while preserving readiness and reasons. Add assertions for warmup, unreliable state, generation/tick resets and invalid evidence; fully ready passing results must remain unrejected.

**MF-02 — Float subtraction retains exactly expired activity.**

`rdp_book_filter.py:261–279` constructs window cutoffs by subtraction. With both books initialized at zero and trades received at `.3`, evaluation at `.3 + 2` produces:

```text
cutoff2 = 0.2999999999999998
real_trade_in_2s = True
```

Both sides remain active and the filter passes, although the trades are exactly two seconds old under the supplied receipt/deadline pair.

Use consistent boundary arithmetic that preserves exact expiration without introducing a strategy tolerance. Cover `.3` and integer boundaries, just-inside/just-outside events, depth-update expiry and one-second anchor selection.

**MF-03 — Positive decreasing evaluation times remain ready.**

Evaluation compares time with the latest observation but does not track prior evaluation time. Quiet baselines at zero permit:

```text
evaluate(3).ready == True
evaluate(2.1).ready == True
```

The contract requires monotonic receipt-time evaluation and backward-time rejection. Reject decreasing evaluations, invalidate incompatible history and require fresh coverage. Preserve equal-time repeated evaluation. The existing backward test only evaluates before the latest observation and misses this case.

**MF-04 — Unsupported precision silently changes a strict predicate.**

`rdp_book_filter.py:134–136` rounds depth addition at precision 64 without detecting lost information. This finite existing-book input reproduces an incorrect admission:

```text
YES best depth = 60
YES adjacent depth = 1e-70
NO effective depth = 15
recent valid trades on both sides
```

Exact YES depth exceeds 60, so its ratio to 15 exceeds four and Depth Extreme must trigger. Actual output is:

```text
Q_eff = 60.00000000000000000000000000000000000000000000000000000000000000
depth_extreme=False
ready=True
rejected=False
```

Implement exact threshold arithmetic within supported precision or explicitly fail closed when precision is unsupported. Cover depth addition, cumulative variation and threshold comparisons. Do not add threshold tolerances or alter the Frozen comparisons.

### NOTE

Production callback composition remains deferred as approved.

## Frozen SOP Compliance

Approved total variation, token-local trade activity, adjacent-level depth, ordinary strict thresholds and bid excursion are implemented. MF-02 and MF-04 produce actual predicate deviations; MF-01 and MF-03 violate the approved evidence-safety contract. No strategy revision is required to repair them.

## External Interface Review

No new external dependency, HTTP/WebSocket client, RPC path or runtime interface is introduced.

## Trading Safety Review

The observer has no order placement, cancellation or reconciliation authority. No real-money action occurred. Its admission result must be repaired before integration so unavailable or incorrectly evaluated evidence cannot certify NORMAL eligibility.

## Inventory / Capital Accounting Review

Inventory, confirmed-fill accounting and capital policy are unchanged. The approved $5 reserve is unaffected.

## State Machine Review

No trading state machine is added or modified. Tick changes invalidate the affected token; generation changes invalidate both histories. Evidence readiness and evaluation-time behavior require the scoped fixes above.

## Test Review

Independent current-tree results:

```text
uv run pytest -q tests/test_rdp_book_filter.py
36 passed

uv run pytest -q tests/test_marketdata.py tests/test_orderbook.py tests/test_estimators.py tests/test_rdp_quotes.py tests/test_rdp_capital.py
138 passed

uv run pytest -q
871 passed, 2 skipped
```

The full suite ran after the Worker’s final lint edit. Passing tests do not cover the four reproduced defects. Existing tests adequately exercise normal reversal, equal observation timestamps, dynamic ticks, generation resets, callback reuse and input immutability.

## Scope Review

Only the two authorized implementation files were added. All requested repairs fit that scope. No Architect revision or human decision is necessary.

## Residual Risks

Runtime wiring, broader feed reliability and live validation remain deferred. These tests establish mechanics, not RDP economics or integrated runtime readiness.

## Final Decision

RETURN_TO_WORKER.

Repair MF-01 through MF-04, add the identified regression cases, rerun the required suites and return for independent re-review.

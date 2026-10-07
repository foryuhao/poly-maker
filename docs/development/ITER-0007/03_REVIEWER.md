# ITER-0007 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- Active `AGENTS.md`, Frozen SOP and current iteration records.
- `01_ARCHITECT.md` and `02_WORKER.md`.
- Actual gateway/test diff, existing placement controls, installed SDK batch submission and relevant predecessor tests.
- Official documentation confirms per-order batch results and independent acceptance/rejection. [Polymarket Place Orders](https://docs.polymarket.com/trading/place-orders#post-a-batch-of-orders)

## Repository / Diff Reviewed

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `b44e32cf2c03f2525201b8b4a1793789331e59f1`.
- Implementation changes: `gateway.py`, `test_execution.py`.
- Other changes are Orchestrator-owned index/audit records.
- Preserved `research_outputs/` remains untouched.
- `git diff --check`: passed.

Reviewed SHA256:

```text
86892176a2a017d3f228a9cc896711c2a4c74e3997573a617830d52a2674e60b  src/polymaker/execution/gateway.py
146c6c555431f4ee056a94f9dec07ad66d2107e9104254272104b3d3057fc857  tests/test_execution.py
```

## Acceptance Criteria Review

| AC | Assessment |
|---|---|
| 1 | PASS — default placement behavior and existing tests are preserved. |
| 2 | PASS — one batch POST, two GTC entries, forced post-only. |
| 3 | PASS — accepted orders follow request positions. |
| 4 | PASS — two explicit rejections return empty. |
| 5 | PASS — either partial acceptance raises the specialized exception with exact evidence. |
| 6 | PASS — accepted/unknown receipts retain independent acknowledgment evidence. |
| 7 | PASS — malformed, contradictory and duplicate receipts fail closed. |
| 8 | PASS — ambiguous IDs remain observation evidence without fabricated attribution. |
| 9 | PASS — POST exception remains unknown; no retry or cancel. |
| 10 | Behavior verified; required flag-denial regression coverage is incomplete. |
| 11 | PASS — paper pair works without client invocation; default paper behavior remains intact. |
| 12 | PASS — no caller, lifecycle, inventory or strategy changes. |

## Existing Codebase Compatibility

The extension reuses the existing gateway, signing, rate limits, minimum-size checks, submit guards, post-only construction and observation hooks. It does not replace or duplicate execution infrastructure. Default single-order parsing remains unchanged. No IM dependency is introduced.

## Findings

### BLOCKER

NONE.

### MUST_FIX

**MF-01 — Required flag-denial tests do not exercise the flags.**

In `test_opt_in_pair_invalid_shape_or_flags_fail_before_signing`, both missing-flag cases use `quotes=None`:

```text
(None, False, True)
(None, True, False)
```

They therefore fail quote-shape validation before reaching either flag check. Removing the strict/post-only requirements would leave these cases passing.

Add durable tests using an otherwise valid pair:

- `force_post_only=False`, `strict_placement=True`;
- `force_post_only=True`, `strict_placement=False`.

Each must raise `ValueError` and produce zero signatures and zero POSTs. The production implementation already behaves correctly: independent fake-client probes verified both cases. This repair requires tests only.

### NOTE

Current unfiltered test Ruff output contains three findings: one local import-order finding and two assigned lambdas. All three were independently reproduced against HEAD. HEAD also contained a top-level import-order finding that the current change fixes. No new lint finding was identified.

## Frozen SOP Compliance

The change enables acknowledgment handling for the approved NORMAL dual-maker pair without changing quote prices, sizes, gates or state semantics. Acknowledgment remains separate from confirmed fill truth.

## External Interface Review

The existing official CLOB SDK submission path is reused. No new runtime interface, dependency or RPC path is introduced.

## Trading Safety Review

Partial acceptance never returns an ordinary successful pair. Unknown or duplicate receipts cannot certify two orders. Strict exceptions retain attributable acknowledgments and separately observed IDs. Existing pre-submit and final-after-signing guards remain active. No automatic retry, cancellation or real-money action occurred.

## Inventory / Capital Accounting Review

Response status and amounts do not update inventory, fills or capital. Existing authoritative accounting remains unchanged.

## State Machine Review

No trading state machine or controller is modified. Future orchestration must reconcile partial/unknown outcomes before admitting further risk, as explicitly deferred by the contract.

## Test Review

Independent current-tree results:

```text
uv run pytest -q tests/test_execution.py
70 passed

uv run pytest -q tests/test_filltest.py tests/test_rdp_lifecycle.py tests/test_favorite_first.py tests/test_favorite_first_review.py tests/test_gateway_positions_v2.py
147 passed

uv run pytest -q
913 passed, 2 skipped

uv run ruff check src/polymaker/execution/gateway.py
All checks passed!
```

No test exclusions were introduced. The required valid-pair flag-denial tests remain missing despite correct production behavior.

## Scope Review

The shared-core extension is explicitly authorized and remains bounded to the existing gateway and tests. MF-01 fits the approved scope; no Architect revision or human decision is needed.

## Residual Risks

Per-token signing metadata and controller handling of incomplete/unknown batches remain deferred. This iteration does not establish integrated RDP readiness or live execution evidence.

## Final Decision

RETURN_TO_WORKER.

Add the two valid-pair flag-denial regression cases, rerun appropriate checks and return for independent re-review. No production repair is currently required.

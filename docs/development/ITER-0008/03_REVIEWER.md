# ITER-0008 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- Active `AGENTS.md`, Frozen SOP and approved `01_ARCHITECT.md`.
- Worker artifact `02_WORKER.md`.
- Actual gateway/test diff and existing placement controls.
- Installed SDK `TickSize`, rounding configuration and price-rounding helper.
- Relevant RDP, market-data and predecessor tests.

## Repository / Diff Reviewed

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `cb80a59c2234d7380c91ef234b588163e8597e3a`.
- Implementation changes remain confined to `gateway.py` and `test_execution.py`.
- Other changes are Orchestrator-owned audit/index records. Preserved `research_outputs/` remains untouched.
- `git diff --check`: passed.

Reviewed SHA256:

```text
a3c34633bd423ed1181ad60e222635d8d54daad0a3f1d451a17f378cd479f45c  src/polymaker/execution/gateway.py
849e6f420de72a669259bfcdd888e19bfd37d9f3eaddc29a47164eba0d9c8122  tests/test_execution.py
```

## Acceptance Criteria Review

| AC | Assessment |
|---|---|
| 1 | PASS — distinct pair ticks reach signing in request order. |
| 2 | PASS — single YES/NO quotes use their supplied tick. |
| 3 | PASS — successive calls use updated supplied ticks. |
| 4 | PASS — invalid keys, values, prices, flags, guards and shapes reject before signing. |
| 5 | PASS — mapping and quote sequence are copied before the first await. |
| 6 | PASS — final guard blocks POST after a signing-time tick change. |
| 7 | PASS — signing rejection produces no POST, retry or cache modification. |
| 8 | Behavior verified; required new-option unknown-receipt regression is missing. |
| 9 | PASS — paper mode validates without a client. |
| 10 | PASS — default signing/parsing and predecessor tests remain compatible. |

## Existing Codebase Compatibility

The implementation extends the existing gateway and reuses signing, guards, minimum-size checks and acknowledgment parsing. Omitting `token_ticks` preserves legacy signing options. No execution subsystem is replaced or duplicated. No IM dependency is introduced.

## Findings

### BLOCKER

NONE.

### MUST_FIX

**MF-01 — Required unknown acknowledgment coverage omits `token_ticks`.**

The approved Required Tests explicitly require partial/unknown pair acknowledgment propagation with the new option. The new `test_per_token_tick_pair_partial_ack_uses_strict_existing_parser` covers partial acceptance only. Existing unknown-receipt tests do not supply `token_ticks`.

Add durable fake-client cases exercising accepted-plus-unknown receipts through guarded per-token pair placement, preferably in both request positions. Assert:

- `PlacementUnknown` propagates through `place()`;
- the independently accepted order retains correct request-position attribution;
- observed IDs and reason survive;
- exactly one POST occurs, with no retry or cancellation.

An independent new-option probe already produces the correct behavior:

```text
PlacementUnknown
reason=UNRESOLVED_BATCH_RESULT
acknowledged_orders=(accepted, no-token)
observed_order_ids=(accepted, observed)
POSTs=1
cancels=0
```

This is a tests-only repair; no production defect was identified.

### NOTE

The three current test Ruff findings were independently reproduced against HEAD: one local import-order finding and two assigned lambdas. No new lint finding remains.

## Frozen SOP Compliance

Dynamic YES/NO ticks remain independent. Prices are validated exactly against their token grid and are passed unchanged to signing. No fallback tick, repricing, size adjustment or strategy-semantic change is introduced.

## External Interface Review

The existing official SDK and runtime interfaces are unchanged. No new dependency, metadata service or private SDK cache manipulation is added.

## Trading Safety Review

The opt-in requires strict placement, forced post-only and a callable guard. Captured requests survive caller mutations across awaits. Both submit checks remain active, including the final check after signing. Signing failures prevent POST. No real-money action occurred.

## Inventory / Capital Accounting Review

No inventory, confirmed-fill or capital-accounting changes. Acknowledgment remains distinct from fill confirmation. SDK quantity-precision verification remains explicitly deferred.

## State Machine Review

No controller or trading state changes. The eventual caller remains responsible for authoritative tick/generation verification through its guard.

## Test Review

Independent current-tree results, including the final test formatting:

```text
uv run pytest -q tests/test_execution.py -k per_token
38 passed, 72 deselected

uv run pytest -q tests/test_execution.py
110 passed

uv run pytest -q tests/test_rdp_quotes.py tests/test_rdp_lifecycle.py tests/test_gateway_positions_v2.py tests/test_marketdata.py tests/test_orderbook.py tests/test_engine.py tests/test_favorite_live.py
216 passed

uv run pytest -q
953 passed, 2 skipped
```

Independent SDK helper checks verified all **11,704** legal prices strictly inside `(0,1)` across all six declared supported ticks: rounding preserves each price. No live signing or network operation was used.

## Scope Review

The bounded shared-core extension is authorized. Changes remain within the approved files. MF-01 requires no architecture revision or human decision.

## Residual Risks

Runtime tick provenance, generation consistency, public SDK cache refresh and quantity/minimum-size integration remain deferred. This iteration does not establish integrated readiness.

## Final Decision

RETURN_TO_WORKER.

Add the missing per-token unknown-acknowledgment regression cases and return for independent re-review. Production changes are not currently required.

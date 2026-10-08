# ITER-0019 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Repaired MF-01 through MF-04 in the approved ITER-0019 receipt-time FillWindow scope. No strategy semantics, submission, cancellation, reconciliation, or accounting behavior changed.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Base HEAD: `aa0208c67ec53b0ec2aea00f1ab60959eaa4169f`
- Final HEAD remains unchanged; no Git history operations were performed.
- Orchestrator-owned index/audit changes and preserved untracked `research_outputs/` were left untouched.

## Existing Components Reused

Reused the retained session’s current placement scope, processor accepted-fill evidence, original receipt metadata, session health and generation checks, `snapshot_inventory`, and the existing book-filter result path.

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `src/polymaker/execution/rdp_fill_window.py`
- `tests/test_rdp_placement.py`

Final SHA-256:

- `src/polymaker/rdp_runtime.py`: `9489e47353845803d91769a5fc276788525ebe4800b3707ccc93b404f043c3fd`
- `src/polymaker/execution/rdp_fill_window.py`: `e84a82c68bd24a740286aa02d7b0600c17588285fb588f66a219de10a859ddd2`
- `tests/test_rdp_placement.py`: `4ee9174ff7b72bb6b978503fd881753475bc854aff49796f3748e482e7ff314b`

## Implementation Summary

The FillWindow retains its established first receipt and deadline. If later-admitted accepted confirmation evidence has an earlier original receipt, the decision now fails closed with `CONFIRMED_RECEIPT_PRECEDES_ESTABLISHED_WINDOW`; it does not reset or extend the window.

Receipt generations must now be non-boolean integers before comparison. The decision’s `execution_authorized` field is constructor-independent and fixed false.

Added window-specific tests for contradictory late admission, malformed generation and non-repair, authorization construction/replacement, foreign and observed-only IDs, invalid market/token/economics, zero-size, FAILED and quarantined evidence, partial/unknown/pending-overflow placement outcomes, and healthy book movement with a Book Filter rejection.

## Behavior Before

A later-admitted earlier confirmation could be ignored while the later deadline remained certified. Float generation `4.0` could compare equal to integer generation `4`. A caller could construct or replace decision evidence with `execution_authorized=True`. Adverse evidence lacked direct FillWindow decision coverage.

## Behavior After

Contradictory timing preserves the original established deadline but makes continuation uncertified. Malformed generation values fail closed. Decision evidence cannot claim execution authorization. The required adverse cases reject certification or contribute no inventory, while healthy book changes and filter rejection preserve the existing deadline.

## Tests Added / Changed

Added the exact two-leg late-admission reproduction with an intermediate query; float-generation and unrepairable malformed-receipt cases; constructor and `dataclasses.replace` authorization checks; and the specified adverse-scope and book-movement cases.

## Tests Executed

- `uv run pytest -q tests/test_rdp_placement.py -k 'fill_window or preack_confirmations or book_change' --tb=short` — **35 passed, 77 deselected**
- `uv run pytest -q tests/test_rdp_placement.py -k 'late_admission_of_earlier or unreliable_confirmation_receipt or starts_on_confirmed' --tb=short` — **5 passed, 107 deselected**
- `uv run pytest -q tests/test_userstream_parse.py tests/test_rdp_userstream.py tests/test_rdp_runtime.py tests/test_rdp_placement.py tests/test_rdp_accounting.py tests/test_rdp_capital.py tests/test_rdp_lifecycle.py tests/test_engine.py` — **399 passed**
- `PYTHONPATH=/tmp uv run pytest -q -p rdp_network_block` — **1,357 passed, 2 skipped**; `RDP_NETWORK_GUARD_ATTEMPTS=0 names=[]`
- Scoped Ruff across all seven ITER-0019 source/test paths — **passed**
- `git diff --check` — **passed**

The network guard blocked and counted `socket.connect`, `socket.connect_ex`, `socket.create_connection`, and `socket.getaddrinfo`; it failed the run if any attempt occurred.

## Test Results

All repair-specific, affected regression, and guarded full-suite checks passed. No network attempts were recorded. No failing test runs occurred during this repair round.

## Regression Results

The affected suite included user-stream parsing, RDP userstream/runtime/placement/accounting/capital/lifecycle, and the FL engine regression tests. All passed.

## Git Diff Summary

Repair changes were confined to the runtime decision, FillWindow component, and placement tests. No commits or other Git history changes were made. The pre-existing untracked `research_outputs/` and Orchestrator-owned audit/index changes were preserved.

## Safety Checks

- No real-money trading, orders, cancellations, or account actions were performed.
- FillWindow remains read-only and cannot authorize execution.
- The final full suite ran with DNS/socket access blocked and zero attempted network calls.

## Known Limitations

This iteration reports timing only. It does not automatically close the cycle or perform the separate cancel/reconcile barrier.

## Deferred Findings

Production submission, automatic cycle closure, subsequent NORMAL/REBALANCE operation, unknown-placement recovery, and broader metrics remain deferred under the contract.

## Reviewer Attention

Please verify the late-arriving earlier receipt leaves the established deadline unchanged while permanently preventing certified continuation, and confirm the decision authorization field cannot be set through construction or replacement.

## Human Live Trading Performed

NONE

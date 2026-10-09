# ITER-0025 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented DG-001, **Authorized Initial NORMAL Submission**, as specified in `docs/development/ITER-0025/01_ARCHITECT.md`.

The submission defaults to denial. An explicitly authorized operation can make one guarded initial placement through the existing gateway and retain the actual outcome through the existing placement owner. This does not close DG-002–DG-012.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Base HEAD: `d8cddca33fb595d7f6be1874af16a07fb3c70890`
- Working tree initially contained only the preserved untracked `research_outputs/` directory.
- No Git history operations were performed.

## Existing Components Reused

- Initial admission, factual intent capture and event-loop bridge in `RDPInputSession`.
- Existing `ExecutionGateway.place()` strict pair parsing, post-only enforcement and captured per-token ticks.
- `PlacementUnknown` and `RDPPlacementOwner.resolve()` for retained acknowledgment, partial-fill and uncertainty ownership.
- Existing processor pending-fill replay, lifecycle task ownership, safe diagnostic helper and SDK diagnostic suppression.

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `src/polymaker/execution/gateway.py`
- `tests/test_rdp_placement.py`
- `tests/test_execution.py`

## Implementation Summary

Added one default-denied initial submission operation with exact-`True` authorization, current admission provenance, finite positive order and close budgets, a strict market-time boundary, and an absolute monotonic order deadline.

The operation arms existing pre-ack capture before delegating once to `ExecutionGateway.place()`. Both existing Gateway guard points combine current factual validation with operation authorization, interruption and deadline checks. The existing worker-thread bridge continues to capture mutable state on the owning event loop.

The operation retains complete, empty, partial, unknown, pre-POST failure, attempted-POST failure and resolution-error outcomes through the existing placement owner. Caller cancellation, teardown, deadline expiry and waiter failure revoke further send permission but drain the same Gateway task; they do not cancel or detach blocking SDK work. A resolution exception preserves the owner’s actual state and does not trigger a second resolve.

The Gateway’s new `safe_diagnostics` option defaults off. Its opted-in path emits categorical diagnostics outside SDK exception handlers, returns typed pre-POST failure evidence, strips exception causes and raw messages, and preserves actual placement evidence when a logging handler fails. Existing default behavior remains covered by a legacy compatibility regression.

## Behavior Before

Initial admission, arming and factual revalidation existed, but the runtime did not connect them to a retained initial Gateway placement operation. The Gateway’s generic placement failure path could log raw exception text and return an unclassified empty list.

## Behavior After

Without explicit authorization, submission returns denied before arming, signing or posting. With explicit authorization, one fresh session-issued admission can delegate one strict post-only pair through the existing gateway. Both factual guard locations remain active, and the operation retains actual owner state through successful acknowledgment, uncertainty, interruption and cleanup.

The captured evidence remains `execution_authorized=False`; this operation does not activate CLI or automatic-cycle trading.

## Tests Added / Changed

Added direct fake-SDK coverage for:

- Default denial, exact-`True` authorization, invalid budgets and exact market-time equality.
- Actual threaded Gateway success with captured ticks and forced post-only pair placement.
- Complete, clean-empty, partial, unresolved-row and ambiguous-cardinality responses.
- Changed-book rejection after rate wait and after signing.
- Deadline expiry during rate wait and each signing position, plus late complete, partial and unknown outcomes after POST begins.
- Caller cancellation and teardown, including repeated cancellation, during actual SDK signing and blocked POST.
- Pre-ack matched/confirmed fill replay, exactly-once confirmed inventory accounting, and resolution failure before and after owner update.
- Waiter failure while a fake POST is blocked, followed by draining and retaining the actual acknowledgment.
- Opted-in POST exception sanitization with a failing logging handler, and legacy default generic-failure behavior.

## Tests Executed

All pytest invocations used a global DNS/socket guard installed before importing pytest. The guard source was:

```python
import socket
import sys

blocked = []

def deny(name):
    def _blocked(*_args, **_kwargs):
        blocked.append(name)
        raise AssertionError(f"network access blocked during guarded pytest: {name}")
    return _blocked

socket.socket.connect = deny("socket.connect")
socket.socket.connect_ex = deny("socket.connect_ex")
socket.getaddrinfo = deny("socket.getaddrinfo")
socket.create_connection = deny("socket.create_connection")

import pytest

result = pytest.main(sys.argv[1:])
if blocked:
    print(f"GLOBAL_NETWORK_GUARD_BLOCKED={blocked}", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(int(result))
```

The final full-suite command was:

```bash
uv run python /tmp/rdp_phase0_network_guard.py -q
```

The final focused cancellation/deadline/waiter command was:

```bash
uv run python /tmp/rdp_phase0_network_guard.py -q tests/test_rdp_placement.py -k 'submission_waiter_failure or submission_deadline_after_post_started or submission_interruption_drains_real_gateway or submission_deadline_during_rate_or_signing'
```

The composed-operation matrix command was:

```bash
uv run python /tmp/rdp_phase0_network_guard.py -q tests/test_rdp_placement.py -k 'authorized_initial_submission or initial_submission_defaults_denied or initial_submission_rejects_authority or categorical_failure_without_raw_sentinel or gateway_opt_in_place_error or second_guard_revalidates or actual_gateway_outcome_matrix or preack_confirmed_fill or submission_keeps_actual_owner_state or submission_interruption_drains_real_gateway or submission_deadline_during_rate_or_signing or submission_revalidates_changed_book_after_rate_wait or submission_deadline_after_post_started'
```

The affected integration and FL regression command was:

```bash
uv run python /tmp/rdp_phase0_network_guard.py -q --maxfail=1 tests/test_rdp_placement.py tests/test_execution.py tests/test_rdp_runtime.py tests/test_rdp_lifecycle.py tests/test_favorite_first.py tests/test_favorite_live.py tests/test_engine.py
```

Lint and whitespace checks:

```bash
uv run ruff check src/polymaker/execution/gateway.py src/polymaker/rdp_runtime.py tests/test_rdp_placement.py
uv run ruff check tests/test_execution.py --ignore I001,E731
git diff --check
```

## Test Results

- Final full suite: **1615 passed, 2 skipped**.
- Final focused cancellation/deadline/waiter matrix: **11 passed**.
- Composed-operation matrix: **29 passed**.
- `git diff --check`: passed.
- Global socket/DNS guard: **zero blocked access attempts**.

Failure history:

- The first focused run had five failures from a local import shadowing `PlacementNotAttempted`, producing `UnboundLocalError`; removing the local import fixed the implementation.
- An early pre-ack test injected only a `CONFIRMED` event and expected inventory to change. The existing processor requires matched-fill evidence before confirmation; the test now injects the matched event followed by its confirmation.
- The legacy diagnostic test initially assumed one logging capture path. Under combined tests the repository routes structlog through pytest logging capture instead of captured stdout. The test now checks stdout, stderr and pytest-captured logs; the corrected affected suite passed.
- A broader test run before that correction stopped at this new legacy diagnostic assertion. No production failure was reported by that run.

The 647-test affected suite passed before the final waiter-failure handling addition. After that source change, the focused 11-test matrix passed and the final full suite passed on the resulting tree.

## Regression Results

The 647-test affected run included RDP placement/runtime/lifecycle, execution Gateway, Favorite First/live and Engine tests. The final full suite includes all tests and passed with two existing skips.

A repository-baseline Ruff check showed three pre-existing findings in `tests/test_execution.py` (`I001` and two `E731`). The exact base-file verification was:

```bash
git show HEAD:tests/test_execution.py | uv run ruff check --stdin-filename tests/test_execution.py - --output-format concise
```

The changed test file passes Ruff with those existing rules ignored; changed production files and `tests/test_rdp_placement.py` pass without ignores.

## Git Diff Summary

- 4 tracked files changed: 1,291 insertions and 24 deletions.
- Final `git diff --check` passed.
- Existing untracked `research_outputs/` data remains untouched.
- No audit files or Git history were modified.

Final SHA-256:

```text
01321c410a842adef8ad825d1be4f6ad1e03a647d62cbf43cbf89f3548b4e0ef  src/polymaker/execution/gateway.py
64f025846274cbfbcbac92175147027b85b3db022b17daf9728bd38be1c93564  src/polymaker/rdp_runtime.py
24609cacf882ec70daf6cdb540cbfdde943eec880a4cc4b4ab28c8aa0f066cda  tests/test_execution.py
4067a2aff24de7683c87b19cbaa2e22b8b581f2dfe68c0a69ac0d9286760de0f  tests/test_rdp_placement.py
```

## Safety Checks

- All placement tests used fake SDK clients; no live HTTP or WebSocket access was performed.
- No real-money order or cancellation was performed.
- Gateway default behavior remains off for the new safe-diagnostics option.
- No send gate was weakened; authorization remains explicit and exact.
- SDK work is drained before ownership release or teardown closes the session.

## Known Limitations

DG-002–DG-012 remain open. This iteration does not add CLI activation, automatic cycle operation, cancellation/retry behavior, unknown-ID recovery or real-money validation.

## Deferred Findings

No unrelated findings were changed. Remaining delivery gaps are outside DG-001’s approved scope.

## Reviewer Attention

Please verify the deadline-triggered revocation and child-task drain paths, especially that a waiter failure preserves the child’s actual typed result exactly once. Verify that `PlacementNotAttempted` remains distinct from attempted-POST uncertainty and that the default Gateway diagnostic behavior remains compatible.

## Human Live Trading Performed

NO.

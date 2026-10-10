# ITER-0028 — Worker Implementation

## Status

IMPLEMENTED

Fix02 addresses only Reviewer rereview finding M3 under frozen ITER-0028 Architect Contract 01. DG-003 acceptance remains OPEN pending independent review of this exact tree. DG-002 and DG-004 remain OPEN. This report does not claim Reviewer PASS, CODE_READY, or MICRO_LIVE_READY.

## Architect Contract

Implemented within the frozen DG-003 contract and the ordinary M3 correction returned by `06_REVIEWER_REREVIEW_01.md`. The continuation completion must report capital certification only from current cumulative registered scope, complete retained owner proofs, current registry/fill evidence, and valid current close state. Report generation remains zero-I/O and does not release ownership. Known confirmed inventory remains available when capital cannot be certified, with uncertainty reported honestly.

No strategy semantics, external interfaces, or scope were changed. The prior Fix01 repairs for B1, M1, and M2 remain in place and were not revised.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- Verified HEAD: `ec6c3af559095fa911a14a7008d9a8ebeab70487`.
- The tree already contained the ITER-0028 implementation and Fix01 changes, immutable audit artifacts, copied Reviewer probes, Orchestrator-owned index/gap updates, and the preserved untracked `research_outputs/` directory. I inspected these and did not overwrite them.
- No staged changes were present. HEAD remained unchanged. No Git history operation was performed.

## Existing Components Reused

The completion reporter now reuses `RDPPlacementOwner`'s registered cumulative IDs, retained close proofs and attempt state, `UserEventProcessor`'s current registry/fill/store evidence, and the existing `project_rdp_capital()` validator. The projection is synchronous and in-memory. It performs no account read, SDK call, cancellation, submission, or owner release.

## Files Changed

This Fix02 modified:

- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_placement.py`

The cumulative authorized ITER-0028 implementation diff in the current tree also includes the existing Fix01 changes in `src/polymaker/execution/rdp_placement.py`, `src/polymaker/risk/rdp_capital.py`, and `tests/test_rdp_capital.py`. No other source or test file was changed in this repair. The Orchestrator-owned `DELIVERY_GAPS.md` and `ITERATION_INDEX.md` modifications and audit artifacts were preserved and not edited by this Worker stage.

## Implementation Summary

`_continuation_completion()` no longer accepts a retained cycle snapshot solely because its inventory equals the currently captured inventory. It now requires a nonempty current owner-registered run scope, a complete and released current attempt, and retained close proof for each acknowledged attempt. It then reprojects the full current run scope through `project_rdp_capital()` using current processor evidence and the retained scoped close proofs. Certification requires that projection to succeed and that its inventory agrees with the retained inventory capture; STOP and terminal-failure gates remain in force.

If the current owner is open/replacement-blocked, any proof is missing, registration/fill/store evidence contradicts closure, or projection fails, the result retains known inventory and reports capital as uncertified. A registered scope without a current certified projection is reported as unresolved exposure. Owner release remains exclusively in the existing close path.

Three durable integration regressions were added: stale initial projection during concurrent later-cycle denial with a live 5.3125 reservation; a locally resurrected owned order rejected as `CLOSED_ORDER_LOCAL_OPEN`; and a clean current close that remains certifiable. They assert zero additional POST/cancel on denied calls and the DNS/socket fixture reports no network attempts.

## Behavior Before

A concurrent denied continuation could report `capital_certified=True` from a two-order initial snapshot after the owner had registered four IDs and the subsequent pair held 5.3125 in live reservations. A different denied continuation could report certified capital after the processor observed a formerly closed owned order as LIVE; the existing projector rejected that close as `CLOSED_ORDER_LOCAL_OPEN`, but the completion helper only compared inventory.

## Behavior After

Capital certification is based on a revalidated projection of the entire current registered scope and retained proof chain. The concurrent case is uncertified while the current owner is replacement-blocked. The resurrected-order case is uncertified and unresolved while preserving known confirmed inventory. A clean fully closed current scope still certifies. These reporting paths do not read the network, submit, cancel, or release the owner.

## Tests Added / Changed

Added to `tests/test_rdp_placement.py`:

- `test_iter0028_m3_concurrent_denial_requires_current_cumulative_scope`
- `test_iter0028_m3_local_order_resurrection_invalidates_completion_capital`
- `test_iter0028_m3_clean_current_close_remains_certifiable`

The new tests reuse the real retained initial cycle, existing placement owner, actual fake SDK/gateway, account fixture, processor/store, capital projector, and pre-import DNS/socket-blocked pytest runner. One missing `OrderEvent` test import was found by the first narrow run and added before the passing rerun.

## Tests Executed

All commands ran from `/home/ubuntu/poly-maker`. Fake-client execution tests used `docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py`, which blocks DNS and socket access before test imports.

1. Reproduce the two independent M3 failures on the pre-fix tree:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:docs/development/ITER-0028/review_evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no docs/development/ITER-0028/rereview_01_evidence/iter0028_rereview01_certification_v2.py
```

2. First narrow run, before fixing the missing test import:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no tests/test_rdp_placement.py -k 'iter0028_m3'
```

3. Corrected narrow run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no tests/test_rdp_placement.py -k 'iter0028_m3'
```

4. Rerun the independent Reviewer M3 probes after the fix:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:docs/development/ITER-0028/review_evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no docs/development/ITER-0028/rereview_01_evidence/iter0028_rereview01_certification_v2.py
```

5. Affected RDP placement/capital/runtime/marketdata suite:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no tests/test_rdp_placement.py tests/test_rdp_capital.py tests/test_rdp_runtime.py tests/test_rdp_marketdata.py
```

6. Full suite:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no
```

7. Final tree check:

```bash
git diff --check
```

## Test Results

- Pre-fix independent M3 reproduction: **2 failed**, as expected for the defect. `test_concurrent_denial_does_not_certify_prior_scope` failed because the result incorrectly certified the stale two-order snapshot over four current IDs. `test_resurrected_order_denial_not_capital_certified` failed because the result incorrectly certified after the current projector returned `CLOSED_ORDER_LOCAL_OPEN`. Full log: `/tmp/iter0028_fix02_pre_m3_probes.log`.
- First narrow run: **2 passed, 1 failed**. The failure was a test-only `NameError: name 'OrderEvent' is not defined` in the new resurrection regression. The test import was corrected. Full log: `/tmp/iter0028_fix02_narrow_m3.log`.
- Corrected narrow run: **3 passed, 421 deselected**. Full log: `/tmp/iter0028_fix02_narrow_m3_rerun.log`.
- Independent post-fix Reviewer probes: **2 passed**. They print `RESURRECTED DENIED False True False CLOSED_ORDER_LOCAL_OPEN 1`; the concurrent denial rejects stale capital, and both calls preserve zero extra POST/cancel. Full log: `/tmp/iter0028_fix02_m3_probes_fixed.log`.
- Affected RDP suite: **623 passed in 85.22s**. Full log: `/tmp/iter0028_fix02_affected_rdp.log`.
- Full suite: **1703 passed, 2 skipped in 103.32s**. Full log: `/tmp/iter0028_fix02_full_suite.log`.
- `git diff --check`: **passed**, exit code 0.
- All guarded test runs ended with `REVIEW_NETWORK_ATTEMPTS []`.

## Regression Results

The two independent M3 reproductions now pass, and the clean-current-close positive regression remains certifiable. The complete affected and full suites pass on the final source/test tree. The initial expected reproduction failure and the first-run test-import mistake remain recorded above and in their exact logs; neither is omitted.

## Git Diff Summary

- Branch remains `rdp-phase0-dev`; HEAD remains `ec6c3af559095fa911a14a7008d9a8ebeab70487`.
- Cumulative authorized implementation diff currently spans five source/test files: 1818 insertions and 55 deletions relative to HEAD. Fix02 itself changed runtime reporting and placement tests only.
- The working tree also contains Orchestrator-owned changes to `docs/development/DELIVERY_GAPS.md` and `docs/development/ITERATION_INDEX.md`, immutable prior iteration artifacts, copied reviewer probes, and the preserved untracked `research_outputs/` directory. These were not modified or removed by this Worker stage.
- No staged changes. `git diff --check` passes. `AGENTS.md` and `docs/strategy/RDP_PHASE0_FROZEN.md` are unchanged.
- No commit, push, merge, rebase, reset, tag, or other Git history operation was performed.

## Safety Checks

- The capital revalidation uses only the existing in-memory processor, owner, retained scoped proofs and projector. No account read or other runtime I/O was introduced.
- Fake SDK tests confirmed denied/report-only paths make no additional POST or cancel call. Network attempts remained empty under the pre-import DNS/socket blocker.
- No real HTTP/WebSocket connection, real order, real cancellation, credential access, or `main` merge occurred.
- Existing human authorization, live-send, gateway, cancellation/reconciliation, and STOP controls were not weakened. `execution_authorized` remains false on retained completion results.

## Known Limitations

- This repair changes only completion capital reporting. It does not close DG-003 pending independent re-review and the required contract acceptance review.
- As already disclosed in Fix01, the repeated-cycle integration harness does not separately inject every later-attempt partial/unknown/interrupted POST, held later cancellation/FillWindow teardown, and later-cycle zero-fill invalidation combination. Existing initial-path tests exercise reused lifecycle/gateway behaviors, but they are not claimed as direct subsequent-attempt coverage.
- No real-market or live validation was performed.

## Deferred Findings

- DG-002 cumulative-capital operation for repeated REBALANCE submissions remains OPEN.
- DG-004 deficient-side REBALANCE operation/recovery remains OPEN.
- Other delivery gaps remain as recorded by the current delivery register. No unrelated findings were changed.

## Reviewer Attention

Please verify both independent M3 scenarios against the actual tree, including that completion revalidation consumes all current registered IDs and proof/current registry state, that local LIVE resurrection remains uncertified/unresolved, and that clean complete current close remains certifiable. Confirm the result-reporting projector path performs no network/account I/O and no owner release. The preserved expected failure/recovery logs are `/tmp/iter0028_fix02_pre_m3_probes.log` and `/tmp/iter0028_fix02_m3_probes_fixed.log`; durable regressions are listed above. Prior B1/M1/M2 repairs remain unchanged.

## Human Live Trading Performed

No.

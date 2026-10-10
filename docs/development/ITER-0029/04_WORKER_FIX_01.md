# ITER-0029 — Worker Implementation

## Status

IMPLEMENTED

This is Worker Fix 01 for the immutable Reviewer `RETURN_TO_WORKER` findings B1 and M1–M3. The implementation is ready for independent re-review; it does not claim Reviewer PASS or close DG-004.

## Architect Contract

Implemented under the unchanged, approved [ITER-0029 Architect Contract](docs/development/ITER-0029/01_ARCHITECT.md) and immutable [Reviewer report](docs/development/ITER-0029/03_REVIEWER.md). The review identified repair-path terminal safety/result composition and truthful inventory/close evidence defects. Fix scope remained the already authorized retained runtime and placement tests; no strategy semantics, external interfaces, or iteration scope changed.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Base HEAD: `3286e56ac4e8ac3f72ccf0edda7722d9dcbc3827`
- Final HEAD: unchanged at `3286e56ac4e8ac3f72ccf0edda7722d9dcbc3827`; no Git history operation was performed.
- The source/test implementation from the original ITER-0029 Worker remains an uncommitted diff alongside this repair. The Orchestrator also made additive audit-document changes in the shared working tree; those were not Worker edits. Pre-existing `research_outputs/` remains preserved.
- Network-blocking pytest runner: `docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py`, SHA256 `bac8efb151d4e69c33bad9ef345bb9ad1c6d4e412aea38d0ec6d2a7e80fe9641`.

## Existing Components Reused

The repair reuses the retained `RDPInputSession`, placement owner, actual registered-order filter, processor inventory, existing terminal cleanup/drain task, close coordinator, cumulative accounting projection and structured log events. Tests reuse the actual fake official SDK client and existing `ExecutionGateway` path, including its worker-thread `_io` behavior for held POST cases.

## Files Changed

This Fix 01 added production changes only in `src/polymaker/rdp_runtime.py` and durable regression tests only in `tests/test_rdp_placement.py`. Their final SHA256 values are respectively `0b01182d5068fb91610fa5dd5709a726e817833e1fe424638c36f1da2c07363d` and `ccebd1d78ced1250b89d896005bac8769b50b26504bb20674d424992cba6ad61`.

The full ITER-0029 source/test diff also contains the original authorized implementation changes in `src/polymaker/execution/gateway.py`, `src/polymaker/execution/rdp_placement.py`, `src/polymaker/risk/rdp_capital.py`, `tests/test_execution.py`, and `tests/test_rdp_capital.py`; those files were not modified during this Fix 01 round. No Frozen SOP, strategy source, configuration, dependency, shared core, CLI or external runtime interface was changed by the Worker.

## Implementation Summary

The REBALANCE continuation now catches ordinary failures at the operation boundary, latches terminal STOP, drains submitted SDK work, and runs the existing scoped cleanup for the current REBALANCE attempt. Once submission has entered the SDK, the operation consumes the terminal/interruption result before attempting to monitor or place again. Deadline results remain the primary `ORDER_OPERATION_DEADLINE` outcome; caller cancellation and teardown preserve cancellation semantics.

The four frozen STOP categories are emitted as the public terminal reason: `TIME_EXHAUSTED`, `CAPITAL_INSUFFICIENT`, `MARKET_NOT_TRADABLE`, or `STATE_UNRELIABLE`. Specific causes such as `INSUFFICIENT_CURRENT_CASH` and `OPERATION_CLOCK_FAILED:RuntimeError` remain separately retained in `_terminal_stop_detail` and structured diagnostics. The existing NORMAL path and its retry policy are unchanged.

Terminal inventory derives from actual registered matching ownership. When a repair acknowledgement fails before registration, already confirmed inventory and its acquisition cost remain visible as a clean registered subset; the acknowledgement remains unregistered/uncertain, total exposure remains unresolved, and capital is not certified. STOP evidence now derives close IDs/completion only from the current repair attempt. An incomplete cleanup has `close_complete=false` and no `close_proof_attempt_id`; it cannot inherit a preceding NORMAL close proof.

## Behavior Before

The independent P2 probe found held POST deadline and teardown paths that did not reliably preserve/drain the primary operation result, outer operation clock failure that could return an exception and leave re-entry possible, monitor clock/quarantine failures without correct cleanup, and an initial capital rejection reported as a noncanonical detail. E3 found a pre-registration repair acknowledgement caused known prior QY=5 and acquisition cost 2.75 to disappear from completion output. A terminal STOP event could attribute an earlier attempt's close IDs/proof to the current repair.

## Behavior After

Held POST deadline, caller cancellation and teardown wait for SDK drain before current registered cleanup. A clock exception before repair entry yields a latched `STATE_UNRELIABLE` terminal result with diagnostic cause retained and no POST; further risk entry is denied. Nonfinite monitor clock or quarantined evidence closes the actual registered repair order and returns terminal state. Initial capital insufficiency yields `CAPITAL_INSUFFICIENT` while retaining `INSUFFICIENT_CURRENT_CASH` as its detail.

Registration-before failure preserves the known QY=5/QN=0 inventory and 2.75 acquisition cost using only registered IDs; it preserves the unregistered acknowledgement as unresolved, sends no speculative cancel, and does not certify capital. Current STOP evidence no longer reports a complete earlier close for this repair.

## Tests Added / Changed

Added five durable fake-SDK integration tests under `tests/test_rdp_placement.py` covering held SDK POST deadline/caller-cancel/teardown drain, unreadable outer clock plus denied re-entry, nonfinite monitor clock, quarantine cleanup, and pre-registration acknowledgement with known-subset inventory/current-close evidence. Extended the existing four-case REBALANCE safety-stop integration matrix to assert that its public result category matches the retained STOP category. Added a direct initial cash-rejection test for category/detail separation.

The pre-existing healthy exact-cash and NORMAL compatibility tests remain in place and pass. Reviewer P2/E3 scripts were rerun unchanged as independent probes; their results supplement but do not replace durable tests.

## Tests Executed

All commands ran from `/home/ubuntu/poly-maker`, with bytecode disabled and the pre-import DNS/socket-blocking runner listed above.

Focused durable regression command:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no tests/test_rdp_placement.py -k 'iter0029_fix01 or rebalance_safety_stop_closes_scope'
```

Reviewer P2 and E3 probes:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp/ITER-0029-review-evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no /tmp/ITER-0029-review-evidence/probes_v2.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp/ITER-0029-review-evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no /tmp/ITER-0029-review-evidence/extra_v3.py -k 'not normal_proof_order_variation'
```

Affected integration files:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no tests/test_rdp_placement.py tests/test_execution.py tests/test_rdp_capital.py
```

Broader RDP regressions and full suite:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no tests/test_rdp_*.py tests/test_execution.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no
```

Final tree checks:

```bash
git status --short
git ls-files --others --exclude-standard
git diff --stat
git diff --check
```

## Test Results

- Final focused durable regressions: **12 passed, 440 deselected**.
- Final independent P2: **9 passed**; final independent E3: **3 passed, 1 deselected**.
- Affected placement/execution/capital integration tests: **649 passed**.
- All `tests/test_rdp_*.py` plus `tests/test_execution.py`: **1119 passed in 91.64s**.
- Full suite: **1739 passed, 2 skipped in 100.75s**.
- Each final runner log ends `REVIEW_NETWORK_ATTEMPTS []`.
- Final `git diff --check`: exit 0, no output.
- The full run includes the previously intermittent NORMAL positive case; this run passes but does not establish a root cause for its earlier archived failure.

## Regression Results

The original independent P2 result was **6 failed, 3 passed**. Exact failures in `probes_v2_initial.log`: `test_repair_held_sdk_post_drains[deadline]` returned `RDPPlacementError` instead of the retained `ORDER_OPERATION_DEADLINE`; `[teardown]` returned `RDPPlacementError` instead of `CancelledError`; `test_repair_outer_clock_failure_latches` returned no result; `test_repair_monitor_unreliable_evidence_cleans[clock]` latched but made zero cancel calls; `[quarantine]` escaped with `ValueError`; `test_repair_initial_capital_predicate_stops` returned `TIME_EXHAUSTED` instead of a terminal capital stop. The corrected E3 result was **1 failed, 2 passed, 1 deselected**; `test_registration_before_preserves_prior_inventory` returned `inventory=None` despite actual registered prior QY=5/QN=0. After the repair, the same corrected independent P2/E3 probes pass, and the held close/fill mutation cases continue to pass.

Intermediate implementation runs are retained without rewriting: P2 fix1 was 3 failed/6 passed (`[teardown]` returned normally instead of raising `CancelledError`; outer clock returned no result; initial-capital logging raised `AttributeError: 'NoneType' object has no attribute 'closed_order_ids'` at both guarded result accesses). P2 fix2 was 1 failed/8 passed (`test_repair_initial_capital_predicate_stops` expected canonical `CAPITAL_INSUFFICIENT`, got `INSUFFICIENT_CURRENT_CASH`); P2 fix3 was 9 passed. The first durable regression run was 2 failed/5 passed: `test_iter0029_fix01_unreadable_rebalance_clock_latches_before_reentry` expected `STATE_UNRELIABLE` but got `OPERATION_CLOCK_FAILED:RuntimeError`; the M3 test initially expected prior-cycle IDs in the current-attempt `registered_order_ids`, which is empty for a pre-registration failure. The next runs were 1 failed/6 passed (`close_complete` correctly observed `False`, test had expected `None`), then 1 failed/6 passed (`close_proof_attempt_id` incorrectly reported current attempt 2 for incomplete cleanup; production logging was corrected). Runs then passed 7 and final 12 cases. Each intermediate failure was diagnosed and fixed within the authorized scope; exact logs remain immutable below.

The original archived intermittent NORMAL failure `CLOSE_PROOF_NOT_RELEASED` remains unexplained and was not addressed by this fix. The broad RDP and full suite passed, but that does not prove why the earlier failure occurred.

## Git Diff Summary

HEAD and branch remain unchanged. The full uncommitted diff reports 9 modified tracked files (2 Orchestrator document files plus 7 authorized implementation/test files) and 2298 insertions, 37 deletions in the shared tree; the Worker Fix 01 incremental production/test changes are confined to `rdp_runtime.py` and `test_rdp_placement.py`. `git status` also shows additive Orchestrator audit artifacts and the pre-existing untracked `research_outputs/`; none were removed or rewritten. Full status and untracked-file evidence is in `final_tree_checks_01.log`.

## Safety Checks

- No real-money order or cancel was sent; no live account was used.
- Test SDK calls use deterministic fake clients; DNS and socket connections were blocked before test imports. Final probe and suite logs report zero attempted network calls.
- Existing authorization, gateway, owner registration, close barrier, SDK drain and no-speculative-cancel boundaries remain active.
- No commit, push, merge, rebase, reset or Git history operation was performed.
- No main-branch merge or Frozen SOP edit occurred.

## Known Limitations

This is fake-SDK evidence only; it does not validate venue behavior or live execution. The previously recorded intermittent NORMAL proof-release failure remains a diagnostic uncertainty as the Reviewer requested. DG-004 remains OPEN pending independent re-review of the exact final tree; DG-002 and other delivery gaps remain open. No readiness or live authorization is claimed.

## Deferred Findings

The unexplained original NORMAL intermittent failure remains deferred for independent diagnosis; this repair did not alter unrelated NORMAL logic. Other gaps and findings outside the immutable Reviewer B1/M1–M3 scope remain deferred to their authorized iterations.

## Reviewer Attention

Please independently verify on this exact tree: canonical category versus detailed cause for each of the four STOP conditions; held POST deadline/caller cancellation/teardown drain ordering; clock and quarantine cleanup; preservation of known registered-subset inventory/cost with unresolved unregistered acknowledgement; and that STOP close proof fields come only from an actual complete current-attempt barrier. Confirm healthy exact-cash admission and the existing NORMAL path remain compatible. The Worker does not claim PASS or DG-004 closure.

## Human Live Trading Performed

No. No real order or cancellation was performed.

### Evidence log manifest

All commands and outputs are retained in `/tmp/ITER-0029-worker-fix-01-evidence/`; none of the prior logs were overwritten. The first independent reproductions and all intermediate failures are included.

| Log | SHA256 | Result |
|---|---|---|
| `probes_v2_initial.log` | `f5ba5334a93f89bf33a61de2f4e949e08d72c61842c890e6e2fbd3d37f924ad1` | P2 initial: 6 failed, 3 passed |
| `probes_v2_fix1.log` | `1f58413f176b6003f7035c511ec3d1b7750251d11b76db6c3f1b6f9a9f3fe70b` | 3 failed, 6 passed |
| `probes_v2_fix2.log` | `a467b02ffbec3011b6acdd0f3bbf8a907e62fbf2bd1183e20936c290502780d5` | 1 failed, 8 passed |
| `probes_v2_fix3.log` | `afb97400b970b61d3e7de4714167bf5ec50e628e5c5435f12989e014c0e4e07f` | 9 passed |
| `extra_v3_initial.log` | `7f3ce75411025e1f2a16a5082dfd23ae1bddfe73707cb71196eea01fcd57ef1b` | E3 initial: 1 failed, 2 passed, 1 deselected |
| `extra_v3_fix3.log` | `1c7521ab2c9b0cd2b5a0b724caaf259bd10cd1680d8171f37e931f4f180bca44` | 3 passed, 1 deselected |
| `durable_fix01_narrow_01.log` | `a71ec08f732a5c28dd53a50837bc26d9406aee4be3579cfdc220feaa88847ee3` | 2 failed, 5 passed |
| `durable_fix01_narrow_02.log` | `f5f5c651a1dd66ddcb83eea07216761c56dee5e02c57f4c8721b5b893571d179` | 1 failed, 6 passed |
| `durable_fix01_narrow_03.log` | `518cc6d371ae4f6dca1f71fe084cbd0ec1ada3e86e85212daec485aa01837df2` | 1 failed, 6 passed |
| `durable_fix01_narrow_04.log` | `f437e6c22fb585fd74d2d8168a32229cc5f230e0507ba22ee742746dbe74c844` | 7 passed, 444 deselected |
| `durable_fix01_narrow_05.log` | `6297c6d3ac7bd77ce700307bda4451a463d01ef3d5334de3b44bc1a193dd60bd` | 12 passed, 440 deselected |
| `reviewer_p2_fix01_01.log` | `4bda171adbb310627e92146e3703e02d540ca2e8fde2c21d067ce62c31ae7e8b` | 9 passed |
| `reviewer_e3_fix01_01.log` | `512bf7444256e9465ba490cd5135e625cbd245796c6067caf3fcd38c7556880c` | 3 passed, 1 deselected |
| `reviewer_p2_fix01_02.log` | `de8802cfa6cd8a752b458dc99461afa33017fea226cd327f3dfbd25c90546634` | final P2: 9 passed |
| `reviewer_e3_fix01_02.log` | `100cd96bb35d1028dc607ef867c60fe855bd619a2f331c18090a44dac9b93b9e` | final E3: 3 passed, 1 deselected |
| `affected_rdp_integration_01.log` | `365ba03928837443d7f4796514339276a3ddad322b8b16ed48f20cb3794cfe4e` | 649 passed |
| `rdp_regression_01.log` | `4c5006504fbaa622615df47bcda4fd0ed9bc99c2fec8eab39fbaa756194dc0b4` | 1119 passed |
| `full_suite_01.log` | `a9ab0945f89119d8bfac8045c04df297ed96431b6876a38cd3d6cfc76c82dd57` | 1739 passed, 2 skipped |
| `final_tree_checks_01.log` | `126ccc95b8fa80717963a24c81bdedbf765aaa7d8acf4e7c46fd3c772bb031e3` | status, untracked files, diff stat and `git diff --check` |

The evidence directory checksum manifest is `/tmp/ITER-0029-worker-fix-01-evidence/SHA256SUMS`, SHA256 `0098a538056a6178ad4030c1c3af7ae87932e0f8623ebc0dc84753eb5325690a`. The two corrected independent final probe logs above use the exact original Reviewer probe source files in `/tmp/ITER-0029-review-evidence/`; those source files and their immutable original failures remain preserved separately.

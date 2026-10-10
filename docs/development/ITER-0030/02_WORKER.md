# ITER-0030 — Worker Implementation

## Status
IMPLEMENTED

This reports implementation and evidence only. It does not claim independent Reviewer PASS or DG-002 closure.

## Architect Contract
Implemented the verification-first, production-unchanged contract in [01_ARCHITECT.md](/home/ubuntu/poly-maker/docs/development/ITER-0030/01_ARCHITECT.md), targeting DG-002. The added tests use an independent fake venue/account oracle to exercise retained production APIs across NORMAL, REBALANCE, and resumed NORMAL. No strategy or runtime behavior was changed.

## Base Repository State
- Branch: `rdp-phase0-dev`
- HEAD: `2d97e70fd16edba809b0754828f18adfd97249dd`
- Final tracked diff: one file, `tests/test_rdp_placement.py`
- The pre-existing untracked `research_outputs/` remains present and untouched (748 files).
- No Git history operation was performed.

## Existing Components Reused
The tests use the existing `RDPInputSession` admission/continuation APIs, `ExecutionGateway` executor and fake SDK seam, existing placement owner and user-event processor, existing inventory snapshots and capital/backing projections, account evidence types, feed processing, close/reconcile lifecycle, and ITER-0026 pre-import network-blocker runner. The fake oracle computes physical quantities, costs, commitments, cash, and allowance from its own scripted venue events; it does not use production projectors to produce expected account state.

## Files Changed
- `tests/test_rdp_placement.py`: added a local independent fake venue/account oracle and five ITER-0030 tests (including parameterized signs and mode/fault cases), preserving pre-existing test bodies.
- Evidence logs for attempts 03–06 and 08–25 are present in the working tree evidence directory; the attempt-07 baseline comparison log is unavailable here. No other tracked files changed.

## Implementation Summary
The new positive-path scenario uses the real gateway executor with a fake SDK. It starts at actual initial NORMAL submission, continues through subsequent NORMAL fills to both REBALANCE signs, leaves a partial repair resting for an account-monitor read, forces reprice/cancel/reconcile before replacement, repairs back to NORMAL, and observes an actual resumed NORMAL post. Feed confirmations are scheduled through the event loop after attributed registration.

Independent account captures compare cumulative QY/QN/CY/CN against actual retained production snapshots. The test checks partial-order remaining commitment, exact REBALANCE cash and allowance sufficiency, reprice chronology, and the unchanged $30 capital model. Bounded negative scenarios exercise unsafe account evidence at both next-action modes and a final SDK guard that rejects stale captured account evidence without posting.

## Behavior Before
Production had operating APIs for successive orders and mode transitions, but DG-002 lacked complete evidence against account state generated independently of the production processor/projectors. Existing common continuation fixtures could derive their account values from processor state, so they could not independently establish this agreement.

## Behavior After
Test evidence now drives unchanged production operating APIs with a separately scripted fake account oracle. Unsafe inputs are asserted to prevent subsequent POSTs. Production remains unchanged.

## Tests Added / Changed
Added:
- `test_iter0030_independent_account_oracle_crosses_normal_rebalance_and_resumes` (both repair signs).
- `test_iter0030_independent_account_faults_prevent_subsequent_risk_post` (stale receipt, position contradiction, closed-order resurrection, foreign commitment, insufficient cash, and insufficient allowance in both modes; NORMAL reserve boundary).
- `test_iter0030_physical_headroom_cannot_override_thirty_dollar_model`.
- `test_iter0030_rebalance_monitor_rejects_independent_remaining_size_mismatch`.
- `test_iter0030_stale_final_account_guard_denies_subsequent_normal_post`.

Acceptance mapping for Reviewer:
1. Both signs traverse actual initial NORMAL → subsequent NORMAL → REBALANCE → resumed NORMAL POST in one session/owner/processor.
2. Independent scripted fractional fills are compared to production snapshots at repeated account boundaries; fake feed reports MATCHED and confirmed evidence through the attributed asynchronous path. Existing replay and duplicate-confirmation coverage was exercised in the runtime-first regression selection.
3. Partial REBALANCE order remains live through monitor read; independently computed remaining notional is asserted; cancellation precedes replacement and resumed NORMAL POST.
4. $100 physical headroom cannot override the unchanged $30 model and $5 NORMAL reserve; insufficient cash and insufficient allowance deny POST; exact-sufficient REBALANCE backing is asserted at $2.35 / $2.975 for the tested repair-side candidates.
5. Foreign BUY commitment is counted once ($9 of a $10 account, leaving $1 support); it denies POST without cancellation. Positive-path owned repair reservation is checked against remaining size.
6. Stale receipts, selected-position contradiction, closed-order resurrection, foreign commitment, insufficient cash, and insufficient allowance are exercised at both mode boundaries. Independent remaining-size mismatch is explicitly exercised in the REBALANCE monitor only; Reviewer should determine whether existing NORMAL-side evidence is sufficient for this criterion.
7. Existing late-fill/replay/current-proof runtime-first selection passed (7 tests); the new positive chronology also verifies cancel/reconcile before replacement.
8. The actual final guard rejects newly stale account evidence; result is `NO_SEND`, `post_started=False`, and zero SDK POSTs. Existing terminal/known-inventory regressions are included in affected and full-suite runs.
9. Only the authorized test file changed; protected hashes match HEAD; affected and full suites pass; valid offline runner invocations report zero network attempts.

## Tests Executed
All test commands used the pre-import network blocker, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=src:tests`, and `TMPDIR=/var/tmp/poly-maker-iter0030-worker`, unless noted as shell/interpreter setup failures.

Successful final commands:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests TMPDIR=/var/tmp/poly-maker-iter0030-worker .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_runtime.py tests/test_rdp_placement.py -k 'late_prior_fill or later_confirmation_and_duplicate_replay or processor_change_during_final_open_read or stale_close_proof_after_local_order_reappears'
```
Result: 7 passed, 535 deselected, 2.15s; `REVIEW_NETWORK_ATTEMPTS []`.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests TMPDIR=/var/tmp/poly-maker-iter0030-worker .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_runtime.py tests/test_rdp_placement.py tests/test_rdp_capital.py tests/test_rdp_lifecycle.py tests/test_rdp_account.py tests/test_execution.py
```
Result: 832 passed, 109.43s; `REVIEW_NETWORK_ATTEMPTS []`.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests TMPDIR=/var/tmp/poly-maker-iter0030-worker .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests
```
Result: 1767 passed, 2 skipped, 127.85s; `REVIEW_NETWORK_ATTEMPTS []`.

`git diff --check` passed.

## Test Results
The final affected suite and full suite pass. Individual new scenario runs also passed:
- Cross-mode positive, both signs: 2 passed (attempt-19).
- Negative account fault matrix: 12 passed (attempt-10); a subsequent capital-focused matrix run also passed 14 cases before two then-unfixed fixture/clock issues (attempt-21).
- Cross-mode positive and physical-headroom/model-cap case: 3 passed (attempt-22).
- Final stale SDK guard: 1 passed (attempt-17).

The exact output logs retained under `docs/development/ITER-0030/worker_evidence/` are attempts 03–06 and 08–25, alongside raw logs in `/var/tmp/poly-maker-iter0030-worker/`. Attempt 07 was the clean baseline comparison and its 73-pass result was inspected, but that log is not present in the current evidence directory. Attempts 1 and 2 were not retained, so their exact output/commands cannot be attested. For attempts 3–22, the raw logs preserve pytest output, failures, and network footer; the exact original shell command text was not embedded in every log and is not reconstructed here. No valid runner attempt reported a network call.

## Regression Results
The runtime-first affected suite passed 832 tests, and the full suite passed 1767 with 2 skipped.

An earlier placement-first regression attempt (attempt-05) reported 901 passed and 11 failures in existing placement/runtime tests, including structured-capture/order-sensitive assertions. A clean baseline runtime-first selection and the same current runtime-first selection each passed 73 tests (baseline evidence was checked before this final run; current evidence is attempt-08). A narrower runtime-first current late-fill/replay/proof selection passed 7 tests. The order-sensitive attempt-05 failures were preserved, not suppressed or changed. The final affected/full suites ran runtime-first and passed; Reviewer should adjudicate this known alternate-order sensitivity.

Other preserved failed attempts document fixture development rather than production defects: initial confirmation delivery from the executor thread hit SQLite thread ownership (attempt-03); early final-guard attempts lacked current feed/clock evidence or mutated the wrong account capture (attempts 13–16); the first cross-mode chronology ended repair before the monitor read (attempt-18); and capital matrix attempts exposed incorrect test expected-side amount and an unbounded/stale-feed fixture (attempts 20–21). Each was followed by the corrected passing run noted above. Attempts 11–12 were command setup failures (`python: command not found`, then `pytest` unavailable under system Python) and are retained.

## Git Diff Summary
```
 tests/test_rdp_placement.py | 767 +++++++++++++++++++++++++++++++++++++++++++-
 1 file changed, 766 insertions(+), 1 deletion(-)
```

Final protected hashes match their HEAD blobs:
- `src/polymaker/rdp_runtime.py`: `cbe73dcf589ea2d7f14e1df3b300cd49991c40d4c93a5b9a52c699e4891b0c98`
- `src/polymaker/rdp_account.py`: `edbf3c9fc303d0666d78f97a32b3bfef87b991018a8cf9de10f1723dcf003efe`
- `tests/test_rdp_capital.py`: `687de05bd028554081f632c206d5b435cffea219d065fd8fcdd8e1c8b1344218`
- `tests/test_rdp_runtime.py`: `7d04de533142c8117ad020f0ae96def3e5dcedccc06eaa6a25562295b8132a44`
- `tests/test_rdp_lifecycle.py`: `1acf9b0769dc1c60852e1e89d2feb3b3c633c359f906cf334ed4d754e42995ab`
- `tests/test_rdp_account.py`: `1bf42bf62532f647e83beb01d935912efc5fd9d9bed36c1199d0095f0fb66dd5`
- `tests/test_execution.py`: `df6f2ea4ccfd4f1c4550d0cb7c5a243f4dfe3be6843fb01fe90bb9d58ddefb2a`

Modified file SHA-256: `tests/test_rdp_placement.py` = `f20fee03a998831ca9e044d715f7e10067ec778dc9dc738a2c4d17f43d0e3d7c`.

## Safety Checks
- No real HTTP/network was attempted in valid test runs; the blocker footer is empty.
- No real order, cancel, account operation, or market execution occurred.
- Production source, configuration, dependencies, Frozen SOP, and other reviewed tests remain byte-identical to HEAD.
- `research_outputs/` was preserved; no cleanup or modification was performed.
- No commit, push, merge, rebase, reset, or tag operation was performed.
- `git diff --check` passed.

## Known Limitations
- Independent remaining-size mismatch has an explicit new REBALANCE-monitor test, but not a matching newly added NORMAL-boundary case; this is called out under acceptance criterion 6.
- The successful broad runs use runtime-first ordering. An earlier placement-first run reproduced 11 order-sensitive existing failures; the exact failing log remains available for independent review.
- This Worker report is not an independent review. DG-002 remains OPEN until Reviewer establishes all Architect criteria and Orchestrator archives the resulting PASS.

## Deferred Findings
No unrelated production issue was modified. The retained alternate-order capture behavior is reported for independent Reviewer classification.

## Reviewer Attention
Please inspect the new oracle independence and executor event scheduling, exact Decimal boundaries, cancellation/reconciliation chronology, and acceptance criterion 6's NORMAL-side remaining-size coverage. Confirm the placement-first ordering failures against the preserved log and the runtime-first pass evidence. Do not infer DG-002 closure from this Worker report alone.

## Human Live Trading Performed
No. Fake SDK/fake account only; no real orders or cancellations.

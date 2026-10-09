# ITER-0027 — Reviewer Report

## Verdict

PASS

BLOCKER = 0. MUST_FIX = 0.

This PASS closes the approved **post-reconciliation next-action factual admission prerequisite** under DG-002. It does **not** close DG-002. Actual successive NORMAL/REBALANCE submissions must still consume current cumulative run inventory, confirmed cost, attributable commitments and fresh account evidence before each send. DG-002 remains OPEN; MICRO_LIVE_READY remains NO; real-money and main-merge authorization remain NOT_GRANTED.

## Reviewed Inputs

Independently reviewed the actual implementation and tests, rather than relying on Worker results:

- Actual repository AGENTS.md, Frozen `docs/strategy/RDP_PHASE0_FROZEN.md`, BASELINE.md, ITERATION_INDEX.md and DELIVERY_GAPS.md.
- Frozen `docs/development/ITER-0027/01_ARCHITECT.md` (APPROVED_FOR_IMPLEMENTATION), initialization and current-stage records.
- `ITER-0027/02_EXECUTION_INTERRUPTION.md` and verbatim `03_WORKER.md`.
- Approved reserve and filter decisions in ITER-0005/03_HUMAN_DECISION.md and ITER-0006/03_HUMAN_DECISION.md.
- Actual four-file implementation diff, changed tests, existing initial admission/submission/cycle/close/STOP ownership, account parser/gateway capture, capital projector/close validator, accounting, quote builders and Book Filter.
- Relevant untracked scope and the pre-import blocked test runner archived under ITER-0026/review_evidence.

Input SHA256:

| Input | SHA256 |
|---|---|
| AGENTS.md | `0dedaab0b6653bd030d01c494d42d10619727f0e9f78bed9703002816538aa25` |
| Frozen SOP | `92e455097c7567007ed8688faae81241588d77af2f614cd3f8a663486856d32d` |
| Architect01 | `c2291f33ef8b5d7dc049e382b7c76f9427be3fc7b917d818d0963d73d91776ec` |
| Interruption02 | `7bd5dc0a7813b352322d4cdc232cd5bf78de30b3135316eb6918114a699fdf28` |
| Worker03 | `006937a07293018eb7b0191edcaeee06d666fc4741fd7fa2af3717ee546467e7` |

## Repository / Diff Reviewed

Repository `/home/ubuntu/poly-maker`; branch `rdp-phase0-dev`; verified HEAD/base `b5bd9a854f43578f3baa3ae370fba0551129d526`. Reviewer performed no Git mutation and no source, test or repository-document edits.

Actual uncommitted source/test change: four authorized files, 1303 insertions and 12 deletions:

| File | Additions / deletions | Reviewed SHA256 |
|---|---|---|
| src/polymaker/rdp_runtime.py | 519 / 6 | `077c620176384ed4a337fb80dfa4c7b1d2ed0cbbac42b25714af840ca7014ab3` |
| src/polymaker/risk/rdp_capital.py | 145 / 0 | `6b060412ee37c70ddb92b479b167c047aee461e7058c858db46693193ccc8f69` |
| tests/test_rdp_capital.py | 108 / 1 | `b0c734a15c00dff9244de16d5549a4b36c67129dcd2db76aaa4acc159ba53ea5` |
| tests/test_rdp_placement.py | 531 / 5 | `04d6b207ce31bfb8e24fb58da45bbdd2aa84d2bd1888760b4764cabca9765d7e` |

These hashes were computed directly and rechecked after tests. The complete tracked diff additionally contains Orchestrator changes to index/gap current-stage narrative only (REVIEWER, Worker IMPLEMENTED, review pending). These do not close DG-002 or claim prior PASS. Untracked files comprise the two current iteration artifacts plus the preserved 748-file research_outputs tree. The research output is unrelated pre-existing data, not an execution dependency or implementation addition. No staged change, protected-file change or unexpected implementation path was found. `git diff --check` passed.

## Acceptance Criteria Review

| Frozen contract criterion | Independent conclusion and evidence |
|---|---|
| 1. Actual integrated starting state | Satisfied. `_complete_cycle_with_confirmed_fill` arms/resolves an actual acknowledged pair, observes confirmed events and completes the existing session cycle through real lifecycle/gateway code backed by fakes. Admission obtains the session's retained completion and current owner; the API takes no caller inventory, scope, mode or close proof. |
| 2. Nonempty fractional NORMAL | Satisfied. Confirmed 1.25 YES at .55 retains CY/InventoryCost .6875 and FreeCash 29.3125; candidate sizes are 5 YES/6.25 NO. Current physical cash 11 supports the candidate plus protected $5 without initial-empty rejection. |
| 3. Both REBALANCE boundaries | Satisfied. Actual +5 and -5 completed cycles produce deficient-side BUY5 at current ask minus tick. Independent .02 metadata/book update yields .46 NO rather than cached .47. Minimum size 6 rejects. |
| 4. Current physical backing | Satisfied. Existing strict account evidence validation and exact commitment arithmetic precede the current evaluator's inventory comparison. Account-wide BUY commitments are subtracted once from cash and allowance. Confirmed cost remains exclusively in the independent $30 model. Low cash/allowance and NORMAL reserve boundaries are covered. Independent both-sign REBALANCE probes with physical balance/allowance 3 and rejecting/not-ready NORMAL filter remain eligible without protected NORMAL reserve. Higher observed balance does not reset model capital. |
| 5. Scope and close safety | Satisfied. Exact retained completion/owner/market/attempt, reliable complete barrier, certified retained close, registry agreement and local processor/store scope are required. Existing projector validates present fill evidence, proof-covered absence, local resurrection and exact close inventory. Independent mutations after either official await reject STOP, unresolved owner, registry change and resurrected local order. Pending, quarantine, FAILED and late confirmed evidence reject in repository integration tests. |
| 6. Freshness and coherent capture | Satisfied. Market/account identity, timestamps, status, end time, health, public/user generations, backlog, current book identity/generation/age and dynamic metadata are validated after reads and at final capture. Raw malformed collateral/allowances with nonempty positions cannot bypass parser validation. Contradictory selected positions never supply inventory or acquisition cost. |
| 7. Serialization and drain | Satisfied. Active admission is serialized against competing admission and initial submission/cycle/close/prefill/capture paths. The owned task shields/drains child reads; teardown sets closed before draining admission and resource close. Independent repeated cancellation during a blocked account await waits for release, retains STOP rejection and clears ownership/task flags; a competing authorized initial submission starts no POST. Repository tests independently cover status-read cancellation and teardown. |
| 8. No new trading authority | Satisfied. New operation calls only status/account reads, pure projections/builders and logging. Both eligible/rejected results have frozen `execution_authorized=false`; explicit time/final-submit/human gates remain outstanding. Independent probes preserve current placement owner, owned registry, pending-capture absence and prior fake cancellation counters. No sign/post/cancel/reconcile/release path is introduced. |
| 9. Compatibility/evidence | Satisfied. Independent targeted check, additional actual-runtime probes and the exact-tree full suite passed under pre-import network blocking. Scope/untracked/protected-state checks passed. The earlier waiter-test failure and correction are disclosed and independently inspected. |

This prerequisite is necessary now: empty initial admission rejects retained inventory and cannot represent current spendability, while safe successive actions require the existing $30 allocation and confirmed costs to survive reconciliation. Integrated criteria 1–3 establish the needed runtime boundary; helper tests alone were not used to claim completion. The still-open delivery condition is actual successive-send consumption of this evidence, including cumulative prior fills and partially resting reservations. No cumulative-cycle framework or send loop is claimed here.

## Existing Codebase Compatibility

Worker reuses the existing retained RDP session and placement owner, confirmed processor evidence, account reader/parser, strict official gateway surface, capital projector and close-proof validator, CapitalGate, pure quote builders and retained market-data/Book Filter components. The two new immutable results distinguish current factual backing from initial empty-run backing and do not keep a parallel capital or inventory ledger.

No existing subsystem is duplicated or unnecessarily replaced. Initial admission, one-shot submission flags, provenance, SDK guards, close ownership, terminal latch and existing caches remain intact. Added conflict checks prevent older public operations from overlapping the new read operation. The projector, quote builders, gateway, lifecycle, shared store/tracker, CLI, configuration and dependencies are unchanged. Existing FL/IM/shared functionality passed the full regression suite.

## Findings

### BLOCKER

None.

### MUST_FIX

None.

### NOTE

1. PASS is deliberately a partial DG-002 prerequisite. It is not proof of successive-order execution, an operating REBALANCE loop, complete CanSafelyContinue, full market orchestration or MICRO_LIVE_READY. The existing stable gaps retain those acceptance conditions.
2. The current account comparison uses the documented existing float-evidence tolerance of 0.000001 share. It does not adopt account positions or use their average prices as confirmed cost.
3. The independent scratch-probe first collection attempt used the wrong gateway module import and failed before test execution. A separately created v2 corrected the probe import; v3 added further checks. This was a Reviewer harness error, not a repository defect. The original scratch file/log were preserved.

## Frozen SOP Compliance

Frozen SOP and approved human decisions are unchanged. NORMAL uses confirmed Delta, inventory skew, dual maker pricing, current Price Gate/Book Filter and protected $5 repair capacity. REBALANCE uses the correct deficient side, fixed five-share clip, current ask minus dynamic tick, post-only legality and existing model capital constraints without NORMAL filter/reserve restrictions. No quote quota, no-fill stop, hard rebalance duration or new strategy state is added.

The new decision follows successful Cancel → Reconcile → inventory evidence. Incomplete/stale closure never creates new factual eligibility. STOP is honored before reads, after either await and before result publication.

## External Interface Review

No new external runtime interface, dependency, blockchain read, RPC or unofficial service. Existing official Gamma status and authenticated official account reads are used. Web3 address validation remains an existing local identity utility; this diff adds no network RPC use. All independent execution was offline with socket/DNS blocking installed before pytest/project import. No real network, signing, order or cancellation occurred.

## Trading Safety Review

The API admits a factual candidate only and grants no send authority. Its result always retains operational time sufficiency, final submission revalidation and deliberate human authorization as outstanding. Actual expiry/freshness checks are present without claiming a safe Order → Cancel → Reconcile time budget.

Scope cannot be supplied or broadened by callers. Unreliable placement/close/fill/order state rejects. Shared serialization prevents overlapping new-risk operations; the active flag also rejects the old SDK-intent path. Teardown closes the session first, drains owned read work and prevents later eligibility. Existing ITER-0026 termination behavior remains covered by the independent full suite; it is not reimplemented.

## Inventory / Capital Accounting Review

Confirmed processor fills remain the sole quantity and acquisition-cost authority. `project_rdp_capital` validates owned economics, fill state/checksums and attributable close proof, and retains Capital=30=FreeCash+ReservedCash+InventoryCost. In this fully closed initial-cycle prerequisite, no model resting order remains; their absence is supported by complete close outcomes and current local state, not by a fresh empty account list alone.

Physical cash and allowance are current official observations. Their account-wide BUY commitments are removed once, while prior confirmed cost is not deducted again from physical balance. Foreign orders and positions cannot augment RDP inventory or model capital. An independent foreign selected-token SELL probe contributes neither BUY commitment nor RDP cost. Observed physical cash 50 still leaves model FreeCash 29.3125 after .6875 confirmed cost. Repository capital tests cover independent cash/allowance boundaries and mode-specific reserves. Existing inventory/PnL formula implementations are unchanged.

## State Machine Review

Mode is derived from current confirmed Delta with the exact five-share boundary, compared with retained close classification and built using existing pure builders. No new mode or autonomous transition/send loop is introduced. Both deficiency signs were exercised through actual retained cycle completion. New processor evidence after close causes reprojection/close-inventory rejection; stale evidence cannot silently admit a changed state. Result retention is evidence, not owner release or pending capture.

## Test Review

Independent commands ran from `/home/ubuntu/poly-maker` with pre-import socket/DNS blocking:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_capital.py tests/test_rdp_placement.py -k 'current_backing or post_cycle_next_action or submission_waiter_failure'
```

Result: **36 passed, 444 deselected in 2.08s**, `REVIEW_NETWORK_ATTEMPTS []`.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q /tmp/iter0027_reviewer_probes_v3.py > /tmp/iter0027_reviewer_probes_v3.log 2>&1
```

Result: **22 passed in 1.87s**, `REVIEW_NETWORK_ATTEMPTS []`. One existing websockets legacy deprecation warning from the isolated scratch run; no failure. Probes independently exercise raw malformed balance/allowance with nonempty selected inventory, required-spender absence, conflicting normalized duplicate allowance, extra malformed allowance, both await-boundary safety mutations, both-sign low-backing REBALANCE/filter exclusion, foreign selected SELL accounting isolation, coherent dynamic tick changes, minimum rejection and repeated account-read cancellation/STOP/competing submission.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q > /tmp/iter0027_reviewer_full.log 2>&1
```

Result: **1683 passed, 2 skipped in 83.76s**, `REVIEW_NETWORK_ATTEMPTS []`. The two existing live-market-data tests are intentionally disabled unless POLYMAKER_LIVE=1; no offline implementation test was excluded. This exact-tree full run includes capital, placement/runtime, affected prerequisites and FL compatibility; duplicative separate broad runs were unnecessary after its success.

Additional inspected checks: `git status --short --branch`, `git rev-parse HEAD`, recent graph, `git diff`, `git diff --numstat`, `git diff --name-status`, `git diff --check`, `git ls-files --others --exclude-standard`, direct pre/post test SHA256 verification. No current failure remains.

The waiter-failure test correction adds an asyncio event immediately before the injected waiter raises; the test waits for that event before releasing fake POST. Event.set has no await before the raise, ensuring injection has executed before the test can resume. Expected interruption/terminal assertions remain intact; no production behavior or assertion was weakened. Its exact branch passed in the independent targeted and full runs.

Scratch evidence SHA256:

| Evidence | SHA256 |
|---|---|
| /tmp/iter0027_reviewer_probes_v3.py | `d32f689a178a8692b62ee185575228a85cc9ca999cf791b8e65ed4184dc0ec0d` |
| /tmp/iter0027_reviewer_probes_v3.log | `bf40e554d15efa8ceda694a51a08b91706498ecb9e46d3fea425d1e276fab71e` |
| /tmp/iter0027_reviewer_full.log | `c903ba538274d41b15c06b052c2e1dcf3076fb020409a5d003d35b5e442f7f60` |

## Scope Review

One frozen delivery prerequisite implemented in exactly the authorized four implementation/test files. No out-of-scope source, test, runtime interface, configuration or dependency change. No prior iteration reimplementation. Orchestrator current-stage documentation changes and the interruption/Worker artifacts were reviewed separately. Pre-existing research data was preserved. Specialist role separation, immutable prior audit history and read-only independent review were maintained.

## Residual Risks

Official account/status reads are sequential and may lag confirmed evidence. Disagreement remains ineligible; this review does not prove live API behavior. The factual result can become stale after return and therefore cannot be reused as final-send authority. Actual subsequent-send guards, cumulative successive-cycle consumption, active NORMAL/REBALANCE operation, operational time budgeting, market termination/resolution and readiness/live validation remain required in their existing gaps. These are explicit contract non-goals, not deferred defects within this implementation.

## Final Decision

PASS for the exact reviewed four-file implementation hashes and repository state above. All frozen ITER-0027 prerequisite acceptance criteria are satisfied with integrated runtime evidence and independent offline checks. No architecture revision or Worker repair is required.

Orchestrator may persist this report verbatim as ITER-0027/04_REVIEWER.md and complete its prescribed RESULT/index/Git archival. **DG-002 remains OPEN** until actual successive sends satisfy its full registered acceptance condition. No real-money trading or main merge is authorized.

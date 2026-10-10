# ITER-0030 — Reviewer Report

## Verdict

RETURN_TO_ARCHITECT

DG-002 remains OPEN. BLOCKER = 1; MUST_FIX = 2. The unchanged production implementation permits a subsequent new-risk POST while an independently reported foreign BUY order exists on a selected-market token. This violates Architect01 acceptance criterion 5. Production repair is outside the initial verification-only contract and therefore requires an additive Architect revision before Worker resumes.

Passing current tests do not establish complete DG-002 acceptance. Two additional positive tests pass without reaching the production condition they claim to verify.

## Reviewed Inputs

- `AGENTS.md`, `.codex/config.toml`, `.codex/agents/reviewer.toml`.
- `docs/strategy/RDP_PHASE0_FROZEN.md`, particularly confirmed accounting, $30 capital, NORMAL/REBALANCE, cycle sequencing and reliable-state requirements.
- `docs/development/BASELINE.md`, `ITERATION_INDEX.md`, `DELIVERY_GAPS.md`.
- `ITER-0030/01_ARCHITECT.md`, SHA-256 `096c492bef0bd35a3c2bf9f485bad4c32a2569ea92765b1553a1ce12f61272c6`.
- `ITER-0030/02_WORKER.md`, SHA-256 `c4615056b0a4d7bba361140d69d6e2bcbbb58b056a9eb0b880a8b0cdc0bfd085`.
- Actual source: retained runtime admission/guards/NORMAL/REBALANCE monitor; current capital projection/backing; placement ownership; existing accounting, fake gateway and reconciliation test paths.
- Actual modified and relevant existing tests, Worker raw logs and `WORKER_EVIDENCE_ARCHIVE.md`. All 22 gzip copies were independently verified byte-for-byte against their decompressed raw logs.
- Archived ITER-0026 pre-import network blocker used for independent validation.

## Repository / Diff Reviewed

Branch: `rdp-phase0-dev`.
Base and reviewed HEAD: `2d97e70fd16edba809b0754828f18adfd97249dd`.
The local tracking ref `origin/rdp-phase0-dev` points to this same commit. Reviewer did not perform a remote operation or Git history operation.

Implementation diff: `tests/test_rdp_placement.py`, 766 additions / 1 deletion. SHA-256 `f20fee03a998831ca9e044d715f7e10067ec778dc9dc738a2c4d17f43d0e3d7c` remained unchanged throughout review. The deletion replaces an existing initial-admission await with a five-second `wait_for`; its existing assertions remain intact.

Orchestrator pending-state edits affect `DELIVERY_GAPS.md` and `ITERATION_INDEX.md`; they do not close DG-002. New Worker report, evidence manifest and compressed logs are untracked audit material. Staging is empty. `git diff --check` passes. Relevant ignored raw evidence was inspected on disk rather than omitted because Git does not list it.

Seven protected files explicitly compared byte-for-byte to HEAD are unchanged: `src/polymaker/rdp_runtime.py`, `src/polymaker/rdp_account.py`, and tests for capital, runtime, lifecycle, account and execution. The actual diff contains no production, configuration, dependency or Frozen SOP change.

Pre-existing `research_outputs/` was preserved. Reviewer changed no repository source, tests, configuration, documentation, old audit artifact or research data. New diagnostic scripts and raw evidence exist only under the parent-authorized `/var/tmp/poly-maker-iter0030-review/` scratch directory.

## Acceptance Criteria Review

| Criterion | Assessment | Actual evidence / limitation |
|---|---|---|
| 1. Complete actual cross-mode path | Substantially demonstrated | Both signs start at actual initial NORMAL submission, use the same retained session/processor/owner and real gateway executor, traverse subsequent NORMAL and repair, and reach a seventh actual batch. Strengthen mode, token-pair and guard correlation under M1 rather than relying only on ordinal POST counts. |
| 2. Independent cumulative economics | Demonstrated with existing supplemental tests | The oracle calculates quantities and acquisition costs from its own scripted fills and actual signed prices, without production projectors generating expected account state. Repeated current snapshots compare QY/QN/CY/CN. Existing MATCHED-only, replay and late-proof tests pass. |
| 3. Partial resting reservations and release | NOT SATISFIED | The positive scenario changes the ask in the first partial account capture. The monitor exits before its actual capital projection. A reviewer observation reports zero monitor projection calls in both signs. Oracle commitment arithmetic alone does not establish actual ReservedCash/FreeCash. M1. |
| 4. Model versus physical capital | Substantially demonstrated | Actual subsequent risk attempts exercise $100 physical backing versus the $30 model, NORMAL $5 reserve, independent cash and allowance denials, and exact REBALANCE backing ($2.35 / $2.975). Remaining full-path capital comparison belongs to M1. |
| 5. Account-wide commitments without adoption | FAILED | Foreign-token commitments are counted once and denied when backing is insufficient. However, selected-token foreign orders with sufficient backing are accepted and both modes actually POST. B1. Matching partial owned commitments are not independently checked against an executed monitor projection. M1. |
| 6. Unsafe evidence in both modes | Partial | Stale receipts, position contradiction and closed-order resurrection are exercised in both modes. The explicit remaining-size test is REBALANCE-only and asserts a generic terminal outcome; it should retain the factual failure and independently established confirmed-fill/remaining state. The selected-order scope hole at the NORMAL boundary is independently reproduced by B1. Do not claim complete unsafe-order acceptance from helper tests. |
| 7. Late fill / proof validity | Demonstrated by retained regression | Existing cancel-race, replay and late-proof cases compare actual retained inventory/cost and deny stale certification. Reviewer affected regression includes these tests. Their prior functionality was reused, not reimplemented. |
| 8. Final guard / honest output | NOT SATISFIED | The new account-staleness test actually rejects `MARKET_STATUS_STALE` before account backing is reevaluated. Its generic NO_SEND assertion passes without testing captured-account expiry. M2. Existing terminal known-inventory and uncertified-output regressions pass. |
| 9. Reuse / compatibility / evidence | Partial | Production unchanged; independent new acceptance18 and affected832 pass with zero network attempts. Worker full1767/2 evidence is retained. Earlier failed attempts and missing command/log limitations remain disclosed. Complete delivery acceptance still fails B1/M1/M2. |

This is neither complete closure nor a new independently accepted partial prerequisite: the necessary full acceptance remains open while defects are repaired within ITER-0030.

## Existing Codebase Compatibility

Worker reuses the retained runtime APIs, one placement owner, confirmed-fill processor, existing capital/backing gates, existing real gateway executor and fake SDK seam. There is no parallel production strategy, ledger, gateway or runtime dependency. The test oracle extends the existing fake client and computes only venue/account expectations.

The required B1 correction should extend the existing shared current-backing scope validation rather than create another execution or ownership subsystem. Existing owned active repair orders must remain legal when independently proved; unrelated-token commitments must still be accounted for without being adopted or cancelled.

## Findings

### BLOCKER

**B1 — Selected-market foreign BUY orders do not block subsequent new risk.**

Locations: `src/polymaker/risk/rdp_capital.py:243`, especially acceptance of `SELECTED_STATE_NOT_EMPTY` and the all-orders loop around lines 338–348; `src/polymaker/rdp_runtime.py:2193`, `:4184`, `:4359`.

`evaluate_current_rdp_backing()` deliberately permits nonempty selected positions for cumulative owned inventory. It subsequently validates registered run-order IDs and active scope, but does not reject a selected-token order whose ID is outside the run. Such an order is charged as an account commitment and otherwise accepted. Both subsequent admission and the final SDK guard reuse that acceptance.

Reviewer independently supplied a current `foreign-selected-buy` BUY on `yes-token`, price $0.50, remaining 1 share, consistent selected/all-order evidence and ample physical backing. Starting from retained confirmed inventory of 1.25 YES (NORMAL) or 5 YES (REBALANCE), the actual public continuation APIs produced:

- NORMAL: `admissions [(True, None)]`, `posts 1`, actual complete dual acknowledgment and `post_started=True`.
- REBALANCE: `admissions [(True, None)]`, `posts 1`, actual complete repair acknowledgment and `post_started=True`.

The reviewer probe then advances the fake market clock **only after the fake SDK POST** to bound the run and invoke existing cleanup. That later terminal outcome does not undo the preceding unsafe admission. No real venue operation occurs. The probe expects zero POST and fails in both modes: **2 failed, 62 deselected, exit 1, network attempts []**.

Evidence: `03-foreign-selected-probe.log` and `test_foreign_selected_probe.py` in reviewer scratch. The log retains successful admission, both final factual guards, POST-started output and the failing zero-POST assertions.

Minimum correction: Architect authorizes a local extension of existing current-backing/scope validation rejecting selected-market foreign order IDs before new risk, with both-mode integration evidence for no POST, no adoption and no foreign cancellation. Preserve valid owned resting repairs and unrelated-token commitment behavior. This is an existing required safety condition, not a new strategy decision. Production changes require an additive contract revision; do not defer this defect or weaken criterion 5.

### MUST_FIX

**M1 — The independent positive partial-resting scenario bypasses the projection it claims to verify.**

Locations: `tests/test_rdp_placement.py:11078`, `:11207`; `src/polymaker/rdp_runtime.py:1755` through `:1797`.

At POST5, `independent_capture()` immediately changes the repair ask. After the account await, the actual monitor rebuilds the quote and returns `close_for(None)` when it differs from the pre-read candidate. That return precedes `project_rdp_capital()` and `evaluate_current_rdp_backing()`. The test subsequently compares only its oracle's commitment to its oracle's own remaining quantity. It never compares a successful monitor projection's ReservedCash/InventoryCost/FreeCash against that independent oracle.

A pass-through reviewer observation of runtime capital calls confirms `MONITOR_CAPITAL_CALLS []` for **both** positive parameterizations, while the tests still pass. Evidence: `05-partial-projection-observation.log`, with `observe_capital_plugin.py`. The wrapper calls the original production function and only records its returned data/caller; it neither fabricates success nor changes accounting.

Minimum repair: keep the partial repair and quote unchanged through at least one actual successful monitor projection/backing evaluation; use events to establish that boundary before triggering reprice. Inspect the original returned production outputs and assert exact independent remaining reservation, cumulative confirmed cost, FreeCash and $30 identity, account commitment counted once, and scoped proof release. Correlate each actual POST with its retained mode/admission/final guards and explicit cancel → reconciliation → replacement chronology. Do not substitute a manually assembled projection or oracle self-comparison.

**M2 — The final-account-staleness test passes on market-status staleness instead.**

Location: `tests/test_rdp_placement.py:11609` through `:11640`.

The signing callback advances `_now` by 100 seconds. The retained market-status receipt therefore expires before the guard reaches account evaluation. Independent logged execution records `guard_reasons=(None, 'MARKET_STATUS_STALE')`, submission `reason=MARKET_STATUS_STALE`, NO_SEND and no POST. Consequently the current test does not establish criterion 8's captured-account expiry, despite its name/comments/report claim.

Evidence: `04-final-guard-chronology.log`: **1 passed, 541 deselected, exit 0, network attempts []**.

Minimum repair: expire the captured account evidence while keeping prior guard inputs legitimately current; do not fake a successful predicate or alter the captured admission's provenance. Assert the actual existing account-staleness factual reason at the final guard, alongside NO_SEND/post_started=False/zero POST and honest known inventory/capital output. Preserve the market-status rejection test as separate existing evidence if useful, without calling it account acceptance.

### NOTE

- Worker03–25 preserves genuine failed development attempts. Attempts1/2 and some early exact commands are not recoverable from the delivered evidence and must remain explicitly unavailable. The raw baseline attempt07 actually exists under `/var/tmp/poly-maker-iter0030-worker/attempt-07-baseline-runtime-first.log` and says 73 passed; it is absent from the current delivered repository evidence directory. Do not silently amend Worker02 to change its historical delivery statement. An additive evidence receipt may preserve that file.
- Earlier placement-first selection reports 11 failures (901 passed); an isolated selection reports two failures. Passing runtime-first tests do not prove the alternate ordering fixed. The unchanged tests and logging capture warrant bounded diagnosis if used for future acceptance. No unsupported production cause or general order-independent PASS is assigned here. This concern is preserved rather than repaired by Reviewer.
- Existing initial-prefill foreign-selected coverage does not establish subsequent-mode rejection; B1 demonstrates the distinction directly.

## Frozen SOP Compliance

No Frozen SOP or strategy semantics changed. The new tests retain BUY-only, maker/post-only and cumulative confirmed accounting; the runtime continues using NORMAL `<5` and REBALANCE `>=5`, deficient-side repairs, dynamic ticks and retained close barriers.

B1 concerns reliable selected-market execution scope under existing authority. Its correction does not require inventing a state, stop policy, capital definition or external interface. The initial production-unchanged contract must nevertheless be revised before Worker edits runtime behavior.

## External Interface Review

No new runtime interface or dependency. Every independent execution used the pre-import DNS/socket blocker, and every network footer is empty. All SDK orders/cancellations are fake. Official connection validation was NOT_RUN and is not claimed by this iteration.

## Trading Safety Review

B1 prevents independent DG-002 safety acceptance. Ample collateral cannot authorize new selected-market risk when a foreign selected-token order is present.

Existing send authorization, stale public/order state, unknown submission, scoped cleanup, drain and terminal safety remain unchanged and their affected regressions pass. Reviewer neither enabled MICRO_LIVE nor sent/cancelled a real order. No real-money or main-merge authorization exists.

## Inventory / Capital Accounting Review

The independent oracle is separate from the production processor. It derives QY/QN and CY/CN from scripted accepted signed orders and venue fills, and accounts for unfilled notional using remaining quantity. Its expected costs do not call `snapshot_inventory`, capital projectors or backing evaluators.

Cross-mode snapshot agreement and exact capital/backing boundaries provide useful evidence. They do not fill M1: actual successful monitor capital/reservation/backing must be retained and independently compared. Production safety rejection must distinguish unsupported certification from known confirmed inventory; existing related regressions pass. Final MarketPnL/resolution is outside DG-002 and not claimed.

## State Machine Review

The primary positive test starts at actual initial submission, reaches a later NORMAL-to-REBALANCE handoff and actual subsequent SDK activity after repair, for both signs. It uses existing public continuation handoffs and one cumulative owner/processor. There is no new production state or duration-based rebalance stop.

B1 appears at both actual subsequent operating modes. In its probe, fake expiry is introduced after POST solely to bound execution and preserve the preceding admission/guard/POST chronology. No fabricated runtime predicate grants success.

## Test Review

Independent commands below used `.venv/bin/python`, `PYTHONDONTWRITEBYTECODE=1`, a fresh writable `TMPDIR=/var/tmp/poly-maker-iter0030-review/tmp`, and the archived pre-import runner (which disables pytest cache). Commands executed from `/home/ubuntu/poly-maker`.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests TMPDIR=/var/tmp/poly-maker-iter0030-review/tmp .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_runtime.py tests/test_rdp_placement.py -k iter0030 > /var/tmp/poly-maker-iter0030-review/01-new-acceptance.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests TMPDIR=/var/tmp/poly-maker-iter0030-review/tmp .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_runtime.py tests/test_rdp_placement.py tests/test_rdp_capital.py tests/test_rdp_lifecycle.py tests/test_rdp_account.py tests/test_execution.py > /var/tmp/poly-maker-iter0030-review/02-affected-regression.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/var/tmp/poly-maker-iter0030-review TMPDIR=/var/tmp/poly-maker-iter0030-review/tmp .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s tests/test_rdp_runtime.py /var/tmp/poly-maker-iter0030-review/test_foreign_selected_probe.py -k foreign_selected_no_new_risk_probe > /var/tmp/poly-maker-iter0030-review/03-foreign-selected-probe.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests TMPDIR=/var/tmp/poly-maker-iter0030-review/tmp .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s tests/test_rdp_runtime.py tests/test_rdp_placement.py -k iter0030_stale_final_account_guard > /var/tmp/poly-maker-iter0030-review/04-final-guard-chronology.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/var/tmp/poly-maker-iter0030-review TMPDIR=/var/tmp/poly-maker-iter0030-review/tmp .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s -p observe_capital_plugin tests/test_rdp_runtime.py tests/test_rdp_placement.py -k iter0030_independent_account_oracle_crosses > /var/tmp/poly-maker-iter0030-review/05-partial-projection-observation.log 2>&1
```

| Execution | Actual result | Exit | Network attempts |
|---|---|---|---|
| New tests | 18 passed, 524 deselected, 19.77s | 0 | [] |
| Affected regression | 832 passed, 112.54s | 0 | [] |
| Independent selected-foreign-order probe | 2 failed, 62 deselected, 2.30s | 1 | [] |
| Existing new final-guard test with chronology | 1 passed, 541 deselected, 4.15s | 0 | [] |
| Existing new positive tests with pass-through observation | 2 passed, 540 deselected, 12.18s | 0 | [] |

Probe failures: `test_foreign_selected_no_new_risk_probe[NORMAL]` and `[REBALANCE]`, both at the assertion requiring `client.posts == 0`.

Reviewer full suite: **NOT_RUN**. Worker current full-suite log records **1767 passed, 2 skipped, 127.85s**, network attempts []; its authorized test hash and protected production hashes match the reviewed tree. This is Worker evidence, not an independently rerun full-suite result.

Reviewer evidence SHA-256:

| File | SHA-256 |
|---|---|
| `01-new-acceptance.log` | `550aea5b61af22ff647e4163f8b2c74cb654744fb0d3a53869352bd6e7f172ca` |
| `02-affected-regression.log` | `f11403f30b99c2966a985da5549f3295caa95791c0e674daa90f12649ee3f31d` |
| `03-foreign-selected-probe.log` | `627fd26724128d6d7ff07587ad737786ec5245397a23daafd013909cf0bacbf8` |
| `test_foreign_selected_probe.py` | `9cfed339592debb1f0b07851cf50b124bf9a7536f4ef307c15a9a2642f3a9393` |
| `04-final-guard-chronology.log` | `bb6caaaa05257500eea1c60458b8c610bdb1a1f16951f2ba2d5639591dc89770` |
| `05-partial-projection-observation.log` | `daca6018cca2ec2371a051f6a270380c0921ca413dc636fae5b748ad85d461cb` |
| `observe_capital_plugin.py` | `88313a33aae9352c8daaee5cbbb2ec5e8dfb96824e94eba585db428195c1b3c6` |

Orchestrator should archive these evidence bytes additively with this report. Do not overwrite prior Worker failures or reviewer evidence.

## Scope Review

Worker remained within the test-only scope and introduced no duplicate production subsystem. The newly demonstrated required runtime defect cannot be implemented under Architect01's production-unchanged scope. Thus RETURN_TO_ARCHITECT is required even though M1/M2 independently admit test-only repair.

Use an additive revision in the same ITER-0030 for B1's smallest existing-component correction and M1/M2 acceptance repair. Do not open another speculative Gap, reopen independently closed Gaps, change strategy semantics, or repair unrelated logging infrastructure in this scope.

## Residual Risks

- DG-002 cannot close until B1 is fixed and the complete independent capital/reservation/final-account acceptance is actually reached.
- Fake-SDK evidence is not actual venue execution, official connectivity or real-market profitability evidence.
- Alternative test-order failures and unavailable early exact command evidence remain disclosed; normal-order success is not universal evidence.
- DG-005 and DG-007–DG-012 remain OPEN. MICRO_LIVE_READY = NO. Real-money validation remains NOT_STARTED / NOT_GRANTED.

## Final Decision

RETURN_TO_ARCHITECT.

Persist this report and independent evidence unchanged. Continue ITER-0030 with an additive Architect revision authorizing the minimum current-backing/scope repair, then Worker implementation/test correction and independent re-review. Leave DG-002 OPEN. No CODE_READY, delivery closure or MICRO_LIVE_READY declaration is justified by the reviewed tree.

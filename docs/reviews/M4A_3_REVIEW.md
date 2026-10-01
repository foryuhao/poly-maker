# M4A.3 Review

Verdict:
PASS

Reviewed baseline:
`m4a2-reviewed-baseline`

Reviewed baseline commit:
`aa9ada9de6127c44c7aae6177a0689568a8e6389`

Reviewed working tree / commit:
`m4-dynamic-ev` at `f5618debddf79cee0e17fb8e81cb1aee13ab2c3c`, plus the uncommitted M4A.3 files and edits listed below. The working tree was reviewed without modifying implementation or tests.

Architecture reference: `docs/architecture/M4A_ARCHITECTURE.md`; Research authority: `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md`.
Implementation report reference: `docs/reports/M4A_3_IMPLEMENTATION_REPORT.md`.

## Scope Reviewed

Inspected the corrected M4A.2 baseline, M4A.3 implementation and integration seams, actual M4A.2 producer payloads, Research schema, tests, reports, and the cumulative diff. This is an M4A.3 review only. The older `m4a2-pass` tag is not the reviewed baseline.

## Changed File Audit

| File relative to corrected baseline | Classification | Assessment |
| --- | --- | --- |
| `src/polymaker/research/lifecycle.py` (untracked) | Expected M4A.3 | Episode, projection, scheduler and snapshots; contains the blocking findings below. |
| `tests/test_research_lifecycle.py` (untracked) | Expected M4A.3 | Nine deterministic tests; misses the producer-payload and terminal failure cases below. |
| `src/polymaker/research/capture.py` | Justified supporting change | Admitted-event listener and synchronized snapshot boundary; no M4A.2 event semantics rewritten. |
| `src/polymaker/engine.py` | Justified supporting change | Constructs, starts and closes Research lifecycle only when Research exists. |
| `src/polymaker/favorite_live.py` | Justified supporting change | Passes an observed writer error reason to lifecycle close. |
| `docs/reports/M4A_3_IMPLEMENTATION_REPORT.md` (untracked) | Expected M4A.3 | Worker handoff; inaccuracies/limits noted below. |
| `docs/CURRENT_STATUS.md` | Expected status change | Still names incomplete `m4a2-pass` as implementation baseline; updated by this review. |
| `docs/reviews/M4A_2_REVIEW.md` | Out-of-scope documentation discrepancy | Current branch lacks the six-line later checkpoint metadata present in the corrected baseline. Historical review content and verdict are otherwise unchanged. Reviewer ownership forbids editing it here. |

The eight M4A.2 integration files dirty relative to current HEAD match the corrected baseline except for the M4A.3 additions in `engine.py` and `favorite_live.py`; no M4A.2 causal producer diff was found relative to the corrected baseline. The only deletion in the tracked diff is the later checkpoint note above.

## Findings

### P0

NONE.

### P1

1. **Actual owned-order updates leave remaining quantity stale.** `state/tracker.py:365-367` emits `ORDER_STATE` with `remaining_size`, but `research/lifecycle.py:284-290` accepts only `remaining_qty` or `qty`. Once the initial registered quantity and accepted fills are projected, a later authoritative WS update cannot replace the reported remainder. A read-only reproduction registered quantity 5, accepted a 2-share fill, then emitted a WS update with `remaining_size=1`; the next snapshot still reported 3. This corrupts working-order state and as-known reconstruction. Use the actual M4A.2 payload contract and test that path.
2. **First-leg finalization time is overwritten by later safety observations.** `research/lifecycle.py:313-315` resets `first_finalized_at_us` whenever a SAFETY payload says `first_finalized=true`. `engine.py:979-987` emits that flag on subsequent recomputes. A read-only reproduction changed the timestamp from 101,200,000 to 101,300,000 microseconds without another finalization. Later Episode revisions can therefore report an incorrect historical landmark. Set it only on first authoritative transition and preserve it across later observations.
3. **Terminal FavoriteFirst state lacks a material snapshot.** `research/lifecycle.py:189-200` appends an Episode revision for `COMPLETE`/`HALTED`/`UNPAIRABLE_RESIDUAL`, but lines 210-216 emit snapshots only for FILL, order and transition-marked SAFETY events. In a read-only reproduction, `BASELINE_DECISION(COMPLETE)` added zero snapshots and the latest snapshot still said `UNKNOWN`. Architecture and Research Design require material finalization/terminal state samples. Preserve the exact as-known terminal state at its event sequence without waiting for the next periodic tick.
4. **A writer failure discovered after horizon leaves a false COMPLETE terminal revision.** `research/lifecycle.py:611-614` prevents any terminal correction once status is COMPLETE. `favorite_live.py:721-728` detects writer error at wrapper shutdown, potentially after the T+300 scheduler has finalized. A deterministic reproduction emitted each deadline through horizon, observed COMPLETE, then called `close("WRITER_FAILURE")`; the latest Episode stayed COMPLETE with no failure reason. M4A.3 requires writer failure to be represented as incomplete/censored when discovered; this does not require implementing the broader M4A.4 durability protocol.

### P2

1. The nine new tests do not explicitly cover quarantined FIRST trigger exclusion, a real tracker-style `remaining_size` update, terminal baseline material sampling, or writer failure detected after horizon. Only one projection-failure seam is exercised; scheduler ownership and disabled parity with the lifecycle attached lack an integration assertion. Add focused regressions while fixing P1 findings.
2. The Implementation Report and pre-review status still frame `m4a2-pass` plus dirty files as the baseline and direct the reviewer to that old tag. The later corrected `m4a2-reviewed-baseline` is the review baseline. The Worker report is historical evidence and remains unchanged under Reviewer ownership.
3. The current worktree omits the later checkpoint metadata note from the M4A.2 Review relative to the corrected baseline. Keep that discrepancy visible when eventually checkpointing M4A.3; do not silently commit a deletion of historical checkpoint evidence.

## Episode Trigger Review

The lifecycle starts only after an admitted `FILL_REVISION` with role FIRST, BUY side, `accepted is True`, no quarantine, positive quantity, valid fill key and known YES/NO token (`research/lifecycle.py:336-370`). Submission, ACK, PAIR, FAILED and unaccepted events do not trigger. The first knowledge timestamp is local `available_at_us`; external `exchange_at_us` is preserved separately. Existing trigger tests prove most exclusions, but quarantine is not explicitly asserted.

## Episode Identity / Revision Review

Identity is SHA-256 over compact JSON `[condition_id, initial accepted FIRST maker_order_id]`, matching the accepted architecture and stable across later partial fills and revisions. Revisions append validated Episode objects in causal event order. Duplicate identical fill revision delivery leaves economics and revision count unchanged. The finalization timestamp defect above makes later revisions inaccurate.

## Pre-Fill Ring Review

The ring stores admitted `(event_seq, available_at_us)` references, retains approximately 20 seconds and caps at 4,096 entries. Full evidence remains in the M4A.2 event stream. It performs no disk/network I/O and creates no pre-fill snapshot. It is separate from FavoriteFirst's strategy timing rule. Its 20-second cutoff and cap are deterministic; no future event is inserted into prior context.

## Knowledge Causality Review

`CausalCapture` admits events and invokes the projection listener under one lock; periodic snapshots take that same lock. Snapshot `knowledge_seq` is taken at the boundary. Pydantic snapshot/book/order values are independently validated and immutable; later fill revisions do not mutate earlier snapshots. The late exchange-time discovery test correctly anchors inventory to local knowledge time. The working-order and finalization findings are projection errors at valid sequence boundaries, not evidence of future-event leakage.

## Inventory / Partial Fill Review

The fill map is keyed by `(trade_id, maker_order_id)` and current accepted economics are summed with `Decimal(str(...))` from fill quantity and accepted price. FIRST +2/+3 and PAIR +1/+2/+2, status duplicates, FAILED reversal and non-5 initial quantity are tested. Paired and unpaired quantities follow current accepted fills. No duplicate economic application or quote-price substitution was found.

## Snapshot Scheduler Review

The scheduler uses monotonic deadlines based on first knowledge time plus integer multiples of one second, records actual capture time and deadline, advances over missed intervals, and stops at market end. The deterministic `tick` seam tests cadence and post-COMPLETE horizon. `Engine` starts and joins the task only for enabled Research. No cumulative sleep drift or task leak was found in the inspected path. The false COMPLETE writer-failure path remains blocking.

## Material Event Snapshot Review

FILL, material ORDER, safety transitions and FINAL are sampled without per-book-delta amplification. Terminal `BASELINE_DECISION` creates an Episode revision but no immediate StateSnapshot, contrary to the specified material terminal observation. This is P1-3.

## T+300 Post-Exit Observation Review

The one-market live wrapper remains alive through market start plus 300 seconds; Research scheduling is independent of strategy COMPLETE and PAIR completion. The deterministic post-COMPLETE test emits periodic snapshots through horizon and stops thereafter. T+270 remains a live order cutoff only. No multi-market runner or new trading action was added.

## Censoring Review

Early clean shutdown creates CENSORED with `PROCESS_SHUTDOWN`; missed deadlines and connection interruptions also censor. Temporary disconnections remain conservatively marked as incomplete, as documented. Writer failure after a completed horizon cannot revise status and is P1-4. Crash durability belongs to M4A.4.

## Book / Order / Fill Projection Review

Actual YES and NO full books, deltas, REST replacements and tick changes feed separate local projections. Unseen books remain invalid with null best prices; no complementary market is synthesized. Snapshots contain copied top-five tuples and accepted-fill inventory. Cancel request and ACK clocks are distinct. Raw M4A.2 events retain request IDs because the accepted `WorkingOrder` schema lacks that field. The actual WS `remaining_size` mismatch is P1-1. No exchange query was found in snapshot construction.

## FavoriteFirst Baseline Projection Review

Pure decision, effective quote, phase, trigger and safety state are copied from admitted baseline/safety events; there is no second strategy state machine. A terminal baseline decision is missing its material snapshot, and repeated first-finalized flags overwrite the actual finalization timestamp.

## Failure Isolation Review

Projection listener errors are caught after the live event transition and counted in Research; Research enqueue is nonblocking and snapshot code has no disk/network I/O. Engine lifecycle construction/start/close exceptions are contained at narrow Research boundaries. The single failure test forces `_snapshot` to raise and confirms the event remains recorded. Broader failure injection remains untested. No observed path gives Research execution authority.

## Research Disabled / Live Behavior Review

Lifecycle construction requires enabled `CausalCapture`; absent Research creates no ring or scheduler. The existing paper-path parity regression passes, but it does not attach the new lifecycle, so it cannot establish parity for M4A.3 hooks. Inspection found no order-intent, sizing, cancel, reconciliation or settlement change in the M4A.3 diff. No live trading regression was reproduced; callback latency remains unmeasured until M4A.5.

## M4A.4 / M4A.5 Boundary Review

No full GAP/fsync/checksum/crash recovery protocol, final audit engine, FutureLabels, policy replay, Dynamic EV, Q functions, shadow scoring, optimizer or continuous market orchestration was added. The writer-failure finding concerns truthful Episode terminal status, not a request to implement M4A.4 durability machinery.

## Tests Reviewed

Read all nine M4A.3 tests and the relevant M4A.2 producer contract. The tests meaningfully cover trigger timing, identity persistence, immutable snapshots, partial fills, book/order projection, cadence, T+300 continuation and early-shutdown censoring. The P1 cases are absent from the suite and were reproduced independently with read-only scripts against the real projection.

## Tests Executed

- `.venv/bin/pytest tests/test_research_lifecycle.py -q`
- `.venv/bin/pytest tests/test_research_*.py -q`
- `.venv/bin/pytest -q`
- `.venv/bin/ruff check src/polymaker/research/lifecycle.py src/polymaker/research/capture.py src/polymaker/engine.py src/polymaker/favorite_live.py tests/test_research_lifecycle.py`
- `.venv/bin/mypy --follow-imports=silent src/polymaker/research/lifecycle.py src/polymaker/research/capture.py src/polymaker/engine.py src/polymaker/favorite_live.py`
- `.venv/bin/mypy --follow-imports=silent src/polymaker/research`
- Baseline comparison: `/home/ubuntu/poly-maker/.venv/bin/mypy --follow-imports=silent src/polymaker/engine.py src/polymaker/favorite_live.py` in the separate corrected-baseline worktree.
- `git diff --check`
- Read-only deterministic reproductions of the four P1 cases.

## Test Results

M4A.3: **9 passed**. Research: **33 passed**. Full suite: **570 passed, 2 skipped**. Targeted Ruff and diff check: clean. Research mypy: clean. Broader targeted mypy reports five diagnostics in `engine.py`/`favorite_live.py`; the corrected baseline reports the same five expressions at shifted line numbers, so no new targeted diagnostic was found. Passing tests do not exercise the four reproduced P1 failures.

## Implementation Report Accuracy

The identity, trigger, fill accounting, cadence, T+300 continuation and no-trading-authority descriptions match the inspected code. The working-order projection claim is inaccurate for actual `remaining_size` WS updates. The report's terminal-state observation claim overstates snapshot coverage; it records a terminal Episode revision only. Its writer-failure limitation is real but conflicts with M4A.3 censoring requirements. Its baseline/handoff text predates the corrected tag.

## Required Fixes

1. Consume the actual M4A.2 order-update remainder field and prove causal snapshots reflect it.
2. Keep the first authoritative first-leg finalization timestamp stable in subsequent Episode revisions.
3. Emit a material snapshot for a newly observed terminal FavoriteFirst baseline transition at that event's knowledge boundary.
4. Make a subsequently discovered writer failure visible as an incomplete/censored terminal revision, including when the scheduler has already reached T+300.
5. Add deterministic tests for these paths, plus an explicit quarantined FIRST trigger case. Preserve M4A.2 causal semantics and avoid M4A.4/M4A.5 expansion.

## Deferred Issues

Full writer durability, GAP accounting, crash recovery, completeness/latency audit and request-ID schema expansion remain in their accepted later slices. Restore the corrected-baseline checkpoint metadata in a future checkpoint process without modifying the historical M4A.2 verdict. Integration proof for disabled parity and scheduler cleanup would strengthen the next review.

## Final Verdict

**NEEDS FIX.** Four P1 correctness defects prevent M4A.3 PASS. No P0 or demonstrated live-trading regression was found. The passing suite and clean targeted lint do not override the reproduced projection errors.

## Handoff

Authorize only repair of M4A.3 P0/P1 findings and their focused regressions. M4A.4 remains unauthorized. Re-review against `m4a2-reviewed-baseline` after repair; do not use `m4a2-pass`.

## Re-Review After P1 Repair

Reviewed baseline:
`m4a2-reviewed-baseline`

Reviewed baseline commit:
`aa9ada9de6127c44c7aae6177a0689568a8e6389`

Reviewed working tree:
`m4-dynamic-ev` at `f5618debddf79cee0e17fb8e81cb1aee13ab2c3c`, with the tracked M4A.3 integration diff and four untracked files listed below. The reviewed lifecycle SHA-256 was `b4664dc3c1ddeaacee2d791eceaddd9250eaa04987c23b3f5347a82562ceb7f7`; the test SHA-256 was `9d099454dd1a8a2efdc976506a7dc3b7160bffe8923ec88f975fc2b0e75556eb`. No implementation or test file was edited in this re-review.

### P0

NONE.

### P1

1. **Authoritative cancellation is not projected as terminal when the WS status is nonterminal.** `userstream/parse.py:220-236` recognizes `type=CANCELLATION` as `is_cancel=True` independently of the status text. `state/tracker.py:358-367` removes the live order and emits `ORDER_STATE` with `cancel=True`. `research/lifecycle.py:305-313` ignores `cancel` and derives terminal visibility only from the status/transition string. A read-only reproduction through the real parser, `UserEventProcessor`, `StateStore`, and Research lifecycle supplied `type=CANCELLATION`, `status=LIVE`, original size 5 and matched size 2. The live store had zero working orders, while the next StateSnapshot still contained FIRST order `first` as `LIVE` with remaining quantity `3.0`. This is a blocking P1-A working-order projection error: a canceled order is falsely represented as working at a valid `knowledge_seq`.
2. **A quarantined WS order update is projected as resolved.** `state/tracker.py:343-367` flags a size-matched/fill gap as quarantined, marks the owned order unresolved, and emits `quarantined=True`. `research/lifecycle.py:309-311` marks unresolved only when the status/transition text is literally `QUARANTINED`; it ignores the boolean emitted by this producer path. A read-only tracker reproduction with accepted fill quantity 2 and venue `size_matched=3` yielded `quarantined=True` in the admitted ORDER_STATE event, but the resulting StateSnapshot retained `order_state_resolved=True` and lacked `UNRESOLVED_ORDER`. This falsely represents current order evidence as resolved and is another blocking P1-A projection error.

### P2

- The corrected baseline is now named in `docs/CURRENT_STATUS.md` and the Implementation Report; their historical explanation of `m4a2-pass` is appropriate. The current branch still lacks the later checkpoint metadata note present in `docs/reviews/M4A_2_REVIEW.md` at the corrected baseline. Preserve that note when preparing a future checkpoint rather than committing its deletion as an M4A.3 change.
- The new tests cover the four original reproduced paths, but still lack a parser/tracker-driven cancellation with nonterminal status, a `quarantined=True` WS gap, an explicit quarantined FIRST trigger test, and a lifecycle-attached disabled-parity or task-cleanup integration check. The first two gaps correspond to the P1 findings above; the others remain nonblocking coverage gaps.
- A late writer-failure correction is enqueued through the existing Research queue. If the writer has already stopped or the producer is closed, durability of that correction is not guaranteed. The Worker report states this limit; complete durability, crash recovery and health audit belong to M4A.4.

### P1-A Verification

The original stale-remainder path no longer reproduces: a registered quantity 5 changed to WS `remaining_size=3`, and the next snapshot showed 3 while the earlier snapshot retained 5. A fill of quantity 2 discovered later, already counted by WS `size_matched=2`, left Research remaining at 3. `research/lifecycle.py:288-295,503-508` implements that bounded subtraction without applying the same fill twice. Cancel-request timing and a `status=CANCELED` terminal update work in the new unit test.

The broader authoritative lifecycle still fails for `cancel=True` with a nonterminal status and for `quarantined=True` with an ordinary status, as reproduced above. Both payloads are generated by the accepted M4A.2 parser/tracker contract. Repair these flags in the Research projection without redefining M4A.2 event meaning. Preserve before/after snapshot immutability and add deterministic producer-path regressions.

### P1-B Verification

The original overwrite no longer reproduces. A FIRST_FINALIZED transition at 101,100,000 microseconds followed by `first_finalized=True` safety observations at 101,200,000 and 101,300,000 left `first_finalized_at_us` at 101,100,000 in later Episode revisions. The first Episode revision and earlier snapshots remained unchanged. `research/lifecycle.py:318-321` assigns the historical time only when it is unknown. No reset/replay path was found.

### P1-C Verification

The original missing terminal snapshot no longer reproduces. A `BASELINE_DECISION(COMPLETE)` at 101,400,000 microseconds appended an immediate `SAFETY` material StateSnapshot with the event's `knowledge_seq` and terminal baseline state. A later scheduled `PERIODIC` snapshot still appeared at the fixed one-second deadline, with Research OPEN. A duplicate identical terminal decision did not add another material snapshot. When a terminal event coincided exactly with a scheduled deadline, the two samples had distinct `sample_kind` values (`SAFETY`, `PERIODIC`) and distinct IDs; this is distinguishable event versus cadence evidence under the accepted enum. No order/cancel action was introduced.

### P1-D Verification

The original late writer-failure status bug no longer reproduces at the Research queue boundary. After deterministic T+300-equivalent deadlines, Episode revision 6 was COMPLETE. `close("WRITER_FAILURE")` appended revision 7 as CENSORED with `WRITER_FAILURE`; revision 6 and its snapshot stayed unchanged. Repeating the same notification appended no further revision. `research/lifecycle.py:617-630` changes only Research Episode/snapshot state. The test also confirms Research events, accepted fill projection and baseline state are unchanged. Writer durability after its own failure remains deferred as stated in P2.

### Regression Verification

The pre-existing trigger, late knowledge-time discovery, fill-keyed partial economics, duplicate status delivery, immutable snapshots, actual separate YES/NO books, monotonic scheduled cadence, post-COMPLETE observation to horizon and early-shutdown censoring tests all passed. `Engine` creates a lifecycle only with enabled Research and owns its task; default-off construction has no lifecycle or scheduler. The inspected M4A.3 diff adds no strategy, order, cancel, Fill Truth, settlement, network request or multi-market authority. The two new P1 errors are Research-side projection divergence, not observed changes to live trading behavior.

### Untracked File Audit

`git ls-files --others --exclude-standard` returned exactly:

| Untracked file | Re-review assessment |
| --- | --- |
| `src/polymaker/research/lifecycle.py` | Read in full. Expected M4A.3 source; original four code repairs exist, with two P1 order-projection gaps above. |
| `tests/test_research_lifecycle.py` | Read in full. Expected M4A.3 tests; 13 deterministic tests, including four repair regressions. |
| `docs/reports/M4A_3_IMPLEMENTATION_REPORT.md` | Read in full. Expected canonical Worker report; corrected baseline and repair section recorded, but the claim that terminal/cancel visibility follows observed evidence is overbroad for the two P1 cases. |
| `docs/reviews/M4A_3_REVIEW.md` | Read in full and appended in this Reviewer role. Original NEEDS FIX history remains intact. |

All four must be included in any eventual M4A.3 checkpoint. Normal tracked `git diff --stat m4a2-reviewed-baseline` omits them; the tracked diff contains M4A.3 changes to `engine.py`, `favorite_live.py`, `research/capture.py`, status, and the pre-existing six-line M4A.2 checkpoint-note discrepancy. No M4A.2 causal producer semantics changed relative to the corrected baseline.

### Tests Executed

- `.venv/bin/pytest tests/test_research_lifecycle.py -q`: **13 passed**.
- `.venv/bin/pytest tests/test_research_*.py -q`: **37 passed**.
- `.venv/bin/pytest -q`: **574 passed, 2 skipped**.
- Targeted Ruff on lifecycle, capture, Engine, wrapper and lifecycle tests: clean.
- Research-package mypy: clean. Broader targeted mypy: five diagnostics in `engine.py`/`favorite_live.py`; running the same check in the corrected baseline worktree produced the same five expressions at shifted lines, so no new diagnostic was found.
- `git diff --check`: clean.
- Read-only independent reproductions of the four original P1 paths, the coincident terminal/scheduled deadline, and the two new parser/tracker order-state failures. No code or test files were changed.

### Final Verdict

**NEEDS FIX.** The four original reproductions pass after repair, but two authoritative WS order-state flags still produce incorrect Research snapshots. These P1-A correctness failures block M4A.3 PASS. Repair only those M4A.3 projection paths and add focused regressions, then request independent re-review. M4A.4 remains unauthorized.

## Final Re-Review After Order-Projection Repair

Reviewed baseline:
`m4a2-reviewed-baseline`

Reviewed baseline commit:
`aa9ada9de6127c44c7aae6177a0689568a8e6389`

Reviewed working tree:
`m4-dynamic-ev` at `f5618debddf79cee0e17fb8e81cb1aee13ab2c3c`, plus the tracked M4A.3 integration/status edits and the four untracked artifacts audited below. The untracked lifecycle SHA-256 was `10ef4ed1a7e2ca9817c1ba35acc63be553b39e870c37aad1cefcc5fdd9cfd13f`; the test SHA-256 was `4690072e8bee944c4259ebbc95c54628ef93e70891a124fa436c559d27602540`. No implementation, test or Worker report file was edited by this Reviewer.

### P0

NONE.

### P1

NONE. The two remaining order-projection failures and the four earlier P1 failures no longer reproduce in the inspected paths.

### P2

- The current branch lacks the six-line later checkpoint metadata note in `docs/reviews/M4A_2_REVIEW.md` relative to `m4a2-reviewed-baseline`. Preserve that historical note during checkpoint creation; do not commit its deletion as an M4A.3 change.
- The focused suite does not explicitly test a quarantined FIRST fill as the initial Episode trigger exclusion or a lifecycle-attached disabled-mode/task-cleanup integration path. The trigger and disabled ownership paths were inspected, and the existing tests cover the main causal cases. These are nonblocking coverage gaps.
- Late writer-failure correction uses the existing Research queue. Persistence after a stopped or failed writer is not guaranteed by M4A.3; durable failure accounting and crash audit belong to M4A.4. No final M4A.5 completeness or latency claim is made.

### P1-E Cancellation Projection Verification

`userstream/parse.py` normalizes `type=CANCELLATION` to `is_cancel=True` independently of `status=LIVE`. `UserEventProcessor.on_order` removes the order from `StateStore` when that flag is true, then emits `ORDER_STATE` with `cancel=True`. Research `_order_one` now uses that normalized flag when setting terminal working-order visibility. This follows the existing M4A.2 producer result rather than a raw-status guess; the producer and live tracker were not changed.

An independent read-only parser → tracker → projection reproduction registered a five-share FIRST order and accepted a two-share fill. Its first snapshot contained the working order with remaining quantity 3. A `CANCELLATION` carrying `status=LIVE`, `size_matched=3` and `cancel=True` removed it from the live store and from the next snapshot. The earlier snapshot still contained it. The later snapshot's `knowledge_seq` matched the admitted cancellation event. The Worker regression also exercises a nonquarantined cancellation and confirms resolved state stays true.

The prior remaining-size regression still checks a 5→3 authoritative WS update, late discovery of the same two-share fill without double subtraction, cancel-request pending state, terminal status, and immutable earlier remainder. A terminal/canceled order is excluded from `working_orders` even if a later Fill Truth revision changes economic inventory.

### P1-F Quarantine Resolution Verification

`UserEventProcessor.on_order` marks a WS match gap in `quarantined_orders`, marks the owned order unresolved, and emits `ORDER_STATE` with `quarantined=True`. Research now maintains current unresolved order IDs separately from historical quality reasons. The matching snapshot has `order_state_resolved=False` at that event sequence; an earlier resolved snapshot remains true. The read-only reproduction observed this transition through the real tracker.

In the same reproduction, a canceled order was absent from `working_orders` while `order_state_resolved` remained false, proving the two facts are projected independently. Actual `reconcile_owned_order_once` returned `COMPLETE` only after reconciling REST order/trade evidence and cleared the tracker's quarantine. Engine's existing `FILL_TRUTH_RECONCILE` safety evidence then restored the later Research snapshot to `order_state_resolved=True`; no timer or inferred recovery did so. Earlier unresolved snapshots remained unchanged. The successful recovery snapshot's `knowledge_seq` matched its admitted safety event. No M4A.2 event semantics were altered.

### Prior P1 Regression Verification

The focused tests and code inspection reconfirmed WS remaining quantity and late-fill accounting, write-once `first_leg_finalized_at_us`, an immediate terminal FavoriteFirst material snapshot, scheduled sampling after terminal state, and an immutable CENSORED revision after a late writer failure. The fill projection remains keyed by `(trade_id, maker_order_id)` and duplicate status delivery does not add inventory twice. The two new parser/tracker tests cover the exact order-state payloads that were absent from the previous re-review.

### Core M4A.3 Invariants

The Episode trigger still requires an admitted authoritative positive FIRST BUY fill and starts at local `available_at_us`, retaining external exchange time separately. Snapshot projections are copied at a `CausalCapture` sequence boundary; later events do not mutate previous records. FIRST/PAIR partial economics use accepted fill values. The monotonic scheduler continues after FavoriteFirst COMPLETE and ends at the one-market T+300 horizon; early shutdown is CENSORED. Actual YES/NO book projections remain independent. Research-disabled Engine construction creates no lifecycle or scheduler. The repair adds no trading, cancel, network, settlement, M4A.4 durability, M4A.5 audit, Dynamic EV, replay or multi-market authority. Projection errors remain contained at the existing Research capture boundary after the live tracker transition.

### Untracked Artifact Audit

`git ls-files --others --exclude-standard` returned exactly these four files, and each was read during review:

| File | Assessment and checkpoint requirement |
| --- | --- |
| `src/polymaker/research/lifecycle.py` | Expected M4A.3 implementation; contains the reviewed projection repair. Stage for the complete checkpoint. |
| `tests/test_research_lifecycle.py` | Expected M4A.3 deterministic regressions; 15 tests including the two parser/tracker paths. Stage for the complete checkpoint. |
| `docs/reports/M4A_3_IMPLEMENTATION_REPORT.md` | Canonical Worker report with the second repair, test results and deferred limits. Stage for the complete checkpoint. |
| `docs/reviews/M4A_3_REVIEW.md` | This canonical Review report, with both prior NEEDS FIX histories preserved and this final verdict appended. Stage for the complete checkpoint. |

The tracked M4A.3 diff relative to the corrected baseline covers `engine.py`, `favorite_live.py`, `research/capture.py` and current status. The only other tracked difference is the M4A.2 checkpoint-note deletion described under P2; restore that note when preparing the checkpoint. Normal `git diff --stat` does not include the four untracked files, so the checkpoint must explicitly stage them.

### Tests Executed

- `.venv/bin/pytest tests/test_research_lifecycle.py -q`: **15 passed**.
- `.venv/bin/pytest tests/test_research_*.py -q`: **39 passed**.
- `.venv/bin/pytest -q`: **576 passed, 2 skipped**.
- Targeted Ruff on lifecycle, capture, Engine, live wrapper and lifecycle tests: clean.
- Research-package mypy: clean (6 source files). Broader targeted mypy reports five diagnostics in `engine.py` and `favorite_live.py`; the corrected baseline worktree reports the same five expressions at shifted line numbers, so this repair adds none.
- `git diff --check`: clean before this Review update.
- Independent read-only parser/tracker/projection reproduction of cancellation, quarantine, canceled-but-unresolved state and successful authoritative reconciliation: passed. The observed snapshots had `(knowledge_seq, working-order count, order_state_resolved)` of `(2,1,true)`, `(3,1,false)`, `(4,0,false)` and `(6,0,true)`.

### Final Verdict

**PASS.** No P0 or P1 remains in the inspected M4A.3 implementation. The two remaining order-projection defects are closed using the accepted M4A.2 event semantics; prior P1 repairs and the core causal lifecycle remain intact. This verdict applies to the working tree and artifact hashes recorded above, including untracked files. Create a complete M4A.3 Git checkpoint with all reviewed artifacts and without the historical M4A.2 note deletion before beginning M4A.4.

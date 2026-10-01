# M4A.3 Implementation Report

Status: **IMPLEMENTED — AWAITING REVIEW**

Implementation baseline at original Worker start: `f5618de` (`m4a2-pass`), with eight pre-existing uncommitted M4A.2 integration-file edits. The subsequently reconstructed complete reviewed baseline for M4A.3 review and this repair is `m4a2-reviewed-baseline` (`aa9ada9de6127c44c7aae6177a0689568a8e6389`).
Previous reviewed checkpoint: `m4a2-reviewed-baseline`; stable live rollback remains `m3c-settlement-live-pass` (`fff2842`).
Architecture reference: `docs/architecture/M4A_ARCHITECTURE.md`; program boundary `docs/architecture/M4_ARCHITECTURE.md`.
Research reference: `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md`.

## Baseline Verification

`m4a2-pass` exists at HEAD, but that commit omitted eight M4A.2 integration files from the independently reviewed state. The separate `m4a2-reviewed-baseline` checkpoint reconstructs that complete reviewed state without M4A.3 code; it is the baseline for re-review. The original tag remains unchanged historical evidence.

## Scope Implemented

Added a single committed-market, Research-only Episode and StateSnapshot projection. It consumes admitted M4A.2 `ResearchEvent` records, queues immutable Episode revisions and snapshots through the existing `ResearchObserver` and writer, and owns a monotonic 1-second scheduling task. No FutureLabel, model, policy, new external request or market orchestrator was added.

## Changed Files

- Added `src/polymaker/research/lifecycle.py` and `tests/test_research_lifecycle.py`.
- Modified `src/polymaker/research/capture.py` for ordered event listeners and synchronized projection reads.
- Modified `src/polymaker/engine.py` to attach and own the single-market lifecycle.
- Modified `src/polymaker/favorite_live.py` to pass an observed writer failure reason to shutdown.
- Updated this report and `docs/CURRENT_STATUS.md`.
- Other modified integration files visible in `git status` were present before M4A.3 work and were not edited for this slice.

## Episode Identity

`episode_id = SHA256(JSON compact array [condition_id, initial accepted FIRST maker_order_id])`, lowercase hexadecimal. JSON uses `separators=(",", ":")` with the two string values in that order. Later status revisions, partial FIRST fills and PAIR fills retain the ID. No exchange-time, market-path or random UUID input enters the identity. The session ID remains separate. Restart reuse requires recovery of the same authoritative FIRST order ID; actual in-memory strategy continuity after process restart is not claimed.

## Episode Trigger Semantics

Only an admitted `FILL_REVISION` with authoritative `accepted=true`, `quarantined=false`, `role=FIRST`, `side=BUY`, a positive quantity, a recognized YES/NO token, and nonempty `(trade_id, maker_order_id)` starts the Episode. The local event `available_at_us` is the first knowledge time. Venue `exchange_at_us` remains separate. Entry submission/ACK, FAILED or quarantined fill and PAIR fill do not trigger.

## Episode Revision Semantics

Each material fill status/economic revision, FIRST finalization, PAIR terminal/reconcile transition and observed baseline terminal transition appends a new validated `Episode` record. Duplicate identical fill revision delivery does not append an Episode revision or add inventory. Revision numbers increase in admitted-event order. Initial price/quantity/key/knowledge time remain those of the first accepted FIRST event even if its later truth changes. A later FAILED revision removes that fill from current economics and marks current Fill Truth invalid without rewriting earlier records.

## Pre-Fill Ring

The lifecycle retains only `(event_seq, available_at_us)` for admitted committed-market evidence before first fill, with a 20-second age limit and 4,096-entry cap. Full evidence is already persisted once in `events.jsonl`; Episode `capture_start_us` records the oldest retained event time, and joining its condition and first-known timestamp to that interval recovers the context. The ring does no disk I/O and emits no fake pre-fill snapshots. It is cleared by lifecycle ownership at session end.

## Snapshot Scheduler

The immediate `FILL` snapshot is emitted inside the same ordered capture boundary as the first accepted FIRST event. Subsequent periodic deadlines are `first_fill_monotonic_ns + n × 1 second`; each sample records actual `available_at_us`/`monotonic_ns` and its deadline-derived `scheduled_at_us`. When execution is late, the scheduler advances by integral intervals without cumulative sleep drift. A deterministic `tick` seam tests the deadlines without real sleeps. Engine startup owns the task; Engine shutdown cancels and joins it. Disabled Research creates no lifecycle or timer.

## Material Event Snapshots

FILL revisions produce `FILL` snapshots; changed owned-order state/cancel evidence produces `ORDER`; transition-marked safety/finalization and newly terminal baseline decisions produce `SAFETY`; horizon or shutdown produces `FINAL`. The accepted schema has no separate baseline material sample kind, so `SAFETY` distinguishes these terminal material samples from scheduled `PERIODIC` samples. Repeated identical terminal/order observations do not add snapshots. Book deltas remain raw events and do not each produce a snapshot.

## Observation Horizon

The existing one-market wrapper sleeps through market start + 300 seconds and keeps its existing feeds running. Strategy `COMPLETE`, PAIR completion and the T+270 order cutoff do not stop Research sampling. Periodic sampling ends at T+300, followed by a terminal Episode revision and `FINAL` snapshot. No new order or subscription is issued to extend the path.

## Censoring / Incomplete Episodes

Shutdown before T+300 appends `CENSORED` with `PROCESS_SHUTDOWN`. A detected writer error before shutdown appends `CENSORED` with `WRITER_FAILURE`; if a prior horizon revision said COMPLETE, the late known failure appends a newer CENSORED revision and FINAL snapshot without mutating the old record. Market/user disconnects and missed scheduled intervals mark coverage invalid and terminal observation censored even if the clock reaches T+300. Scheduler failure is censored. A process crash may prevent any final record; durable crash detection remains M4A.4.

## Knowledge Sequence Semantics

The listener executes under the existing `CausalCapture` event lock only after an event is accepted into the Research queue. Periodic reads acquire that same lock. Each snapshot records the highest admitted event sequence included; later event mutation cannot alter a validated frozen snapshot. Its `available_at_us` is the actual capture clock, never exchange occurrence time. Admission loss and capture failures mark later snapshot quality invalid; full GAP evidence remains M4A.4.

## Inventory Projection

A map keyed by canonical `(trade_id, maker_order_id)` holds the latest accepted status/economics. Quantities and costs are accumulated with `Decimal(str(value))` from accepted BUY fill price × quantity. FIRST, PAIR, YES, NO, paired and unpaired quantities are derived without a five-share assumption. Rejected, quarantined and FAILED revisions contribute zero current economics. Prior snapshots remain unchanged. The schema serializes quantities, prices and costs as finite decimal strings.

## Working Order Projection

Registered FIRST/PAIR owned-order facts and later M4A.2 order-state events project identity, token, price, lifecycle, submitted/acknowledged/cancel clocks and current remaining quantity. WS `remaining_size` replaces the prior reported remainder; `size_matched` prevents a later-discovered already-matched fill from reducing it twice. Accepted fills beyond the quantity already reflected in that authoritative update reduce the reported remainder. The M4A.2 normalized `cancel` field controls terminal working-order visibility even when the raw status still says `LIVE`. Its `quarantined` field and the later authoritative `FILL_TRUTH_RECONCILE` outcome control current order-state resolution separately from working-order visibility. Request correlation IDs remain in raw M4A.2 request/result events; the accepted `WorkingOrder` schema has no request-ID field. Pre-ACK submit attempts without venue order identity likewise remain in raw events rather than a fabricated working order.

## Book Projection

Applied WS full books, individual deltas, REST replacements and tick events maintain separate actual YES/NO projections. Snapshots copy real top-five levels, best prices and quantities, tick, causal book sequence and validity. An unseen book is invalid with null best prices; no complementary NO price is inferred. Disconnect invalidates carried books until a replacement full book is observed.

## FavoriteFirst Baseline State

The projection copies pure strategy state/trigger/quote, terminal state, effective quote and safety/finalization evidence from M4A.2 events. It does not run another strategy state machine. Unknown state remains `UNKNOWN`; nullable quotes remain null. The accepted boolean snapshot schema requires unknown readiness to be represented as false with a quality reason, not as invented true.

## Pair Telemetry Fields

The snapshot computes contemporaneous quoted and theoretical pair PnL from accepted cost, actual opposite book and a tick-rounded ask-minus-tick maker candidate. Existing paired gross value is included; only the currently unpaired FIRST remainder is hypothetically quoted. Submission feasibility checks observed finalization, minimum quantity, Fill Truth readiness and T+270 cutoff. These are diagnostics only. No EV, Q function, future label, execution model or trading decision is produced.

## Failure Isolation

Listener/projection exceptions are caught in `CausalCapture` after the live transition and counted. Engine lifecycle attachment/start/close failures are contained at Research-only boundaries. The scheduler runs as a separate owned Research task. Callback projection performs no disk/network I/O or live-state mutation. A projection-failure test confirms an admitted event remains recorded while the exception does not escape the capture boundary.

## Research Disabled Behavior

`Engine` constructs `EpisodeLifecycle` only when enabled `CausalCapture` exists. No ring, listener or scheduler is created in default-off mode. The existing enabled/disabled paper-path parity regression passed unchanged.

## Tests Added

Thirteen deterministic tests cover trigger exclusions, late discovery, partial FIRST/PAIR fills, duplicate/status/FAILED revisions, immutable prior snapshots, actual YES/NO books, tick and working orders, material order events, first finalization/pair completion, fixed deadline cadence and missed deadlines, post-COMPLETE observation, horizon stop, bounded pre-fill ring, shutdown censoring and projection failure isolation. Four tests were added for the independent Review's P1 repairs, detailed below.

## Tests Executed

- `.venv/bin/pytest tests/test_research_lifecycle.py -q`
- `.venv/bin/pytest tests/test_research_skeleton.py tests/test_research_causal_capture.py tests/test_research_lifecycle.py -q`
- `.venv/bin/pytest -q`
- `.venv/bin/ruff check src/polymaker/research src/polymaker/engine.py src/polymaker/favorite_live.py tests/test_research_lifecycle.py`
- `.venv/bin/mypy src/polymaker/research`
- Targeted mypy on Research plus `engine.py` and `favorite_live.py`
- `git diff --check`

## Test Results

After P1 repair: focused M4A.3: 13 passed. All Research tests: 37 passed. Full suite: **574 passed, 2 skipped**. Targeted Ruff: clean. Research package mypy: clean (6 source files). Broader targeted mypy: five diagnostics in existing, unchanged non-Research expressions (`engine.py` optional book/float paths and `favorite_live.py` `min` path); the two M4A.3 close-path diagnostics found during initial implementation were repaired. `git diff --check`: clean. Original pre-review counts were 9, 33 and 570 passed, respectively.

## Architecture Deviations

NONE in live architecture. Schema constraints create explicit limits: request correlation is available in raw events but has no `WorkingOrder` field; unknown boolean readiness cannot be serialized as null. No schema extension was made by the Worker.

## Known Limitations / Deferred Work

- A queue admission failure may leave an incomplete projection; later snapshots are marked invalid, while durable sequence GAP/manifest accounting is M4A.4.
- Writer failure known after horizon can append a corrective CENSORED Episode revision while the Research queue still accepts records. Failure discovered after producer closure or a hard writer crash may leave that correction non-durable; M4A.4 owns durable health and crash audit.
- Pre-fill context is associated by Episode `capture_start_us`, first-known time, condition and admitted event sequence in `events.jsonl`, without a new schema field listing ring entries. Events outside the 4,096-entry cap remain run-level evidence rather than Episode pre-fill context.
- Book-derived, quote-derived and readiness-dependent fields remain null or invalid when causal evidence is missing. Some FeatureVector fields require longer prefix windows or M4B and remain null.
- The fixed top-five and callback latency completeness gates, exact request-to-working-order join, durable GAP/fsync/checksums and final audit belong to M4A.4/M4A.5. No collection GO/NO-GO is asserted.
- The `m4a2-pass` tag is an incomplete reconstruction of its reviewed working tree. This is a checkpoint/handoff defect; no existing tag was changed by the M4A.3 Worker.

## Safety / Live Behavior Impact

Live trading semantics changed: **NO**. Research observes existing fills, books, orders and strategy evidence. It does not call exchange APIs, change strategy/order guards, adjust size or create a market loop. The wrapper's original one-market T+300 lifecycle remains the authority.

## Git Diff Summary

M4A.3 adds one lifecycle module, one focused test module, an ordered listener/snapshot seam in `capture.py`, narrow Engine lifecycle ownership, and a writer-error close reason in the existing wrapper. The total working-tree diff also contains the eight pre-existing M4A.2 integration edits against `m4a2-pass`; Reviewer should separate them from this slice using the baseline noted above.

## Handoff to Reviewer

Independently re-review the four repaired P1 paths, event admission/lock ordering, first-fill identity, status reversal, scheduler horizon, shutdown and writer-error paths, book/order projection and the one-market wrapper. Use `m4a2-reviewed-baseline`, not `m4a2-pass`, as the reviewed M4A.2 baseline. `docs/reviews/M4A_3_REVIEW.md` remains the independent NEEDS FIX review and was not edited by the Worker. M4A.3 repair is implemented and awaits independent re-review; no Worker PASS is claimed. M4A.4 remains unauthorized.

## Review Repair — P1

Repair status: **IMPLEMENTED — AWAITING REVIEW**. The four blocking findings are from the independent `docs/reviews/M4A_3_REVIEW.md`; that report was not modified.

### P1-A WS Order Remaining Quantity

Reviewer finding: A real WS `ORDER_STATE` update left the snapshot remainder stale.

Root cause: M4A.2 `UserEventProcessor.on_order` emits `remaining_size` and cumulative `size_matched`, while the projection recognized only `remaining_qty`/`qty`.

Repair: Read the authoritative `remaining_size` at its admitted event boundary. Use `size_matched` as the quantity already reflected in that remainder so a later discovered accepted fill does not subtract the same execution again. Later fills beyond that baseline reduce the remainder. Earlier immutable snapshots retain their prior values. Cancel-pending and terminal visibility still follow observed lifecycle evidence.

Regression test: `test_ws_remaining_size_replaces_causal_order_remainder` covers 5→3, historical snapshot immutability, a delayed fill already reflected by WS, further FIRST fill accounting, cancel pending and confirmed terminal order state.

### P1-B First-Finalization Timestamp

Reviewer finding: Later SAFETY observations moved `first_leg_finalized_at_us`.

Root cause: The projection assigned the timestamp on every `first_finalized=true` observation emitted by later recomputes.

Repair: Assign it only when the historical first occurrence is still unknown. Subsequent safety state remains observable, while later Episode revisions preserve the original finalization time. The accepted StateSnapshot schema contains `first_finalized` but no finalization timestamp field; the time is retained in Episode revisions.

Regression test: `test_first_finalization_time_is_first_observed_and_immutable` emits the transition at T1, later SAFETY flags at T2/T3 and a later PAIR fill revision; it verifies the timestamp remains T1 and earlier snapshots are unchanged.

### P1-C Terminal Material Snapshot

Reviewer finding: A terminal FavoriteFirst `BASELINE_DECISION` appended an Episode revision but no immediate StateSnapshot.

Root cause: Event-driven snapshot dispatch covered FILL, ORDER and SAFETY only.

Repair: A newly observed `COMPLETE`, `HALTED` or `UNPAIRABLE_RESIDUAL` baseline state now produces an immediate `SAFETY` material snapshot at that event's knowledge sequence. The accepted sample-kind enum has no separate baseline kind. An identical repeated terminal state creates no duplicate material snapshot. Scheduled observation remains active through T+300 and Research gains no order authority.

Regression test: `test_terminal_baseline_emits_immediate_material_snapshot_and_keeps_cadence` asserts the immediate terminal sample, no duplicate, subsequent one-second scheduled sample, immutable pre-terminal state and no Research order submit.

### P1-D Late Writer Failure / Episode Completeness

Reviewer finding: A writer failure discovered after T+300 left the latest Episode revision COMPLETE.

Root cause: `_finish` returned whenever `capture_status` was already terminal.

Repair: A newly known `WRITER_FAILURE` may correct COMPLETE or another CENSORED status by appending a new CENSORED Episode revision and FINAL snapshot with the failure reason. The prior terminal revision and snapshot remain immutable. Repeating the same close reason is idempotent. This is an in-memory Research lifecycle correction through the existing queue, not a new durability or crash-recovery protocol.

Regression test: `test_late_writer_failure_revises_complete_episode_without_rewriting_it` reaches horizon on deterministic deadlines, then discovers writer failure and checks the newer CENSORED revision, preserved old COMPLETE revision and idempotence.

### Deferred P2

The Reviewer P2 items remain deferred: explicit quarantined-trigger coverage, broader lifecycle-disabled parity and scheduler ownership integration, and the historical M4A.2 checkpoint-note discrepancy in the current branch. The corrected reviewed baseline tag exists and is named for re-review. Full GAP/fsync/checksum/crash recovery and final audit remain M4A.4/M4A.5.

### Repair Validation

- Focused lifecycle: **13 passed**.
- All Research tests: **37 passed**.
- Full suite: **574 passed, 2 skipped**.
- Targeted Ruff on changed Research/test and integration files: clean.
- Targeted mypy on Research plus `engine.py` and `favorite_live.py`: the same five pre-existing non-Research diagnostics; no new Research diagnostics.
- `git diff --check`: clean.

Live trading semantics changed: **NO**. Repair only changes Research-local projection, snapshots and Episode revision classification; no M4A.2 causal producer, execution or strategy code was changed.

## Second Re-Review Repair — Order Projection

Repair status: **IMPLEMENTED — AWAITING REVIEW**. The original four P1 repairs remain in place. This repair changes only the Research lifecycle projection, its deterministic tests and this handoff documentation. The independent Review report remains untouched.

### P1-E Cancellation / Working Order Projection

Reviewer finding: A production WS `CANCELLATION` with `cancel=true` and `status=LIVE` removed an order from the live tracker, but a later Research snapshot still listed it as working.

Root cause: Research set terminal visibility from the status/lifecycle string alone. `LIVE` masked the normalized cancellation fact.

Authoritative tracker semantics: `normalize_order` sets `is_cancel` for a `CANCELLATION` message. `UserEventProcessor.on_order` removes the order from `StateStore` when `is_cancel` is true and emits the admitted `ORDER_STATE` event with `cancel=true`. The event is emitted after the live transition.

Repair: Research uses that normalized `cancel` field to mark the order terminal and exclude it from subsequent `working_orders`, regardless of the raw status string. Earlier validated snapshots retain the old working order and knowledge sequence. No exchange query or M4A.2 producer change was needed.

Regression test: `test_parser_tracker_cancellation_removes_research_working_order` routes a real parser cancellation through `UserEventProcessor` and `StateStore`, checks both live removal and Research absence after the event, and checks the earlier snapshot remains unchanged.

### P1-F Quarantine / order_state_resolved

Reviewer finding: A quarantined WS order update could leave Research `order_state_resolved=true`.

Root cause: Research derived current resolution from its historical quality-reason set and omitted the normalized per-order `quarantined` transition. It also had no current-state recovery path after successful authoritative reconciliation.

Authoritative tracker semantics: `UserEventProcessor.on_order` marks a WS match-quantity gap as quarantined, marks the owned order unresolved, and emits `ORDER_STATE` with `quarantined=true`. `reconcile_owned_order_once` clears the tracker's quarantine/unresolved state only on `COMPLETE`; Engine then emits `SAFETY` with `transition=FILL_TRUTH_RECONCILE`, the order ID and outcome. A non-complete outcome is still unresolved.

Repair: Research tracks unresolved order IDs separately from sticky historical quality reasons. A quarantined update makes current `order_state_resolved=false`; the admitted successful reconciliation transition clears that order ID and can restore the flag. Failed reconciliation retains it. Market recovery remains an independent unresolved condition. Working-order presence and state resolution stay separate, so a canceled order can be absent while a prior quarantine still makes resolution false. Historical snapshots and the historical `UNRESOLVED_ORDER` quality reason remain unchanged.

Regression test: `test_tracker_quarantine_and_authoritative_reconcile_project_resolution` drives a WS gap through the real tracker, confirms quarantine and an unresolved snapshot, then drives `cancel=true/status=LIVE` while unresolved to check independent working-order absence. It runs the actual `reconcile_owned_order_once` path with a deterministic gateway fixture, emits Engine's existing successful reconciliation evidence, and confirms resolved state recovers without changing earlier snapshots.

### Regression of Prior P1 Repairs

The focused lifecycle suite still covers authoritative WS remainder and late-fill accounting, first-finalization write-once behavior, immediate terminal material snapshots and subsequent cadence, and immutable late-writer-failure CENSORED correction. Accepted-FIRST Episode trigger, partial and duplicate fill accounting, causality and early censoring also remain covered. No live trading transition, parser, tracker, Fill Truth or execution code changed in this repair.

### Deferred P2

The previously documented P2 items remain deferred. Current `order_state_resolved` may recover after authoritative reconciliation while `UNRESOLVED_ORDER` remains in historical quality reasons; this intentionally preserves the fact that the Episode had an earlier evidence interruption. Full GAP/durability and final completeness audit remain M4A.4/M4A.5. The original `m4a2-pass` checkpoint-note discrepancy remains historical; use `m4a2-reviewed-baseline` for review.

### Validation After Second Re-Review Repair

- Focused lifecycle: **15 passed**.
- All Research tests: **39 passed**.
- Full suite: **576 passed, 2 skipped**.
- Targeted Ruff on Research, changed integration files and lifecycle tests: clean.
- Research mypy: clean (6 source files). Broader targeted mypy retains exactly the five previously documented diagnostics in `engine.py` and `favorite_live.py`; this repair introduced none.
- `git diff --check`: clean.

The M4A.3 lifecycle source, focused tests and this report remain untracked working-tree artifacts awaiting the eventual checkpoint; the Reviewer-owned Review report is also untracked and was not edited by this Worker. Ensure all intended artifacts are included in the checkpoint after independent PASS. Status remains **IMPLEMENTED — AWAITING REVIEW**; no Worker PASS is claimed. Live trading semantics changed: **NO**.

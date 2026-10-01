# M4A.4 Implementation Report

Status: IMPLEMENTED — AWAITING RE-REVIEW
Commit / working tree base: `eb061fac91a8f77450137c3cd912f4d48266e676` plus the M4A.4 working tree; no M4A.4 commit or PASS tag created.
Reviewed predecessor baseline: `m4a3-reviewed-baseline`
Reviewed predecessor commit: `eb061fa`
Architecture reference: `docs/architecture/M4A_ARCHITECTURE.md`, especially Writer, storage and failure model; Research authority: `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md`.

## Scope implemented

- Dedicated bounded Research writer persists events, immutable Episode revisions and StateSnapshots to the architecture's `research_data/<dataset>/<session>/` JSONL layout. The existing `.gitignore` already excludes `research_data/`.
- Session manifest starts OPEN and is atomically finalized with artifact SHA-256 hashes, durable event watermark, accepted/persisted counts, queue loss ranges and COMPLETE/INCOMPLETE status. `quality.json` records persistence health only; M4A.5 owns reconstruction and acceptance audit.
- Research writer performs periodic flush (configured target <=1 s) and fsync (configured target <=5 s) off the live callback path. On close it drains admitted records, fsyncs and finalizes; close is repeatable. The admission lock still covers only nonblocking queue insertion/closure.
- Event admission failures retain sequence ranges. The next available admission writes a causal GAP event before the next event; unresolved loss remains in manifest/quality if the queue never recovers. Schema rejection after sequence assignment also produces loss evidence. Snapshot quality marks later observations invalid after a causal capture gap; the live wrapper requests an immutable CENSORED Episode revision before writer shutdown when it detects a writer/queue failure.
- The live wrapper supplies only an allowlisted public FavoriteFirst configuration, market/session identity, schema versions, a config hash and a best-effort Git commit. No config object containing credentials is passed to the writer. Writer startup and shutdown exceptions remain inside the existing Research isolation boundaries.

## Files added

- `tests/test_research_writer.py`
- `docs/reports/M4A_4_IMPLEMENTATION_REPORT.md`

## Files modified

- `src/polymaker/research/writer.py`
- `src/polymaker/research/observer.py`
- `src/polymaker/research/capture.py`
- `src/polymaker/research/schema.py`
- `src/polymaker/research/lifecycle.py`
- `src/polymaker/favorite_live.py`
- `tests/test_research_lifecycle.py`
- `docs/CURRENT_STATUS.md`

`AGENTS.md` was already modified before M4A.4 began and was not edited by this Worker.

## Important implementation decisions

- **Produced → accepted → persisted:** `CausalCapture` assigns an `event_seq` when producing an event. `BoundedResearchQueue.put_nowait` under its short admission/close lock defines acceptance. An accepted item is persisted only after writer-side flush and fsync; quality/manifest keep separate accepted and durable counts. Cross-file records retain queue FIFO order; events retain `event_seq`, Episode revisions retain `revision`, and snapshots retain their original `knowledge_seq`.
- **GAP:** A GAP is emitted with the first missing event sequence, the lost range/count and the discovery-time clock from the next event. It does not backdate writer knowledge. Missing sequences remain in manifest even if no GAP can be enqueued. Non-event queue rejections are accounted by rejected counts and make the dataset INCOMPLETE.
- **Failure:** writer exceptions stop the writer, preserve its exception class in manifest, and mark accepted-but-unpersisted records explicitly. No retry or tail repair is attempted. An OPEN manifest indicates no successful finalization; INCOMPLETE indicates known loss/degradation. A clean COMPLETE manifest requires no recorded loss and matching accepted/persisted counts.
- **Integrity:** JSONL files are append-only. A complete close fsyncs all files, streams SHA-256 hashes and atomically replaces manifest/quality files with fsync of file and parent directory. Crash-tail inspection and full reconstruction remain M4A.5.
- **Secrets:** manifest configuration is derived only from `effective_config(CONTROLLED_CONFIG)`; the schema rejects known credential keys. No wallet, auth or general application config is serialized.

## Deviations from architecture

- `quality.json` contains only derived persistence-health fields now. The architecture reserves the broader read-only audit result for M4A.5; this slice does not claim audit validity or calculate coverage acceptance.
- The `.gitignore` rule for `research_data/` was already present, so no ignore-file edit was needed.

## Tests added

- Deterministic writer tests for ordered durable output, OPEN/final manifest, hashes and quality, repeat close, overflow and causal GAP, schema rejection, serialization failure, writer I/O failure, quality-file failure, concurrent close/admission, and isolated malformed producer input.
- Extended the existing late-writer-failure lifecycle regression to verify COMPLETE then CENSORED revisions survive actual writer persistence without mutation.
- Existing M4A.1 queue tests continue to exercise admission/close races.

## Tests executed

- `.venv/bin/pytest tests/test_research_*.py -q`: **47 passed**.
- `.venv/bin/pytest -q`: **584 passed, 2 skipped** (final run).
- `.venv/bin/ruff check src/polymaker/research src/polymaker/favorite_live.py tests/test_research_*.py`: clean.
- `.venv/bin/mypy src/polymaker/research`: clean.
- `.venv/bin/mypy src/polymaker/research src/polymaker/engine.py src/polymaker/favorite_live.py`: five pre-existing diagnostics in Engine/live wrapper; none in modified Research code. The five expressions match the M4A.3 accepted report.
- `git diff --check`: clean.

## Test results

The deterministic tests verify that Research faults produce degraded evidence while producer calls return without raising. The full suite and Research lifecycle suite pass. No benchmark or real peak-traffic p99 claim is made; M4A.5 owns that acceptance measurement.

## Known limitations / unresolved issues

- A real disk failure can prevent both JSONL censor revisions and final manifest/quality writes. In that case the prior OPEN manifest or missing artifact indicates unclean capture; durability cannot be asserted. The wrapper requests a later CENSORED revision when failure is discovered while the queue can still accept it, and the quality/manifest invalidate otherwise complete-looking Episode evidence when they can be written.
- Process termination before close leaves OPEN manifest and may leave a partial JSONL tail. This slice does not automatically repair or audit that tail.
- File SHA-256 values are finalized only for complete sessions. INCOMPLETE sessions have no claim of artifact integrity.
- No peak-traffic callback latency measurement or M4A.5 completeness reconstruction was performed.

## Safety / behavior impact

Live trading semantics changed: **NO**. No FavoriteFirst entry/exit, PairStop, Runner, order price/quantity, submit/cancel, Fill Truth acceptance, market selection or settlement logic was changed. The only live-wrapper edits construct Research metadata and inspect Research health inside the existing optional Research path. No Research component gains order or reconciliation authority. Callback paths perform bounded in-memory work, never writer disk I/O.

## Git diff summary

M4A.4 source changes are confined to `research/` and optional Research setup/teardown in `favorite_live.py`, plus the named tests and durable docs. `AGENTS.md` is a pre-existing unrelated working-tree edit. Generated `research_data/` was not added to Git. No tag, commit or push was made.

## Handoff to Reviewer

Independently review the exact working tree against `m4a3-reviewed-baseline` (`eb061fa`), including untracked `tests/test_research_writer.py` and this report. Inspect manifest/quality behavior after induced write and serialization failures, causal GAP ordering, closure/admission race, and optional live isolation. The next authorized action is independent M4A.4 Review only. M4A.5 implementation remains unauthorized until Review PASS and a complete reviewed checkpoint.

## Repair After Initial Review

Review authority: `docs/reviews/M4A_4_REVIEW.md` initial NEEDS FIX against `m4a3-reviewed-baseline` (`eb061fa`). This section preserves the original Worker report and records the subsequent repair; it does not claim Reviewer PASS. Status is **IMPLEMENTED — AWAITING RE-REVIEW**. No M4A.4 checkpoint, tag or push was created.

### P1-A Causal GAP Ordering

Root cause: `CausalCapture.emit` released its sequence lock before the outer exception handler recorded a failed sequence. Another producer could admit a later event before the older GAP, as the Reviewer reproduced.

Fix: schema construction, failed-sequence accounting, GAP admission, ordinary record admission and listener delivery now share the same short causal lock. Failure handling after sequence assignment runs before releasing it. The queue remains the single FIFO writer stream; no post hoc sorting or separate GAP channel was added. The GAP carries the next observation's local available time and does not backdate failure knowledge.

Test: a deterministic two-thread regression pauses the first producer during failure accounting, starts a later producer, and asserts that it cannot finish until the earlier loss is recorded. The actual writer then persists `(1, GAP), (2, BOOK), (3, BOOK)` in order. The prior test for snapshot invalidation after a loss still passes.

### P1-B Finalization Integrity

Root cause: the initial implementation wrote `quality.json` as COMPLETE before committing the final manifest, then performed a fallible directory fsync after replacing the manifest with COMPLETE. A final failure could leave both files visibly claiming success.

Fix: OPEN manifest is written during writer-thread initialization. At close, the writer drains and fsyncs the JSONL files, computes hashes, and writes persistence quality with `status=FINALIZING` and `clean_close=null` for an otherwise healthy session. That quality file is **not** a completion certificate. The final manifest is the sole terminal certificate. Its temporary content and directory entry are fsynced before `os.replace`; the replace is the last operation, with no fallible fsync after a visible COMPLETE. A failed write, file fsync, directory fsync, or replace leaves the old OPEN manifest, or an INCOMPLETE manifest when the degraded commit succeeds. A quality write failure forces the manifest to INCOMPLETE. For known capture/writer loss, quality is DEGRADED and the manifest is INCOMPLETE.

Crash-consistency limit: the final rename is not followed by directory fsync. A crash may therefore lose the final rename and leave the earlier OPEN manifest, a safe false negative. If COMPLETE is visible, its content and the prerequisite quality/JSONL files were fsynced before the final replace. This is one-sided certification safety, not a guarantee that a successful close will remain COMPLETE across every power-loss scenario. Consumers must require `manifest.close_status=COMPLETE`; a standalone FINALIZING quality file never certifies a clean dataset.

Tests: injected final manifest write failure, atomic replace failure, final manifest directory fsync failure, JSONL fsync failure, and quality-file failure inspect actual on-disk manifest and quality contents. Normal close checks manifest COMPLETE with quality FINALIZING, matching counts and file hashes. Repeated close, queued-record drain and concurrent close/admission regressions remain green.

### P1-C Startup Isolation

Root cause: `ResearchWriter.start()` synchronously created directories and fsynced the OPEN manifest in the live wrapper before Engine startup/preflight. Git commit discovery also awaited a Research subprocess in that setup path.

Fix: `start()` now only starts the dedicated writer thread. The thread owns Git commit discovery, directory creation, initial manifest fsync and JSONL opens. The live wrapper does not await writer readiness or disk completion before Engine startup/preflight. Producers may enqueue immediately into the existing bounded queue. If initialization fails, the writer records its error, closes admission and leaves no healthy Research certification; trading proceeds independently. If the filesystem stalls, the bounded queue may overflow with explicit Research loss, but the live event loop is not held by the writer's disk work.

Tests: a blocked initial manifest write confirms `start()` returns and the asyncio loop makes progress while the writer thread remains blocked. An injected initialization failure confirms admission closes, `close()` reports failure and no healthy quality artifact is created.

### P2 Follow-ups

- GAP reasons now distinguish `SCHEMA_REJECTION`, `QUEUE_CAPACITY`, `ADMISSION_CLOSED` and `OBSERVER_REJECTION`; adjacent mixed causes use `MIXED_REJECTION`. Writer/persistence failure remains explicit as `WRITER_FAILURE` in session quality. A bounded reason-count map preserves per-cause totals. Tests assert schema, capacity and closed-admission reasons; writer fault tests assert WRITER_FAILURE.
- The queue retains at most **128 recent lost ranges**. Manifest/quality retain exact total lost-event count, total range count, number of omitted older ranges, per-reason counts and the recent ranges. Earlier recovered ranges remain represented by causal GAP rows. A stress test creates more than 128 separate ranges and verifies bounded memory plus exact summary counts.
- If a healthy writer still admits records, the existing immutable COMPLETE→CENSORED revision path remains available before close. If the writer has already failed and closed admission, the lifecycle does not reopen it or claim that a new revision persisted. The session manifest/quality are INCOMPLETE/DEGRADED when writable, so a persisted COMPLETE Episode cannot be used as a complete Research episode. A deterministic regression verifies this exact late-failure case.
- `quality.json` remains a persistence-health artifact, not the M4A.5 audit result. Its clean-session `FINALIZING` state intentionally requires joining to the final manifest; broader reconstruction and audit remain M4A.5 work.

### Repair files changed

`src/polymaker/research/{capture,observer,schema,writer}.py`, `src/polymaker/favorite_live.py`, `tests/test_research_writer.py`, `tests/test_research_lifecycle.py`, this canonical report and `docs/CURRENT_STATUS.md`. The initial Review report and the pre-existing `AGENTS.md` governance edit were not modified.

### Repair tests and results

- `.venv/bin/pytest tests/test_research_writer.py -q`: **16 passed**.
- `.venv/bin/pytest tests/test_research_writer.py tests/test_research_lifecycle.py tests/test_research_skeleton.py tests/test_research_causal_capture.py -q`: **57 passed**.
- `.venv/bin/pytest -q`: **594 passed, 2 skipped**.
- Targeted Ruff on Research, live wrapper and Research tests: clean.
- `.venv/bin/mypy src/polymaker/research`: clean (6 source files).
- Broader targeted mypy on Research, Engine and live wrapper: the same five pre-existing Engine/live-wrapper diagnostics documented in the initial M4A.4 Review; no new Research diagnostic.
- `git diff --check`: clean.

Live trading semantics changed: **NO**. FavoriteFirst entry/exit, PairStop, Runner, order price/quantity, submit/cancel, Fill Truth, market selection and settlement logic are unchanged. Research initialization no longer performs disk work on the live event loop. No M4A.5 implementation was begun.

### Handoff to independent re-review

Inspect the complete working tree against `m4a3-reviewed-baseline` (`eb061fa`), including untracked `tests/test_research_writer.py`, this report and the unchanged initial Review. Reproduce the concurrent schema failure and final manifest fault paths, verify startup loop isolation, and account for the pre-existing `AGENTS.md` edit separately. The only next authorized action is independent M4A.4 re-review; M4A.5 remains unauthorized.

## Final Repair — Close / Finalization Barrier

Status: **IMPLEMENTED — AWAITING RE-REVIEW**. This section responds to the subsequent P1 in `docs/reviews/M4A_4_REVIEW.md`. The preceding implementation/repair history remains intact. Reviewed predecessor baseline remains `m4a3-reviewed-baseline` (`eb061fa`); there is no M4A.4 commit or PASS tag.

### Root Cause

`ResearchWriter.close()` closed producer admission, but the queue continued to count later rejected Research events throughout metadata finalization. `_finalize()` sampled counters once, so a rejection after that sample could disappear from the durable manifest and quality file while the manifest said COMPLETE. The return value also checked accepted-record drain rather than whether COMPLETE was actually certified.

### Final Session Boundary

Closing admission and ending the causal Research session are now distinct. Calling `close()` first closes admission and begins the bounded writer drain. Events that enter capture while admission is closed **before the seal** still belong to this session: they receive sequence numbers, are rejected, and make the session INCOMPLETE. Immediately before final metadata work, the writer thread calls `queue.seal_session()`. This is the final causal boundary. A capture event entering **after the seal**, including after `close()` returns, is outside this Research session: it receives no session sequence or loss count. Direct queue/observer submissions after the seal likewise return false without changing session counters. The production wrapper shuts down its Episode lifecycle before closing the writer.

### Synchronization / Barrier Design

The queue admission guard is now reentrant and is also the enabled capture's causal sequencing lock. `CausalCapture.emit()` holds it from sequence assignment through schema/loss/GAP accounting, ordinary admission and listener delivery. `ResearchObserver.observe()` validates/admissions under the same guard. `queue.seal_session()` acquires that guard; it therefore waits for any already-entered capture or observer admission to finish, then atomically marks the session sealed and admission closed. The writer calls it in `_run`'s `finally` before reading final counters or writing quality/manifest. No disk I/O, writer join, sleep or queue-capacity wait occurs while this guard is held. A failed writer still closes admission immediately, waits for the normal close signal, then seals before finalization.

### Loss Accounting Guarantee

After the seal, accepted, rejected, lost-event, GAP-range and reason counters are stable for this session. Rejections during drain remain visible in manifest/quality and forbid COMPLETE. The writer computes incomplete status from accepted-versus-persisted counts, observer failures, rejected-full/closed counts and **total lost-event count**, not only the retained recent ranges. Thus truncating old range details cannot hide known loss. Previously accepted records still drain or appear as accepted-not-durable when a write/fsync failure prevents persistence. Already closed admission is never reopened to fabricate a late CENSORED revision.

### COMPLETE Certification Guarantee

`close()` now returns true only if the writer thread has finished and the final manifest was successfully committed with `close_status=COMPLETE`. It returns false for known loss even if every accepted record was fsynced; this clarifies the former drain-only return meaning. The prior finalization order remains: JSONL fsync, quality FINALIZING/DEGRADED, temporary manifest file and directory fsync, final manifest replacement last. An in-session rejection forces `manifest.close_status=INCOMPLETE` and `quality.status=DEGRADED`. The manifest remains the sole COMPLETE certificate. A post-boundary event is excluded by the explicit causal session boundary rather than counted after certification.

### New Regression Tests

- The Reviewer's exact race is reproduced by pausing quality-file creation after the writer seals and snapshots counters. An event emitted there is proven post-boundary (`session_sealed=true`, no `event_seq`, no loss count); the on-disk COMPLETE metadata matches that boundary. A second event after `close()` returns and repeated `close()` preserve it.
- Three concurrent producers emit while admission is closed and final `_sync` is paused **before** the seal. All three rejections remain in-session and persist as INCOMPLETE/DEGRADED with `ADMISSION_CLOSED` totals; `close()` returns false.
- A producer paused inside capture admission before close is accepted/drained before the seal; the writer certifies COMPLETE only after that record is persisted.
- A writer write failure during the close window plus a concurrent rejected capture event leaves INCOMPLETE/DEGRADED metadata and a false close result.
- The existing queue close/drain test now checks that an in-session closed-admission rejection yields INCOMPLETE and a false clean-close result. Existing overflow/schema-loss tests now expect false clean-close while still asserting their causal GAP and durable loss evidence.

Tests executed: `.venv/bin/pytest tests/test_research_writer.py -q` **20 passed**; existing Research lifecycle/skeleton/capture tests **41 passed**; `.venv/bin/pytest -q` **598 passed, 2 skipped**. Targeted Ruff and Research mypy are clean. Broader Research/Engine/live-wrapper mypy retains the same five previously documented diagnostics at unchanged Engine/live-wrapper lines. `git diff --check` is clean.

Files changed in this repair: `src/polymaker/research/{capture,observer,writer}.py`, `tests/test_research_writer.py`, `tests/test_research_skeleton.py`, this canonical report and `docs/CURRENT_STATUS.md`. The Review report and pre-existing `AGENTS.md` governance edit were not modified. Live trading semantics changed: **NO**; the only behavioral change is the Research session-close/certification contract. FavoriteFirst decisions, price/quantity, submit/cancel, Fill Truth and settlement remain unchanged.

### Remaining P2 Limitations

The final manifest rename is intentionally not followed by directory fsync, so a crash can leave OPEN after a close that returned true; it cannot create a false COMPLETE from a failed finalization step. `quality.json` remains persistence-health evidence rather than the later M4A.5 audit schema. Neither P2 was redesigned here. Full performance/coverage acceptance remains M4A.5 work after independent M4A.4 PASS and a complete reviewed checkpoint.

Next authorized action: independent M4A.4 re-review only. M4A.5 implementation remains unauthorized.

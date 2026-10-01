# M4A.4 Review

Verdict: **PASS** (final re-review; prior NEEDS FIX findings and reproductions are preserved below)

Reviewed predecessor baseline: `m4a3-reviewed-baseline`
Reviewed predecessor commit: `eb061fac91a8f77450137c3cd912f4d48266e676`
Reviewed working tree: branch `m4-dynamic-ev` at `eb061fa`, with the tracked M4A.4 diff and the two untracked files listed below; no M4A.4 commit or tag. The untracked writer-test SHA-256 was `73f6054ce062c71996059e79b2317286ed9467188d90254bd8c7ed97cdf7829e`; the Worker report SHA-256 was `75d3f5f1f7da73993c5c8624a4d042ae55bb090390ecad9c6183c046be1b1a11` before this Review. This Reviewer did not edit source, tests, the Worker report, or `AGENTS.md`.

Architecture reference: `docs/architecture/M4A_ARCHITECTURE.md`.
Implementation report: `docs/reports/M4A_4_IMPLEMENTATION_REPORT.md`.

## Scope reviewed

Read the M4A.4 diff, writer, queue, capture, lifecycle, schema, optional live setup/shutdown, Research tests, architecture and research contracts, prior M4A.3 Review, and the Worker report. Inspected the existing marketdata, userstream, tracker, Engine, gateway and wrapper Research call sites for persistence work in live callbacks. Independently ran tests and read-only fault/concurrency probes. Review is limited to M4A.4; no M4A.5 audit was performed.

## Diff / Untracked Artifact Audit

The M4A.4 implementation is in `src/polymaker/research/{writer,observer,capture,schema,lifecycle}.py` and the optional Research setup/teardown in `src/polymaker/favorite_live.py`. Tests changed `tests/test_research_lifecycle.py` and added `tests/test_research_writer.py`. Durable M4A.4 documents are `docs/reports/M4A_4_IMPLEMENTATION_REPORT.md` and `docs/CURRENT_STATUS.md`; this Review adds the canonical review document.

`git diff --stat` and `git diff --name-status m4a3-reviewed-baseline` show tracked changes but omit untracked files. `git ls-files --others --exclude-standard` returned exactly:

- `tests/test_research_writer.py` — read in full; actual writer thread, queue and file assertions, but no concurrency regression for the P1 race below and no post-replacement manifest fsync failure.
- `docs/reports/M4A_4_IMPLEMENTATION_REPORT.md` — read in full; records the Worker implementation and tests, but its claims of causal GAP ordering and final metadata safety are too broad for the reproduced P1 paths.

`AGENTS.md` is also modified relative to the baseline. Its diff consists of project governance, checkpoint and handoff protocol revisions; the Worker reported it was present before M4A.4 and left untouched. It is not M4A.4 implementation. The eventual checkpoint must account for it deliberately and include every reviewed M4A.4 source, test, report, Review and status file; no tag should be created from this NEEDS FIX tree.

## Findings

### P0

- NONE. No actual credential material or trading-authority transfer was found.

### P1

1. **Schema rejection can reorder the causal event stream and leave a later snapshot unaware of the loss.** `CausalCapture.emit` assigns a sequence while holding `_lock`, but its outer `except` records `lost_events`, the lost range and `_lost_range` only after `_lock` has been released (`capture.py:63-71,109-120`). A second producer can admit the next event first. In a read-only two-thread reproduction, the first producer failed schema validation at seq 1 and was paused before `note_lost_event`; the second producer admitted `BOOK` seq 2; after releasing the first producer, a third event caused GAP seq 1 to be admitted. The queue order was **`[(2, BOOK), (1, GAP), (3, BOOK)]`**. The seq-2 listener also runs before the loss flag is set, so a dependent snapshot can lack `RESEARCH_PERSISTENCE_GAP`. This violates the architecture's enqueue-order, GAP and no-lookahead contracts. The ordinary schema-failure test is single-threaded and does not detect it.
2. **Final metadata can falsely claim a complete durable session after a finalization failure.** `_finalize` writes `quality.json` with `status=COMPLETE` and `clean_close=true` before the final manifest replacement (`writer.py:196-218`). A read-only injected failure on the final manifest update left `manifest.json` OPEN while `quality.json` still said COMPLETE/clean. More seriously, an injected directory `fsync` failure after the final manifest `os.replace` (`writer.py:66-71`) produced `close_result=False` and `writer.error=OSError`, but the on-disk manifest and quality both remained **COMPLETE/clean**. The exception handler sets only in-memory `error` after the visible replacement; it cannot retract the already-written COMPLETE artifacts. A later consumer can accept a session whose final metadata durability failed. This is a known failure with false durable evidence and blocks PASS.
3. **Research startup still performs potentially blocking disk durability work on the live event loop.** `favorite_live.py:604-641` invokes `research_writer.start()` synchronously before `Engine.start` and preflight. `start()` creates directories and writes/fsyncs the OPEN manifest, including parent-directory fsync (`writer.py:59-79`). A slow or stuck Research filesystem can delay or prevent the one-market preflight/arm path even without raising an exception. M4A.4 adds this fsync path to the optional Research startup. Failure isolation needs a bounded startup path that cannot hold the trading event loop or change live readiness timing; the Worker tests cover immediate exceptions, not stalled I/O.

### P2

- GAP payloads always say `reason=QUEUE_REJECTION` (`capture.py:86-89`), including schema-validation failures before any queue admission attempt. Loss is visible and the dataset is INCOMPLETE, but the stated cause is wrong; preserve the actual rejection reason.
- `BoundedResearchQueue._lost_event_ranges` has no size bound. Alternating accepted and rejected events can create one range per rejection throughout a busy session, despite a bounded record queue. Bound or compact this accounting without losing the total and unresolved-loss evidence.
- If the writer has already failed and closed admission, the wrapper's late `lifecycle.close("WRITER_FAILURE")` cannot persist a CENSORED revision. The manifest/quality path correctly aims to mark the session INCOMPLETE when storage is still writable. The persisted COMPLETE Episode must be treated as invalid whenever the session metadata is incomplete; make this contract explicit in the repair and M4A.5 consumer rules.
- `QualityReport` in `schema.py` describes future audit fields and does not validate this slice's persistence-only `quality.json`. Define or document the persistence-health schema before consumers rely on its keys. The current file does not implement M4A.5 analytics, which is appropriate.

## Writer Acceptance / Durability Semantics

Record creation occurs in `CausalCapture`/`EpisodeLifecycle`; queue acceptance is successful `put_nowait` under `_admission_lock`; writer `_sync` marks written records durable only after all JSONL files flush and fsync. The queue is bounded and FIFO. On a writer write/serialization exception, the writer records `error`, closes producer admission and waits for close; accepted-but-unpersisted counts appear in INCOMPLETE metadata if final metadata can be written. The ordinary close/admission tests pass: closure is coordinated with admission and no disk I/O occurs while holding `_admission_lock`. Repeated close returns without duplicating data. These paths are sound apart from the P1 finalization and startup paths above.

## GAP / Degradation Semantics

Queue overflow and ordinary schema rejection produce lost ranges and a later GAP; unresolved ranges remain in manifest/quality when no retry succeeds. The next snapshot is marked invalid after observed capture loss, and earlier immutable snapshots are not rewritten. The P1 schema-exception race invalidates the causal order of that guarantee under concurrency. Writer I/O/serialization failure gives INCOMPLETE health rather than a fabricated market-silence interval when metadata writes succeed. The GAP reason misattributes non-queue schema failures as noted under P2.

## Manifest / Quality Integrity

The layout is `research_data/<dataset>/<session>/{manifest.json,events.jsonl,episodes.jsonl,states.jsonl,quality.json}`; `.gitignore` excludes `research_data/` and `git check-ignore` confirms it. The normal path writes an OPEN manifest, appends JSONL, fsyncs, computes file hashes and writes final metadata. It records dataset/session/schema, public strategy config hash, market/tokens, timestamps, counts, watermark, gaps and close status. Normal path assertions passed. The P1 finalization probes demonstrate false COMPLETE claims after metadata failure. No M4A.5 reconstruction or checksum verification tool was added.

## Shutdown / Close Behavior

The writer closes admission before draining and joins with a five-second default timeout; a timeout leaves the OPEN manifest until the daemon thread finishes. On writer failure, the thread closes admission, waits for session close, then attempts degraded metadata. This prevents producers believing a dead writer is still accepting. The JSONL close and quality-file failure tests pass. The final manifest failure sequence needs repair as P1. The current `close()` boolean can be true despite rejected records because it reports accepted-record drain rather than dataset completeness; callers must inspect metadata/quality for dataset status.

## Research / Live Failure Isolation

No strategy, price, size, submit/cancel, Fill Truth, market-selection, settlement or order-authority implementation changed in the diff. Research capture and queue operations in live callbacks remain in-memory and catch ordinary exceptions; no callback calls writer write/flush/fsync/join. Worker write/serialization fault tests confirm Research failures are contained at those boundaries. The P1 synchronous Research startup fsync can still delay the optional live session before preflight. No controlled on/off decision-trace latency comparison was provided; the full M4A gate belongs to M4A.5.

## M4A.3 Regression Verification

Focused lifecycle tests and the full suite pass, including FIRST-fill trigger, partial-fill economics, WS remaining quantity, cancellation/quarantine projection, write-once first finalization, material terminal snapshot, periodic cadence through T+300, late-failure COMPLETE→CENSORED revisions and immutable historical snapshots. The persisted revision test uses a healthy writer after the lifecycle has already appended CENSORED; it does not exercise a writer that has failed and closed admission. The concurrent P1 race is new in M4A.4 GAP handling, not a reopening of M4A.3's accepted lifecycle design.

## Secret / Configuration Audit

The live manifest derives config from `effective_config(CONTROLLED_CONFIG)`, whose current fields are numeric strategy parameters, not from `cfg.secrets` or wallet/auth objects. The inspected writer persists model records and that public config only. `ResearchEvent` recursively rejects several credential keys; `DatasetManifest` rejects known keys in its mapping. No actual secret exposure was found. The schema key denylist is not exhaustive (`auth_token` and `api_key` are not listed), so future producers should continue using explicit allowlisted payload construction; no current producer was found persisting those fields.

## Tests Executed

- `.venv/bin/pytest tests/test_research_writer.py tests/test_research_lifecycle.py tests/test_research_skeleton.py tests/test_research_causal_capture.py -q`: **47 passed**.
- `.venv/bin/pytest -q`: **584 passed, 2 skipped**.
- `.venv/bin/ruff check src/polymaker/research src/polymaker/favorite_live.py tests/test_research_*.py`: clean.
- `.venv/bin/mypy src/polymaker/research`: clean (6 source files).
- Broader targeted mypy: five Engine/live-wrapper diagnostics matching the accepted M4A.3 Review; no new Research diagnostic.
- `git diff --check`: clean before this Review document was added.
- Read-only concurrent schema-failure probe: queue order `BOOK 2`, `GAP 1`, `BOOK 3`.
- Read-only final manifest update failure probe: quality COMPLETE/clean, manifest OPEN, close false.
- Read-only final manifest directory-fsync failure probe: quality COMPLETE/clean, manifest COMPLETE, close false with `OSError`.

The Worker tests exercise actual writer threads/files for normal drain, overflow, serialization, write failure and quality-file failure. They do not cover the three P1 cases above. The passing counts do not override the reproduced failure paths.

## Architecture Compliance

The bounded writer, file layout, asynchronous callback admission, versioned event records and normal fsync/checksum path match the accepted architecture. P1 causal GAP ordering, false final metadata and synchronous startup durability work violate its causal trust and isolation requirements. No M4A.5 audit, M4B builder, model, replay, shadow live or strategy change was introduced.

## Required Fixes

1. Keep sequence assignment, schema-failure loss recording and GAP admission ordered under the same causal coordination boundary; add a deterministic concurrent producer regression proving monotonic persisted event/GAP sequence and snapshot invalidation.
2. Make manifest and quality finalization failure-safe. An exception during the final manifest write, replace or directory fsync must leave durable artifacts unable to claim COMPLETE/clean; add deterministic post-replacement and pre-replacement failure tests. State precisely what crash-consistency level is guaranteed.
3. Move Research startup directory/manifest/fsync work off the live event loop or otherwise bound it so a stalled Research filesystem cannot delay the one-market preflight/arm path. Test a blocked startup I/O seam while live progress remains possible.

## Deferred Issues

P2 items above may be handled in the repair or explicitly deferred with their consumer impact documented. M4A.5 reconstruction, latency acceptance and operational collection gates remain later-slice work. Preserve `m4a3-reviewed-baseline` as the predecessor; do not begin M4A.5 or create an M4A.4 PASS checkpoint from this tree.

## Final Verdict

**NEEDS FIX.** No P0 was found. The three P1 findings block M4A.4 durability/causal/isolation acceptance despite 584 passing tests. Authorize M4A.4 repair only, followed by independent re-review of the complete tracked and untracked working tree.

## Re-Review After Durability Repair

Verdict: **NEEDS FIX**. The initial Review above remains the historical record. The three originally reproduced failures are repaired, but a new deterministic close/finalization race still permits false COMPLETE evidence for known Research loss.

Reviewed predecessor baseline: `m4a3-reviewed-baseline`.
Reviewed predecessor commit: `eb061fac91a8f77450137c3cd912f4d48266e676`.
Reviewed working tree: branch `m4-dynamic-ev` at `eb061fa`, with the tracked M4A.4 changes and three untracked artifacts listed below; no M4A.4 commit/tag. Reviewed SHA-256: `writer.py` `fff86ff51a601956de5b82668da07aa061256fad7aae3f3f0be7b820b735d961`, `capture.py` `3040f0f4ed591f54c398580dee9f98b7b3dd2b9f4ea3cfaa0430385b72ba4601`, `observer.py` `013f57ae8dc8ec27ccfc67252d16069e13ee532e72d2636d5384d1157bd608b6`, untracked writer test `d9bfe891bf76855cd8396ce8e96bd9509f7d6f36cf48ee711d0ea972608c5456`, untracked Worker report `fa22c03504cdd6b56cf0daf1300521e18ac6b16ce4d9871dc05392f8dc366f27`.

Architecture reference: `docs/architecture/M4A_ARCHITECTURE.md`.
Implementation report: `docs/reports/M4A_4_IMPLEMENTATION_REPORT.md`, including its repair section.

### P0

- NONE.

### P1

- **New finding: a closed-admission rejection during finalization can disappear while the session certifies COMPLETE.** `ResearchWriter.close()` first closes the queue, but `_finalize()` reads `queue.counters`, loss ranges and observer failures only once, before writing `quality.json` and the final manifest. A live Research producer can still call `CausalCapture.emit()` while admission is closed. Its rejected sequence then updates the queue's loss counters after `_finalize()`'s snapshot, but neither final artifact sees it. In a read-only deterministic reproduction, the writer thread was paused at `quality.json` creation, after the counter snapshot. A `BOOK` event was emitted on the concurrent producer: `capture.lost_events=1`, `queue.rejected_closed=1`, `queue.lost_event_count=1`. After release, `close()` returned `True`; persisted `manifest.json` said `close_status=COMPLETE`, `rejected_closed=0`, `lost_event_count=0`, and `quality.json` said `FINALIZING` with zero loss. `events.jsonl` had no GAP. This is a known causal loss interpreted as ordinary silence, contradicting the architecture's explicit loss/incomplete-session requirement. It is independent of the repaired pre-rename directory-fsync path and blocks PASS. Coordinate the final loss-accounting boundary with producer admission/finalization, or establish and document an enforceable session quiescence boundary before certification; keep disk I/O outside admission/causal locks. Add a deterministic test that emits during finalization and inspects on-disk metadata.

### P2

- `quality.json` uses a persistence-health shape rather than the existing `QualityReport` audit schema. The Worker report now documents that distinction and that consumers must require `manifest.close_status=COMPLETE`; M4A.5 should encode this rule explicitly. This is non-blocking provided the P1 finalization race is fixed.
- A final manifest rename is intentionally not followed by directory fsync. A power loss can remove the final rename and leave OPEN even after `close()` returned success. This is a documented safe false negative, not a false COMPLETE claim; operational close durability is weaker than a directory-fsynced final rename.

### P1-A Causal GAP Ordering Verification

`CausalCapture.emit()` now holds its causal lock through sequence assignment, schema validation, loss recording, GAP admission, ordinary event admission and listener notification. `BoundedResearchQueue.put_nowait()` and `note_lost_event()` use its admission lock without disk I/O. The only production direct observer admissions outside `CausalCapture.emit()` are Episode/StateSnapshot revisions in lifecycle paths already invoked within the capture lock. No second GAP persistence channel or post hoc sorting was found. An independent two-thread probe paused schema-loss accounting while the later producer attempted entry; the later producer could not finish until release. The real writer persisted `[(1, GAP), (2, BOOK), (3, BOOK)]` with `SCHEMA_REJECTION`. The original P1-A ordering failure is closed. The new P1 above concerns a later loss after finalization has sampled counters, not overtaking of an earlier GAP by an accepted event.

### P1-B Finalization Integrity Verification

The normal path drains FIFO records; flushes and fsyncs all three JSONL files; closes them; hashes them; writes and fsyncs `quality.json` as `FINALIZING` with `clean_close=null`; writes/fsyncs a temporary final manifest; fsyncs its parent directory; then replaces `manifest.json` as the last operation. The final manifest is the sole COMPLETE certificate. Known writer/accepted-record failure instead produces `INCOMPLETE` manifest and `DEGRADED` quality when the metadata path remains writable. Independent real-thread injections of final manifest write, temporary-file fsync, pre-rename directory fsync and replace failure each left OPEN manifest plus non-certifying FINALIZING quality. JSONL fsync failure left INCOMPLETE/DEGRADED; quality write failure left INCOMPLETE manifest and no quality file. All returned from close without a hang or live-path exception. Thus the originally reported filesystem-failure subcases are closed. The new P1 still violates finalization integrity because its counters are stale before COMPLETE replacement.

### Directory-Fsync Boundary Verification

`_commit_manifest()` fsyncs the temporary manifest file and directory **before** `os.replace`, then performs no later fsync. Injecting failure in that directory fsync left `manifest.json` visibly OPEN, `quality.json` FINALIZING, and `close()` false. Therefore, under this code path, manifest cannot remain visibly COMPLETE *after a post-replacement directory-fsync failure*: there is no post-replacement directory fsync. If COMPLETE is visible after a successful final rename, the JSONL files, quality file, temporary manifest content and prior directory entry were fsynced. The replacement entry itself is not guaranteed to survive power loss; the old OPEN entry may reappear. The Worker report states this narrower, one-sided crash guarantee. A reader must not infer a clean session from quality alone. This filesystem path does not waive the concurrent-loss P1.

### Manifest / Quality Authority

Only `manifest.close_status=COMPLETE` is intended to certify a clean close, with `quality.status=FINALIZING` deliberately non-certifying on the healthy path. `quality.status=DEGRADED` and `manifest.close_status=INCOMPLETE` describe known failure; absence of a final manifest leaves OPEN. The report documents the join rule. The new reproduction left a COMPLETE manifest and zero-loss quality despite a queue-recorded closed-admission loss, so the authority relationship cannot rescue that dataset.

### P1-C Startup Isolation Verification

The live wrapper constructs in-memory public metadata, calls `ResearchWriter.start()` and then proceeds to `Engine.start()`/preflight without waiting for `writer.ready`. `start()` only creates/starts the thread. Git lookup, directory creation, initial manifest creation, flush and fsync occur in `_run()` on that thread. An independent probe blocked the OPEN manifest write: `start()` returned in about 0.13 ms while the writer remained blocked, and the producer queue stayed bounded. The Worker's asyncio blocked-startup test separately confirms event-loop progress. Startup initialization error sets writer health error, closes admission and does not produce healthy metadata; the live wrapper catches synchronous Research setup exceptions. No synchronous startup disk operation or Git lookup remains in the live loop. The original P1-C is closed.

### P2 Follow-Up Verification

GAP reasons distinguish `SCHEMA_REJECTION`, `QUEUE_CAPACITY`, `ADMISSION_CLOSED` and `OBSERVER_REJECTION`; writer faults appear as `WRITER_FAILURE` in quality. The tested schema GAP is no longer mislabeled as queue capacity. Queue loss history retains at most 128 recent ranges, with exact cumulative lost-event/range/truncation counts and reason counts; the stress test checks more than 128 disjoint ranges. The production reason vocabulary is finite. On a writer failure after a persisted COMPLETE Episode, closed admission is not reopened and no unpersisted CENSORED revision is claimed. The existing COMPLETE revision stays immutable, while writable session metadata becomes INCOMPLETE/DEGRADED; tests confirm this. These P2 follow-ups are adequate, subject to the new finalization P1.

### Prior Writer Regression Verification

The queue remains bounded and FIFO; admission and close coordinate under a short in-memory lock. Accepted records drain and are fsynced on normal close; repeated close is idempotent. Serialization and disk-write failures remain on the writer thread, close admission and lead to incomplete evidence when metadata is writable. Concurrent close/admission tests and actual JSONL assertions pass. No callback path was found performing writer file writes, flush, fsync or join. The new finding is that *rejections after closure* can outlive the finalization counter snapshot and be omitted from the terminal evidence. A queued record accepted before closure is not shown silently lost in the tested paths.

### M4A.3 Regression Verification

The 41 existing Research lifecycle/skeleton/capture tests pass. They exercise FIRST-fill start, partial-fill accounting, remaining quantity, working-order cancellation, quarantine/resolution, first-finalization write-once, terminal material snapshot, 1-second cadence through T+300, historical snapshot immutability and knowledge-sequence causality. Earlier COMPLETE Episode revisions remain immutable; the late CENSORED revision is appended when a healthy writer still admits it. No change to FavoriteFirst entry/exit, PairStop, Runner, pricing, size, submit/cancel policy, Fill Truth economics, market selection or settlement was present in the M4A.4 diff. The only live-wrapper changes are optional Research setup/health/teardown. No M4A.5 or later-scope implementation was found.

### Tests Executed

- `.venv/bin/pytest tests/test_research_writer.py -q`: **16 passed**.
- `.venv/bin/pytest tests/test_research_lifecycle.py tests/test_research_skeleton.py tests/test_research_causal_capture.py -q`: **41 passed**.
- `.venv/bin/pytest -q`: **594 passed, 2 skipped**.
- `.venv/bin/ruff check src/polymaker/research src/polymaker/favorite_live.py tests/test_research_*.py`: clean.
- `.venv/bin/mypy src/polymaker/research`: clean (6 files).
- Broader targeted mypy of Research, Engine and live wrapper: five Engine/live-wrapper diagnostics, at the same unchanged lines documented in the initial Review; no new Research diagnostic.
- `git diff --check`: clean before this review append.
- Independent read-only probes: causal schema rejection produced persisted GAP 1/BOOK 2/BOOK 3; blocked startup returned without waiting; six filesystem-fault scenarios inspected actual manifest/quality contents; **concurrent closed-admission loss during finalization produced false COMPLETE**, as detailed under P1.

### Untracked Artifact Audit

`git ls-files --others --exclude-standard` returned exactly `tests/test_research_writer.py`, `docs/reports/M4A_4_IMPLEMENTATION_REPORT.md` and this `docs/reviews/M4A_4_REVIEW.md`. All were read; the first two are M4A.4 Worker artifacts and the last is the canonical Review artifact. Ordinary `git diff` omits them. Tracked M4A.4 source changes are `src/polymaker/research/{capture,lifecycle,observer,schema,writer}.py` and optional Research integration in `src/polymaker/favorite_live.py`; tracked M4A.4 test change is `tests/test_research_lifecycle.py`; status is `docs/CURRENT_STATUS.md`. `AGENTS.md` is also modified relative to the baseline, but its inspected diff is pre-existing governance/checkpoint protocol work, not M4A.4 implementation; this Reviewer did not modify it. `research_data/` remains Git-ignored. A future PASS checkpoint must account for every reviewed M4A.4 artifact and the separate governance edit deliberately.

### Final Verdict

**NEEDS FIX.** P0: none. Original P1-A, original filesystem P1-B and original P1-C reproductions are closed. The new P1 closed-admission/finalization race can leave durable COMPLETE/zero-loss metadata after a known rejected causal event. Authorize M4A.4 repair of this race and independent re-review only. Do not create a PASS checkpoint or begin M4A.5.

## Final Re-Review After Close-Barrier Repair

Reviewed predecessor baseline: `m4a3-reviewed-baseline`.
Reviewed predecessor commit: `eb061fac91a8f77450137c3cd912f4d48266e676`.
Reviewed working tree: branch `m4-dynamic-ev` at `eb061fa` with the tracked M4A.4 diff and three untracked M4A.4 artifacts listed below. No M4A.4 commit or tag exists. Reviewed SHA-256: `writer.py` `357928974efff5551be21788a50a237966dc30d1842a645114f8cd5883f2f605`, `capture.py` `9160bbd011e86f2ca48cae9db1bb77c81b8afbaed836b4a9478d1249d75d765e`, `observer.py` `43c8002ea8b4e1276cdd44ec8d9ea07cf3fdc49ab3843f5b4faec79513cc69f4`, untracked `tests/test_research_writer.py` `8f9b66e00140c582041d57bae8aee63ed8dcd8d0d2be526861d156cf9315c121`, untracked Worker report `7bcc8e0dd13041cdb4e08ecf6e11b3c16102166e3f54bb57715e17d4979edc20`. This Reviewer changed only this Review and `docs/CURRENT_STATUS.md`.

Architecture reference: `docs/architecture/M4A_ARCHITECTURE.md`.
Implementation report: `docs/reports/M4A_4_IMPLEMENTATION_REPORT.md`, including its Final Repair section.

### P0

- NONE.

### P1

- NONE in the reviewed working tree. The prior close/finalization race is closed by a real session seal before final counter sampling. The original GAP ordering, filesystem finalization and startup-isolation findings remain closed.

### P2

- Final manifest replacement is intentionally not followed by directory fsync. A crash can leave the earlier OPEN manifest after an apparently successful close. The implementation/report claim the narrower one-sided guarantee: visible COMPLETE content has its prerequisite files fsynced, while final rename survival across power loss is not guaranteed. This cannot create a false COMPLETE under that claim.
- `quality.json` is a persistence-health artifact and does not implement the later M4A.5 `QualityReport` audit schema. The manifest is the sole COMPLETE certificate; future consumers must join it with quality and perform their own audit.

### Session Seal / Boundary Verification

`ResearchWriter.close()` closes queue producer admission and sets the stop event. The writer drains the FIFO and performs the final JSONL flush/fsync, then enters `_run`'s `finally` and calls `queue.seal_session()` **before** closing files and `_finalize()` counter reads. The queue's reentrant `_admission_lock` is also the enabled `CausalCapture` causal lock; `ResearchObserver.observe()` uses it too. Seal waits for any capture/observer admission already holding the lock, then atomically sets both closed and sealed. No disk work or writer join occurs inside that lock. An event entering capture after admission closes but before seal remains in-session: it receives a sequence and a rejection/loss count. An event entering after seal exits before sequence assignment; a direct queue/observer call after seal returns false without changing session counters. The production wrapper closes the Episode lifecycle and shuts down Engine before calling writer close. This is an explicit, singular causal boundary.

### Final Counter Stability

`CausalCapture.emit()` checks `session_sealed` under the shared lock **before** incrementing `_seq`; schema validation, GAP creation, normal admission and loss accounting remain under that lock. `BoundedResearchQueue.put_nowait()` and `note_lost_event()` refuse to mutate accepted/rejected/lost/range/reason counters after seal. Direct observer validation/admission is protected by the same lock. Therefore no assigned session event sequence can fall between the seal check and final counter freeze. Already accepted queue records are drained before seal on the normal path; `_persisted`, durable watermark and GAP-row count finish updating with the last successful `_sync`. File-close or hash failures after seal set writer error and force INCOMPLETE. Initialization/write/fsync errors also prevent COMPLETE. Final metadata samples queue counters only after seal, when their in-session values are stable. The writer does not keep a separate unsynchronized loss snapshot capable of changing during metadata I/O.

### Paused-Finalization Race Verification

Independent read-only, real-thread probes paused the writer at two deterministic points. While final `_sync` was paused **before seal** after admission closed, a captured BOOK received sequence 1, was rejected, and raised `lost_event_count=1`; after release `close()` returned false, `manifest.close_status=INCOMPLETE`, manifest loss count 1 and `quality.status=DEGRADED`. While quality creation was paused **after seal** at the point of the prior Review reproduction, an emitted BOOK received no session sequence and changed no lost/rejected counter; after release `close()` returned true, the manifest was COMPLETE with zero session loss and quality remained non-certifying FINALIZING. The two outcomes match the explicit boundary rather than depending on a repeated counter read or timing guess.

### In-Flight Producer Verification

An independent producer was paused inside the queue admission call while holding the shared capture/admission lock. Concurrent `close()` remained blocked on that in-memory decision. After release, the producer's BOOK seq 1 was accepted, the writer drained and persisted it, and the final manifest reported `accepted_count=persisted_count=1` and COMPLETE. No in-flight event was retroactively classified as post-session. The existing queue close-race test and new worker test cover this path through the real writer, not only a helper.

### Post-Seal Event Verification

Both the worker's paused-quality test and the independent probe confirmed that a post-seal `CausalCapture.emit()` assigns no event sequence and leaves accepted/rejected/lost/GAP state unchanged without throwing into the caller. The worker test repeats this after `close()` returns, checks direct queue rejection does not mutate session counters, and confirms repeated close leaves the COMPLETE manifest unchanged. Three producer threads rejected during the pre-seal drain produce three durable `ADMISSION_CLOSED` losses and INCOMPLETE/DEGRADED metadata; there is no ambiguous middle state.

### COMPLETE / close() Semantics

`_finalize()` computes incomplete status from writer error, accepted-versus-persisted mismatch, observer failures, rejected-full/closed totals and **total** lost-event count, including losses whose older range details were truncated. It writes quality as DEGRADED for known loss or FINALIZING for a potentially clean session. Only a successful final manifest commit with `close_status=COMPLETE` sets `_certified_complete=true`; `close()` returns true only after joining the finished writer thread and observing that flag. Independent probes found that known pre-seal loss returned false with INCOMPLETE/DEGRADED artifacts. An independent repeat-close probe on an incomplete schema-loss session returned false twice without rewriting its INCOMPLETE manifest. Previously accepted records were not abandoned by sealing. The authoritative COMPLETE artifact remains `manifest.json`, never a standalone quality file.

### Previously Repaired P1 Regression

The real writer test again persisted `GAP seq=1, BOOK seq=2, BOOK seq=3` after a concurrent schema rejection; the shared lock preserves causal order and no later snapshot is retroactively changed. Independent fault probes inspected actual files after final manifest write, replacement and pre-rename directory-fsync failures: close false, manifest OPEN, quality FINALIZING. JSONL fsync failure left INCOMPLETE/DEGRADED; quality-file failure left INCOMPLETE manifest without a quality file. No injected finalization fault produced false clean COMPLETE. A blocked initial manifest write still ran on the writer thread; `start()` returned in about 0.37 ms while `ready` remained false. Git lookup, mkdir, file operations and fsync remain on that thread. No FavoriteFirst/live callback gained synchronous writer I/O.

### Locking / Deadlock Assessment

Capture sequencing, observer admission, queue counters and seal use one reentrant lock, so nested `observe()`/`put_nowait()` calls do not self-deadlock. The writer's `_close_lock` precedes a short `queue.close_producer()` call; capture/observer paths never acquire `_close_lock`, and the writer releases it before `join()`. On writer failure, `queue.close_producer()` releases the shared lock before waiting for `_stop`; final seal also releases it before file close/metadata I/O. Thus there is no capture→close versus close→capture inversion in the inspected paths. No file write, flush, fsync, Git lookup, join or unbounded queue-capacity wait occurs while the shared lock is held. The lock may cover the existing bounded capture validation/projection work; a full p99 callback measurement remains the M4A.5 gate.

### M4A.3 Regression Verification

The 41 existing Research lifecycle/skeleton/capture tests passed. They cover authoritative FIRST Episode start, partial fills and remaining quantity, working-order cancellation, quarantine/resolution, first-finalization write-once, terminal material snapshots, 1-second cadence through T+300, historical snapshot immutability and `knowledge_seq` causality. The persisted COMPLETE→CENSORED revision test still appends rather than mutates; if a failed writer has already closed admission, an unpersisted censor revision is not claimed and the session is marked incomplete when metadata is writable. The M4A.4 diff has no FavoriteFirst entry/exit, PairStop, Runner, price/quantity, submit/cancel, Fill Truth economic-acceptance, market-selection or settlement change. There is no M4A.5 audit harness or later milestone scope in this tree.

### Tests Executed

- `.venv/bin/pytest tests/test_research_writer.py -q`: **20 passed**.
- `.venv/bin/pytest tests/test_research_lifecycle.py tests/test_research_skeleton.py tests/test_research_causal_capture.py -q`: **41 passed**.
- `.venv/bin/pytest -q`: **598 passed, 2 skipped**.
- `.venv/bin/ruff check src/polymaker/research src/polymaker/favorite_live.py tests/test_research_*.py`: clean.
- `.venv/bin/mypy src/polymaker/research`: clean (6 source files).
- Targeted broader mypy of Research, Engine and live wrapper: five unchanged diagnostics at `engine.py:917,1018,1019,1053` and `favorite_live.py:171`, matching prior Reviews; no new Research diagnostic.
- `git diff --check`: clean before this append; independent pre/post-seal, in-flight-admission, incomplete repeated-close, filesystem fault and blocked-startup probes as detailed above.

### Untracked Artifact Audit

`git diff --stat m4a3-reviewed-baseline` and `git diff --name-status m4a3-reviewed-baseline` show tracked changes in `docs/CURRENT_STATUS.md`, `src/polymaker/favorite_live.py`, `src/polymaker/research/{capture,lifecycle,observer,schema,writer}.py`, `tests/test_research_lifecycle.py` and `tests/test_research_skeleton.py`. `git ls-files --others --exclude-standard` returned exactly `tests/test_research_writer.py`, `docs/reports/M4A_4_IMPLEMENTATION_REPORT.md` and this canonical Review; all were inspected, and ordinary diff omits them. `research_data/` is Git-ignored. `AGENTS.md` also differs from the predecessor, but its separately inspected diff is the pre-existing governance/checkpoint edit, not M4A.4 implementation; this Reviewer did not modify it. A future checkpoint must stage every reviewed M4A.4 source, test, implementation report, Review and status artifact, while deliberately accounting for the unrelated governance edit. No PASS tag/checkpoint was created by this Review.

### Final Verdict

**PASS** for the exact reviewed working tree above. P0: none; P1: none; the two documented P2 limitations may be deferred. The previous close/finalization false-COMPLETE race is closed with a synchronized causal seal and stable final session counters. Next authorized action: create and verify the **complete M4A.4 reviewed Git checkpoint**; only after that checkpoint may M4A.5 implementation begin.

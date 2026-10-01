# M4A.1 Implementation Report

Status: **IMPLEMENTED — AWAITING REVIEW**
Implementation baseline: `9c28bca` (`m4a-implementation-ready`), clean working tree at start.
Architecture reference: `docs/architecture/M4A_ARCHITECTURE.md` and `docs/architecture/M4_ARCHITECTURE.md`.
Research reference: `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md`, sections 5–6.

## Scope Implemented

Created default-off Research capture contracts, bounded nonblocking queue, read-only observer boundary and dedicated-thread JSONL writer skeleton. No live observation hook or trading module changed. Generated `research_data/` is Git-ignored.

## Files Added

- `src/polymaker/research/__init__.py`
- `src/polymaker/research/schema.py`
- `src/polymaker/research/observer.py`
- `src/polymaker/research/writer.py`
- `tests/test_research_skeleton.py`
- `docs/reports/M4A_1_IMPLEMENTATION_REPORT.md`

## Files Modified

- `.gitignore`: excludes `research_data/`.
- `docs/CURRENT_STATUS.md`: records Worker handoff after validation.

## Actual Architecture Mapping

- `schema.py` defines ResearchEvent, Episode, StateSnapshot, DatasetManifest, QualityReport, SourceRef, BookState, WorkingOrder, Quote, Level and the fixed FeatureVector. V1 capture field names follow the Research Design. Accounting values are finite decimal strings; unknown clocks and values remain nullable. There is no five-share bound.
- `observer.py` defines ResearchConfig (default disabled), BoundedResearchQueue (`put_nowait`) and ResearchObserver. The observer accepts validated records only, is inert when disabled, and contains failures.
- `writer.py` owns JSONL files under `research_data/<dataset_id>/<session_id>/` on a dedicated thread. Start/close are explicit; close attempts to drain pending records and reports error/timeout.
- Research has no reverse dependency into Engine, execution, reconciliation, settlement or credentials. Producer methods perform no file/network I/O.

## Important Implementation Decisions

- Pydantic v2 frozen models reject unknown fields. Event payloads are recursively copied into immutable mappings/tuples, permit finite JSON primitives only, and reject common credential field names. M4A.2 must still sanitize each live hook deliberately.
- `execution_estimate` is reserved as null in StateSnapshot. No model object or fake estimate is emitted.
- FeatureVector contains the exact expanded fixed-key allowlist from Research Design section 6. Missing values are explicit nulls; no feature computation is in this slice.
- Queue counters distinguish accepted, full rejection and closed rejection. Sequence assignment and contiguous loss/GAP accounting are later work.
- DatasetManifest includes public effective strategy config, config hash, capture period, market/tokens, version IDs, file hashes, split-boundary slots, event watermark, gap count and close status. The writer creates three append-only record files but does not claim manifest finalization, checksum/fsync durability, GAP recovery or crash repair. Those belong to M4A.4/M4A.5.
- ResearchConfig remains local to the new package; no live configuration seam is necessary before live hooks exist.

## Deviations From Architecture

NONE. `audit.py` was omitted because audit behavior is M4A.5; M4A.1 names only the other four package files.

## Tests Added

`tests/test_research_skeleton.py` checks serialization, nested payload immutability, null timing, arbitrary quantity, finite value and unknown-field rejection, credential-key rejection, immutable public manifest config/round-trip, default-off/invalid config, queue full/closed outcomes, observer failure isolation, writer lifecycle/layout and deterministic concurrent enqueue/close interleavings.

## Tests Executed

- `.venv/bin/pytest -q tests/test_research_skeleton.py -k "close_drains or enqueue_rejected"`
- `.venv/bin/pytest -q tests/test_research_skeleton.py`
- `.venv/bin/pytest -q`
- `.venv/bin/ruff check src/polymaker/research tests/test_research_skeleton.py`
- `.venv/bin/mypy src/polymaker/research`

## Test Results

Current focused tests: 8 passed. Targeted Ruff: clean for Research source and focused tests. Targeted mypy: clean for all four new Research source files.

## Existing Regression Results

Current full suite: **545 passed, 2 skipped** in 7.33s. Existing trading tests were not modified. Pytest reported the two skips; no failure was hidden. The prior Worker run was 543 passed, 2 skipped before repair. The Reviewer ran repository-wide Ruff and mypy and found 92 Ruff errors and 1,427 mypy errors, including 39 in this new test file; targeted Research checks above are clean. This repair did not address repository-wide static-check debt.

## Known Limitations / Deferred Work

No real market/user/order/Fill Truth hooks, clock sampling, event sequence allocator, Episode lifecycle, scheduled snapshots, pre-fill ring, GAP durability, finalized manifest, checksum/fsync contract, crash recovery, audit or dataset collection. These are M4A.2–M4A.5. Clean close is a skeleton lifecycle result, not durability certification. Admission and close are now coordinated; future live shutdown orchestration remains integration work.

## Review Repair — P1 Writer Close / Enqueue Race

Reviewer finding: `ResearchWriter.close()` could return success while a producer already past the old closed check subsequently enqueued an accepted record. Before repair, a forced interleaving produced `close_success=True`, `accepted=1`, a nonempty queue and zero persisted event rows.

Root cause: the closed check, queue insertion and accepted counter were separate from `close_producer()`'s state update. Writer shutdown could observe an empty queue between those operations.

Repair: `BoundedResearchQueue` now uses one short admission lock around closed-state checking, `Queue.put_nowait`, outcome counters and the close transition. `ResearchWriter.close()` continues to close producer admission before setting the writer stop event and joining. The lock is never held during file I/O, flush or thread join; the producer still uses `Queue.put_nowait` and never waits for queue capacity.

Lifecycle invariant: a successful enqueue linearizes before admission closure and remains in the queue for writer drain. After closure, enqueue returns explicit rejection. Successful writer close occurs only after its thread completes draining accepted records under this admission boundary. This is an in-process skeleton guarantee, not an fsync/crash-durability claim.

Regression test: two new event-controlled cases in `tests/test_research_skeleton.py` force (A) a producer paused within queue admission while close starts, then verify accepted record persistence before successful close, and (B) a writer paused during drain after admission closes, then verify a new producer is rejected promptly. Both threads are joined with finite deadlines. No timing-luck loop or sleep is used.

Validation: lifecycle tests 2 passed; focused Research tests 8 passed; full suite 545 passed, 2 skipped. Targeted Ruff and mypy are clean. `git diff --check` is clean.

P2 handling: Reviewer P2 findings on empty output root, invalid-book consistency and strict mypy diagnostics in the new test file are deferred. No P2 or unrelated lint/type cleanup was made in this P1 repair.

## Safety / Live Behavior Impact

Live trading semantics changed: **NO**. Strategy decisions, order intents, Fill Truth, reconciliation, settlement, wallet/auth and live config are untouched. Research defaults off and is not wired into the live process.

## Dependency / Isolation Assessment

Research imports only standard library, Pydantic and its own modules. It accepts Research records rather than gateways, state stores or trading controllers. Producer insertion is nonblocking and returns a boolean. Observer errors are contained.

## Git Diff Summary

Added four package files, one focused test file and this report; modified `.gitignore` and `docs/CURRENT_STATUS.md`. The P1 repair changed only `observer.py`, the focused test file, this report and `CURRENT_STATUS.md` relative to the reviewed implementation. The Reviewer-owned `docs/reviews/M4A_1_REVIEW.md` was not edited. No unrelated refactor or generated dataset.

## Handoff to Reviewer

Independently re-review the P1 admission/close coordination and forced-interleaving tests, along with the original M4A.1 scope. The prior Review remains `docs/reviews/M4A_1_REVIEW.md` with its original NEEDS FIX verdict until a Reviewer updates it. Next authorized action is independent M4A.1 re-review, not M4A.2 implementation.

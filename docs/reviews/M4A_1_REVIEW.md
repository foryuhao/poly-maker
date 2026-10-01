# M4A.1 Review

Verdict: **PASS**
Reviewed baseline: `9c28bca` (`m4a-implementation-ready`).
Reviewed working tree / commit: uncommitted M4A.1 diff against `9c28bca`; eight Worker-changed files, 861 insertions and 6 deletions before this review. No implementation commit exists yet.
Architecture reference: `docs/architecture/M4A_ARCHITECTURE.md`; program boundary `docs/architecture/M4_ARCHITECTURE.md`; schema authority `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md` sections 5–6.
Implementation report reference: `docs/reports/M4A_1_IMPLEMENTATION_REPORT.md`.

The original NEEDS FIX review is retained below as historical evidence. The current verdict is in the re-review section at the end of this document.

## Original Review — Before P1 Repair

## Scope Reviewed

Read the research and architecture contracts, all changed implementation/test/report files, imports and call paths. Checked the baseline and uncommitted diff, tested queue/writer shutdown behavior independently, and reran tests and static checks. No implementation files were edited during review.

## Changed File Audit

| File | Classification | Assessment |
|---|---|---|
| `.gitignore` | EXPECTED M4A.1 | Excludes generated `research_data/`. |
| `docs/CURRENT_STATUS.md` | EXPECTED M4A.1 | Worker handoff status; this review updates it below. |
| `docs/reports/M4A_1_IMPLEMENTATION_REPORT.md` | EXPECTED M4A.1 | Required durable report. |
| `src/polymaker/research/__init__.py` | EXPECTED M4A.1 | Package exports only research classes. |
| `src/polymaker/research/schema.py` | EXPECTED M4A.1 | Capture schema and fixed FeatureVector; 437 of the 861 added lines. |
| `src/polymaker/research/observer.py` | EXPECTED M4A.1 | Config, bounded queue and observer. |
| `src/polymaker/research/writer.py` | EXPECTED M4A.1 | Dedicated-thread JSONL skeleton. |
| `tests/test_research_skeleton.py` | EXPECTED M4A.1 | Focused new tests. |

No out-of-scope changed file or omitted material file was found. The line count comes mainly from the explicit schema field list (437 lines), focused tests (155), observer (97), report (83) and writer (73).

## Findings

### P0

NONE.

### P1

- **Clean close can silently lose an accepted record.** In `observer.py`, `BoundedResearchQueue.put_nowait` checks `_closed` before calling the underlying queue. In `writer.py`, `ResearchWriter.close()` sets `_closed`, waits for the writer, and reports success once the queue appears empty. A producer already past the closed check can enqueue after the writer exits. I paused the producer immediately before its underlying `Queue.put_nowait`, called `writer.close()`, then released it: `close_result=True`, `accepted=1`, `queue_empty=False`, `persisted_rows=0`. This violates the M4A.1 clean close/persistence skeleton contract and gives later GAP/manifest work no truthful close result. Worker must make producer admission and close mutually coordinated while keeping producer admission nonblocking; add a deterministic regression test for the race. This is a lifecycle fix within M4A.1, not authorization to implement M4A.4 durability.

### P2

- `ResearchConfig.output_root` accepts an empty string, which resolves to the working directory when passed through `Path`. Validate it as a usable nonempty path before live integration, or document an explicit policy. This does not affect the default-off path.
- `BookState(valid=False)` does not enforce the architecture's null-scalar/empty-level convention. The schema can represent the correct state, but permits contradictory invalid-book records. Add validation or an M4A.3 capture/audit check before relying on such records.
- The new `tests/test_research_skeleton.py` contributes 39 diagnostics to the repository-wide strict `mypy .` run, largely missing annotations plus a union access and broadly typed fixture dictionary. The new production Research package checks clean; type the new test file when repairing the P1 or explicitly defer this project-wide type-check debt.

## Architecture Compliance

The changed code stays within M4A.1. No real telemetry hook, REST replacement capture, Fill Truth/order hook, Episode lifecycle, ring, periodic snapshot, T+300 observation, GAP persistence, durable checksum/fsync, audit, replay/model/scorer, BTC feed, orchestration, sizing or inventory control was implemented. `audit.py` is appropriately deferred. The accepted schema has generic quantity fields and no five-share constraint. The P1 writer-close race prevents a clean isolation skeleton verdict.

## Dependency / Isolation Review

Research imports only standard library, Pydantic and local Research modules. The existing trading system imports no Research module. There is no callback or reference to strategy, execution gateway, Fill Truth mutation, reconciliation, settlement, wallet/auth or network clients. Importing the package does not start the writer; constructing a disabled observer creates no queue. The observer's `observe()` method performs no disk/network I/O and catches its own errors. Writer file I/O is confined to its thread after explicit `start()`.

## Schema Review

`ResearchEvent` has nullable exchange/receipt clocks, required availability/monotonic time and event sequence, plus nullable connection generation. `StateSnapshot` has `knowledge_seq` and generic first/pair/YES/NO/paired/unpaired quantities. Episode keeps first exchange and knowledge times separate. Unknown nullable values remain `None` in the tested round trips; decimal strings and feature floats reject non-finite values. Frozen Pydantic models, tuples and recursively frozen event payloads support immutability after enqueue. The FeatureVector field list matches the research design's expanded fixed keys. No fake FutureLabel, model decision or execution estimate is emitted. The P2 invalid-book convention remains unenforced.

## Queue / Observer / Writer Review

Queue capacity is finite. Producer calls standard `Queue.put_nowait`, has no retry/fallback buffer, returns explicit success/failure, and counts accepted/full/closed outcomes. Observer is default-off and has no trading authority. Writer path is `research_data/<dataset_id>/<session_id>/` by default, with explicit start/close and no credentials. Writer creates event/episode/state JSONL files only; full manifest, GAP recovery, crash handling and durability are correctly deferred. However, successful `close()` does not guarantee that all accepted records were drained when a producer races with shutdown (P1).

## Live Behavior / Regression Assessment

No existing production trading module or test was modified. Research has no live integration, so strategy decisions, order intents, reconciliation and settlement behavior are unchanged in this diff. The full suite passed. The shutdown race affects only an explicitly started Research writer today; it must be corrected before live hooks are added.

## Tests Reviewed

The six new tests cover default-off behavior, schema round trips/immutability/nulls/generic quantities/non-finite rejection, config validation, full queue nonblocking failure, observer error containment, writer layout/lifecycle and public manifest config. They do not cover concurrent producer shutdown; the independent reproduction demonstrates the missing case. No existing test was changed.

## Tests Executed

- `.venv/bin/pytest -q`
- `.venv/bin/ruff check .`
- `.venv/bin/mypy .`
- `.venv/bin/ruff check src/polymaker/research tests/test_research_skeleton.py`
- `.venv/bin/mypy src/polymaker/research`
- `git diff --check`
- Independent in-memory race reproduction using an intercepted underlying queue admission; no source/test file changes.

## Test Results

- Pytest: **543 passed, 2 skipped** in 7.59s.
- Repository-wide Ruff: **92 errors**, all reported in unchanged files; new Research package and focused test file check clean.
- Repository-wide mypy: **1,427 errors in 37 files** (77 source files checked), including 39 errors in the new test file; targeted new-package source mypy check is clean.
- `git diff --check`: clean before review edits.
- Race reproduction: writer close returned true, one record was accepted afterward, queue remained nonempty, and zero event rows were persisted.

The repository-wide Ruff errors are in unchanged files. Most repository-wide mypy errors predate this diff, but 39 are in the new test file (P2). No implementation code was repaired during review.

## Implementation Report Accuracy

The report correctly lists files, baseline, default-off state, test counts, absent live hooks and later-stage deferrals. Its claim that `close()` drains pending records within a timeout is materially incomplete because concurrent admission can occur after a successful close. The P1 finding supersedes that claim. Its statement that queue counters permit later GAP work is directionally true, but close coordination must be fixed first.

## Required Fixes

1. Repair queue producer-close coordination so `ResearchWriter.close()` cannot return success while an accepted record remains unpersisted, even when producer admission overlaps close. Preserve nonblocking producer semantics.
2. Add a deterministic focused regression test for concurrent producer admission and writer close. Update the implementation report and test results after the repair.

## Deferred Issues

The two P2 issues above may be handled with M4A.1 repair or explicitly carried to the relevant later slice. Full GAP/manifest/checksum/audit functionality remains scheduled for M4A.4/M4A.5; it is not requested as a fix here.

## Original Verdict

**NEEDS FIX**. One P1 clean-close correctness issue blocked M4A.1 acceptance before repair. No P0 was found.

## Handoff

Next authorized action: Worker repairs only the P1 finding, updates the M4A.1 implementation report, and returns for independent M4A.1 re-review. M4A.2 remains unauthorized.


## Re-Review After P1 Repair

Reviewed working tree / commit: uncommitted repaired M4A.1 diff against `9c28bca` (`m4a-implementation-ready`) on 2026-10-01. The prior review report is part of this same working tree; no implementation checkpoint has yet been committed.

Repair summary: `BoundedResearchQueue` now protects the closed check, nonblocking queue insertion and counters with one admission lock; `close_producer()` takes the same lock. `ResearchWriter.close()` closes admission before signaling stop and joining the writer. Two event-controlled tests cover the formerly lost record and rejection after closure.

### P0

NONE.

### P1

NONE. The original clean-close race is resolved under the in-process M4A.1 skeleton contract.

### P2

The original P2 findings on empty output root and invalid-book consistency are unchanged. The new concurrency tests increase diagnostics in `tests/test_research_skeleton.py` under repository-wide strict mypy from 39 to 52 (13 additional test-only diagnostics); total repository-wide errors rise from 1,427 to 1,440. Production Research source remains clean under targeted mypy. This is still deferred P2 type-check debt and does not affect the lifecycle invariant.

### P1 Repair Verification

Admission and close linearize on the same short lock. If `put_nowait()` acquires it first, the item is inserted and counted before `close_producer()` can close admission; writer shutdown then drains it. If closure acquires it first, enqueue returns `False` and increments `rejected_closed`. Once `close()` returns `True`, admission remains closed and its writer thread has exited after draining the queue. No lock covers writer file I/O or `Thread.join`. Capacity stays finite and insertion uses standard `Queue.put_nowait`, with no capacity retry or writer-shutdown wait in the producer. The producer may briefly contend on the lifecycle lock, as allowed by the architecture's short admission guard; the lock does not span disk work or join.

The first new test pauses a producer inside underlying queue admission using Events, starts close, verifies close has not completed, releases admission, and checks that the accepted event reached JSONL before successful close. The second pauses writer output after admission closes and verifies a concurrent enqueue is promptly rejected. Both terminate with bounded joins. I also temporarily substituted the old queue methods in one Python process (without editing source/tests) and ran the first regression test: it failed as intended. Its event ordering forces the original race; the bounded wait is a deadlock guard rather than a random-iteration or sleep-based test.

Only `observer.py` admission coordination, the focused tests, Implementation Report and Current Status changed during repair. No M4A.2 hook or later-stage functionality was added. No existing live trading module changed, and Research still has no reverse execution, reconciliation, settlement or credential dependency.

### Tests Executed

- `.venv/bin/pytest tests/test_research_skeleton.py -q`: 8 passed.
- `.venv/bin/pytest -q`: 545 passed, 2 skipped in 7.41s.
- `.venv/bin/ruff check src/polymaker/research tests/test_research_skeleton.py`: clean.
- `.venv/bin/mypy src/polymaker/research`: clean, four source files.
- `git diff --check`: clean.
- Repository-wide mypy diagnostic count for the deferred P2: 1,440 errors in 37 files; 52 are in the new focused test file. Repository-wide Ruff was not rerun because the repair touched no file with the prior 92 findings; targeted Ruff is clean.

### Final Verdict

**PASS** for M4A.1 as represented by this uncommitted repaired working tree. P0: none; P1: none; the three P2 issues above are explicitly deferred. This certifies only this reviewed scope, not future changes or M4A.2.

### Handoff

Next authorized action: M4A.2 architecture-consistent implementation only. Preserve this M4A.1 working tree as the reviewed scope when creating its Git checkpoint.

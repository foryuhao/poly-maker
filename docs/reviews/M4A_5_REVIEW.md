# M4A.5 Review

Verdict: **PASS** (final re-review; earlier NEEDS FIX findings preserved below)

Reviewed predecessor baseline: `m4a4-pass`
Reviewed predecessor commit: `31dd22c6e6769936212a93e3266f294fdf349d14`
Reviewed working tree: branch `m4-dynamic-ev` at `31dd22c`, with the tracked M4A.5 changes and untracked artifacts listed below; no M4A.5 commit or tag.
Architecture reference: `docs/architecture/M4A_ARCHITECTURE.md`
Implementation report: `docs/reports/M4A_5_IMPLEMENTATION_REPORT.md`

## Scope reviewed

Inspected the offline auditor, CLI, writer-backed fixtures, M4A.4 artifact schema and the M4A engineering gates. Independently exercised a genuine writer/lifecycle session, corruptions of copied artifacts, dataset discovery, and the test suite. This verdict assesses the harness implementation, not whether real M4A data have been collected.

## Diff / Untracked Artifact Audit

Tracked changes against `m4a4-pass`: `.gitignore`, `docs/CURRENT_STATUS.md`, `src/polymaker/cli.py`. Untracked and reviewed: `src/polymaker/research/audit.py`, `tests/test_research_audit.py`, `docs/reports/M4A_5_IMPLEMENTATION_REPORT.md`. This Review file is newly untracked and must join any eventual reviewed checkpoint. No capture, writer, lifecycle, engine, FavoriteFirst, Fill Truth, marketdata or settlement code changed.

## Findings

### P0

- None found.

### P1

1. **Missing terminal-decision material snapshot is certified clean.** `audit.py` checks FILL and SAFETY transition snapshots but omits a terminal `BASELINE_DECISION` transition (`_episode_checks`, lines 592–600). M4A.3 explicitly emits a material `SAFETY` snapshot for this transition (`lifecycle.py`, lines 194–222), and M4A.5 must verify material snapshot completeness. Independent reproduction: create a clean session through the real writer/lifecycle fixture, change the sole snapshot at terminal `BASELINE_DECISION` event sequence 9 from `sample_kind=SAFETY` to `ORDER`, update the state-file checksum, then run `audit_session`. Baseline and corrupted sessions both return `integrity=PASS`, with zero findings. There is no terminal-decision snapshot in the corrupted evidence. This can falsely certify incomplete telemetry.
2. **Dataset CLI silently omits a session with a missing manifest.** `cli.py`, lines 52–55, selects only child directories already containing `manifest.json`. Independent reproduction: add a second session directory with `quality.json` but no manifest next to a clean writer session. `audit_collection([clean, missing])` correctly returns `integrity=FAIL`, while `research-audit <dataset>` reports `integrity PASS` and exits 2 only for insufficient collection/timing. With sufficient other sessions, the absent artifact would not affect dataset integrity or readiness. Dataset discovery must fail closed on a session-shaped child lacking required evidence, or use an authoritative session inventory and detect omissions.
3. **Unsupported caller-supplied provenance and timing can satisfy mandatory readiness gates.** `audit_collection` accepts `capture_mode=LIVE` plus a hash computed from the same local manifest as real-run attestation (lines 892–903); timing/isolation use caller-supplied booleans, arrays and identical traces (lines 915–951). The writer-backed synthetic fixture in `test_real_provenance_and_timing_gates` is counted as eligible and receives `timing PASS` from fabricated values. The 20-session test then reaches `overall PASS` using fabricated attestation, timing and trace data. Matching commit/config strings and a manifest checksum establish identity only; they do not establish a separately authorized real run, controlled peak traffic or actual measured traces. The report acknowledges the external trust requirement, but the machine-readable verdict promotes unsupported input to PASS with no provenance validation or explicit qualified status. This can falsely certify M4B readiness. A credible evidence contract may need an architecture decision because the accepted architecture does not define this external format.

### P2

- Cadence tests cover 100% and severe loss, but do not exercise the exact 99% boundary or a value immediately below it. The formula itself uses the strict `< .99` comparison correctly.
- The CLI reports a single missing-manifest session as a usage error rather than an audit finding. Repair of P1 #2 should make missing required evidence inspectable in the derived report.

## Raw Evidence Immutability

`audit_session` reads manifest, quality and JSONL; `write_audit` writes only `audit.json` and `audit_report.md` outside any audited session. A writer-backed test confirms raw bytes are unchanged. No historical network access or raw repair path was found. Output-path containment rejects writing inside a raw session.

## Manifest / Quality Eligibility

For a discovered session, the manifest is the authoritative COMPLETE certificate; FINALIZING quality is checked as noncertifying health evidence. Hashes, counts, identity, schema and loss/GAP indicators are compared. The dataset discovery omission in P1 #2 bypasses these checks entirely for a missing-manifest child.

## Causal Sequence / Lookahead Audit

Events are inspected in persisted order; no sort or renumbering was found. Sequence holes, reversal, GAP, future `knowledge_seq`, future availability and unsupported feature values fail in existing fixtures. Exchange occurrence time is not used as a substitute for local availability. No independent false PASS was reproduced in these gates.

## Episode / Fill / Inventory Reconstruction

The auditor uses a separate prefix replay of registered owned orders and accepted Fill Truth keys. Writer-backed tests cover duplicate revision idempotence, inventory mismatch, first-fill identity, revision order and censor exclusion. The terminal material-snapshot omission in P1 #1 remains a lifecycle completeness failure.

## Working-Order Reconstruction

Replay checks order identity, remaining quantity, lifecycle and cancellation; `cancel=True` is terminal even when status says LIVE. `working_orders` and unresolved state are checked separately. Existing corruption fixtures exercise a working-order mismatch.

## Book / Tick / Connection Reconstruction

YES and NO books replay independently from full book, delta and REST replacement evidence. Snapshot top-five, best levels, sequence/generation/source and historical tick are checked. Connection DOWN invalidates market books. Existing fixtures cover mutations and a historical tick mismatch.

## Snapshot Coverage / Gap Audit

The denominator runs from FIRST knowledge to market end or T+300, excluding only durably evidenced market-disconnected or censored slots. Observed-valid, observed-invalid, missing and excluded counts are reported; coverage below 99% and unexplained runs above two seconds fail. The terminal-decision omission can still pass these cadence gates because scheduled snapshots continue; material completeness needs its own check.

## Secret Audit

The recursive scan flags sensitive field names and recognizable private-key/Bearer/Basic values, and redacts matches from findings. A persisted `api_secret` fixture fails without printing its value. Public wallet identifiers are not flagged merely for being addresses. No actual secret artifact was found.

## Determinism

No random sampling, wall-clock verdict or live network lookup was found. Every snapshot is replayed, with bounded row and diagnostic limits. The same unchanged fixture produced the same session verdict when run again.

## Collection Sufficiency

The numerical gate requires 20 unique eligible Episodes across three distinct UTC market-days and distinguishes insufficient collection from integrity failure. Censored and degraded sessions are excluded when discovered. Provenance authentication is insufficient as detailed in P1 #3; an external JSON flag and self-computable hash can count a synthetic writer fixture as real.

## Latency Acceptance

Without external evidence, timing and isolation remain `UNESTABLISHED` and overall remains `INCOMPLETE`; that default is correct. With arbitrary caller-supplied matching booleans/numbers/traces, both become PASS. The gate therefore does not yet establish actual callback p99, incremental loop lag or observer isolation. See P1 #3.

## CLI / Output Semantics

The CLI writes separate, machine-readable and human-readable output; its exit codes distinguish PASS, FAIL and INCOMPLETE in the normal path. Missing-manifest dataset children are skipped before auditing (P1 #2). The output states that M4A readiness does not prove Dynamic Exit EV edge.

## M4A.1–M4A.4 Regression Assessment

Only the new offline auditor, CLI command, ignore rule and docs changed. Full suite and existing Research tests pass. No live trading semantic change was found.

## Tests Executed

- `.venv/bin/pytest tests/test_research_audit.py -q`: **23 passed**.
- `.venv/bin/pytest tests/test_research_skeleton.py tests/test_research_lifecycle.py tests/test_research_writer.py tests/test_research_causal_capture.py -q`: **61 passed**.
- `.venv/bin/pytest -q`: **621 passed, 2 skipped**.
- `.venv/bin/ruff check src/polymaker/research src/polymaker/cli.py tests/test_research_*.py`: clean.
- `.venv/bin/mypy src/polymaker/research src/polymaker/cli.py`: clean, eight source files.
- `git diff --check`: clean.
- Independent temporary-artifact probes: terminal material snapshot mutation returned false PASS; missing-manifest dataset child was skipped by CLI; a writer-backed synthetic session was counted as LIVE with caller-provided manifest hash and received `timing PASS`/`isolation PASS` from fabricated numbers and traces. Two audits of the unchanged session returned identical results. No repository implementation/test file was changed for these probes.

## Architecture Compliance

Offline, read-only reconstruction and the basic numerical gates align with M4A.5. The three P1 findings violate fail-closed material completeness, artifact discovery and engineering-evidence requirements. The optional LIVE/timing input format is not specified by the architecture; accepting it as sufficient to certify READY needs a verifiable provenance contract or explicit architecture clarification.

## Implementation Verdict

**NEEDS FIX.** Three P1 findings block an independent PASS despite passing tests.

## Actual M4A Readiness Status

- Telemetry integrity capability: **NOT ESTABLISHED** because the harness can falsely PASS incomplete evidence.
- Real collection sufficiency: **NOT YET AUDITED**; no real completed Research dataset was available under `research_data/` in this workspace.
- Latency gate: **UNESTABLISHED**; no verified controlled peak-traffic measurements were supplied.
- Actual M4B readiness: **NOT READY**. M4B remains unauthorized.

## Required Fixes

Check the terminal `BASELINE_DECISION` material snapshot; make dataset discovery account for missing-manifest sessions; prevent unauthenticated LIVE/timing/isolation assertions from becoming authoritative readiness PASS. Add deterministic negative tests for each false PASS.

## Deferred Issues

Exact 99% boundary coverage and richer malformed-artifact fixtures may be added during repair. M4A.4's documented final-rename directory-fsync and quality-schema P2 limitations remain outside this review.

## Final Verdict

**NEEDS FIX — M4A.5 repair only.** No M4A.5 checkpoint or M4B work is authorized.

## Re-Review After Fail-Closed Repair

Reviewed predecessor baseline: `m4a4-pass`
Reviewed predecessor commit: `31dd22c6e6769936212a93e3266f294fdf349d14`
Reviewed working tree: branch `m4-dynamic-ev` at `31dd22c`, with tracked `.gitignore`, `docs/CURRENT_STATUS.md`, `src/polymaker/cli.py` changes and the untracked M4A.5 files below. No M4A.5 commit or tag.

### P0

- None found.

### P1

1. **A terminal snapshot can carry stale decision state and still pass integrity.** The new material gate matches `(knowledge_seq, sample_kind, available_at_us)` but the independent replay never applies `BASELINE_DECISION` to a projected baseline state, and `check_snapshot` never compares `StateSnapshot.baseline_state` with the event prefix. Reproduction through the real writer/lifecycle fixture: keep the terminal `SAFETY` snapshot's sequence, kind and time unchanged; change only its `baseline_state` from `COMPLETE` to `UNKNOWN`; update the states-file hash in the manifest. `audit_session` returns `integrity=PASS`, zero issues. Thus a snapshot nominally tagged as including the terminal event can still describe the prior decision state. The material snapshot must be validated against the terminal event's actual as-known decision state, not only its labels. This leaves P1-A incompletely repaired.

### P2

- Dataset discovery treats **every** immediate subdirectory as a session. An unrelated `unrelated/notes.txt` directory beside a valid session causes dataset `integrity=FAIL`. The accepted layout normally contains only session directories, so this is fail-closed, but the requested non-session-directory distinction and its test are still missing. The existing test covers only a root-level unrelated file.
- Exact 98.99%, 99.00%, and 99.01% checks exercise `_coverage_passes` directly. The writer-backed cadence tests do not independently bracket the 99% threshold end to end. The production cadence calls the integer helper, so this is a coverage limitation.

### P1-A Material Snapshot Verification

Independent writer-backed probes: valid terminal snapshot `PASS`; changed kind to `ORDER` or `PERIODIC` `FAIL` with `terminal_decision_material_snapshot`; availability one microsecond before the decision `FAIL` with that gate; a censored Episode with no terminal decision `PASS` without the gate. Sequence, kind and availability are compared exactly, preventing a nearby scheduled snapshot from substituting. However, changing only the terminal snapshot's `baseline_state` to `UNKNOWN` returned `PASS` with no issue despite its knowledge sequence naming the `COMPLETE` decision. This fails the as-known material-state requirement.

### P1-B Dataset Enumeration Verification

The CLI now treats immediate dataset child directories as candidates even without a readable manifest. Independent probe with a valid session and missing, malformed, unsupported, and identity-mismatched manifest directories returned exit 1, dataset `integrity=FAIL`, five candidates, one parsed manifest and four pre-manifest failures; every directory appeared by name in the session output. Candidate, parsed, failed, integrity-clean and collection-ineligible counts reconcile. An unrelated root-level file is ignored by the test. An unrelated child directory is treated as a failed session (P2 above).

### P1-C Evidence Provenance Verification

`audit_collection` no longer parses caller claims into hard-gate PASS. It reports only whether optional `real_sessions` and `timing_evidence` were supplied, classifies them `CALLER_ASSERTION`, and sets `accepted_for_gates=False`. A synthetic writer session plus claimed LIVE manifest hash, excellent callback/loop-lag values and identical fabricated OFF/ON/fault traces independently returned `live_attestation=UNESTABLISHED`, `collection=UNESTABLISHED`, `timing=UNESTABLISHED`, `isolation=UNESTABLISHED`, and `overall=INCOMPLETE`.

### LIVE Attestation

No architecture-defined trusted runtime LIVE provenance exists in the current M4A.4 artifacts. Verified eligible Episode and market-day counts remain zero; integrity-clean Episodes/days are reported only as candidates. Even 20 caller-labeled sessions across three days cannot certify collection. This closes the prior false-ready path, while real collection readiness remains unestablished.

### Latency / Isolation Evidence

The CLI describes optional real-session and timing files as non-authoritative. Caller numbers and trading traces cannot produce timing or isolation PASS. No controlled peak-traffic measurement or verified observer OFF/ON/fault trace was supplied, so both gates remain `UNESTABLISHED`. Defining trusted evidence provenance requires a later architecture-authorized contract; the repair did not change raw telemetry or live behavior.

### M4B Readiness Fail-Closed Logic

With current evidence, `overall` is `FAIL` if any audited integrity finding exists and `INCOMPLETE` otherwise. The code has no caller-controlled path to `overall=PASS`, so absent trusted real collection/performance/isolation evidence cannot authorize M4B. This conservatism is acceptable for implementation correctness; the remaining P1 concerns a false session-integrity PASS for stale terminal state.

### 99% Boundary Verification

`_cadence` invokes `_coverage_passes(valid, expected)`, which compares `valid * 100 >= expected * 99` using integers. Focused tests confirm 98.99% fails and 99.00% and 99.01% pass. The denominator still uses scheduled connected post-FIRST slots with durable market-disconnection and censor exclusions.

### Prior Audit Regression Verification

Inspection found the pre-existing event order/GAP, future knowledge, Episode revision, Fill Truth, inventory, owned-order, independent YES/NO book, tick, connection, >2-second gap, secret scan and manifest/quality gates unchanged outside the narrow repair. Raw artifacts remain read-only; output remains separate. No network or trading gateway dependency and no Dynamic EV edge inference were introduced. Full Research and full test suites pass. No FavoriteFirst, execution, marketdata, capture, writer, settlement or live strategy file changed.

### Tests Executed

- `.venv/bin/pytest tests/test_research_audit.py -q`: **36 passed**.
- `.venv/bin/pytest tests/test_research_*.py -q`: **97 passed**.
- `.venv/bin/pytest -q`: **634 passed, 2 skipped**.
- `.venv/bin/ruff check src/polymaker/research src/polymaker/cli.py tests/test_research_*.py`: clean.
- `.venv/bin/mypy src/polymaker/research src/polymaker/cli.py`: clean, eight source files.
- `git diff --check`: clean.
- Independent temporary writer-artifact probes reproduced the repaired missing/wrong-kind/early terminal snapshot paths, missing/bad manifest enumeration and caller-claim rejection. A separate stale-`baseline_state` mutation reproduced the remaining false PASS. No implementation or test file was modified by this Reviewer.

### Untracked Artifact Audit

Reviewed untracked files: `src/polymaker/research/audit.py`, `tests/test_research_audit.py`, `docs/reports/M4A_5_IMPLEMENTATION_REPORT.md`, and this `docs/reviews/M4A_5_REVIEW.md`. All remain untracked and must be included in any later reviewed checkpoint after PASS. Tracked M4A.5 changes remain `.gitignore`, `src/polymaker/cli.py`, and `docs/CURRENT_STATUS.md`.

### Implementation Verdict

**NEEDS FIX.** P1-B and P1-C are closed. P1-A still permits an as-known terminal decision snapshot with stale decision state to receive integrity PASS. Repair only M4A.5; no checkpoint or M4B work yet.

### Actual M4A Readiness

**NOT READY.** No real LIVE collection was audited, and callback latency, incremental loop lag and isolation evidence lack trusted provenance. Unit tests do not establish these real engineering gates. M4B remains unauthorized.

## Final Re-Review After Terminal Semantic Repair

Reviewed predecessor baseline: `m4a4-pass`
Reviewed predecessor commit: `31dd22c6e6769936212a93e3266f294fdf349d14`
Reviewed working tree: branch `m4-dynamic-ev` at `31dd22c`, with tracked `.gitignore`, `docs/CURRENT_STATUS.md`, and `src/polymaker/cli.py` changes plus the four untracked files listed below. No M4A.5 commit or tag.

### P0

- None found.

### P1

- None remaining. The previous terminal-snapshot false PASS is closed by independent writer-backed reproduction with an updated artifact checksum.

### P2

- An artifact-free directory cannot be identified as a damaged session without an authoritative session inventory; the CLI ignores it as unrelated noise. A directory retaining any M4A raw artifact is audited. This discovery limit is documented and does not hide a session with surviving Research evidence.
- Trusted LIVE collection, controlled peak-traffic latency, and trading-isolation evidence are absent from the M4A.4 artifact contract. The auditor reports these gates as `UNESTABLISHED` and cannot certify actual M4A readiness. This is an evidence/architecture handoff, not an implementation PASS for the real dataset.
- The documented M4A.4 final-rename directory-fsync limitation remains unchanged.

### Terminal Snapshot Semantic Consistency

`_Replay` independently projects `BASELINE_DECISION` strategy state, pair trigger, locked-PnL peak and pure quote, plus `EFFECTIVE_QUOTE` quote, from persisted events. Each StateSnapshot is compared with the replay prefix. A mismatch at the matching terminal `SAFETY` snapshot reports `terminal_decision_material_snapshot`; other mismatches report `baseline_reconstruction`. Fill, owned-order, book and tick state retain their separate replay checks. Artifact SHA-256 is checked independently and cannot substitute for this semantic gate.

### As-Known State Verification

Replay advances only through persisted events with `event_seq <= snapshot.knowledge_seq`; the auditor also rejects a prefix event whose availability exceeds the snapshot's availability. It does not derive expected state from later snapshots, Episode revisions or final session outcome. The writer-backed earlier-FILL mutation claiming a later `COMPLETE` state fails `baseline_reconstruction`.

### Terminal Variant Verification

The terminal set comes from canonical `StrategyState`: `COMPLETE`, `HALTED`, and `UNPAIRABLE_RESIDUAL`. Valid writer-generated sessions for all three pass. Incompatible `UNKNOWN`, `RUNNER`, `PAIR_STOP`, or another terminal variant in the matching material snapshot fails. Trigger, peak and quote mutations with correct sequence/kind/time also fail the terminal semantic gate. Decimal-normalized quote identity, side, price and quantity are compared without requiring byte-identical numeric spelling.

### Reviewer Mutation Regression

I independently generated each terminal variant through the real capture/lifecycle/writer path, audited it clean, changed only the terminal material snapshot's `baseline_state` to `UNKNOWN`, and updated the manifest's states-file SHA-256. The checksum matched actual bytes in all three cases. Each changed session returned `integrity=FAIL` with `terminal_decision_material_snapshot`, rather than an artifact-hash finding. This directly reverses the prior false PASS.

### Dataset Discovery Regression

The CLI considers an immediate child a candidate when it contains `manifest.json`, `quality.json`, `events.jsonl`, `episodes.jsonl`, or `states.jsonl`. A writer session plus a `quality.json`-only child produced dataset `integrity=FAIL` and named the damaged child; an unrelated notes directory was ignored. Existing deterministic tests cover missing, empty, malformed, unsupported-version and identity-mismatched manifests. Candidate and failed-before-manifest counts reconcile.

### Evidence Provenance Regression

Optional CLI LIVE and timing files remain labelled non-authoritative; `audit_collection` records them only as `CALLER_ASSERTION` with `accepted_for_gates=False`. My writer-backed probe supplied LIVE and favorable timing assertions and still returned collection, timing and isolation `UNESTABLISHED`, overall `INCOMPLETE`. There is no production CLI flag, test-fixture marker or metadata path that promotes these claims to verified evidence or `READY`.

### 99% Full-Path Verification

The writer-backed 100-slot test passes at 99 valid connected slots and fails the cadence gate at 98; it runs artifact parsing and expected-cadence calculation, not only the percentage helper. Exact integer helper tests cover 98.99% fail, 99.00% pass and 99.01% pass. The denominator remains connected post-FIRST scheduled slots, with exclusions requiring persisted disconnect or censor evidence; unexplained gaps longer than two seconds still fail.

### Prior Audit Regression

Focused inspection and tests found event order/GAP, future knowledge, Episode lifecycle, Fill Truth, inventory, owned-order, independent YES/NO book, tick, connection/resync, secret redaction and manifest/quality checks intact. Missing, early or wrong-kind terminal snapshots fail; censoring before any terminal decision does not fabricate a requirement. The auditor reads raw artifacts and writes derived output separately. No current network dependency, order submission or Dynamic EV edge inference was introduced. No capture, writer, FavoriteFirst, execution, Fill Truth, marketdata or settlement file changed.

### Tests Executed

- `.venv/bin/pytest tests/test_research_audit.py -q`: **50 passed**.
- `.venv/bin/pytest tests/test_research_*.py -q`: **111 passed**.
- `.venv/bin/pytest -q`: **648 passed, 2 skipped**.
- `.venv/bin/ruff check src/polymaker/research src/polymaker/cli.py tests/test_research_audit.py`: clean.
- `.venv/bin/mypy src/polymaker/research src/polymaker/cli.py`: clean, eight source files.
- `git diff --check`: clean.
- Independent temporary writer-artifact mutation and CLI/provenance probes described above; no repository source/test file modified.

### Untracked Artifact Audit

Reviewed untracked: `src/polymaker/research/audit.py`, `tests/test_research_audit.py`, `docs/reports/M4A_5_IMPLEMENTATION_REPORT.md`, and this `docs/reviews/M4A_5_REVIEW.md`. Tracked M4A.5 changes: `.gitignore`, `src/polymaker/cli.py`, `docs/CURRENT_STATUS.md`. All seven files must be included in the eventual complete reviewed checkpoint; ordinary `git diff` omits the four untracked files.

### Implementation Verdict

**PASS.** All previously blocking P1 findings are closed on the reviewed working tree. This is a PASS for the M4A.5 implementation, not a certification of an actual real Research dataset. Next action is a complete reviewed M4A.5 Git checkpoint, then collection and audit of real evidence.

### Actual M4A Readiness

**NOT READY.** No real Research dataset was audited in this workspace. Trusted LIVE Episode count and three-day span are unestablished; controlled callback p99, incremental loop lag p99, and isolation evidence are also unestablished. Unit-test fixtures do not satisfy these gates.

### M4B Authorization

**NO.** M4B requires a later affirmative real M4A engineering acceptance, separate from this implementation PASS.

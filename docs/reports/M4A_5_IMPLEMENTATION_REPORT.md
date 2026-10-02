# M4A.5 Implementation Report

Status: **IMPLEMENTED — AWAITING RE-REVIEW**
Commit / working tree base: `31dd22c6e6769936212a93e3266f294fdf349d14` plus the M4A.5 working tree.
Reviewed predecessor baseline: `m4a4-pass`
Reviewed predecessor commit: `31dd22c`
Architecture reference: `docs/architecture/M4A_ARCHITECTURE.md`; research authority: `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md` section 12.

## Scope implemented

An offline, read-only auditor inspects completed M4A session directories and aggregates a dataset verdict. It does not mutate raw artifacts or import live execution services. It separates session **integrity**, real collection **sufficiency**, controlled **timing/isolation** evidence, and overall M4A telemetry readiness. A telemetry PASS does not establish a Dynamic Exit EV edge.

## Files added

- `src/polymaker/research/audit.py`
- `tests/test_research_audit.py`
- `docs/reports/M4A_5_IMPLEMENTATION_REPORT.md`

## Files modified

- `src/polymaker/cli.py`: one lazy-imported `research-audit` offline command.
- `.gitignore`: excludes generated `research_audits/` reports.
- `docs/CURRENT_STATUS.md`: M4A.5 implementation handoff.

## Important implementation decisions

### Audit inputs and outputs

The auditor consumes `research_data/<dataset_id>/<session_id>/{manifest.json,quality.json,events.jsonl,episodes.jsonl,states.jsonl}`. It streams SHA-256 verification, validates JSON records against the actual strict v1 Pydantic schemas, rejects duplicate JSON keys/nonfinite constants, and keeps bounded diagnostic examples with exact failure counts. Results are separate `audit.json` and `audit_report.md` under `research_audits/` by default. Output inside a raw session is rejected. No wall-clock audit timestamp, random sampling or network query affects a verdict. All snapshots in a session are compared to their event prefixes, so no discretionary sampling is needed. Collection audit retains only per-session summaries.

### Integrity gates

Only `manifest.close_status=COMPLETE` is a writer completion certificate; `quality.status=FINALIZING` is the expected non-certifying healthy companion. OPEN/INCOMPLETE manifest, degraded/contradictory quality, loss counters, GAP rows, rejected records, writer errors, absent artifacts, hash/count mismatches, malformed records and missing source provenance fail integrity. The auditor verifies directory/session/dataset identity, schema and payload versions, public-config SHA-256, source commit identity, session clock bounds and durable event watermark. Optional absolute `SourceRef` paths require a closed-source hash and an in-bounds byte range. Relative source references fail because the v1 manifest defines no stable base directory for them.

The event stream is checked in persisted order, including causal GAP ranges; it is never sorted or repaired. Exchange time is retained separately and never used to order local knowledge. Snapshot `knowledge_seq` and availability must not cross future events or regress. Episode identity is recomputed from condition and first registered FIRST order; revisions must advance immutably, first-finalization time must be write-once, and late COMPLETE→CENSORED requires writer-failure attribution. Required FIRST/FILL, changed-order, SAFETY and FINAL snapshots are checked. Early CENSORED Episodes remain explicit and cannot become collection-eligible.

### Reconstruction methodology

An auditor-owned prefix projector, separate from `EpisodeLifecycle`, replays the persisted YES and NO full books, deltas, REST replacements, tick changes, connection state/generations, owned orders, cancellations, safety transitions and unique Fill Truth keys. It compares every StateSnapshot at its declared causal cutoff with independently computed top five levels, derived best prices/quantities/mid/spread, book sequence/generation/source, historical tick, accepted FIRST/PAIR inventory and costs, working-order quantities/roles/quotes/lifecycle and order-resolution state. `cancel=True` makes an order terminal even with `status=LIVE`. Duplicate revisions of one fill key count once; changed accepted economics for the same key fail. Failed/quarantined fills cannot qualify as accepted economics. Directly observed FeatureVector fields are cross-checked against the as-known state; unsupported M4A model/BTC values fail. No YES↔NO complement reconstruction is used.

### Cadence and collection methodology

The denominator is the fixed 1-second grid after the first fill through the earlier of market end and T+300. A CENSORED capture ends its own expectation at the durable censor time. A second is excluded as disconnected only from durable market CONNECTION evidence; missing connection evidence cannot create apparent healthy coverage. Every connected expected slot must have a valid periodic snapshot to count toward the >=99% gate. The report includes expected, valid, invalid, missing, disconnected/censored exclusions, coverage and longest consecutive unexplained missing run. A run >2 seconds fails. Terminal FavoriteFirst decisions do not stop the grid. UTC market-day is derived from the Episode's market start.

Real collection counts only integrity-clean, COMPLETE, fill-truth-COMPLETE Episodes without quality reasons, and only when an external authorized-run attestation maps the session ID to `capture_mode=LIVE` and its exact final manifest SHA-256. This prevents synthetic/paper fixtures from automatically counting. At least 20 unique Episodes and 3 distinct UTC market-days are required. Unattested, censored and integrity-failed Episodes are excluded with counts/reasons. A valid but small collection is `INSUFFICIENT_COLLECTION`, not corrupted data.

### Controlled timing and isolation

M4A.4 raw records do not contain callback-duration samples or controlled loop-lag/observer OFF traces. The auditor therefore reports these engineering gates `UNESTABLISHED` without external controlled evidence and cannot produce overall PASS. An optional timing evidence file must declare peak-traffic controlled ON/OFF measurement, match the audited source commit and config-hash sets, and provide callback and paired baseline/observer-ON loop-lag samples. Incremental lag is calculated per pair as `max(0, ON−baseline)`; it is not accepted as an unverified precomputed field. The auditor reports sample count, nearest-rank p50/p95/p99/max and enforces callback p99 <1 ms and incremental lag p99 <=5 ms. Isolation requires supplied OFF, ON and observer-fault decision/intent traces to match exactly. This is a verifier for independently produced controlled measurements, not an instrumentor or a claim that the current repository already has peak-traffic samples.

### CLI

`polymaker research-audit <session-or-dataset-path> [--output-dir research_audits] [--real-sessions-file FILE] [--timing-file FILE]` audits one session or immediate session children of a dataset directory. Derived reports are written outside raw evidence. Exit codes: 0 overall PASS, 1 hard integrity/timing/isolation FAIL, 2 incomplete collection or unestablished engineering gates; malformed invocation/configuration uses Typer's usage error. Optional real-session JSON is a mapping from session ID to `{"capture_mode":"LIVE","manifest_sha256":"..."}`. Optional controlled timing JSON uses `controlled_on_off=true`, `peak_traffic=true`, sorted `code_commits` and `config_sha256` arrays, `callback_ms`, paired `baseline_loop_lag_ms` and `on_loop_lag_ms` arrays, plus `off_decisions_and_intents`, `on_decisions_and_intents` and `fault_decisions_and_intents` arrays.

## Deviations from architecture

- The architecture describes `quality.json` as an audit result, but the accepted M4A.4 writer uses it for persistence health. M4A.5 leaves that raw artifact immutable and emits separate `audit.json`/`audit_report.md`; this follows the M4A.4 reviewed authority relationship.
- The architecture does not define a timing telemetry file or LIVE provenance attestation format. The optional external evidence formats above are narrow offline inputs. In their absence the corresponding gates remain unestablished; the auditor never infers them from unrelated event timestamps or fixture names.

## Tests added

`tests/test_research_audit.py` creates actual writer/lifecycle files, verifies a valid session and full post-terminal observation window, checks raw-byte immutability and CLI outputs, and corrupts checksummed evidence to exercise causal sequence, future knowledge, clocks, inventory, working orders, independent books/ticks, features, Episode progression, JSON parsing, secrets, missing artifacts, quality contradiction, cadence and known GAP rejection. It also checks source-reference bounds, duplicate fill idempotence, terminal cancel with LIVE status, early censoring, LIVE attestation, timing thresholds, and 20-Episode/3-day aggregation.

## Tests executed

- `.venv/bin/pytest tests/test_research_audit.py -q`: **23 passed**.
- `.venv/bin/pytest tests/test_research_*.py -q`: **84 passed**.
- `.venv/bin/pytest -q`: **621 passed, 2 skipped**.
- `.venv/bin/ruff check src/polymaker/research src/polymaker/cli.py tests/test_research_*.py`: clean.
- `.venv/bin/mypy src/polymaker/research src/polymaker/cli.py`: clean (8 source files).
- `git diff --check`: clean.

## Test results

The focused real-writer fixture passed all applicable session integrity gates. Its collection and timing/isolation gates correctly remain incomplete without real authorized-run and controlled benchmark evidence. Corrupted variants fail the specific gate without rewriting raw evidence.

## Known limitations / unresolved issues

- No 20 real first-fill Episodes across 3 market-days, authorized-run attestations or peak-traffic controlled timing/ON-OFF traces were supplied. **No actual M4A readiness PASS is claimed.** The harness can calculate these gates when evidence is provided.
- One session is reconstructed in memory; files are streamed for parse/hash, and fail closed above 500,000 event rows or 20,000 Episode/state rows each. This bounds offline memory but may reject unusually high-volume valid sessions until a streaming reconstruction revision.
- Relative SourceRef paths cannot be independently resolved without a manifest-defined base; they fail integrity. Current M4A.4 records use empty/null references.
- LIVE attestation proves manifest identity against an external authorized-run list, not that a local file was physically generated by live trading. The operator/reviewer must verify the attestation source. Controlled timing samples likewise require independently produced credible measurement evidence.
- The final manifest rename retains the documented M4A.4 one-sided crash guarantee; the auditor requires a visible COMPLETE manifest and does not claim stronger durability.

## Safety / behavior impact

Live trading semantics changed: **NO**. FavoriteFirst entry/exit, PairStop, Runner, pricing, quantity, submit/cancel, Fill Truth acceptance, market selection, settlement, capture and writer code are unchanged. The only CLI change adds an offline command. The auditor has no gateway, engine, store-write, network or raw-artifact mutation path.

## Git diff summary

M4A.5 changes are limited to the new offline auditor/tests/report, one CLI command, generated-audit ignore rule and current status. New files remain untracked for independent Reviewer inspection. No M4A.5 PASS tag or checkpoint was created.

## Handoff to Reviewer

Review the exact M4A.5 working tree against `m4a4-pass` (`31dd22c`), including untracked auditor, tests and this report. Independently inspect fail-closed behavior, actual writer artifact reconstruction, evidence provenance, cadence denominator, timing/isolation UNESTABLISHED behavior and raw immutability. The next authorized action is independent M4A.5 Review only; M4B implementation remains unauthorized.

## Repair After Initial Review

The initial independent Review (`docs/reviews/M4A_5_REVIEW.md`) found three P1 false-certification paths. This section supersedes the earlier optional-attestation and timing-acceptance descriptions; it preserves them above as the initial implementation history. The repaired implementation has no route to full M4A readiness PASS from the currently available M4A.4 schema. No raw artifact format or live Research path changed.

### P1-A Material Snapshot Gate

The auditor had checked FILL and explicit SAFETY transitions, but not a terminal `BASELINE_DECISION` state change. It now requires a `SAFETY` material snapshot with the same event `knowledge_seq` and `available_at_us` for a transition to `COMPLETE`, `HALTED` or `UNPAIRABLE_RESIDUAL`. FILL, explicit SAFETY and changed-order material checks also require the event's availability time, so an unrelated later snapshot cannot stand in. Events after a durable censor boundary do not create fictitious terminal requirements. Writer-generated corruption tests change the terminal snapshot's kind or time and assert integrity FAIL; a valid terminal snapshot and a legitimately censored Episode with no terminal event pass this gate.

### P1-B Dataset Session Enumeration

The CLI previously selected only children already holding `manifest.json`. It now treats every immediate directory under a dataset root as a session candidate; unrelated root-level files are ignored. A directory with any M4A artifact is treated as a single session even if its manifest is absent. The auditor records candidate, manifest-parsed, pre-manifest-failure, integrity-clean and collection-eligible/ineligible session counts. Missing, empty, malformed, unsupported-version and identity-mismatched manifests become named session failures in `audit.json` and `audit_report.md`; they cannot disappear from dataset integrity. This conservative directory rule assumes the architecture-defined dataset root contains session directories, not auxiliary subdirectories.

### P1-C Evidence Provenance / Attestation

The accepted M4A.4 raw schema does not contain a trusted runtime LIVE attestation or a controlled peak-traffic callback/loop-lag/observer-isolation evidence contract. The prior CLI files were self-assertions: their manifest hash and code/config strings could be computed from the same local fixture. The auditor now classifies both optional files as `CALLER_ASSERTION`, records only that they were supplied, and never uses their contents for mandatory gate PASS. Integrity-clean Episodes and UTC days remain visible as **candidates**, while verified LIVE Episode/day counts remain zero, collection and LIVE attestation remain `UNESTABLISHED`, and timing/isolation remain `UNESTABLISHED`. Overall readiness is `INCOMPLETE` for integrity-clean input, and `FAIL` for integrity failure. CLI help labels these files non-authoritative. Tests show forged LIVE labels, excellent timing numbers, and matching fake traces cannot promote any gate. This intentionally defers real readiness certification until an architecture-authorized trusted evidence contract exists; introducing one would require architecture change control, not an M4A.5-only schema invention.

### 99% Boundary Coverage

Cadence now compares `valid * 100 >= expected * 99` with integers. Deterministic tests cover 98.99% (fail), 99.00% (pass) and 99.01% (pass), without floating-point boundary ambiguity.

### Remaining Limitations

- Current M4A.4 artifacts cannot establish LIVE provenance or controlled latency/isolation acceptance. The harness remains useful for session integrity and potential collection counts, but actual M4A readiness is **NOT READY** until the missing trusted evidence is defined and collected.
- Immediate dataset child directories are conservatively treated as session candidates; an auxiliary subdirectory will fail closed rather than be skipped.
- The previously documented M4A.4 final-rename directory-fsync limitation and persistence-health `quality.json` semantics remain unchanged.

Repair validation: `.venv/bin/pytest tests/test_research_audit.py -q`: **36 passed**; `.venv/bin/pytest tests/test_research_*.py -q`: **97 passed**; `.venv/bin/pytest -q`: **634 passed, 2 skipped**. Targeted Ruff and mypy clean; `git diff --check` clean. These are the expected counts after the final CLI assertion test and should be rechecked before handoff. Live trading semantics changed: **NO**. No M4B work, checkpoint or PASS tag was created. Next authorized action: independent M4A.5 re-review only.

## Final Repair — Terminal Snapshot Semantic Consistency

The independent re-review preserved the prior NEEDS FIX history and found that matching sequence, kind, and time did not prove a terminal snapshot's **content** was current. This section supersedes the prior claim that material-snapshot validation was complete. Status remains **IMPLEMENTED — AWAITING RE-REVIEW**.

### Root Cause

The auditor checked that a terminal `BASELINE_DECISION` had a `SAFETY` snapshot at the same `knowledge_seq` and `available_at_us`, but its replay did not project FavoriteFirst decision fields. A stale `baseline_state=UNKNOWN` in a `COMPLETE` material snapshot therefore survived even after the state-file checksum was updated.

### Event-to-Snapshot Semantic Contract

The offline replay now projects the existing `BASELINE_DECISION` fields copied by `EpisodeLifecycle`: `strategy_state` → snapshot `baseline_state`, `pair_trigger` → `baseline_pair_trigger`, `peak_locked_pnl` → `baseline_peak_quoted_pnl_usd`, `pure_quote` → `baseline_quote`, and `EFFECTIVE_QUOTE.quote` → `effective_quote`. Quote comparison uses token, BUY side, price, and quantity with decimal normalization. The auditor compares these fields at **every** snapshot prefix; a mismatch at the terminal material snapshot uses `terminal_decision_material_snapshot`, while a mismatch elsewhere uses `baseline_reconstruction`. Existing fill inventory, owned-order and book projections continue to validate their respective state consequences independently. Hash consistency remains a separate artifact gate.

### As-Known State Derivation

Replay advances only through events with `event_seq <= snapshot.knowledge_seq`. The expected decision fields are updated when their persisted decision event is encountered. The auditor does not use a later Episode revision or final outcome to validate an earlier StateSnapshot. A new regression mutates the earlier FILL snapshot to claim the later terminal `COMPLETE` state and receives `baseline_reconstruction` failure.

### Terminal Variant Handling

The terminal set is taken from the canonical `StrategyState` enum: `COMPLETE`, `HALTED`, and `UNPAIRABLE_RESIDUAL`. Writer-generated fixtures for all three pass unchanged. Stale `UNKNOWN`, `RUNNER`, `PAIR_STOP`, or a wrong terminal variant in the matching material snapshot fail. A legitimately censored Episode without a terminal decision does not acquire a fabricated terminal-snapshot requirement.

### Reviewer Mutation Regression

The exact reviewed mutation is now a deterministic writer-backed test: change the terminal material snapshot's `baseline_state` from `COMPLETE` to `UNKNOWN`, update the state-file hash, and assert session integrity FAIL with the terminal material gate. Additional tests mutate trigger, peak, and quote fields while retaining the correct event sequence/time/kind; these also fail semantic consistency. Valid terminal content passes.

### Dataset Discovery P2

The CLI now considers an immediate child directory a session candidate when it contains at least one stable M4A raw artifact marker: manifest, quality, events, episodes, or states. A child with `quality.json` but no manifest still fails dataset integrity. An unrelated `notes.txt` directory is ignored. If a putative session directory has **none** of those artifacts, it is indistinguishable from unrelated noise under the current architecture and is not discovered; an authoritative session inventory would require architecture change control.

### 99% Full-Path P2

A writer-generated 100-slot Episode is audited after removing one or two periodic rows, renumbering snapshot identities, and updating the fixture's count/hash metadata. One missing slot yields 99/100 connected coverage and session integrity PASS; two separated missing slots yield 98/100 and a cadence FAIL. The exact integer helper tests at 98.99%, 99.00%, and 99.01% remain.

### Remaining Limitations

- Trusted LIVE provenance and controlled latency/isolation evidence are not present in M4A.4 artifacts; collection and engineering gates remain `UNESTABLISHED`, so actual M4A readiness is **NOT READY**. Caller assertions remain non-authoritative.
- Dataset discovery cannot prove that an artifact-free directory was once a Research session; it ignores such directories rather than classifying unrelated noise as corrupt telemetry.
- M4A.4's documented final-rename durability limitation remains unchanged.

Validation after this repair: `.venv/bin/pytest tests/test_research_audit.py -q`: **50 passed**; `.venv/bin/pytest tests/test_research_*.py -q`: **111 passed**; `.venv/bin/pytest -q`: **648 passed, 2 skipped**. Targeted Ruff, Research/integration mypy and `git diff --check` passed. No Research capture/writer, FavoriteFirst, Fill Truth, execution, marketdata, or settlement file changed. Live trading semantics changed: **NO**. No PASS tag, final milestone report, checkpoint or M4B work was created. Next authorized action: independent M4A.5 re-review only.

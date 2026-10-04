# Execution Replay Data Audit — 2026-10-03

Status: 32 markets verified for execution replay; 10 capture sessions excluded as too short.
Git base: `97ab0f98168f989b1878e4058022044c8372dcd3` (`m4a-a1-pass`). The workspace already contained unrelated uncommitted implementation changes; this audit did not modify source code or raw capture data.

## Scope and method

Audited every immediate M4A capture session under `research_data/m4a/` as present on 2026-10-03. First ran the existing read-only M4A collection audit. Then ran the existing Backtest 01 execution replay against the complete/gap-free sessions meeting its established minimum of 50,000 persisted events, directing generated outputs to `research_outputs/execution_replay_audit_20261003/`. The replay itself checks that each selected market has YES and NO book records and reconstructible BBO decisions. No network access, live command, order, or raw-data repair was used.

## Findings

- 42 capture sessions cover 38 unique market slugs and contain 8,284,525 persisted records in total.
- Every manifest reports `close_status=COMPLETE`; all report zero gap/lost-event counts.
- 32 sessions passed the existing execution-replay selector and represent 32 distinct markets. Every selected market reconstructed both YES and NO books and had at least one BBO decision. The replay completed for all 32.
- 10 sessions are excluded by the replay selector's `persisted_count < 50,000` rule. Five are four-record attempts for the same market (`1790929200`); the other five contain 86–1,215 records each. These records do not contain enough observed market history to recover a five-minute execution path. They cannot be made replayable by relabeling, combining unrelated sessions, or changing manifests.
- The full M4A telemetry auditor reports integrity PASS for 29/42 sessions and FAIL for 13/42. Failures include missing material order snapshots, inadequate post-fill periodic snapshot coverage, and one session exceeding the auditor's 500,000-event in-memory limit. These M4A telemetry gates are stricter than the execution replay's market-book requirements; they do not change the replay selector's 32-market result. The detailed reports are in `research_audits/research_data/m4a/`.
- The dedicated replay output has 0/32 settlement-PnL-usable markets because it was run without the separately identity-validated Backtest 01 settlement-label file. It establishes execution replay availability, not outcome-labeled profitability evidence.

## Artifacts

- `research_outputs/execution_replay_audit_20261003/dataset_audit.csv` — all 42 sessions, selected/excluded status, reason, event and book counts.
- `research_outputs/execution_replay_audit_20261003/summary.json` — replay sample counts and run summary.
- `research_outputs/execution_replay_audit_20261003/{market_results,orders,fills,paired_deltas}.csv` — replay outputs for the 32 usable markets.
- `research_audits/research_data/m4a/audit.json` and `audit_report.md` — read-only M4A integrity audit.

These generated artifacts are local research outputs and remain outside Git under existing ignore rules. Raw `research_data/m4a/` files were not modified.

## Conclusion and handoff

The current corpus has **32 distinct, execution-replay-usable markets**, up from the previously reported 20. All 12 sessions captured on 2026-10-03 pass the execution-replay selector. The other ten short attempts cannot be recovered into full historical market paths from the available records. The corpus is ready for execution replays on the 32 selected markets; it is not equivalent to 42 independent usable markets, and it is not a settlement-labeled PnL sample. A future run should use `dataset_audit.csv` to preserve the exact inclusion/exclusion decisions and should keep the original `DP_BACKTEST_01` fixed-universe outputs unchanged.

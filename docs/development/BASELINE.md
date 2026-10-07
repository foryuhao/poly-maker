# RDP Phase 0 — Repository Baseline

## Status
FL RECONSTRUCTION VALIDATED; RDP CHECKPOINT PENDING.

## Repository
Original /home/ubuntu/poly-maker remains unchanged on dirty historical m4-dynamic-ev. Reconstruction worktree /tmp/poly-maker-fl-reconstruct-20261007, branch reconstruct/fl-canonical-20261007, rooted at 97ab0f9. Historical controls/archive are evidence only, not merged code ancestors.

## GitHub Remote
origin: https://github.com/foryuhao/poly-maker.git. No upstream/main write.

## Reconstructed FL Canonical Baseline
Parent commit: 97ab0f98168f989b1878e4058022044c8372dcd3.
Reconstruction commit: resolve fl-reconstructed-baseline^{commit} after tag creation; exact numeric identity recorded in RDP follow-up.
Files changed: livecfg/strategy.toml; src/polymaker/config.py; src/polymaker/favorite_live.py; tests/test_favorite_live.py, plus readiness/evidence documentation.
Provenance source: selected 19c322a profile/config wiring hunks, M4A startup and entry-range reports, 42 manifest effective-config/hash matches and 41 corresponding run-record matches. Exact patch and per-hunk evidence in ITER-0000/FL_PROVENANCE_RECONSTRUCTION.md and preservation/fl-reconstruction.patch.
Excluded: neighboring Data API-only startup cleanup, engine auto-merge/on-chain reconciliation removal, inclusive cutoff policy, RPC timeout/logging, unrelated offline research, all IM development. No local signature_type=2 imported; committed parent value=3 preserved.
Relationship to capture data: recovers three observed configuration groups; committed live profile uses latest late-entry group. Full history-specific session replay needs per-session manifest parameters; no one profile is claimed to reproduce all markets.
Test result: 656 passed, 2 skipped full unfiltered suite; exact historical spender test also passed alone currently. Prior reproduced predecessor hang is retained as contextual historical evidence, no acceptance waiver needed.
Known limitations: source was not committed in this exact form during capture; no exact live workspace fingerprint. Cutoff equality and legacy RPC/merge behavior remain parent semantics; these historical FL interfaces are not authorized RDP runtime interfaces. Reconstructed baseline does not certify live readiness/edge.

This baseline is a reconstructed canonical predecessor.
It does not claim that the historical live workspace was committed exactly in this form at the time.
It reconstructs the minimum committed tree supported by surviving operational evidence.

## FL Historical Dataset
Paths: original research_data/m4a/, livecfg/logs/runs/, logs/runs/, research_audits/, related research_outputs/.
42 sessions/38 slugs, 210 raw files, 5,180,619,849 bytes, 8,284,525 persisted records. Existing audit selects 32 replay-usable markets; 10 short sessions excluded, stricter telemetry quality 29/42. All session headers/sample JSONL readable. Untouched ignored/untracked assets; no dataset commit/move or GitHub data backup. Historical data, not RDP edge evidence.

## IM Status
PAUSED / incomplete. Branch im-paused; commit a03b59fd0b3c42955029193b30893a395e03dfa4; tag im-paused-20261007. docs/history/IM_PAUSED.md preserved. Historical snapshot grants no PASS and is NOT an RDP predecessor.

## FL → IM Change Classification
Previous provisional table retained in ITER-0000 audit; source runtime reconstructions separately classified in FL_PROVENANCE_RECONSTRUCTION.md. Final canonical-FL-to-IM comparison recorded after tagging.

### IM Strategy-Specific
MI strategy/live/read-only/cash/reservation/CLI/tests/program documents: exclude.

### Unfinished / Experimental
Unfinished M3 and uncertain historical production deltas: exclude.

### Shared-Core Candidates
Generic authorization/identity/readiness, signing-price normalization and order-read handling: deferred review candidates.

### Accepted Shared-Core Fixes
NONE.

### Deferred Shared-Core Candidates
All above. Not required to begin implementation; future explicit minimal iteration if needed.

## RDP Baseline
Branch: rdp-phase0-dev — PENDING.
Commit/tag: PENDING clean FL acceptance and documentation-only RDP checkpoint.

## Test Baseline
Python 3.12.14, existing .venv. Exact gate commands/logs in reconstruction audit.
655 passed, 2 skipped, 1 deselected authorized gate; 656 passed, 2 skipped stronger full gate. No current failure; historical spender timeout retained, not silently excused.

## Existing Components Available to RDP
Official Gamma metadata/BTC5m selection, market WebSocket/orderbook, CLOB gateway, Data API positions, risk/Fill Truth/reconciliation, FavoriteFirst/reporting, research observer/schema/durable writer confirmed in root code. Preserve FL; future RDP runtime must use official HTTP/WebSocket only and isolate legacy RPC/merge paths.

## Known Gaps Against RDP Phase 0
No RDP strategy/state/inventory/accounting/metrics implemented. Official-interface isolation and any shared-core need belong to future explicit Architect contract. No live execution authority granted.

## Readiness Decision
FL validated; RDP checkpoint pending.

## Blockers / Human Decisions
No provenance/test blocker after human reconstruction authority and full passing gate. Finish additive checkpoints and remote verification. Prior blocked documents preserved under ITER-0000/preservation/PRE_RECONSTRUCTION_*.

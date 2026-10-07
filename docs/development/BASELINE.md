# ITER-0000 Closure — 2026-10-07

Current status: READY_FOR_RDP_DEVELOPMENT.
FL: fl-reconstructed-baseline / 76fe93ffeb8cc1f8ca440587ab901ea402221228.
RDP: rdp-phase0-baseline / 6a5ed93c84d66960eaa73b27a0fe34acfa0bf6c9, branch rdp-phase0-dev.
Exact completed remote/topology/test/preservation verification: docs/development/ITER-0000/CHECKPOINT_RECEIPT.md.
Canonical current baseline/audit documents are on rdp-phase0-dev. This archival branch is NOT an RDP source ancestor.
No shared-core/RDP implementation/agent installation/trading occurred. Next authorized action after ITER-0000: archive historical AGENTS.md, install RDP framework, then ITER-0001.

## Historical record below
The following previous BLOCKED/pending statements are retained as historical evidence and superseded by this closure.

---

# RDP Phase 0 — Repository Baseline

## Status
BLOCKED — HUMAN_DECISION_REQUIRED

## Repository
/home/ubuntu/poly-maker. Audit tracked on pre-rdp-controls-20261007, an archival branch with IM ancestry; do NOT use it as RDP code predecessor.

## GitHub Remote
origin: https://github.com/foryuhao/poly-maker.git
upstream: https://github.com/warproxxx/poly-maker.git (not written).

## FL Canonical Baseline
Commit: NOT SELECTED.
Tag: NOT SELECTED.
Evidence: manifests name 97ab0f9 but reflect dirty FL changes later archived in 19c322a. Earlier fff2842 has live evidence but predates durable capture. See ITER-0000/01_READINESS_AUDIT.md for exact candidates and evidence conflict.

## FL Historical Dataset
Paths: research_data/m4a/; research_audits/; logs/runs/; livecfg/logs/; related research_outputs/.
Approximate coverage: 42 sessions, 38 slugs, 8,284,525 records, 5.18GB raw captures; prior audit selects 32 distinct replay-usable markets.
Preservation status: unchanged local ignored/untracked assets, all session headers/sample JSONL readable. Not RDP strategy evidence; no bulk data remote backup claimed.

## IM Status
Status: PAUSED, incomplete.
Branch: im-paused.
Commit: a03b59fd0b3c42955029193b30893a395e03dfa4.
Tag: im-paused-20261007.
Documentation: docs/history/IM_PAUSED.md. Archive only, no new PASS.

## FL → IM Change Classification
Provisional 19c322a-to-pause comparison, pending canonical FL selection; complete file table in audit.

### IM Strategy-Specific
A: MI strategy/runtime/account/cash/CLI/tests/program docs; exclude.

### Unfinished / Experimental
B: MI strict metadata/cache controls; unfinished M3 preserved, excluded.

### Shared-Core Candidates
C: generic authorization/identity/readiness and gateway helper changes, review only.

### Accepted Shared-Core Fixes
NONE.

### Deferred Shared-Core Candidates
All C changes bundled with MI development. No cherry-pick, transplant or full merge.

## RDP Baseline
Branch: rdp-phase0-dev — NOT CREATED.
Commit: NONE.
Tag: rdp-phase0-baseline — NOT CREATED.

## Test Baseline
Python: 3.12.14, existing .venv.
Command: isolated candidate 19c322a, exact commands in audit.
Result: 190 passed, 2 skipped focused regression; full suite incomplete, isolated documented spender hang exit 124.
Known failures: hang documented historically; no new assertion failure established. No canonical FL/RDP PASS claimed.

## Existing Components Available to RDP
Confirmed in candidate code: official Gamma BTC5m selection, market WebSocket/orderbook reconstruction, official CLOB gateway, Data API positions, Fill Truth/order reconciliation, risk checks, FavoriteFirst policy/wrapper/reporting, isolated Research observer/schema/durable JSONL writer. Legacy RPC/merge/settlement code remains and must not gain RDP runtime authority.

## Known Gaps Against RDP Phase 0
Canonical FL predecessor, required regression gate and official-interface integration unresolved. No RDP controller, inventory/accounting or metrics implemented. Preserved control documents confer no implementation authorization.

## Readiness Decision
BLOCKED.

## Blockers / Human Decisions
Choose/validate FL candidate and establish a completable predecessor regression standard, then resume ITER-0000. No RDP AGENTS/subagent configuration installed; no trading.

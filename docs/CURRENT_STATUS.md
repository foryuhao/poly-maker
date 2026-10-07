# Current Project Status — ITER-0000

Current program: RDP readiness.
Current milestone / slice: ITER-0000 — Pre-RDP Codebase Isolation.
Status: READY_FOR_RDP_DEVELOPMENT upon completed final checkpoint/remote verification.
Canonical FL predecessor: fl-reconstructed-baseline (76fe93ffeb8cc1f8ca440587ab901ea402221228), rooted at 97ab0f9 with proven FL reconstruction only.
Canonical RDP baseline: rdp-phase0-baseline on rdp-phase0-dev; resolve tag for exact commit. Documentation-only delta from FL; no shared-core included.
Latest baseline: docs/development/BASELINE.md.
Latest audit/result: docs/development/ITER-0000/01_READINESS_AUDIT.md; FL_PROVENANCE_RECONSTRUCTION.md; RESULT.md.
Current blocker: NONE after final clean ref/push verification.
Tests: full FL and full RDP each 656 passed, 2 skipped; known historical spender hang did not recur currently, no test waived.
IM preserved: im-paused-20261007 (a03b59f), incomplete/PAUSED, not RDP predecessor.
Next authorized action after verification: archive historical AGENTS.md, install RDP AGENTS/custom Architect/Worker/Reviewer config, then ITER-0001.
Explicitly unauthorized in ITER-0000: new agent framework installation, RDP features, live order/cancel, full IM merge, history/tag rewrite.
Data unchanged in original historical workspace; original local wallet/control/IM files remain preserved. Legacy historical FL RPC/merge paths confer no RDP runtime authority. RDP runtime is official Polymarket HTTP/WebSocket only.

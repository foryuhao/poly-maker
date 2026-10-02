# Current Project Status

Current program: M4 — Dynamic Exit EV Research
Current milestone: M4A — Research Telemetry
Completed slice: M4A.5 — Audit + Acceptance Harness
Current completed iteration: M4A-A1 — Config Identity Repair
Status: M4A-A1 REVIEW PASS — CHECKPOINTED
Review: PASS
Implementation status: IMPLEMENTED — INDEPENDENT RE-REVIEW PASS
M4A implementation: COMPLETE / CHECKPOINTED
M4A Acceptance Preflight: BLOCKED
Actual M4A readiness: NOT READY
Reason: The A1 identity repair and P1 context-validation repair passed independent re-review. Real FIRST-fill verification and trusted LIVE, callback latency, incremental loop-lag and isolation evidence remain outstanding.

Canonical predecessor baseline: `m4a4-pass` (`31dd22c`).
Canonical current baseline: `m4a-a1-pass`.
Reviewed checkpoint: `m4a-a1-pass`.
Canonical predecessor checkpoint: `m4a5-pass` (`4d75a19`).
Stable rollback: `m3c-settlement-live-pass` (`fff2842`).

Latest research: `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md`.
Latest architecture: `docs/architecture/M4A_ARCHITECTURE.md`.
Latest implementation: `docs/reports/M4A_A1_IMPLEMENTATION_REPORT.md`.
Latest review: `docs/reviews/M4A_A1_REVIEW.md` (initial NEEDS FIX; re-review PASS for the reviewed A1 working tree).
Acceptance preflight: `docs/reports/M4A_ACCEPTANCE_PREFLIGHT.md` (BLOCKED).
Acceptance architecture: AE-1 in `docs/architecture/M4A_ARCHITECTURE.md`.

The terminal material-snapshot audit compares FavoriteFirst decision content against the causal event prefix. Dataset enumeration and caller-evidence provenance remain fail-closed. No real M4A collection or verified peak-traffic evidence was audited; actual readiness remains NOT READY.

M4A.4 reviewed checkpoint: implementation `08e4186`, separate governance `7a51272`, status `31dd22c`, tag `m4a4-pass`. M4A.5 test/check results and limitations are recorded in its Implementation Report.
M4A.5 reviewed implementation commit: `d375830`. Its separate checkpoint-status commit is included in `m4a5-pass`; neither commit establishes actual M4A readiness.

Checkpoint history: `m4a2-pass` is historical and incomplete; `m4a2-reviewed-baseline` is the corrected M4A.2 baseline. `m4a3-pass` contains the reviewed M4A.3 tree before ancestry repair; `m4a3-reviewed-baseline` is the canonical M4A.4 predecessor. `m4a4-pass` is the canonical M4A.5 predecessor. Preserve all historical tags.

Current blocker: A real FIRST-fill identity audit is not yet available; preflight blockers B–E remain. Historical M4A.5 implementation PASS remains valid for its reviewed scope.
Next authorized action: Exactly one manually supervised FL BTC 5min live market with Research enabled.
Purpose: Validate A1 config identity on a real FIRST-fill path.
Real multi-day collection: NOT YET AUTHORIZED.
Live test: Only after Review PASS; one manually authorized FL BTC 5min market, then audit if a FIRST fill occurs.
M4B authorization: NO.
M4B: UNAUTHORIZED.
Explicitly unauthorized next actions: multi-day real collection, new trading behavior, M4B implementation before real M4A engineering acceptance.

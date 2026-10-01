# Current Project Status

Current program: M4 — Dynamic Exit EV Research
Current milestone: M4A — Research Telemetry
Completed slice: M4A.4
Status: PASS — CHECKPOINTED

Canonical predecessor baseline: `m4a3-reviewed-baseline` (`eb061fa`).
Canonical current baseline: `m4a4-pass`.
Stable rollback: `m3c-settlement-live-pass` (`fff2842`).

Latest research: `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md`.
Latest architecture: `docs/architecture/M4A_ARCHITECTURE.md`.
Latest implementation: `docs/reports/M4A_4_IMPLEMENTATION_REPORT.md` (includes Final Repair — Close / Finalization Barrier).
Latest review: `docs/reviews/M4A_4_REVIEW.md` (final independent re-review: PASS).

Independent re-review verified the final causal session seal. Rejections before it make the dataset INCOMPLETE; observations after it receive no session sequence or loss count. Final counters are sampled only after sealing, and `close()` returns true only after a COMPLETE manifest commit. Prior GAP ordering, filesystem finalization and asynchronous startup repairs remain intact. Two documented P2 limitations remain in the Review.

Reviewer tests: 20 writer and 41 existing Research tests passed; full suite: 598 passed, 2 skipped. Targeted Ruff and Research mypy are clean; broader targeted mypy retains five previously documented Engine/live-wrapper diagnostics. M4A.4 implementation and reviewed artifacts were committed as `08e4186`; the pre-existing `AGENTS.md` governance update was committed separately as `7a51272`.

Checkpoint history: `m4a2-pass` is historical and incomplete; `m4a2-reviewed-baseline` is the corrected M4A.2 baseline. `m4a3-pass` contains the reviewed M4A.3 tree before ancestry repair; `m4a3-reviewed-baseline` is the canonical M4A.4 predecessor. Preserve all historical tags. The `AGENTS.md` governance edit is outside the M4A.4 implementation commit but included in the `m4a4-pass` checkpoint.

Current blocker: none for M4A.4.
Next authorized action: M4A.5 — Audit + Acceptance Harness.
Explicitly unauthorized next actions: M4B implementation.

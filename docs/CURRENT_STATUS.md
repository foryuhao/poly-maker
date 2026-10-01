# Current Project Status

Current program: M4 — Dynamic Exit EV Research
Current milestone: M4A — Research Telemetry
Current slice: M4A.4
Status: PASS

Canonical predecessor baseline: `m4a3-reviewed-baseline` (`eb061fa`).
Stable rollback: `m3c-settlement-live-pass` (`fff2842`).

Latest research: `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md`.
Latest architecture: `docs/architecture/M4A_ARCHITECTURE.md`.
Latest implementation: `docs/reports/M4A_4_IMPLEMENTATION_REPORT.md` (includes Final Repair — Close / Finalization Barrier).
Latest review: `docs/reviews/M4A_4_REVIEW.md` (final independent re-review: PASS).

Independent re-review verified the final causal session seal. Rejections before it make the dataset INCOMPLETE; observations after it receive no session sequence or loss count. Final counters are sampled only after sealing, and `close()` returns true only after a COMPLETE manifest commit. Prior GAP ordering, filesystem finalization and asynchronous startup repairs remain intact. Two documented P2 limitations remain in the Review.

Reviewer tests: 20 writer and 41 existing Research tests passed; full suite: 598 passed, 2 skipped. Targeted Ruff and Research mypy are clean; broader targeted mypy retains five previously documented Engine/live-wrapper diagnostics. No M4A.4 commit or PASS checkpoint exists yet.

Checkpoint history: `m4a2-pass` is historical and incomplete; `m4a2-reviewed-baseline` is the corrected M4A.2 baseline. `m4a3-pass` contains the reviewed M4A.3 tree before ancestry repair; `m4a3-reviewed-baseline` is the canonical M4A.4 predecessor. Preserve all historical tags. `AGENTS.md` has a pre-existing governance edit outside M4A.4 scope; account for it deliberately during any future checkpoint.

Current blocker: complete reviewed M4A.4 Git checkpoint has not yet been created or verified.
Next authorized action: Create complete M4A.4 reviewed Git checkpoint, then begin M4A.5 only.
Explicitly unauthorized next actions: M4A.5 implementation before the complete reviewed checkpoint; omission of reviewed untracked M4A.4 artifacts from that checkpoint.

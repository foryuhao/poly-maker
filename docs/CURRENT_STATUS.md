# Current Project Status

Current program: M4 — Dynamic Exit EV Research
Current milestone: M4A — Research Telemetry
Current slice: M4A.1 — Schema + Isolation Skeleton
Status: **PASS**

Stable rollback: `m3c-settlement-live-pass` (`fff2842`)
Implementation baseline: `m4a-implementation-ready` (`9c28bca`)

Research: `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md`
Program architecture: `docs/architecture/M4_ARCHITECTURE.md`
Milestone architecture: `docs/architecture/M4A_ARCHITECTURE.md`
Latest implementation: `docs/reports/M4A_1_IMPLEMENTATION_REPORT.md`
Latest review: `docs/reviews/M4A_1_REVIEW.md`

M4A.1 implemented the capture schema, default-off observer, bounded nonblocking queue and writer skeleton. No live hooks or trading semantics changed. The Reviewer found a P1 clean-close race. Worker repaired admission/close coordination and added deterministic concurrent tests. Current full suite: 545 passed, 2 skipped; targeted Ruff and Research-source mypy clean. Independent re-review verified the P1 repair and recorded PASS for the current uncommitted working tree. Repository-wide Ruff/mypy debt and the Reviewer's three P2 issues remain deferred.

Architecture decisions remain: M4 is fixed minimum-scale FavoriteFirst entry and research-only post-first-fill Exit EV. M4A must eventually capture causal state and Fill Truth through T+300 through an isolated observer; quantities are generic. Existing live wrapper handles one committed market per invocation. Raw journal lacks causal clocks and complete book-mutation evidence, so later M4A slices must add observation-only capture.

Open/deferred: M4A.2 live event hooks and clocks; M4A.3 Episode/state lifecycle; M4A.4 durability/GAP/manifest; M4A.5 audit and collection gate. Raw-journal byte-offset provenance remains unavailable without a proven seam. Continuous multi-market orchestration, BTC reference feed, execution calibration, online residual valuation, model choice and live control remain deferred.

Next authorized action: **M4A.2 architecture-consistent implementation only.** Do not begin M4A.3 or later slices.

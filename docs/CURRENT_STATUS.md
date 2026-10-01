# Current Project Status

Current program: M4 — Dynamic Exit EV Research
Current milestone: M4A — Research Telemetry
Current slice: M4A.1 — Schema + Isolation Skeleton
Status: READY FOR IMPLEMENTATION (architecture only; no M4A code or review yet)

Stable rollback: `m3c-settlement-live-pass` (`fff2842`)

Research: `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md`
Program architecture: `docs/architecture/M4_ARCHITECTURE.md`
Milestone architecture: `docs/architecture/M4A_ARCHITECTURE.md`
Latest implementation: N/A
Latest review: N/A

Architecture decisions: M4 remains fixed minimum-scale FavoriteFirst entry and research-only post-first-fill Exit EV. M4A captures actual causal state and Fill Truth through T+300 via a default-off, isolated observer; quantities are generic. Existing live wrapper handles one committed market per invocation. Raw journal alone lacks causal clocks and complete book-mutation evidence; M4A must add observation-only capture. The architecture has not changed live safety semantics.

Attempted/completed: inspected code and Research Design at `fff2842`; moved the previously untracked Research Design to its required canonical path without content edits; created the M4 program and M4A technical architecture. No implementation, testing, review or collection gate has occurred. The source research file was untracked before normalization; `git mv` was used after staging it so Git records its canonical path.

Open/deferred: raw-journal byte-offset provenance is unavailable without a proven capture seam; M4A therefore requires self-contained sanitized applied events. Continuous multi-market orchestration is separate future operational work. BTC reference feed, execution calibration, online residual valuation, model choice and live control remain deferred.

Next authorized action: implement **M4A.1 only** against `docs/architecture/M4A_ARCHITECTURE.md`; do not implement M4A.2+ or claim M4A PASS.

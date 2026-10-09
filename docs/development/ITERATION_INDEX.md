# RDP Phase 0 — Development Iteration Index

## Program

Program: `RDP Phase 0`

Strategy source of truth:

`docs/strategy/RDP_PHASE0_FROZEN.md`

Development contract:

`AGENTS.md`

---

## Repository Baseline

Canonical FL predecessor:

- Commit: `76fe93f`
- Tag: `fl-reconstructed-baseline`

Canonical RDP starting baseline:

- Commit: `6a5ed93`
- Tag: `rdp-phase0-baseline`

Current RDP development branch:

`rdp-phase0-dev`

Latest archived implementation HEAD:

`7df8722cfec7f2a4b16468124ff2ae6b8382b8e2`

IM preservation state:

- Branch: `im-paused`
- Commit: `a03b59f`
- Tag: `im-paused-20261007`
- Status: `PAUSED`
- RDP predecessor: `NO`

Baseline details:

`docs/development/BASELINE.md`

---

## Project State

Development Status: `IN_PROGRESS`

Current Iteration: `ITER-0028`

Current Stage: `WORKER`

MICRO_LIVE_READY: `NO`

Real-Money Trading Authorization: `NOT_GRANTED`

Live Validation Status: `NOT_STARTED`

Main Branch Merge Authorization: `NOT_GRANTED`

---

## Iterations

| Iteration | Problem | Architect | Worker | Reviewer | Code Status | Remote Status | Live Status | Decision |
|---|---|---|---|---|---|---|---|---|
| ITER-0028 | DG-003: retained automatic repeated NORMAL operation | APPROVED | PENDING | NOT_STARTED | NOT_READY | PENDING | NOT_RUN | PENDING |
| ITER-0027 | DG-002 prerequisite: current post-cycle next-action admission | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | KEEP |
| ITER-0026 | DG-006: partial/unknown and unreliable-state terminal closure | APPROVED | IMPLEMENTED_FIX | PASS | CODE_READY | ARCHIVED | NOT_RUN | KEEP |
| ITER-0025 | DG-001: authorized initial NORMAL submission | APPROVED | IMPLEMENTED_FIX | PASS | CODE_READY | ARCHIVED | NOT_RUN | KEEP |
| ITER-0024 | Coherent final initial NORMAL intent revalidation | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0023 | Acknowledged initial NORMAL cycle completion owner | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0022 | Initial acknowledged-pair pre-fill validity decision | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0021 | Fresh exact-market NORMAL tradability admission | APPROVED_REVISION | IMPLEMENTED_REVISION | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0020 | Started initial FillWindow scoped-close coordinator | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0019 | Scoped confirmed-fill FillWindow receipt preservation | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0018 | Retained initial-placement close barrier composition | APPROVED_REVISION | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0017 | Initial NORMAL placement ownership capture | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0016 | Initial empty-run NORMAL admission composition | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0015 | Authoritative feed wiring for NORMAL Book Filter | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0014 | Shared single-market read-only input-session lifetime | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0013 | Read-only authenticated user-channel heartbeat check | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0012 | Frozen $30 initial capital backing | APPROVED_REVISION | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0011 | Read-only authenticated account snapshot | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0010 | Read-only single-market WS readiness | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0009 | Read-only single-market CLI metadata bootstrap | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0008 | Opt-in per-token signing ticks | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0007 | Strict opt-in dual-order acknowledgments | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | PENDING |
| ITER-0006 | NORMAL Book Filter with approved total variation | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | KEEP |
| ITER-0005 | NORMAL CapitalGate with approved $5 future repair reserve | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | KEEP |
| ITER-0004 | Scoped cancel/reconcile close barrier | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | KEEP |
| ITER-0003 | Pure deficient-side REBALANCE quote | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | KEEP |
| ITER-0002 | Pure NORMAL quote candidates | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | KEEP |
| ITER-0001 | Confirmed-fill inventory/accounting projection | APPROVED | IMPLEMENTED | PASS | CODE_READY | ARCHIVED | NOT_RUN | KEEP |
| ITER-0000 | Pre-RDP codebase isolation, FL reconstruction, IM preservation, and RDP baseline readiness | N/A | N/A | N/A | READY | ARCHIVED | NOT_RUN | KEEP |

---

## Current Iteration

Explicit human resume authorizes automatic required delivery iterations and repairs until MICRO_LIVE_READY; live trading, main merge, strategy revision and unapproved runtime interfaces remain denied. ITER-0028 Architect01 is frozen APPROVED_FOR_IMPLEMENTATION for full DG-003 repeated NORMAL delivery using existing gateway/lifecycle and cumulative confirmed evidence; Worker is next. DG-002 remains OPEN for REBALANCE successive-order coverage and DG-004 for its operating loop. ITER-0027 independent PASS and RESULT are archived at 7df8722cfec7f2a4b16468124ff2ae6b8382b8e2, with receipt commit a1061aac2d0a3b40b6f5a705977f3314c57f356d exactly verified on origin. Full1683 passed / 2 skipped; independent22 probes passed; zero network attempts.

ITER-0026 is fully archived at e4ab1532043f47af9e416e9e8ef632440bb8608a; Reviewer12 PASS matched its archived implementation. ITER-0027 Reviewer04 verifies the current extended runtime. DG-001 and DG-006 CLOSED; other gaps OPEN. No ITER-0025/0026 reimplementation. See ITER-0027/00_INITIALIZATION.md for startup evidence and current authorization.

---

## Readiness Gates

### CODE_READY

An implementation iteration is `CODE_READY` only after:

- Architect approval;
- Worker implementation;
- required targeted tests;
- required regression tests;
- independent Reviewer `PASS`;
- complete immutable audit artifacts;
- `RESULT.md`;
- reviewed Git state archived on `rdp-phase0-dev`.

### MICRO_LIVE_READY

Project-level `MICRO_LIVE_READY` remains `NO` until all required RDP Phase 0 capabilities are independently reviewed and ready for supervised live validation.

`MICRO_LIVE_READY` does not authorize real-money trading.

---

## Current Constraints

- `poly-maker` is the single canonical project root.
- FL, IM, and RDP are strategies inside the same application.
- RDP derives from `fl-reconstructed-baseline`, not IM.
- IM remains preserved and paused.
- No IM-era shared-core changes are included in the RDP starting baseline.
- Deferred IM-era shared-core candidates are not approved RDP dependencies.
- Shared historical/research/runtime data remains under the existing `poly-maker` data foundation.
- FL historical capture data may be used for replay and infrastructure validation.
- FL historical data is not RDP strategy-edge evidence.
- Runtime external interfaces are limited to official Polymarket HTTP APIs and official Polymarket WebSocket APIs.
- No autonomous real-money trading is authorized.
- Existing local residue stash `pre-rdp-local-residue-20261007` must not be applied to RDP automatically.

---

## Index Maintenance Rule

The Orchestrator owns this file.

Update it when one of the following changes:

- current iteration;
- current stage;
- iteration verdict;
- code readiness;
- remote archive status;
- live-validation status;
- project readiness;
- human authorization state.

Do not copy detailed implementation, test, review, research, or Git history into this file.

Detailed evidence belongs in:

`docs/development/ITER-XXXX/`

and Git history.

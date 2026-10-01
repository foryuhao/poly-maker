# M4 Dynamic Exit EV Research — Program Architecture

Status: architecture ready for M4A.1 implementation; research only.
Git base: `fff2842` (`m3c-settlement-live-pass`).
Research authority: `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md`.

## Objective and boundary

Test whether the causal market path **after the first accepted FavoriteFirst maker fill** improves the timing of HOLD versus initiating a real maker PAIR protocol. PAIR value includes possible nonfill, partial fill, cancel latency, costs and residual payout. Future best price is a hindsight label, not an online return. M4 never gives a model order authority.

FavoriteFirst entry and minimum-scale trading stay fixed. M4 does not optimize position size, inventory target, capital allocation, multi-market inventory, entry signal or FavoriteFirst entry thresholds, BTC directional prediction, settlement, wallet/auth, or production-scale deployment. Quantities remain generic in all research contracts; the current five-share live limit is a configuration fact, not a schema constant. Trading safety semantics remain unchanged.

## Repository anchor

At the inspected base, `src/polymaker/strategy/favorite_first.py:evaluate` is pure; `engine.py` owns books, operational gates, Fill Truth and execution; `favorite_live.py:run_favorite_first_live` selects one market and runs to T+300. `marketdata/service.py` maintains separate actual YES/NO token books. The existing journal lacks causal receipt/knowledge clocks and some mutation evidence, so legacy runs cannot be promoted to exact replay. The detailed capture seams and limitations are in `M4A_ARCHITECTURE.md`.

## Stage contracts and advancement

| Stage | Consumes → durable product | Gate |
|---|---|---|
| M4A Research Telemetry | Existing live observations → versioned ResearchEvent, observed Episode revisions, StateSnapshot, manifest and quality audit | Faithful causal capture, Fill Truth identity, book reconstruction, isolation and collection coverage; **no edge requirement** |
| M4B Episode / Feature / Label Builder | Accepted M4A evidence → reconstructed episodes, point-in-time features, FutureLabels and censoring/coverage report | Deterministic as-of joins, no lookahead, valid horizons and explicit unsupported cases |
| M4C Execution Model & Policy Replay | M4B episodes/features/labels → common maker simulator and PolicyReplayResult | Executable maker behavior, partial fills and residual valuation, calibrated/stressed assumptions; quoted prices alone do not pass |
| M4D Dynamic EV Model | Validated M4C replay → model and offline ModelDecision/policy evaluation | Out-of-sample gain beyond replay noise and simple baselines, with support/uncertainty |
| M4E Shadow Live | Frozen model + causal live snapshots → live shadow ModelDecision (`EV_PAIR_NOW`, `EV_CONTINUE`, difference, PAIR/HOLD/ABSTAIN, support) | Causal feature parity and valid live coverage/latency; FavoriteFirst remains authoritative |
| M4F Evidence Review | Prior evidence → GO / NO EDGE / INCONCLUSIVE report | Research Design section 12 economic, risk, precision and conservative-scenario criteria; GO is a research conclusion only |

M4C initially compares A existing FavoriteFirst v0.3, B Immediate Pair, C Fixed Time, D PnL Target, E Simple Trailing under Optimistic, Base and Conservative execution scenarios. The common simulator must treat each policy's orders, inventory and fill readiness as its own world; partial acquisition requires the distinct fixed-inventory and all-first-fill panels specified in the Research Design. M4D progresses from Model 0 descriptive conditional distributions, to Model 1 simple interpretable supervision, to Model 2 restricted stopping policy. There is no commitment to RL or deep learning.

No stage absorbs the next: M4A captures but does not build outcome labels; M4B makes causal data but does not certify execution realism; M4C replays but does not train Dynamic EV; M4D evaluates offline but does not send orders; M4E scores only; M4F cannot deploy. Advancement requires a documented stage review and gate result. A failed M4A trust gate blocks evidentiary claims downstream even if fixture-based M4B work exists.

## Safety, provenance and failure policy

The sole dependency direction is live system → research observation → offline/shadow products. Research code has no execution gateway, reconciliation, settlement or wallet capability. Fill Truth and actual token books are source authorities. Every derived object carries versioned provenance, availability/knowledge time and censoring. Later discovery never changes earlier state. Capture gaps invalidate dependent intervals; they are not repaired by interpolation. Only independently authorized future architecture may permit live model control.

Each stage requires its own architecture, implementation reports and independent reviews under `AGENTS.md`; a passing review refers to a specific scope/commit. M4A's detailed slices are in `M4A_ARCHITECTURE.md`. After all slices pass, `docs/milestones/M4A_FINAL_REPORT.md` records the gate and rollback point. The final M4 report records GO / NO EDGE / INCONCLUSIVE and cannot authorize trading.

## Deferred decisions

Exact M4D model family, maker fill-probability calibration, online residual valuation, any BTC reference feed, position sizing/inventory work and production live-control architecture remain open. Continuous multi-market collection may need a separate operational orchestrator; it is not part of M4A. Any change to the frozen research boundary requires explicit Research/Architecture revision.

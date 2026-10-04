# CIP B4 Residual Budget Research Design

Status: Frozen research-only design, 2026-10-04. Implementation awaiting review.
Git base: `m4a-a1-pass` (`97ab0f9`); local B2 research is unreviewed and uncheckpointed.
Comparator: `docs/research/CIP_B2_RESEARCH_DESIGN.md`, `docs/research/CIP_B2_BACKTEST_20261003.md`, and the exact B2 script/output.

## Question and fixed treatment

Replay the same 32 execution-replayable BTC5m markets and official settlement labels. B4 changes only Phase 1: when an intended five-share BUY would increase absolute net beyond five, block it; at `abs(Net)>=5`, request cancellation of the already working heavy-side BUY. The lighter side continues under B2 quote, capital, book-validity, latency and fill rules. At flat inventory, B2 two-sided gamma quoting resumes. The matched inventory target, skew and pair-cost admission remain B2's. No pair-level matching or 0.98 gate is added.

Phase 2 inherits B2 `TwoPhaseReplay.reconcile` without modification. B2's exact T150 transition, maker fill predicate, reconstructed books, book-invalid cancel-all rule, $25 cost-plus-reserve ceiling, five-share clip, order timing and T300 end remain fixed. Existing orders stay fill-eligible through cancel latency. No real order, new interface or production code is involved.

## Measurement and invariants

Use B2's full market, order, fill, path and action outputs. Add `guardrail_events.csv` with activation, recovery, blocked placement, heavy-side cancellation and overshoot records. Integrate Phase 1 absolute net and guardrail-active duration against the available-time clock. Record `QY`, `QN`, costs, average prices, matched shares, pool pair cost, GammaPnL and capital at T150 before switch cancellations. Report Phase 1 throughput, Reduce fills, flat rate, final residual and official-settlement economics. Compare all 32 paired B2 markets, plus the paired total-PnL delta after excluding the best market.

An overshoot is an actual Phase 1 fill causing `abs(Net)>5` after a prior lower-absolute-net state. It is allowed only through an order already submitted before the current budget boundary and still fill-eligible under its lifecycle, normally during cancellation latency. Count events, excess shares, affected markets and maximum actual absolute net. New risk-increasing placement whose projected net breaches five, a fill outside that lifecycle explanation, Phase 2 drift, book-invalid semantics drift, market mismatch, capital violation or any BBO-only reprice invalidates the implementation.

`PairingEfficiency = 2*MatchedQty/(QY+QN)`. The matched-share-weighted T150 pool cost is `1 - aggregate GammaPnL_T150 / aggregate Matched_T150`. `ResidualPnL` uses B2's official winner and average heavy-side cost. No inference is made from a single market or from unmodeled queue positions.

## Predeclared interpretation

For reproducible labels, classify in this order after invariant failure: `GUARDRAIL TOO RESTRICTIVE` if Phase 1 fill count or T150 matched shares falls below half of B2; `GUARDRAIL TOO LOOSE` if aggregate T150 residual exceeds 160 shares (one clip per market); `RESIDUAL CONTROL WORKS, STRATEGY STILL NEGATIVE` if total improves at least $10 but remains below −$10; `RESIDUAL BUDGET SUPPORTED` if T150 and final residual each fall at least 50%, residual PnL improves at least $20, total PnL improves at least $10, throughput floor holds and positive paired total delta remains after excluding the best market; `RESIDUAL CONTROL WORKS BUT GAMMA COST TOO HIGH` if T150 residual halves but total does not improve; otherwise `NO MATERIAL BENEFIT VS B2`. This ordering is a reporting convention, not parameter selection.

## Scope and limitations

The full-order trade-through maker fill model has no queue or public-print-size partial-fill model. Only 19 of 32 markets overlap stricter M4A integrity PASS. The local B2 comparator has no independent reviewed checkpoint. B4 cannot establish live profitability or authorization. No 10/15-share sweep, Phase 2 change or quote optimization is permitted.

# B2 Reduce Maker Frontier Implementation Report

Status: **IMPLEMENTED — AWAITING REVIEW**
Commit / working tree base: `97ab0f9` on `m4-dynamic-ev`; unrelated dirty worktree files preserved.
Reviewed predecessor baseline: `m4a-a1-pass` (`97ab0f9`) for Git. Local B2 and prior quote-attribution research artifacts are the analytical predecessors and remain unreviewed/uncheckpointed.
Architecture reference: User's 2026-10-04 frontier request and `docs/research/B2_REDUCE_MAKER_FRONTIER_RESEARCH_DESIGN.md`; M4 production architecture unchanged.

## Scope implemented

Read-only maker-safe frontier attribution for B2's 89 accepted/unfilled new Reduce BUY orders, with 24 filled orders for frontier-gap reference. Computed original, frozen-latency best-bid, zero-delay always-current-frontier and frozen-latency dynamic-frontier opportunity layers; original-path capital checks; local static pool economics; price concession; time remaining; and market-deduplicated residual-share ceilings.

## Files added

- `research_tools/b2_reduce_maker_frontier_attribution.py`
- `docs/research/B2_REDUCE_MAKER_FRONTIER_RESEARCH_DESIGN.md`
- `docs/research/B2_REDUCE_MAKER_FRONTIER_ATTRIBUTION_20261004.md`
- `docs/reports/B2_REDUCE_MAKER_FRONTIER_IMPLEMENTATION_REPORT.md`
- Untracked local outputs: `research_outputs/b2_reduce_maker_frontier_attribution_20261004/{summary.json,order_frontier_analysis.csv,opportunity_ladder.csv,latency_frontier.csv,market_dedup_upper_bound.csv,price_concession.csv}`.

## Files modified

- `docs/CURRENT_STATUS.md`: concise research handoff entry only.

## Important implementation decisions

- Used the existing B2 captures, B2 orders/fills/actions, the prior 89-order best-bid attribution, and the inherited R0 Book. Re-ran the best-bid candidate screen and required exact 16 raw/14 capital-valid per-order agreement with the prior output.
- Frontier quotes are generated only from valid reconstructed books at strictly less than ask and at least bid. The zero-delay layer uses the latest legal frontier quote and carries it through invalid-book intervals without generating a new price there. This is the user's prescribed dynamic-frontier upper layer, not a supremum over all maker quote paths.
- The latency layer starts with the original accepted quote, requests cancellation on each frontier change or invalid book, waits B2's cancel delay, submits at the latest legal frontier, waits B2's activation delay, rejects a quote no longer maker-safe, and checks strict SELL print timing and price. No hypothetical fill affects later B2 or hypothetical observations.
- Static pool economics use original B2 fills strictly before a hypothetical print and add one hypothetical 5-share BUY locally. No projected PnL is propagated or summed as strategy PnL.
- Market dedup starts with B2 T150 residual and caps each distinct capital-valid opportunity at five shares without crossing zero; final-residual coverage is capped again by each market's actual B2 final residual.

## Deviations from architecture

- NONE. This analysis does not change B2 or production trading behavior. The non-monotonic dynamic-frontier result is explicitly documented because resetting a quote downward can lose a fill that a higher persistent maker quote would see.

## Tests added

- NONE. The full data replay and independent output invariants validate this research artifact.

## Tests executed

- `python3 research_tools/b2_reduce_maker_frontier_attribution.py` processed all 32 B2 markets and wrote six required files.
- Reconciled 113 accepted new Reduce orders, 24 filled reference orders, 89 no-fill orders, 27 B2 stage fills, 380 T150 and 245 final residual shares, and 16/14 prior best-bid opportunities.
- Reconstructed 7,700,057 book events with no nonmonotone skip; asserted no original B2 no-fill qualifying print, no newly placed frontier quote at/above ask, and no latency opportunity outside the instantaneous frontier set.
- Audited the 7 lost best-bid paths against their current frontier and strict SELL price, the latency sequence's 194 raw / 167 capital-filtered activations and zero qualifying active maker prints, and local pool/accounting summaries.
- `python3 -m py_compile research_tools/b2_reduce_maker_frontier_attribution.py`, `git diff --check`, and new-file whitespace checks passed. An independent output audit verified six files, 113/89 order counts, 0/16/7/0 raw ladder counts, 0/14/7/0 capital-valid counts, all 32 market dedup caps, and frontier opportunity maker/strict-print/capital inequalities. The prior best-bid rows lack frontier-placement metadata and were checked by exact per-order reconciliation with their predecessor analysis.

## Test results

Best-bid opportunities remain 16 raw / 14 capital-valid; instant dynamic frontier has 7 / 7, and latency-respecting dynamic frontier 0 / 0. All seven instant frontier orders were already in the best-bid capital-valid set. Gross instant frontier share ceiling is 35; T150-market dedup remains 35, and final-residual-capped coverage is 15 shares. Classification: **BEST BID IS NEAR MAKER EXECUTION FRONTIER**, pending independent review.

## Known limitations / unresolved issues

- The so-called instant `FRONTIER_UPPER_BOUND` is an upper bound only for the specified always-current-frontier observation with zero requote delay. It is not a universal maker execution upper bound; downward quote resets can lose opportunities that persistent resting quotes retain.
- The latency sequence has no queue loss, exchange acknowledgement or feedback from hypothetical fills. The inherited B2 fill predicate has no public print-size cap or queue model. None of the observed opportunities is a realized fill or additive strategy PnL.
- Only 19 of 32 execution-replayable markets overlap the stricter M4A integrity-PASS subset. The local B2 research predecessor and this attribution remain unreviewed/uncheckpointed.

## Safety / behavior impact

Live trading semantics changed: **NO**. No production code, real order, external interface or B2 strategy setting changed.

## Git diff summary

Four research-only source/documents, six untracked generated outputs and a short `CURRENT_STATUS` entry. Existing production/config/test changes were not touched; no commit, tag or push was made.

## Handoff to Reviewer

Independently inspect valid-book frontier construction, strict maker placement, event order, the best-bid 16/14 reconciliation, the distinction between zero-delay and latency layers, the seven lost persistent-bid paths, all zero-hit latency transitions, original-path capital checks, static pool economics and market-level share caps. Review tracked and untracked artifacts before any checkpoint decision.

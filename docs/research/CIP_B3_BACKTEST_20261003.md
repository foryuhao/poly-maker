# BTC5m Two-Phase Continuous Inventory Strategy — B3 Backtest, 2026-10-03

Result: **NO MATERIAL BENEFIT VS B2**. This is a research-only replay; no production code, live orders or external interface changed.

## Sample and controls

All 32 B2 execution-replayable BTC5m markets, captured events and identity-checked official settlement labels matched. Nineteen overlap the stricter M4A integrity-PASS subset. B3 inherited B2's T150/T300 timing, $25 cumulative capital, 5-share clip, 415,281 μs activation, 234,783 μs cancel delay, full-remaining-order trade-through fill predicate, and persistent quote rule. Data errors, `BBO_ONLY_REPRICE`, invalid-book new placements, reduce-only heavy-side placements and `BOOK_INVALID_KEEP_VIOLATIONS` were all zero. The B3 gamma path differs from B2 after the first retained order, as the requested Book Invalid rule applies in both phases.

## Book Invalid behavior

The replay observed **1,127** invalid episodes totaling **2,170.405 seconds** across 32 markets. Episode reason counts overlap: `CROSSED_BOOK` 1,072; `MISSING_NO_ASK` 56; `MISSING_YES_ASK` 56; `MISSING_NO_BID` 47; `MISSING_YES_BID` 37; `OTHER` 32. The frozen book representation does not reliably identify `STALE_BOOK`, `SEQUENCE_OR_RECONSTRUCTION_GAP` or `MARKET_INACTIVE`, so none was guessed. `OTHER` is an invalid state not resolved by the available bid/ask predicates.

B3 kept **71 distinct known active risk-reducing orders** at first invalid encounter (59 in gamma, 12 in reduce-only), requested cancellation of 335 risk-increasing and 220 flat-state orders, and blocked 1,176 new-order decision events while invalid. There were **zero KEEP violations**. The counts of cancellations and blocked decisions are event/action counts, not independent inventory opportunities. B2's published diagnostic identified 89 accepted/unfilled new Reduce orders ultimately canceled after invalidation; B3 has no invalid-book cancel request for a known active risk-reducing order.

## Reduce execution and persistence

| Measure | B2 | B3 |
|---|---:|---:|
| Post-T150 new Reduce orders accepted | 113 | 26 |
| These orders filled / unfilled | 24 / 89 | 20 / 6 |
| Fill rate of accepted new Reduce orders | 21.24% | 76.92% |
| Fills per 100 new Reduce submissions | 21.24 | 76.92 |
| All Reduce-phase fills, including older orders | 27 | 29 |
| All Reduce-phase acquired shares | 135 | 145 |
| Median / mean accepted-to-close, new Reduce orders | 2.803 / 7.002 s | 3.214 / 11.996 s |
| p75 / p90 accepted-to-close | 9.296 / 17.189 s | 12.243 / 20.092 s |
| Median / mean no-fill accepted-to-close | 2.332 / 7.423 s | 0.114 / 27.511 s |

The high B3 new-order conversion percentage has a denominator of just 26, because Phase 1 retention changes inventory and capital and suppresses many subsequent new orders. Across all B3 Reduce-phase fills, two cancel-pending orders increased absolute net by 10 shares; hence 145 acquired Reduce-phase shares yielded only 125 net residual reduction. Twenty-two orders can be matched exactly to a B2 order that was canceled for `BOOK_INVALID` and subsequently filled in B3, but **all 22 are Phase 1 first-kept orders**. Nine Phase 2 kept orders filled after B2's cancel-all rule would have become effective *on the same B3 state*; none matches the original B2 order path exactly after Phase 1 divergence. These local policy opportunities do not equal nine incremental final-market fills.

Of 21 kept orders that ultimately did not fill, 18 were first kept in gamma and three in reduce-only. None had a later SELL print meeting the frozen price and all three timestamp predicates before closure. Their per-order lifetimes, survived-invalid time and market time remaining are in `kept_reduce_orders.csv`.

## Residual and economics

| Metric | B2 | B3 |
|---|---:|---:|
| T150 residual | 380 | **575** |
| Reduce fills / shares | 27 / 135 | **29 / 145** |
| Final residual | 245 | **450** |
| Residual reduction | 135 (35.53%) | **125 (21.74%)** |
| Flat after switch / nonflat at switch | 9 / 26 | **4 / 28** |
| Median time to flat | 28.987 s | **68.594 s** |
| GammaPnL at T150 | −$1.7560 | **+$17.6969** |
| GammaPnL final | +$3.0267 | **+$61.4714** |
| ResidualPnL | −$72.4467 | **−$127.1214** |
| TotalPnL | −$69.42 | **−$65.65** |
| `REDUCE_CAPITAL_BLOCKED` decisions | 4,075 | **2,070** |
| Book-invalid cancels of accepted, unfilled new Reduce orders | 89 | **0 known active reducing orders** |
| Known risk-reducing orders kept at invalid | 0 | **71** |

TotalPnL improves only $3.77, below the prespecified $10 materiality threshold, while final residual rises 205 shares. Larger positive matched-pair GammaPnL is offset by $54.67 more residual loss. This does not support the intended residual-control hypothesis.

## Phase 2-only attribution diagnostic

To isolate Phase 2 handling, a labeled diagnostic retained B2's **exact** gamma order prefix and T150 residual of 380, then applied B3's invalid-book rule only after T150. It kept 25 reducing orders but achieved **22 Reduce-phase fills / 110 shares, 270 final residual, −$80.8188 ResidualPnL and −$76.85 TotalPnL**. B2 had 27 fills, 245 residual and −$69.42 TotalPnL. This diagnostic is not the specified B3 strategy, since B3 requires the new safety rule in Phase 1 as well; it shows that the Phase 2 retention change alone did not improve this sample.

## Safety and representative path

There were zero risk-increasing fills and zero risk-increasing shares **while the book was invalid**, zero unknown replay lifecycle states, zero KEEP violations, and zero `BBO_ONLY_REPRICE`. Two 5-share NO orders pending cancellation filled just after T150 when the book was valid, taking net −40→−45 in `btc-updown-5m-1790936700` and −5→−10 in `btc-updown-5m-1790963100`. These 10 shares are new B3 cancel-race exposure versus B2's zero and are included in the 450 final residual; the inherited cancel latency was unchanged.

In actual market `btc-updown-5m-1790941200`, B3 NO BUY `:B1:15` at $0.44 was accepted at **2026-10-02 11:43:03.354115 UTC** with YES-heavy net +30. At 11:43:03.567274 the reconstructed book became invalid; B3 kept this known active NO order. Applying B2's cancel-all rule to that exact B3 state would have ended fill eligibility at 11:43:03.802057. The order instead filled at 11:43:15.635985, reducing net +30→+25, with 11.834 seconds of extra resting beyond that hypothetical cancellation. Its direct terminal cash-flow contribution was +$2.80. This B3 order does **not** have an exact corresponding B2 order because the earlier Phase 1 path had diverged; `kept_reduce_orders.csv` distinguishes this same-state policy counterfactual from exact B2 order matches.

## Interpretation and limits

Book-invalid retention made some real replay orders survive long enough to fill, but it did not produce a material aggregate improvement in this 32-market run. Applying the rule in gamma changed T150 exposure and capital allocation, while the Phase 2-only diagnostic also underperformed B2. The two cancel-race fills show that a zero risk-increasing-fill count *during* invalid periods does not prove zero new exposure over the full strategy path.

The maker fill model has no queue position or public-print-size cap, and reconstructed book invalidity does not establish that an exchange order disappeared. Only 19 markets meet the stricter M4A integrity screen. The local same-state policy counterfactual proves fill eligibility past a hypothetical cancellation on B3's path, but downstream inventory and subsequent orders mean its per-fill cash-flow effect cannot be summed into a full-market B2-versus-B3 causal PnL estimate.

Reproduce with `python3 research_tools/cip_b3_book_invalid_backtest.py` from the repository root. Outputs are under `research_outputs/cip_b3_20261003/` and include `summary.json`, `market_results.csv`, `orders.csv`, `fills.csv`, `inventory_paths.csv`, `book_invalid_episodes.csv`, `kept_reduce_orders.csv` and `action_audit.csv`.

Next action: independent review of B3's Phase 1 path divergence and the two cancel-race fills before considering any further policy change.

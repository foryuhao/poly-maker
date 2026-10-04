# BTC5m Two-Phase Continuous Inventory Strategy — B3 Research Design

Status: Frozen research-only experiment from the user's B3 specification, 2026-10-03.
Git base: `97ab0f9` (`m4a-a1-pass`); B2 research files and unrelated production edits are pre-existing uncommitted work.
Comparator: `docs/research/CIP_B2_RESEARCH_DESIGN.md` and `research_outputs/cip_b2_20261003/`.

## Question and scope

Test whether a known accepted/resting BUY order that reduces current `QY−QN` should survive a reconstructed invalid book. All market data, 32 sessions, official settlement labels, latency, full-order maker trade-through fill semantics, $25 capital, 5-share size, T150 phase boundary and persistent quote rule remain B2. No live or production trading authority is granted. This experiment changes only existing-order handling during `Book.valid() == false`; new orders remain frozen.

## Frozen policy delta

For `Net>0`, retain a known active NO BUY and request cancellation of YES BUY. For `Net<0`, retain a known active YES BUY and request cancellation of NO BUY. At `Net=0`, request cancellation of both sides. A submitted order without acceptance is not treated as resting; an already cancel-pending order continues through the inherited lifecycle. No new quote is calculated or placed while invalid. At recovery, normal B2 reconcile resumes and the persistent quote remains at its original price unless an existing legal B2 cancellation reason applies. The rule applies during both gamma harvest and reduce-only, as requested. In reduce-only, a zero net latches stop even if the book is invalid.

The B2 book object exposes only bid, ask, seen and tick. Missing YES/NO bid/ask, crossed BBO and `OTHER` for an invalid state not resolved by those predicates are supportable reasons. Staleness, reconstruction sequence gap and market inactivity cannot be assigned reliably from `Book.valid()` and are not inferred.

## Measurement and attribution

An invalid episode begins at the first reconciliation observing invalid books and ends at the next reconciliation observing valid books or at T300. It records start/end, duration, observed reason set, initial net and orders, the B2 policy action (`CANCEL_ALL_EXISTING_ORDERS`) and B3 actions. Counters distinguish unique accepted orders first kept, risk-increasing and flat orders cancel-requested, blocked new-order decision events, and any known active reducing order canceled solely for `BOOK_INVALID` (must be zero). Decision-event counts are not unique missed submissions.

Compare B3 against B2's exact stored 32-market paths. A direct extra fill against actual B2 requires identical market/order identifier, side, quote, acceptance timestamp, a B2 `BOOK_INVALID` cancellation, and a later B3 fill after that cancellation would be effective. Because Phase 1 can diverge after the first retained order, an additional *same-state policy* flag records whether B2's cancel-all rule, applied at B3's first invalid observation, would have become effective before B3's fill. This is a local causal opportunity, not an exact full-path B2 market outcome. Per-fill direct incremental terminal cash flow is winner payout minus purchase cost; total market PnL deltas also include all downstream strategy actions.

An additional attribution diagnostic starts from B2's exact gamma path and changes the invalid-book rule only after T150. It is labeled `phase2_only_diagnostic` and is not the specified full B3 strategy. It separates Phase 2 retention from Phase 1 path effects.

## Safety and decision rules

Require matching 32 captured market and official settlement identities, exact B2 T150 prefix in the Phase 2 diagnostic, no new order while invalid, no heavy-side reduce-only placement, $25 inherited capital invariant, zero `BBO_ONLY_REPRICE`, and zero `BOOK_INVALID_KEEP_VIOLATIONS`. Report every risk-increasing fill observed while invalid, including cancel-pending state. Critical invariant or data failure is `IMPLEMENTATION INVALID`. Increased material risk exposure or unresolved order-state ambiguity is `SAFETY TRADEOFF UNACCEPTABLE`. Otherwise compare aggregate Phase 2 fills (including pre-T150 orders filled after switch), terminal residual and total PnL. A >$10 total-PnL improvement plus reduced residual and improved fills is material support; otherwise use the requested named result category according to the observed bottleneck. Report accepted post-T150 order conversion separately because it excludes pre-T150 resting orders that fill in Phase 2.

## Limits

The inherited replay fills full remaining size on a qualifying SELL print without queue or print-size inference. Only 19 of 32 markets pass the stricter M4A telemetry-integrity screen. No missing book history or unknown lifecycle state is repaired.

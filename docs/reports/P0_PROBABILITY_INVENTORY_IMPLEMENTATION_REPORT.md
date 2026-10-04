# P0 Probability Inventory Implementation Report

Status: IMPLEMENTED — AWAITING REVIEW
Commit / working tree base: `97ab0f9` (`m4a-a1-pass`); branch `m4-dynamic-ev` had substantial pre-existing tracked and untracked changes. P0 files remain uncommitted by request.
Reviewed predecessor baseline: `m4a-a1-pass` (`97ab0f9`) for research code; no P0 reviewed baseline exists.
Architecture reference: independent fixed research design `docs/research/P0_PROBABILITY_INVENTORY_RESEARCH_DESIGN.md`; no production architecture change.

## Scope implemented

Local-only 30-market selection, causal book replay, three fixed A/B/C policies, taker depth and 500 ms arrival, 5-share threshold, Merge and redemption cash delays, $100 C cash ledger, zero rebates, conservative $20 stop, market and order ledger outputs. The report `docs/research/P0_PROBABILITY_INVENTORY_20261004.md` records the result and limits.

## Files added

- `research_tools/p0_probability_inventory.py`
- `tests/test_p0_probability_inventory.py`
- `docs/research/P0_PROBABILITY_INVENTORY_RESEARCH_DESIGN.md`
- `docs/research/P0_PROBABILITY_INVENTORY_20261004.md`
- This implementation report.
- Local, untracked runtime outputs under `research_outputs/p0_probability_inventory/`.

## Files modified

- `docs/CURRENT_STATUS.md`: a concise P0 handoff note appended. Existing unrelated edits preserved.

## Important implementation decisions

- Uses the saved replay list and local official settlement labels; never calls the network.
- Keeps 30 preselected markets including 27 with zero C trades after the loss stop.
- Treats each Taker order as partial-fill-or-kill at a submit-time ask plus one observed tick limit. Arrival uses the causal book as of exactly 500 ms after submit; no later event is applied first.
- Applies fee as separately recorded USDC cost with zero rebate. The earliest 20 metadata snapshots confirm the schedule and minimum quantity; ten later snapshots are absent and explicitly marked as assumed.
- Makes Merge proceeds available after 60 seconds and residual payout after 15 minutes, both assumed.
- Pair/Residual allocation uses average含费 cost and always reconciles to gross cash accounting.

## Deviations from architecture

- NONE. This is an independent research design, not an M4 production slice.

## Tests added

Three primitive tests for two-sided probability, depth/limit/cash treatment, and stale-book rejection.

## Tests executed

- `.venv/bin/python -m pytest -q tests/test_p0_probability_inventory.py`
- `python3 research_tools/p0_probability_inventory.py`
- Independent per-market reconstruction of all 90 rows from `ledger.csv`; Pair + Residual equality, ±25 net cap, nonnegative C cash, and post-stop no-buy audit.
- `git diff --check`.

## Test results

- 3 tests passed.
- 30/30 market rows for each mode reconciled to buy, fee, Merge, and residual settlement ledger entries.
- No C buy after the third market; no negative modeled cash; no net cap breach. C final cash $93.200664875 = $100 + total PnL −$6.799335125.
- `git diff --check` clean. No PASS verdict is claimed for this implementation.

## Known limitations / unresolved issues

C is a conditional historical depth replay, not a verified exchange fill trace. Ten later markets lack historical fee/minimum metadata snapshots, although C makes no buys in them after stopping. The fee debit is modeled in USDC from the saved schedule; hypothetical exchange token receipts cannot be checked. Merge and redemption delays are assumptions, and chain cost/failure is absent. B and C do not isolate scale from minimum-lot effects. The C stop causes only three traded markets, so A/B-to-C differences are not a clean spread/latency causal decomposition. Live or future profitability is unproven.

## Safety / behavior impact

Live trading semantics changed: **NO**. No production source was edited, no real order was placed, no credential or account state was read, and no external API was called. No commit, tag, or push was made.

## Git diff summary

Only P0 research code, test, and documentation were added by this work, plus a handoff note in the pre-existing modified status document. Generated artifacts remain local and untracked. The working tree has many unrelated pre-existing modifications and untracked research files that this work did not alter.

## Handoff to Reviewer

Independently inspect the script's causal event order, fee/receipt model, stop line, 30-market selection, and ledger reconciliation. Review the retained zero-trade markets and ten assumed-metadata flags. Do not treat this Worker report as a Review PASS or authorize live use.

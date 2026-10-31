# Sequential cross-venue FOK research policy

Registered before inspecting any fresh automatic-confirmation outcomes. Only
the earlier13:13:20 UTC ManchesterUnited transient was known; its execution
availability was unobserved. This is a NEW policy, not a rewrite of the frozen
parallel-cap experiment. Both policies require independent future confirmation;
do not choose their winning historical episodes after looking at returns.

Existing fixed signal and event60s cooldown: fee-adjusted paired quote net>.002
per10-contract unit, exact outcome/date match. Buy10 Kalshi contracts FIRST at
the original cap, at the .5s observation. If unavailable, no second order, zero
exposure. Existing snapshots are counterfactual depth, never personal fills.

If Kalshi fills, use ORIGINAL gross Poly quantity and actual Kalshi cash+fee to
derive the largest valid Poly price cap whose worst-price paired floor covers
.02USD reserve. The cap is decided using only the first fill, not future Poly
prices. BUY fees remove shares; account for changed execution-price delivery.
Send a capped Poly hedge arriving at2.5s; initial metadata delay0/1s plus.25s
transport must fit after the observed first-leg acknowledgement. If not, abort
second order and unwind first leg at5s. If Poly capped FOK lacks full depth at
2.5s, unwind Kalshi at5s, including entry and exit fees. ALL failures/losses and
zero-first-fill signals remain in the population. Missing evidence cannot be
dropped or upgraded into a profitable pair; retain worst exposure bounds.

This tests whether ordering the faster venue first and allowing a CAUSAL hedge
cap improves joint execution. Ordinary completed-game payouts are equivalent;
postponement/fair-price and final-data exceptions are basis risks. Funding,
USD/USDC and nonatomic execution remain risks. Reserve .02USD/10 units is a
scenario allowance, not an actual charged fee. Paper evidence still needs
source/clock/minimum-notional/rule validation and independent time samples.

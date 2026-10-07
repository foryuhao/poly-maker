# ITER-0006 — Human Decision

Status: APPROVED. Source: explicit human instruction in resumed development session. Decision preserved verbatim below.

Human Decision — ITER-0006

APPROVED DEFINITION:

Use cumulative absolute consecutive depth change ("total variation").

For each outcome side independently:

Q_eff(t) =
    Size(BestBid(t))
    + Size(BestBid(t) - TickSize(t))

Over the rolling 2-second window:

DepthChange2s =
    sum(abs(Q_eff_i - Q_eff_{i-1}))

across valid order-book updates in time order.

A side is inactive iff:

NoRealTradeInLast2s
AND
DepthChange2s < 5 shares

The overall Inactive Filter triggers if either outcome side is inactive,
consistent with the Frozen SOP.

Exactly 5 shares of cumulative change is NOT inactive.

Repeated snapshots with unchanged Q_eff contribute zero.

A real trade within the 2-second window makes that side active regardless
of depth-change magnitude.

Decision:
USE CUMULATIVE ABSOLUTE CONSECUTIVE CHANGE.

## Resume

Earlier blocked reports remain immutable. Next artifact 04_ARCHITECT_REVISION_01.md; no implementation until approved revision. Continue autonomous serial program. Real-money authority remains NOT_GRANTED.

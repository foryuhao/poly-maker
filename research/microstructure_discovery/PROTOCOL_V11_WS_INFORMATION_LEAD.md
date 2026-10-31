# V11 fresh-WS execution gate on the V8 information-lead rule

V8 remains frozen, including its second game's K-only unwind loss bound.
That event's independent raw WS shows the .79 REST ask was already obsolete
at decision: current ask .82, then .91. Receipt skew and server timestamp
age do not make two REST responses simultaneous executable quotes.

New future-only registration; same fixed 32-pair universe and source-end
time, 3-second lead rule, strict .50 USD margin, actual USD-FOK budget,
fee bounds, K .5s/P 1.5s observations, failed-leg accounting, cooldown,
capital reservation and finality requirements as V8. No old observations
count as V11 validation. Do not replace V8's recorded losses with V11 skips.

Additional predecision gate: direct public market WS subscribed to all
44 tokens, full initial book for the asset in the current connection
generation, connection live and last frame/PONG <=.5s old, no timestamp
regression or reported-BBO mismatch. Quiet assets may retain valid books
while the same connection remains live; the age of a quiet price alone
does not prove it stale. Reconnect clears all state. No REST overwrite of
the WS book or invented cancellation/queue priority.
Reported-BBO mismatches stay invalid until a fresh full snapshot; a later
matching best price cannot by itself repair uncertain deeper cash depth.
Crossed/empty books cannot pass the gate.

Using only messages already received, require sufficient current WS cash
depth under the ORIGINAL planned Poly price cap to spend the whole USD
budget and receive the target fee-bounded 10 tokens. If the current ask is
above the cap or depth insufficient, skip BEFORE reserving capital or
observing a hypothetical K fill. Preserve skip counts; do not call these
skips zero-profit executed trades. Save the actual gate state with every
future decision and raw WS messages including generation/source clocks.

This filter removes already-known obsolete entry prices; it cannot prevent
cancellations or news arriving during the subsequent matching delay. Keep
all delayed failures and exception bounds. First prove future economics
and executable availability; green source checks or a retrospective skip
of V8's losing case do not establish an edge. No actual orders.

# CA-triggered maker pullback entry

This is an opt-in entry policy. It is disabled by default; the running trading
configuration and processes have not been changed. The initial parameters below
are development starting values, not evidence of profitable trading.

## Definition

1. During NORMAL, accept an independently recorded CA confirmation whose
   formation lasts more than 100 ms. Freeze its outcome, original bid anchor,
   confirmation ask, tick, generation and timestamps. CA is an observation
   trigger, not immediate permission to buy.
2. Wait at most `advance_timeout_sec`, measured from CA confirmation. Both the
   best bid and best ask must advance by at least `advance_ticks * tick` relative
   to the original bid anchor and confirmation ask respectively. A last trade
   alternating between bid and ask, or a change in only one quote side, does not
   qualify. Every formation requires fresh, consistent books throughout.
3. Once this advance is observed, make the same outcome eligible for one fixed
   post-only BUY at `original_bid + entry_offset_ticks * tick`. Require the price
   to remain below the current best bid before native submission: the maker
   order waits for a future retracement, rather than joining after the first
   retracement has already arrived. Preparation, signing and POST guards repeat
   the validity checks.
4. The order's absolute deadline is `advance_observed_monotonic + order_ttl_sec`.
   Preparation and network time use up this lease; neither repeated quotes nor
   a later CA reset it. Existing BA network admission budgets and its maximum
   entry age apply from the recorded advance event, without resetting the
   original CA timestamps in the evidence.
5. Once sent, a bid returning to the entry price is the intended fill
   opportunity. A bid below the original CA anchor invalidates the formation.
   Expiry, tick or generation changes, stale/inconsistent data, and opposite-side
   formation conflicts also invalidate it. Invalidation is latched: later fresh
   books cannot revive the same formation. Normal cancellation and complete
   order/fill close proofs remain mandatory; requesting cancellation does not
   establish that an order cannot fill.
6. Owned fills use the existing TP/repair/settlement path. This entry policy does
   not change the configured exits, fill accounting, minimum size or sizing.

For example, with a 0.01 tick and CA quotes 0.60/0.61, the initial settings require
quotes to reach at least 0.62/0.63. The candidate maker price is then fixed at
0.61. It must be submitted before the bid first returns to 0.61, and remains
subject to the absolute lease and the original 0.60 floor. This does not guarantee
that a break can be canceled before a fill.

## Configuration

No `config/` or `livecfg/` files are edited by this change. A future explicit
activation would require these settings together in the selected configuration:

```toml
[rdp_sm]
normal_entry_mode = "ca_v1"
ca_entry_direction = "signal"

[rdp_sm.ca_observe]
enabled = true

[rdp_sm.ca_pullback]
enabled = true
advance_ticks = 2
entry_offset_ticks = 1
advance_timeout_sec = 2.0
order_ttl_sec = 3.0
```

The policy rejects `higher_bid`: the observed CA, price advance, resting quote
and eventual position must refer to the same token. `entry_offset_ticks` must
be smaller than `advance_ticks`. The order TTL must exceed the configured POST
budget floor plus minimum live budget. A short TTL or expired network admission
can legitimately produce no order.

The research convention of 1.00 risk and `1/p` shares is not implemented here.
The existing sizing and venue minimums still apply. This prevents silently
converting a theoretical sub-minimum research position into a live order.

## Ownership and evidence

Each formation is owned by its original CA episode ID. Original CA expiry or
anchor change after confirmation does not by itself erase the recorded trigger;
the pullback policy checks its own floor, quote quality and deadlines. The
original CA detector and its legacy entry adapter are unchanged when this feature
is disabled.

The existing CA event stream records `CA_PULLBACK_STARTED`,
`CA_PULLBACK_ADVANCED`, `CA_PULLBACK_SUBMITTED` and `CA_PULLBACK_CLOSED`, including
the frozen parameters and event timestamps. `ca_observation.pullback` in native
POST evidence binds the formation to the submitted order. Session headers record
the configuration and the new module's source hash.

## Validation scope

Offline tests exercise the real WS parsing and native maker SDK boundaries:
CA-only rejection, both-side quote advance, same-token selection, missed
retracement, support failure, stale-data gaps, reconnect, absolute deadlines,
one-shot consumption, final POST invalidation, and owned-fill TP/close proofs.
Existing CA, higher-bid, sizing and TP tests provide regression coverage.

The focused suite has 23 passing cases; a related 142-case combined regression
run also passed. A larger 365-case run initially hit
`CROSS_ORDER_TRADE_SET_CHANGED` in the pre-existing delayed-opening TP close-proof
test with pullback disabled (364 passed, 1 failed). The exact test subsequently
passed individually, in the combined regression run, and in three checks using
unmodified HEAD source. That intermittent close-proof failure has not been
attributed or changed by this implementation.

These checks establish implementation behavior, not forward EV. Historical
evaluation needs continuous ordered quotes/trades and conservative maker queue
assumptions; the old 59 filled CA trades cannot identify fills of this new policy.

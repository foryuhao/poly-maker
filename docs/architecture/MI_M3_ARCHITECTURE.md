# MI M3 — Single-Market Micro-Live Architecture

Status: implementation design for independent review
Base: `57a9e9ce2f260e4015fedfd632771fcc37fd6b8d`

## Objective and scope

Expose one explicit `MICRO_LIVE` BTC5m lifecycle through `mi-micro-live`. A human supplies `--arm` and types `ARM MI MICRO LIVE` after a valid MI candidate is shown. One selected market is evaluated. At most one BUY, post-only, five-share order can be placed. The process stops after its fill, cancellation, rejection, or fail-closed outcome. There is no trading rollover.

## Boundaries and flow

`src/polymaker/mi_micro_live.py` is the sole M3 orchestrator. It selects a BTC5m epoch with at least 180 seconds left, resolves exact official market metadata, validates the connected identity, reads official collateral and account-wide open orders, then takes an identity- and condition-bound $25 reservation from the existing process authority. It uses the existing M2A official market-data and account adapters and the M1 MI decision, with `max_abs_inventory=5`. The raw strategy candidate must be exactly five shares at the current best bid. The price handed to the gateway is normalized with the same official SDK rounding primitives and metadata tick used by `create_order()`. That normalized price must remain strictly below the fresh best ask, on the signing tick, within the reservation and before T-15 seconds. If the SDK's current minimum tick makes the supplied metadata tick invalid, `create_order()` denies before POST.

The submit boundary repeats Generic Gate authorization, identity, active reservation, official open-order and fresh book checks. `ExecutionGateway.place([quote], meta, force_post_only=True, strict_placement=True)` signs through the official CLOB SDK and calls `post_orders(post_only=True)`. A nonempty official `orderID` is required. The existing gateway handles dict and singleton-list responses.

The order is polled through the authenticated official CLOB `get_order`; terminal proof is paired with an empty official `open_orders` snapshot, collateral read and official Data API position read. A partial fill triggers immediate cancellation of its remainder. An unfilled order is cancelled when its observation window expires or the cutoff arrives. Both paths require a subsequent terminal read. Uncertain cancel or account state retains the reservation and exits nonzero with manual attention. A fully confirmed terminal state releases it. Exceptions attempt safe cancellation of a known order; they never unconditionally release.

## Fixed M3 limits

| Limit | Value |
| --- | --- |
| Market | BTC 5-minute Up/Down, one market |
| Minimum startup remaining | 180 seconds |
| Final new-order cutoff | T-15 seconds |
| Clip / target cap | 5 shares / ±5 shares |
| Working orders / filled clips | 1 / 1 |
| Cash reservation | $25 ACTIVE, identity and market bound |
| Side / liquidity | BUY only / maker only |
| Observation | 30 seconds, then cancel if still open |

## Exclusions and failure model

No continuous trading, SELL, taker order, second filled clip, layered quotes, dynamic capital model, new external interface, database, cross-process locking, or crash recovery. Reservation authority is process-local, so an uncertain live order requires manual attention after process exit. The operator must use an independent review before real execution. Implementation and automated tests use no authenticated trade writes.

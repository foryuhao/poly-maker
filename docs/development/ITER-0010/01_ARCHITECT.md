# ITER-0010 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `9acbbd66b3d005f674eded2c4c9f4917cebadfcc`.
- ITER-0009 PASS archived at `047c901332360b4cb6e4e474ecba6ec04a030736`.
- Inspected AGENTS.md, Frozen SOP, baseline, index, latest result/review/receipt, RDP bootstrap/CLI, market-data service/parsers/books and relevant tests.
- Preserved untracked `research_outputs/` is the only working-tree residue.
- Prior full-suite evidence: `1007 passed, 2 skipped`.

## Problem

The RDP command validates official HTTP selection and metadata but never connects the official market WebSocket. It cannot establish that both selected outcome books belong to one live connection generation.

Compose the existing market-data service into the read-only preflight and produce bounded, immutable feed-readiness evidence.

## Evidence / Current Behavior

- `MarketDataService` already subscribes by token, maintains independent books, discards obsolete-generation backlog and exposes connection/snapshot generations.
- Its bounded queue and connection cleanup infrastructure already exist.
- Existing Engine readiness checks require a live connection and current-generation snapshots for both books.
- Generic parsers tolerate missing or malformed fields; their successful mutation alone cannot certify RDP input reliability.
- Generic tick-change handling updates the book but does not wake `on_dirty`.
- The RDP bootstrap already supplies independently validated HTTP tick/minimum metadata.

## Existing Components Reused

- Existing official `MarketDataService` transport, subscription, queue, parsers and order books.
- Existing `BookView`, connection callbacks and snapshot-generation evidence.
- Existing RDP HTTP bootstrap and token metadata.
- Existing CLI, logging and HTTP test fixtures.
- Existing fake-WebSocket and old-generation backlog tests.

## Existing Components Modified

- `src/polymaker/rdp_live.py`: compose bounded public feed validation after HTTP bootstrap.
- `src/polymaker/cli.py`: call the composed preflight and report its scope accurately.
- Relevant RDP preflight tests.

**Shared-Core Change: YES**, limited to CLI wiring. Default market-data service/parser behavior remains unchanged.

**FL Behavior Impact:** NONE.

**IM Dependency:** NONE.

## New Components Required

A thin RDP-local market-data adapter, preferably in `src/polymaker/rdp_marketdata.py`, that extends or wraps the existing service.

It adds selected-frame validation, tick-change wakeups and immutable readiness evidence. It must reuse existing transport and book mutation rather than implementing another WebSocket client or book engine.

## Desired Behavior

The existing command remains:

```bash
uv run polymaker rdp-live --market <exact BTC5m slug> --preflight-only
```

After successful HTTP bootstrap:

1. Create the existing market-data service through the RDP adapter, using its official market WebSocket URL.
2. Subscribe to exactly the two selected outcome tokens under their selected condition.
3. Seed each token’s tick from validated bootstrap metadata; record that source explicitly.
4. Start one owned service task.
5. Wait for two valid, nonempty, uncrossed books whose snapshots belong to the same positive, currently connected generation.
6. Copy immutable book views, token ticks, tick-source evidence, generation and receipt time without yielding between final readiness check and capture.
7. Recheck selected-market expiry.
8. Record the result, stop/cancel and await the owned task, then exit.

Use a bounded startup timeout, default 15 seconds, also capped by remaining market time. This is a connection-preflight deadline, not a REBALANCE stop timer.

A result certifies the observed feed state at capture only. It does not grant account readiness or trading authority.

## Minimal Scope

The adapter must validate selected book-changing frames before delegating to existing handlers:

- Selected-token frames must have the selected condition identity.
- Book snapshots require explicit bid/ask lists and valid level mappings.
- Prices and quantities must be finite, representable numeric values; reject booleans, invalid prices, negative quantities and malformed selected entries.
- Price-change entries require valid token identity, side, price and quantity. Validate applicable entries before any mutation.
- Explicit snapshot ticks and tick changes require finite positive ticks inside `(0,1)`.
- Entirely foreign-token events are ignored and cannot satisfy readiness.
- Contradictory or malformed selected frames fail the preflight explicitly.
- Recognized tick changes delegate to existing mutation and wake the selected-token callback.

Do not require `tick_size` on every WS snapshot: the existing parser supports its absence. Preserve validated REST seeding and distinguish it from an explicit WS snapshot/change source. Do not describe a REST-seeded tick as newly confirmed by that WS snapshot.

Keep this provenance distinction visible for subsequent runtime work.

## Invariants

- One explicit market; no rollover.
- Existing official HTTP plus official market WebSocket only.
- No gateway connection, account request, credential derivation, POST, DELETE or RPC.
- No strategy filter or trading state is introduced.
- Quiet books on a live connection are not stale merely because they do not mutate.
- Disconnect or generation change invalidates old readiness.
- Old-generation frames cannot certify the new generation.
- Tick changes remain token-specific and trigger observation.
- No background task survives success, failure, timeout or cancellation.
- Public preflight success cannot arm orders.

## Acceptance Criteria

1. The composed CLI subscribes only to the selected YES/NO tokens.
2. Connection alone, one snapshot, empty/crossed books or obsolete snapshots cannot pass.
3. Two valid snapshots from one live current generation produce copied feed evidence.
4. Reconnect requires fresh snapshots for both tokens.
5. Disconnect before capture fails readiness.
6. Foreign frames cannot satisfy readiness; malformed selected frames fail explicitly before partial mutation.
7. Independent seeded ticks, explicit snapshot ticks and tick-change values retain correct source attribution.
8. Tick changes wake observation even without another price/depth event.
9. Timeout, expiry, service failure and caller cancellation close the session and await all owned tasks.
10. CLI success/failure and durable logs distinguish HTTP metadata from observed WS readiness.
11. Existing market-data and predecessor behavior remains unchanged.

## Required Tests

Use fake WebSocket transport and mocked HTTP:

- Exact subscription payload.
- No connection, one book, empty/crossed book and valid two-book readiness.
- Disconnect/reconnect and old-generation backlog.
- Generation change between first and second snapshots.
- Invalid identity, malformed levels/deltas, nonfinite/boolean numbers and invalid ticks.
- No partial selected mutation from an invalid multi-entry delta.
- Foreign-token events.
- REST seed with missing snapshot tick; explicit snapshot tick; token-specific tick change and wakeup.
- Immutable capture after subsequent book mutation.
- Startup timeout, market expiry, service failure and caller cancellation.
- Task cleanup in every exit path.
- Complete CLI success/failure with forbidden execution/auth components patched to raise.

Run focused RDP tests, existing market-data/parser tests, affected catalog/BTC5m/CLI regressions and the full suite when practical. Record exact commands/results and inspect status, untracked files, diff stat and `git diff --check`.

Safely exercise CLI help and the complete command through mocks.

## Observability Requirements

Reuse the existing RDP preflight log.

Record:

- selected condition and tokens;
- observed connection/snapshot generation;
- copied best bid/ask for each token;
- token tick and source;
- capture timestamp;
- feed success or categorized failure;
- `real_money_trading_performed: false`.

Keep bootstrap evidence and feed evidence distinguishable. Do not claim integrated readiness.

## Live Validation

An optional public HTTP/market-WS-only invocation may demonstrate both official paths without trading authorization.

If performed, retain command, result and log location. No account mutation or execution component is permitted. Fixtures do not substitute for final project-level live read-only verification.

## Explicit Non-Goals

- Account/order/user WebSocket integration.
- Capital readiness.
- Book Filter orchestration or quote creation.
- NORMAL/REBALANCE controller or timers.
- Execution, cancellation or reconciliation.
- SDK cache refresh.
- Periodic metadata refresh or final trading-time tick certification.
- Trading-mode CLI options.
- Real-money trading.

## Risks

Readiness evidence is momentary; a subsequent runtime must maintain continuous generation/reliability guards.

REST-seeded tick metadata may predate subscription. Its source must remain explicit; this iteration does not certify freshness for a trading decision.

Generic service reconnects must remain bounded by the owned preflight deadline.

## Deferred Findings

- Runtime composition must reconcile REST metadata and WS tick provenance before admission.
- Book Filter must later consume current-generation books/trades and monotonic receipt times.
- Authenticated account readiness, controller/lifecycle integration and metrics remain outstanding.

## Human Approval Required

NO.

This is bounded reuse of existing official market-data infrastructure within the read-only RDP command. It changes no Frozen semantics or trading authority.

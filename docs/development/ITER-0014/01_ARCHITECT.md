# ITER-0014 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `8ff62be233b900d3ab0adc2c9ce54aead1f28ded`
- HEAD and `origin/rdp-phase0-dev` match.
- ITER-0013 is CODE_READY, archived at `42a3ac58b1167115f8818b7930cd456fc57a345e`; final Reviewer07 returned PASS.
- Current iteration: ITER-0014, ARCHITECT.
- Only preserved `research_outputs/` is untracked.
- MICRO_LIVE_READY: NO.
- Real-money authorization: NOT_GRANTED.

**Shared-Core Change: NO.** Changes are confined to RDP composition, its CLI command and tests. Existing shared gateway, stream, store and processor behavior remains unchanged.

**FL Behavior Impact:** NONE.

**IM Dependency:** NONE.

## Problem

RDP has separate bounded observations, but no owner that keeps its public feed, authenticated user feed, account gateway and event processor alive together for one selected market.

Implement one prerequisite: a shared single-market input-session lifetime, exercised through a read-only CLI observation path.

## Evidence / Current Behavior

- `await_rdp_feed` creates a public service, captures readiness and closes it.
- `observe_rdp_account_and_user_channel` owns a gateway, in-memory store, processor and user stream, then closes them immediately after account capture.
- `preflight_rdp_market` calls these sequentially. Its returned evidence describes connections that have already closed.
- `RDPMarketDataService` already validates selected frames, reconstructs books, tracks generation and applies dynamic ticks.
- `UserStream.run_once` already provides one authenticated connection with application heartbeat and sanitized diagnostics.
- Existing account helpers support reads through an already connected gateway.
- `snapshot_inventory` already projects confirmed fills from an explicit order scope in the existing processor.
- Frozen quote-cycle and reconciliation behavior will require these components to share one lifetime and processor. Reopening disconnected probes for each future action cannot provide that ownership.

## Existing Components Reused

- Exact HTTP bootstrap and official-origin validation.
- `RDPMarketDataService`, existing public transport and reconstructed books.
- `UserStream.run_once`, selected-condition subscription, heartbeat and sanitized diagnostics.
- Account preparation, identity validation and `_capture_account_with_gateway`.
- Existing initial-$30 backing evaluator.
- `StateStore`, `UserEventProcessor` and confirmed-fill accounting.
- Existing cancellation-resistant task draining and gateway `aclose`.
- Existing `rdp-live` CLI and logging.

## Existing Components Modified

- `rdp_marketdata.py`: minimal extraction of readiness waiting/capture so an external owner can retain the service.
- `rdp_userstream.py`: minimal extraction of existing authenticated ownership/capture so an external owner can retain the gateway, stream, store and processor.
- `rdp_live.py`: reuse bootstrap and dispatch the read-only observation path.
- `cli.py`: add `--observe-only`, mutually exclusive with `--preflight-only`, and report termination.
- Relevant tests.

Existing bounded APIs retain their signatures and behavior through the extracted helpers. No shared transport or execution modification is authorized.

## New Components Required

One RDP input-session owner, preferably `src/polymaker/rdp_runtime.py`, providing:

- One scoped lifetime for existing input components.
- Startup evidence.
- Current input-health observation.
- Deterministic termination and cleanup.

A context manager or equivalent owned coroutine is an ordinary implementation choice. Do not introduce a second engine, transport, parser or ledger.

## Desired Behavior

The command:

```bash
uv run polymaker rdp-live --market <exact-BTC5m-slug> --observe-only
```

uses configured credentials to establish one read-only input session and keeps it alive until the selected interval ends.

The session owns:

```text
exact bootstrap
→ public books
→ derive-only authenticated gateway
→ selected user subscription and heartbeat
→ account/startup backing evidence
→ shared store and processor
→ continued observation
→ market-end cleanup
```

It creates no orders and cancels no existing orders.

The session exposes the same processor that receives user events. Existing inventory and orders are not adopted. Startup backing must certify the existing empty-run requirement before observation is reported as started.

This path exercises resource ownership and market termination. It does not claim integrated trading readiness.

## Minimal Scope

1. Freeze relevant configuration and authorities before asynchronous bootstrap.
2. Start one public service and one derive-only gateway/user-stream owner for the selected market.
3. Establish existing public readiness, user heartbeat evidence and account reads while both streams remain alive.
4. Evaluate existing initial backing immediately from the captured account evidence. An ineligible result prevents successful observer startup; preserve its reason.
5. Retain the public service, gateway, user stream, store and processor after startup capture.
6. Monitor owned tasks, public connection/generation and existing fatal validation state, user connection/generation and heartbeat health.
7. After successful startup, any disconnect, generation change, fatal selected-frame error, lost user heartbeat or unexpected task termination ends this observation attempt. Do not automatically restart it.
8. Stop normally at the selected market’s end. Also support ordinary task cancellation/interrupt.
9. Drain every owned task and gateway operation before returning or propagating cancellation.

Factor existing ownership code rather than copying complete setup, validation or cleanup implementations.

## Invariants

- Exactly one selected BTC5m market, public subscription pair and user condition.
- Official HTTP/WebSocket interfaces only; no Engine startup, blockchain RPC or legacy merge/cleanup path.
- One gateway connection and one event processor per session.
- No credential creation, signing, submission, cancellation, allowance update or order adoption.
- Credentials never enter session evidence, logs or CLI output.
- The observer uses an isolated in-memory store; configured project state databases remain untouched.
- Existing confirmed-fill projection remains the accounting authority for future scoped orders.
- A quiet connected market is not declared stale merely because no book mutation occurs.
- Generation changes invalidate established observations.
- Heartbeat evidence remains transport evidence, not an explicit authentication acknowledgment.
- Startup account/backing evidence is timestamped historical evidence. Keeping its gateway open does not make that snapshot continuously fresh.
- No NORMAL, REBALANCE or STOP trading policy is implemented here.
- No new strategy timer or maximum REBALANCE duration.

## Acceptance Criteria

1. `--observe-only` and `--preflight-only` conflict before network activity; absence of either retains the existing refusal to run.
2. Existing public-only and authenticated preflight behavior remains intact.
3. Observation opens one gateway and one instance of each stream, scoped exclusively to the selected market.
4. Both streams remain alive through account capture and for the complete successful observation lifetime.
5. Startup requires existing book/user readiness and eligible initial backing; selected pre-existing inventory/orders prevent certification without cleanup.
6. The exposed session processor is the exact processor passed to `UserStream`; no independent fill state exists.
7. Public book and tick changes continue updating that same retained service.
8. Disconnect, reconnect/generation change, fatal feed validation, user heartbeat failure and task termination produce categorized observation failure.
9. A healthy quiet session remains active until market end.
10. Market end produces a normal `MARKET_END` termination and closes all resources.
11. Cancellation, repeated cancellation and partial-startup failures drain all owned tasks, stores, sockets and gateway executor work.
12. All structured results state read-only observation and `real_money_trading_performed=false`; none claims full state reliability or MICRO_LIVE_READY.
13. Required tests pass with zero attempted live network operations.

## Required Tests

Use deterministic fake services, gateways, credentials and clocks.

- CLI mode conflict/refusal and existing preflight regressions.
- Integrated successful startup: one selected pair, one selected condition, one derive-only connection and same processor identity.
- Streams remain alive before, during and after account capture.
- Dynamic tick/book updates remain visible through the retained service.
- Existing startup backing boundaries, foreign commitments and selected-state rejection remain unchanged.
- Quiet healthy operation through market end.
- Public/user disconnect and generation change, including changes immediately before startup capture or observation termination.
- Public fatal validation, unexpected stream completion and user heartbeat failure.
- Partial startup, startup expiry, market end and cancellation cleanup.
- Repeated cancellation while gateway reads or cleanup are draining.
- Synthetic owned-event fixture demonstrating that the retained processor can be consumed by existing confirmed-fill accounting; MATCHED evidence must not become confirmed inventory. Production observation must not register such synthetic orders.
- Credential sentinels absent from output, logs, errors and returned evidence.
- Forbidden mutation/signing methods fail immediately if called.

Run targeted tests, affected RDP/account/userstream/marketdata regressions, predecessor regressions and the full suite when practical.

Block socket connection and DNS operations during controlled validation; record attempted-network count. Replace complete composed dependencies in CLI fixtures so environment credentials cannot reach real services.

## Observability Requirements

Record only the evidence needed for this ownership prerequisite:

- Observation startup/termination.
- Selected slug and condition.
- Initial public and user generations.
- Initial account/backing timestamps and eligibility.
- Initial tick/minimum-size metadata.
- Termination time, category and reason.
- `mode=READ_ONLY_OBSERVATION`.
- `real_money_trading_performed=false`.

Input objects retain current books/ticks for inspection. Do not introduce a high-volume telemetry subsystem or claim full Phase 0 metrics.

## Live Validation

Exercise command help and the full observation path with controlled integration fixtures advancing through market end.

A later safe official read-only check may verify sustained connections and normal market-end cleanup. This iteration requires no live account calls or real credentials for acceptance.

The documented observation command must be exercised safely before its behavior is claimed. It remains incapable of autonomous trading.

## Explicit Non-Goals

- Order creation or live-send authorization.
- Trading controller, quote-cycle execution or FillWindow.
- Cancellation/reconciliation integration.
- Automatic reconnect recovery.
- Periodic account refresh or continuous capital certification.
- Fill/order adoption from existing account state.
- Persistent trading-run state or restart recovery.
- Complete required RDP metrics.
- Refactoring shared Engine, gateway, processor or FL behavior.

## Risks

- Factoring ownership could change bounded preflight cleanup; preserve and test those APIs.
- A transient disconnect could be missed if monitoring checks only current booleans; generation changes and interruption evidence must invalidate the attempt.
- Cancellation must not close the gateway before its executor work drains.
- A retained account snapshot can become stale. It must never be presented as continuously authoritative.
- Quiet transport observation cannot prove delivery of actual owned order/fill events.

## Deferred Findings

- Continuous authoritative account refresh and reconnect reconciliation remain required.
- Future cycle orchestration must use the retained processor and confirmed-fill projection, rather than optimistic store positions.
- FillWindow, placement capture, strict acknowledgments, close barrier and NORMAL/REBALANCE composition remain unintegrated.
- Real execution safety gates and complete accounting/activity metrics remain outstanding.
- This observation session is not MICRO_LIVE_READY.

## Human Approval Required

NO.

This is read-only composition of approved existing components. It changes no Frozen strategy semantics, external interface or trading authority.

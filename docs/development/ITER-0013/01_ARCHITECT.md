# ITER-0013 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Canonical root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `645ed67c7f42d2beec6dd6183806804f1a66d6bb`
- Previous iteration: ITER-0012, CODE_READY; final Reviewer `06_REVIEWER_REVISION_01.md` returned PASS.
- Archive evidence: ITER-0012 `08_ARCHIVE_RECEIPT.md`.
- Current iteration: ITER-0013, ARCHITECT.
- Working tree contains the Orchestrator’s ITER-0013 initialization artifacts and preserved untracked `research_outputs/`.
- MICRO_LIVE_READY: NO.
- Real-money authorization: NOT_GRANTED.

**Shared-Core Change: YES** — narrowly authorized, default-off application-heartbeat support and sanitized RDP diagnostics in the existing `UserStream`.

**FL Behavior Impact:** NONE intended. Existing callers retain current defaults, reconnect behavior, event parsing and processor semantics.

**IM Dependency:** NONE.

## Problem

The authenticated RDP preflight reads account state but does not exercise the selected-market user WebSocket. The existing `UserStream` also lacks the application heartbeat required by that official endpoint.

Implement one prerequisite: a bounded, read-only user-channel check composed with the existing authenticated account preflight.

## Evidence / Current Behavior

- `rdp_live.preflight_rdp_market` composes public HTTP metadata, public market WebSocket evidence and optional authenticated account reads.
- `rdp_account.account_snapshot` derives existing credentials, performs strict official reads and closes its gateway. Derived credentials never enter the returned snapshot.
- `userstream.client.UserStream` already sends authenticated, condition-scoped subscriptions and routes wire events through existing normalization and `UserEventProcessor`.
- Its WebSocket protocol pings do not implement the required text-frame heartbeat. Official documentation specifies text `PING` every ten seconds and text `PONG` responses. It also states that reconnects require authoritative account refresh because missed changes are not replayed. [Polymarket real-time order documentation](https://docs.polymarket.com/trading/realtime-order-updates)
- `UserStream.connected` becomes true immediately after sending the subscription. That flag alone does not establish application responsiveness or subscription acceptance.
- The existing doctor helper accepts an idle timeout as success; that readiness assumption must not be reused.
- Frozen `StateReliable` requires reliable market, order and fill state. This iteration supplies transport evidence; it does not complete that gate.

## Existing Components Reused

- `ExecutionGateway.connect(derive_only=True)`, authenticated reads and cancellation-safe `aclose`.
- Existing account identity, official-origin, exact-order and collateral validation.
- `UserStream` connection, subscription, normalization and processor routing.
- `UserEventProcessor` and `StateStore`, using an isolated in-memory store for this observation.
- Existing RDP CLI, preflight result and structured logging.

## Existing Components Modified

- `src/polymaker/userstream/client.py`: opt-in application heartbeat, observable heartbeat receipts and sanitized diagnostics for the RDP opt-in path.
- `src/polymaker/rdp_account.py`: minimal internal factoring to compose account reads and user observation under one owned derived-credential gateway.
- `src/polymaker/rdp_live.py`: invoke that composition for `account_check=True`; expose immutable user-channel evidence.
- `src/polymaker/cli.py`: describe and display the additional authenticated-path evidence.
- Relevant tests.

The existing standalone `account_snapshot` API and its account-only behavior remain available. No gateway execution behavior or shared processor accounting change is authorized.

## New Components Required

One small RDP user-observation adapter/helper, preferably `src/polymaker/rdp_userstream.py`, containing:

- Immutable, secret-free readiness evidence.
- Categorized errors.
- Bounded readiness waiting and owned-task cleanup.
- RDP-specific observation of subscription rejection and transport failure.

Reuse the existing transport and event processor. Do not introduce another WebSocket client or fill ledger.

## Desired Behavior

Running the existing command with `--preflight-only --account-check` additionally:

1. Derives credentials through the existing read-only gateway.
2. Opens the official user endpoint and immediately subscribes to exactly the selected condition ID.
3. Sends an initial text `PING`, then maintains the documented ten-second cadence while the connection is owned.
4. Requires an actual text `PONG` from the current connection generation.
5. Keeps the user connection alive while existing account reads complete.
6. Captures account evidence and current-generation user transport evidence before closing all owned resources.
7. Reports both results without granting trading authority.

Evidence must distinguish:

- Subscription frame sent.
- Application heartbeat response observed.
- Successful authenticated account reads using the same credentials.
- Connection generation and receipt time.

Do not label `PONG` as a server acknowledgment that authentication or subscription was accepted. This is point-in-time transport and account evidence; complete order/fill reliability remains a later integration prerequisite.

## Minimal Scope

1. Add default-off application-heartbeat support to `UserStream`. Retain existing WebSocket protocol ping settings.
2. Recognize text or byte `PONG` control frames without passing them to event normalization.
3. Maintain at most one heartbeat task per connection. Missing responses must invalidate connection health and terminate that connection within a bounded interval.
4. For RDP, require an initial heartbeat response within fifteen seconds, bounded additionally by market end. Use monotonic time for elapsed deadlines.
5. Compose this observer with existing account reads under one gateway connection; no second credential derivation.
6. Preserve the same connection generation throughout successful capture. A disconnect, heartbeat failure, subscription rejection or reconnect during observation fails this preflight attempt.
7. Capture the successful result only while the stream remains connected, its heartbeat is current and its task has not completed or failed.
8. Close the in-memory store, WebSocket, heartbeat task, stream task and gateway on success, error, market expiry and cancellation.

A thin adapter, callback or internal helper is an ordinary implementation choice. The change must avoid duplicating account validation or transport loops.

## Invariants

- Only official Polymarket HTTP and WebSocket interfaces.
- User endpoint is fixed to `wss://ws-subscriptions-clob.polymarket.com/ws/user`; configuration mutation cannot redirect it.
- Credentials remain transient and are never returned, printed, journaled or logged.
- RDP diagnostics contain categories and exception types, never raw network exceptions or authentication/error payloads.
- Existing orders are neither adopted nor canceled. Existing inventory is neither imported nor changed.
- The observation store has no owned-order registry and cannot acquire order authority.
- No order signing, submission, cancellation, allowance update or credential creation.
- Application heartbeat receipts are generation-scoped; old receipts cannot certify a new connection.
- Idle timeout, arbitrary JSON and WebSocket protocol pong do not substitute for an application `PONG`.
- Existing account precision, startup backing, $30 capital and approved $5 reserve behavior remain intact.
- NORMAL/REBALANCE strategy semantics and confirmed-fill accounting remain unchanged.

## Acceptance Criteria

1. Public-only preflight makes no authenticated account or user-channel connection.
2. Authenticated preflight uses one derive-only gateway and subscribes with its same credentials to exactly one selected condition ID.
3. A silent connection fails; an actual current-generation application `PONG` permits transport capture.
4. Initial `PING` follows subscription; subsequent application pings use the documented cadence.
5. Heartbeat timeout, recognized authentication/subscription error, disconnect, reconnect, stream termination and elapsed market prevent successful capture.
6. Account reads proceed while the user stream remains alive. Final capture checks current health rather than relying on earlier readiness.
7. Returned evidence contains the selected condition, generation, heartbeat receipt and explicit subscription/transport evidence; it contains no credentials and claims no explicit server authentication acknowledgment.
8. Existing account-only callers preserve their return type and behavior. Existing FL user-stream callers retain default behavior.
9. User events continue through existing normalization/processor routing; no duplicate parser or ledger appears.
10. All owned resources terminate on every exit, including repeated cancellation. Existing gateway executor draining remains effective.
11. Required tests pass; no real-money actions occur.

## Required Tests

Use deterministic fake WebSockets, clocks and gateways; tests must not contact live account services.

- Exact endpoint, initial subscription, selected condition and same-derived-credential assertions.
- Initial and recurring application heartbeat; text/byte `PONG`; protocol pong and unrelated frames cannot satisfy readiness.
- Silent connection, absent/stale heartbeat, authentication rejection, send/receive failure and task completion.
- Disconnect/reconnect invalidates previous-generation evidence, including a race immediately before capture.
- User connection remains active throughout account reads; failure during those reads prevents overall success.
- Market expiry before readiness and during capture.
- Public-only path remains unauthenticated.
- Account-only API and exact decimal account/backing regression.
- Existing order/trade fixtures still route through the existing processor; unrelated/unowned events cannot mutate authoritative project state.
- Success, failure, cancellation and repeated-cancellation cleanup: socket, heartbeat task, stream task, store and gateway drained.
- Credential sentinel tests across CLI output, logs, errors and returned evidence, including exceptions containing credential-like text.
- Forbidden gateway methods fail the test if invoked.

Run targeted tests, affected userstream/tracker/account/preflight regressions, predecessor regressions and the full suite when practical. Record exact commands and results.

## Observability Requirements

Add one structured user-channel preflight result with:

- Success/failure category.
- Selected condition ID.
- Subscription-sent status.
- Connection generation.
- Application heartbeat receipt and capture timestamp.
- Explicit point-in-time transport evidence.
- `real_money_trading_performed=false`.

Keep account-read success and initial capital eligibility separately observable. CLI wording must not claim MICRO_LIVE_READY or complete order-state reliability.

## Live Validation

The existing command shape remains:

```bash
uv run polymaker rdp-live --market <exact-current-BTC5m-slug> --preflight-only --account-check
```

Exercise help and the safe composed path with deterministic integration fixtures.

A configured read-only official check may subsequently verify subscription transmission, application heartbeat and authenticated account reads without creating an order. A quiet account cannot prove order/fill delivery or subscription acceptance through an actual event; preserve that limitation explicitly.

This iteration does not require live credentials or live network access for acceptance.

## Explicit Non-Goals

- Trading controller or live execution activation.
- Continuous runtime ownership beyond the bounded preflight.
- REST recovery after a runtime reconnect.
- Order registration, placement capture, cancellation or reconciliation integration.
- FillWindow, NORMAL/REBALANCE transitions or STOP policy implementation.
- New strategy filters, states or timing rules.
- Adoption of existing selected-market inventory/orders.
- Changes to legacy FL transport defaults, processor economics or execution gates.
- New dependencies, SDK migration or external interfaces.

## Risks

- Application `PONG` proves responsiveness, not explicit subscription acceptance.
- A healthy transport does not establish authoritative order/fill state.
- Shared transport edits could affect FL unless defaults and task lifetimes are explicitly tested.
- Background heartbeat failures could be swallowed by reconnect behavior; RDP observation must retain failure evidence and reject the attempt.
- Cleanup must not strand WebSocket tasks or bypass gateway executor draining.
- Returned evidence becomes historical immediately after resources close.

## Deferred Findings

- A long-lived RDP runtime must own public/user streams and authenticated reads together.
- Reconnect recovery requires authoritative order/trade reconciliation before new risk.
- Existing `UserEventProcessor` applies MATCHED fills optimistically; RDP inventory and FillWindow integration must use confirmed-fill authority.
- Selected-position reads still use the existing float representation.
- Controller, lifecycle composition, continuous capital reliability and full required metrics remain outstanding.

## Human Approval Required

NO.

This contract adds read-only transport evidence through existing approved infrastructure. It changes no Frozen strategy semantics, trading authorization or external runtime interface.

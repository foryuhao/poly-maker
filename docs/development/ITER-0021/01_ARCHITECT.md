# ITER-0021 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Canonical root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `dad76b7c2f352dd90020961de814973a326e2a8c`, matching the local remote-tracking reference.
- Latest archived PASS: ITER-0020, `414c9490fb9786151ed53b352640ff43756feabf`.
- Reviewed prior evidence: ITER-0020 `RESULT.md`, `05_REVIEWER_REREVIEW_01.md` and `06_ARCHIVE_RECEIPT.md`.
- Working tree contains only preserved untracked `research_outputs/`.
- MICRO_LIVE_READY: NO.
- Real-money authorization: NOT_GRANTED.

Shared-Core Change: **NO**.

FL Behavior Impact: **NONE**.

IM Dependency: **NONE**.

## Problem

Initial NORMAL admission has fresh account and public-feed evidence but relies on startup market-status validation. It explicitly leaves `CURRENT_MARKET_TRADABILITY` unresolved.

Implement one prerequisite: a fresh exact-market Gamma status read composed into the existing initial NORMAL admission.

## Evidence / Current Behavior

- Frozen SOP §19 treats a market that cannot normally trade as invalidating a pre-fill quote; §26 requires the market to continue accepting legal Maker orders.
- `bootstrap_rdp_market()` validates `acceptingOrders is True` and `closed is False` through the official Gamma path, but only during startup.
- `GammaClient.market_by_slug()` already implements exact-slug discovery.
- RDP’s existing `_validate_market()` provides strict status, BTC5m identity, outcome mapping and interval validation.
- Existing application metadata refresh also uses Gamma order-acceptance and closed status.
- `initial_normal_admission()` refreshes account state and synchronously captures current books, filter, candidates and CapitalGate. It does not refresh market status.
- The retained runtime already provides read ownership and teardown patterns.

## Existing Components Reused

- Official `GammaClient` and its HTTP client/context cleanup.
- Existing RDP official-origin and Gamma validation helpers.
- Immutable retained bootstrap identity.
- Initial NORMAL admission, account backing and final synchronous capture.
- Existing input-generation, expiry and admission-serialization checks.
- Existing read-task draining, logging and HTTP mock infrastructure.

## Existing Components Modified

- `src/polymaker/rdp_live.py`: small RDP-only current-status helper and immutable evidence type.
- `src/polymaker/rdp_runtime.py`: captured read configuration, task ownership and admission composition.
- Relevant RDP live/runtime/placement tests.

Do not modify shared Gamma client behavior, gateway, processor, strategy formulas or existing close coordinators.

## New Components Required

One immutable RDP market-status evidence type and one fresh-read helper in the existing RDP modules.

No new client, periodic refresh subsystem or independent controller.

## Desired Behavior

Every initial NORMAL admission obtains a fresh official Gamma response for the retained exact market.

Positive evidence requires the existing explicit open-for-orders booleans and agreement with the retained market’s slug, condition, ordered outcome tokens, neg-risk identity and BTC5m end time.

The final admission capture requires that this evidence remains fresh while account, feed and generation checks also pass. A successful capture includes the status evidence and no longer lists `CURRENT_MARKET_TRADABILITY` as unresolved.

This satisfies current market-status validation for that capture. Final submission must still revalidate execution conditions; eligibility grants no send authority.

## Minimal Scope

1. Capture and validate the official Gamma origin from the copied runtime configuration before startup awaits. Later caller configuration mutation must not redirect admission reads.
2. Use `GammaClient.market_by_slug()` and existing RDP validation for one fresh lookup per admission.
3. Bind the response to the retained immutable market identity; never replace the selected market from a refresh.
4. Record the actual response receipt time and validated status in immutable evidence.
5. Use the captured existing `risk.ws_stale_halt_s` as the market-status maximum age. Require a finite positive limit and `0 <= age < limit`; equality is stale.
6. Compose status and account reads within existing admission serialization. Check session health, generation and market expiry after each await.
7. Recheck both status freshness and existing account freshness in the final synchronous decision.
8. Own and finish the HTTP read/client cleanup through cancellation and teardown.
9. Include evidence and categorical rejection reasons in the admission result.

Read ordering and internal names are ordinary implementation choices. No additional CLOB read is required by this contract.

## Invariants

- Startup success or a previous admission cannot certify a later status read.
- `acceptingOrders` and `closed` require strict booleans; truthy strings or integers are insufficient.
- A status response cannot select another market, reverse outcome mapping or change signing identity.
- Gamma tick/minimum values cannot replace dynamic WebSocket ticks or validated CLOB token minima used by existing quote construction.
- Missing, malformed, failed, future-dated, expired or stale status evidence rejects admission.
- No await separates final freshness/health checks from the completed admission capture.
- Selected pre-existing account state remains rejected without adoption or cancellation.
- Frozen quantities, accounting, $30 capital and the approved $5 NORMAL reserve remain unchanged.
- Execution authorization remains fixed false.
- Remaining reconciliation, operational-time, final-submit and human-authorization gates remain unresolved.
- This read never activates placement or either close coordinator.

## Acceptance Criteria

1. A healthy empty session with a fresh matching explicitly tradable response and existing valid account/books produces the same Frozen five-share pair and CapitalGate result.
2. Every admission performs a fresh exact-slug Gamma read through the captured approved origin.
3. False, missing or non-boolean order-acceptance/closed status rejects admission.
4. Changed slug, condition, outcome tokens/order, neg-risk identity or end interval rejects without replacing bootstrap identity.
5. HTTP failure, missing market or malformed envelope rejects without using cached startup evidence.
6. Status receipt age below the captured limit passes; exact-limit age, greater age, future or invalid timestamps reject.
7. Slow status/account reads cannot produce eligibility if either evidence set is stale at final capture.
8. Public/user failure, generation change, market expiry or session closure during either read prevents admission.
9. Mutation of the original configuration after startup begins cannot change the read endpoint or age limit. Unapproved origins and redirects cannot cause a request to an alternate runtime service.
10. Overlapping admissions retain existing exclusion. Cancellation and teardown retrieve task failures and complete HTTP client cleanup before releasing ownership.
11. Successful evidence appears in the admission and clears only the current-market-status gate. Failed admission retains categorical reasons and grants no authority.
12. No order-create/cancel call, inventory adoption or coordinator activation occurs. Existing preflight and observe-only behavior remains compatible.
13. Targeted, affected integration, predecessor regression and practical full-suite checks pass.

## Required Tests

Use fake Gamma HTTP responses and existing fake account/feed infrastructure; block and count real DNS/socket access.

- Matching positive response and unchanged quote/capital behavior.
- Strict boolean status matrix, including missing values, strings and integers.
- Wrong identity, reversed tokens/outcomes, changed neg-risk and end time.
- Missing market, malformed response/envelope and HTTP errors.
- Freshness immediately below, at and above the limit; future/invalid receipts.
- Account read making earlier status stale, and status read making earlier account evidence stale.
- Both input generations, input failure, market expiry and closure during delayed reads.
- Configuration mutation, unapproved origin and redirect handling.
- Concurrent admission, first/repeated cancellation and teardown with an active HTTP read; no detached task or unhandled exception.
- Real admission validation over fake transport responses, rather than a supplied truthy certification flag.
- Zero create/cancel/coordinator calls and existing read-only CLI regressions.

Run narrow status tests, affected RDP live/runtime/placement/account/capital tests, FL regressions and the full suite when practical. Preserve exact commands and outcomes.

## Observability Requirements

Expose status identity, strict order-acceptance/closed evidence, receipt time, evaluated age/limit and rejection category through the admission and existing structured logging.

No raw HTTP payload logging, additional metrics system or periodic polling logs.

## Live Validation

No live calls are required or permitted during this iteration.

After complete delivery, safe read-only validation can compare the exact selected market’s current official Gamma status with admission evidence. Real order acceptance remains part of explicitly authorized human-supervised validation.

## Explicit Non-Goals

- Production submission or final submit-guard implementation.
- Pre-fill controller or periodic market-status polling.
- Subsequent NORMAL or REBALANCE operation.
- Unknown-placement recovery.
- New account reconciliation, capital reservations or accounting.
- CLOB metadata refresh or replacement of dynamic tick authority.
- New strategy filters or additional status flags.
- CLI execution activation or external-interface changes.

## Risks

Status can change immediately after a read. Evidence is valid for its capture and freshness checks; it is not permanent authority to send.

Read sequencing can age earlier evidence. Final joint validation must reject stale combinations.

HTTP failure and cancellation must not leave an owned client/task active after teardown.

## Deferred Findings

Final submission revalidation, pre-fill control, continuous account/order reliability, subsequent NORMAL/REBALANCE execution, unknown recovery and complete metrics remain required.

This iteration does not establish MICRO_LIVE_READY.

## Human Approval Required

**NO.**

The change reuses the existing official status path and approved strict startup semantics inside an existing admission decision. It adds no strategy decision or external runtime interface.

Real-money authorization remains NOT_GRANTED.

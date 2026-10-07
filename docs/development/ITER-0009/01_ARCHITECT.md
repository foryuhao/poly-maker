# ITER-0009 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `2ebe01a1890db35f4a630e7eafeae2c7d56248f2`.
- Latest archived PASS: ITER-0008 at `5cc18affc68528ce364f299c016a1a845dd457c4`.
- Inspected AGENTS.md, Frozen SOP, baseline, index, ITER-0008 result/final review/archive receipt, gateway, BTC5m selection, Gamma parser, CLI and relevant tests.
- Working tree contains only preserved untracked `research_outputs/`.
- Prior evidence: execution tests `112 passed`; full suite before the final tests-only repair `953 passed, 2 skipped`.

## Problem

RDP has reviewed strategy prerequisites and gateway extensions but no CLI/runtime entrypoint that selects one BTC5m market and obtains validated official metadata.

Establish only the read-only single-market bootstrap that later runtime work can extend.

## Evidence / Current Behavior

- The existing CLI exposes FavoriteFirst and BTC5m paper commands, but no RDP command.
- `GammaClient.market_by_slug()` already performs exact official discovery.
- `parse_market()` provides existing domain mapping but defaults missing tick/minimum-size fields.
- `resolve_btc5m_market()` returns parsed metadata and discards raw tradability evidence.
- Existing gateway public-book helpers discard tick and minimum-size metadata.
- FavoriteFirst preflight can cancel orders and imports predecessor account/strategy assumptions. It cannot serve as RDP’s read-only bootstrap unchanged.

## Existing Components Reused

- Existing Typer application and CLI conventions.
- `Config`, official endpoint configuration and existing logging.
- `slug_for_epoch()` and `PERIOD_S`.
- `GammaClient.market_by_slug()` and `parse_market()`.
- Existing `MarketMeta`/`TokenMeta` domain types.
- Existing `httpx` dependency for public CLOB GETs.
- Existing BTC5m/catalog test patterns and development HTTP mocking tools.

## Existing Components Modified

- `src/polymaker/cli.py`: register the bounded RDP command.

**Shared-Core Change: YES.** Authorized change is CLI registration only; no existing command behavior changes.

**FL Behavior Impact:** NONE.

**IM Dependency:** NONE.

## New Components Required

- A small `src/polymaker/rdp_live.py` bootstrap module, or equivalently named RDP runtime module inside the existing application.
- An immutable bootstrap result containing selected market identity, interval and separately validated per-token metadata.
- Focused bootstrap/CLI tests.

The public metadata reader belongs in this small module because existing public-book helpers omit required fields. Do not create a second book service or execution gateway.

## Desired Behavior

Provide this actual command:

```bash
uv run polymaker rdp-live --market <btc-updown-5m-epoch> --preflight-only
```

Optional existing-style `--config-dir` is allowed.

For this iteration, `--preflight-only` is required. Missing it fails before network access. Expose no trading mode or authorization switch yet.

The bootstrap:

1. Validates a canonical `btc-updown-5m-<epoch>` slug whose positive integer epoch is aligned to the existing 300-second period.
2. Commits to that exact slug; no rollover, scanning or alternate-market fallback.
3. Uses configured official Gamma/CLOB HTTPS endpoints. Reject an unapproved runtime origin before network access.
4. Obtains the exact raw Gamma market through the existing client.
5. Validates exact slug, nonempty condition ID, two distinct outcome token IDs, expected BTC5m Up/Down mapping, explicit accepting-orders state and non-closed state.
6. Validates a timezone-aware end timestamp matching `epoch + PERIOD_S`. Reject an already ended market; recheck this after metadata reads.
7. Reuses `parse_market()` for the existing domain representation.
8. Fetches `/book?token_id=<token>` for each selected outcome using bounded public HTTP GETs.
9. Validates each response mapping’s condition/token identity, explicit finite positive `tick_size`, explicit finite positive `min_order_size`, and boolean `neg_risk` consistent with the selected market. Reject missing fields, booleans masquerading as numbers and contradictory identity.
10. Retains ticks and minimum sizes separately by token. Gamma defaults do not certify these fields.
11. Reports success or an explicit bootstrap failure, closes HTTP resources and exits.

Public numeric strings are valid inputs when they represent finite numbers. Ticks must be inside `(0,1)`.

Bootstrap success certifies selection and public metadata only. It does not certify account state, WS readiness, CapitalGate, quote eligibility or permission to trade.

Do not impose FavoriteFirst’s five-share startup cap. Record venue minimum sizes unchanged; later RDP candidate validation determines whether frozen quantities are legal.

## Minimal Scope

One public, read-only selection/metadata bootstrap exposed through the existing CLI.

Return validated context for later composition. Do not construct an Engine, connect an execution gateway, derive API credentials, start user/account services or submit/cancel orders.

Use explicit startup error categories rather than pretending a bootstrap failure is a completed strategy STOP transition.

## Invariants

- Exactly one explicitly named BTC5m market.
- Only official public Gamma/CLOB HTTP requests.
- No POST, DELETE, blockchain RPC or authenticated account request.
- No strategy price, size, gate or state changes.
- No fallback/default certifies missing venue metadata.
- Token ticks and minimum sizes remain independent.
- Public preflight success does not arm trading.
- Existing FL/IM commands and shared data remain preserved.

## Acceptance Criteria

1. CLI help includes `rdp-live` and accurately states its read-only scope.
2. Missing preflight mode, malformed slug or unapproved origins fail before requests.
3. A valid fixture performs one exact Gamma lookup and one public CLOB book GET per outcome, then returns the validated context.
4. Different token ticks and minimum sizes remain distinct in output.
5. Gamma tick/minimum defaults cannot substitute for missing CLOB metadata.
6. Wrong market/token, duplicate tokens, invalid outcome mapping, unusable tradability flags, wrong/naive end timestamp and elapsed interval fail explicitly.
7. HTTP errors, timeouts, malformed responses and invalid numeric metadata fail closed.
8. End-time expiry during reads fails rather than selecting another market.
9. Success/failure produces durable structured bootstrap evidence and a corresponding CLI exit code.
10. No execution, credential derivation, cancellation, RPC or legacy Engine startup occurs on any tested path.
11. Existing catalog, BTC5m and predecessor CLI/runtime tests remain passing.

## Required Tests

Use HTTP mocks and CLI fixtures:

- Valid single-market discovery and both metadata reads.
- Separate ticks/minimum sizes, including a venue minimum above five without alteration.
- Missing, false, malformed or contradictory raw metadata.
- Identity and outcome-order mismatch.
- Canonical slug validation and no alternate selection.
- Timezone/end-boundary validation and expiry during reads.
- Network error/timeout and malformed JSON/envelope cases.
- Missing `--preflight-only` and unapproved endpoint origins.
- Exact GET-only request inspection.
- Forbidden Engine/gateway/auth/cancel paths patched to raise if invoked.
- CLI exit codes and durable success/failure evidence.

Run focused tests, affected catalog/BTC5m/CLI regressions, then the full suite when practical. Safely exercise:

```bash
uv run polymaker rdp-live --help
```

Exercise the complete command through mocked CLI tests. Record commands/results and inspect status, untracked files, diff stat and `git diff --check`.

## Observability Requirements

Reuse existing structured logging and append to an RDP-specific log under the configured log directory.

Record:

- bootstrap result or failure category;
- selected slug, condition ID and interval;
- outcome token IDs;
- validated per-token ticks/minimum sizes;
- receipt timestamp;
- `real_money_trading_performed: false`.

Do not log credentials or claim trading readiness.

## Live Validation

A public GET-only invocation against one currently available exact BTC5m market may validate discovery/metadata without human trading authorization. It must perform no account mutation.

If performed, preserve its command, result and log location. Network unavailability is recorded honestly and does not become invented success. Final project readiness still requires independent verification of the integrated safe command.

## Explicit Non-Goals

- WebSocket startup or provenance tracking.
- Account, allowance, cash or position reads.
- Order creation/cancellation/reconciliation.
- NORMAL/REBALANCE orchestration or timers.
- Quote construction or trading eligibility.
- SDK cache refresh.
- Per-token minimum-size changes to builders/gateway.
- Additional CLI modes, rollover or market scanning.
- Real-money trading.

## Risks

Public metadata can change after bootstrap. The eventual runtime must refresh it and enforce current-state guards.

A valid public bootstrap does not demonstrate live execution or integrated RDP readiness.

## Deferred Findings

- Market WS must later maintain certified current ticks and generation readiness.
- Per-token venue minima must later reach quote/execution validation without altering frozen sizes.
- Authenticated account readiness, lifecycle orchestration and metrics remain unintegrated.

## Human Approval Required

NO.

This establishes an existing-application, official-interface, read-only entrypoint. It introduces no strategy decision or trading authority.

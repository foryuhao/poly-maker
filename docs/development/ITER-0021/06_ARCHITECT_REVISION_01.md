# ITER-0021 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

Revision 01 supersedes `01_ARCHITECT.md` only where explicitly expanded below. The problem remains fresh exact-market Gamma status within initial NORMAL admission.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- Current HEAD: `9e250350188abb34c69a566e142ea8e4de178fed`.
- Original planning HEAD: `dad76b7c2f352dd90020961de814973a326e2a8c`.
- Latest archived PASS: ITER-0020, `414c9490fb9786151ed53b352640ff43756feabf`.
- Inspected immutable ITER-0021 artifacts: `01_ARCHITECT.md`, `03_REVIEWER.md`, `04_WORKER_FIX_01.md` and `05_REVIEWER_REREVIEW_01.md`.
- Five implementation/test files remain modified; Orchestrator audit/index work and preserved `research_outputs/` are present.
- Latest review: RETURN_TO_ARCHITECT.
- MICRO_LIVE_READY: NO.
- Real-money authorization: NOT_GRANTED.

Shared-Core Change: **YES**, narrowly authorized default-off Gamma diagnostics.

FL Behavior Impact: **NONE intended**; default lookup, parsing, HTTP behavior and diagnostics remain compatible.

IM Dependency: **NONE**.

## Problem

Complete the original market-status prerequisite by fixing inaccurate rejection-time evidence and untrusted HTTP failure diagnostics.

These are defects in the same admission/status path, not additional strategy functionality.

## Evidence / Current Behavior

- MF-01’s exact Decimal freshness boundary is fixed and must remain intact.
- After a status read at `100s`, a failed account read at `111s` with a `10s` limit still returns recorded status age `0s` and clears `CURRENT_MARKET_TRADABILITY`.
- The rejected admission is ineligible, but its readiness evidence is inaccurate.
- Existing `GammaClient.market_by_slug()` logs `str(HTTPStatusError)`. An actual fake HTTP503 response with a sentinel reason phrase exposes that phrase before RDP handles the failure.
- Installed HTTPX also logs `response.reason_phrase` at INFO before response hooks execute. Configured logging tests must inspect this upstream output.
- The existing RDP numeric guard delegates to the shared validator and preserves accepted parser conversions. It need not be redesigned.

## Existing Components Reused

- Existing exact-slug Gamma lookup, HTTPX client and context cleanup.
- Existing shared parser and RDP status/identity validation.
- Exact Decimal status-freshness evaluator.
- Existing admission rejection factory, read ownership and final capture.
- Existing default/configured logging and fake HTTP tests.

## Existing Components Modified

- `src/polymaker/catalog/gamma.py`: optional safe diagnostics on the existing exact-slug path.
- `src/polymaker/rdp_live.py`: explicitly enable that option for current RDP status reads.
- `src/polymaker/rdp_runtime.py`: terminal rejection-evidence recapture.
- Existing RDP tests and narrowly relevant Gamma compatibility tests.

The shared extension may affect diagnostic production only. Other discovery methods, request/response semantics and legacy callers remain unchanged.

## New Components Required

No new client or subsystem.

If necessary to prevent HTTPX’s own reason-phrase emission, authorize one tiny client-local diagnostic transport adapter around the existing transport. It must preserve existing transport ownership and HTTP behavior.

## Desired Behavior

Every terminal rejected admission reports market-status evidence evaluated at that rejection’s capture time. Its primary failure reason remains unchanged.

Only previously validated status that remains valid and fresh at capture may clear the status gate.

RDP status reads emit categorical diagnostics without untrusted response content. Legacy Gamma callers retain their default diagnostics and behavior.

## Minimal Scope

1. Reuse the exact freshness evaluator when constructing terminal rejected admissions after successful or failed awaits and precondition failures.
2. Record current age/limit and retain the unresolved status gate when evidence is stale, future, invalid, unavailable or outside the selected market interval.
3. Preserve the original account/input/precondition rejection category; status recapture must not replace it.
4. Add a default-off safe-diagnostics option to the existing Gamma exact-slug path, preferably per client where required by HTTPX diagnostics.
5. Enable it only for `read_rdp_market_status()`.
6. In the opted-in path, emit categories and exception types instead of exception strings, raw response fields or payloads.
7. Cover HTTPX’s upstream reason-phrase emission with a local adaptation if needed. Diagnostic response metadata may be sanitized; status code, body, headers, identity, redirect policy and lookup outcome must remain unchanged.
8. Retain the current numeric guard where necessary. Do not add another copy of parser conversions. A default-off parser diagnostic extension is permitted only if needed for this same status path.

No global logger replacement, level/filter mutation, HTTPX monkeypatch, copied request pipeline or alternate client is authorized.

## Invariants

- Exact Decimal comparison remains `0 <= age < limit`; no tolerance or rounding.
- Readiness evidence is current at its terminal capture.
- Fresh status can clear its own gate when an independent prerequisite rejects.
- A prior verification boolean cannot certify evidence after a later await.
- Primary rejection reasons remain preserved.
- Shared defaults retain FL behavior.
- Diagnostic adaptation cannot alter request routing, proxy behavior, redirects, response-body parsing or cleanup.
- Existing identity/status validation, dynamic ticks, CLOB minima, accounting and capital policy remain unchanged.
- Execution authorization remains fixed false.
- No order creation, cancellation, account adoption or coordinator activation.

## Acceptance Criteria

All unaffected original acceptance criteria remain required, plus:

1. Status at `100s`, limit `10s`, and failed positions read at `111s` returns an ineligible admission with the original account-read reason, recorded age `11s`, unresolved status gate and authorization false.
2. Both categorized account errors and unexpected read exceptions receive terminal recapture.
3. Post-await precondition failures also capture current evidence. Invalid/future timestamps and market termination cannot clear the gate.
4. Fresh status with an independent rejection still clears only its own gate.
5. Producer-generated fractional and realistic-epoch exact-limit tests continue passing.
6. An actual fake HTTP503 sentinel reason phrase appears nowhere in opted-in default or configured logging output, including HTTPX diagnostics and durable output.
7. Existing malformed numeric sentinel tests continue passing.
8. Default Gamma callers preserve lookup results, failure return behavior, parser behavior and legacy diagnostics.
9. Safe diagnostics preserve exact requests, redirects, status/body semantics and client cleanup, including failure/cancellation.
10. No global logging mutation or duplicate client/parser implementation is introduced.
11. Required targeted, integration and FL regressions pass; practical full-suite verification passes with zero real network actions.

## Required Tests

Block/count DNS and socket access.

- Delayed categorized and unexpected account failures with stale terminal status.
- Delayed post-await precondition failure, future/invalid evaluation time and market expiry.
- Fresh-status/other-prerequisite rejection.
- Existing exact fractional-boundary producer tests.
- Actual fake HTTP503 with sentinel `reason_phrase` through the existing Gamma/HTTPX path under default and configured logging.
- Inspect emitted structured/stdout/stderr records and configured durable logs, including HTTPX INFO output.
- Legacy default diagnostic/lookup compatibility and successful safe lookup.
- Existing malformed numeric payload tests; parser compatibility if its diagnostics are extended.
- HTTP client cleanup, redirect restrictions, cancellation and retained read ownership.
- Existing read-only/no-action tests.

Run narrow repairs, affected RDP/Gamma tests, FL regressions and the full suite when practical. Preserve commands and outcomes.

## Observability Requirements

Rejected admissions expose current status age/limit while retaining their primary reason.

Opted-in Gamma diagnostics expose categorical failure and exception type without raw HTTP payloads, reason phrases or exception text. No separate logging subsystem is required.

## Live Validation

No live calls or trading actions are permitted.

After complete delivery, official read-only observations may validate market-status evidence. These repairs require fake transport verification, not live execution.

## Explicit Non-Goals

- Submission, final guards or controller expansion.
- New status flags, filters or strategy semantics.
- Periodic refresh or subsequent NORMAL/REBALANCE operation.
- Accounting, reservations or unknown recovery.
- Global logging policy changes.
- Changes to unrelated Gamma methods or FL runtime behavior.
- New interfaces or dependencies.

## Risks

Sanitizing only the application warning misses HTTPX’s earlier diagnostic. Tests must exercise actual transport output.

Terminal recapture must not accidentally overwrite the primary failure or certify status that never passed identity validation.

Any local diagnostic adapter must preserve existing HTTP ownership and routing behavior.

## Deferred Findings

Final submission, pre-fill control, continuous reliability, subsequent strategy operation, unknown recovery and complete metrics remain outstanding.

No additional strategy or architecture blocker was identified.

## Human Approval Required

**NO.**

This revision authorizes a bounded shared diagnostic extension and corrects evidence within the existing approved prerequisite. It changes neither Frozen semantics nor runtime interfaces.

Real-money authorization remains NOT_GRANTED.

# ITER-0016 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `178870e66476c33add8dbaee5b3f26153355fa64`
- HEAD and remote match.
- ITER-0015: CODE_READY, archived at `715521f7752cdca2e3a25075928778f417164c8e`; final Reviewer05 PASS.
- Current iteration: ITER-0016, ARCHITECT.
- Only preserved `research_outputs/` is untracked.
- MICRO_LIVE_READY: NO.
- Real-money authorization: NOT_GRANTED.

**Shared-Core Change: NO.**

**FL Behavior Impact:** NONE.

**IM Dependency:** NONE.

## Problem

The retained input session now supplies live Book Filter evidence, but no runtime method composes that evidence, current account backing, confirmed inventory, NORMAL candidates and CapitalGate.

Implement one prerequisite: initial NORMAL admission for an empty RDP run, using the retained session and existing components.

This contract covers the first decision before any RDP order exists. It does not implement subsequent cycles or execution.

## Evidence / Current Behavior

- Frozen sections14–17 define NORMAL inventory, PriceGate, Book Filter, quote prices and sizes.
- `build_normal_quotes` implements PriceGate and candidate construction.
- `project_rdp_capital` and `evaluate_rdp_capital_gate` implement the fixed-$30 capital model and approved $5 NORMAL reserve.
- `RDPInputSession.book_filter_result` evaluates the production-wired filter using retained input health.
- `session.account` is a startup snapshot; it can become stale while observation continues.
- `_capture_account_with_gateway` already obtains strict, exact account evidence through an existing gateway.
- `evaluate_initial_rdp_backing` already verifies fresh empty selected-market state and independent cash/allowance backing.
- The builder currently uses Gamma’s shared `market.min_order_size`. Bootstrap separately holds authoritative CLOB minimum sizes for both tokens.
- No runtime method currently combines these components.

## Existing Components Reused

- Retained input session, gateway, processor and books.
- Existing account capture and identity validation.
- Initial backing evaluator and exact account-order evidence.
- Confirmed-fill inventory projection through `project_rdp_capital`.
- Production-wired Book Filter.
- NORMAL builder and CapitalGate.
- Existing cleanup and sanitized error conventions.

## Existing Components Modified

- `src/polymaker/rdp_runtime.py`: one asynchronous initial-admission method, captured freshness configuration and ownership of its active read operation.
- Relevant runtime integration tests.

A small RDP-local result module is permitted if needed for readability. No shared implementation change is authorized.

## New Components Required

One immutable initial-admission result containing:

- Eligible/rejected status and reason.
- Initial NORMAL candidate when available.
- Existing backing/filter/capital evidence.
- Public/user generations and account receipt times.
- Token tick/minimum-size metadata used.
- Explicit outstanding execution gates.
- Execution authorization: false.

No new capital ledger, account client, inventory state or controller.

## Desired Behavior

A caller can request initial NORMAL admission from the retained session.

The method:

1. Verifies the session is open, unexpired and healthy.
2. Requires genuinely empty initial local scope: no registered owned orders, pending placement or admitted fill evidence.
3. Re-reads account state through the same connected gateway.
4. Requires fresh existing initial-$30 backing and empty selected account state.
5. Rechecks owner health after those asynchronous reads.
6. Captures current books, ticks and Book Filter evidence without intervening awaits.
7. Projects capital from the same processor, empty run scope and authoritatively empty selected-order snapshot.
8. Builds the unchanged five-share YES/NO NORMAL pair.
9. Evaluates existing CapitalGate.
10. Returns immutable evidence, without placing or reserving an order.

Eligibility means the composed initial NORMAL predicates passed at capture time. It does not authorize execution.

## Minimal Scope

- Add one session method; ordinary naming and result layout are implementation choices.
- Capture the existing `risk.user_ws_blind_halt_s` freshness bound from the frozen runtime configuration.
- Reuse `_capture_account_with_gateway` with the retained gateway and previously validated signer/funder.
- Reuse `evaluate_initial_rdp_backing`; never substitute the historical startup certificate.
- Reuse `project_rdp_capital` with empty scope only after local and fresh account emptiness are established.
- Validate both unchanged five-share sizes against their respective CLOB minima. For this zero-inventory pair, a derived market copy using the larger of the two minima is equivalent and permits reuse of the unchanged builder. Preserve original market identity; do not mutate bootstrap metadata.
- Re-read both ticks from current authoritative books.
- Return rejection/unready evidence for warmup, predicate failure, invalid metadata, unreliable inputs or nonempty initial scope.
- Serialize or reject overlapping evaluations.
- Track and drain the active read operation during session closure/cancellation.
- Add no CLI mode, polling controller or periodic account refresh loop.

## Invariants

- Initial inventory is derived from the existing processor, never adopted from account positions.
- Only confirmed fills affect inventory; this initial scope must contain none.
- Selected pre-existing orders or inventory reject admission without cancellation or adoption.
- All account BUY commitments remain included in backing exactly once.
- Capital remains $30; NORMAL retains the approved $5 reserve.
- Quotes remain five shares per side at current BestBid minus that token’s tick.
- Minimum-size validation rejects an illegal Frozen clip; it never increases size.
- Gamma’s shared minimum cannot override validated CLOB token minima for this decision.
- No await separates final health checks, book/filter capture, candidate construction and gate evaluation.
- Known unprocessed public input backlog cannot certify a current decision. Existing received/processed counters may establish that the retained service has caught up; this is evidence validity, not another strategy filter.
- Quiet connected input is not stale merely because no mutation occurred.
- No signing, placement, cancellation, reservation mutation or execution authority.
- Subsequent-cycle reconciliation, current market tradability, operational time sufficiency and deliberate live authorization remain required before execution.

## Acceptance Criteria

1. A healthy empty session with fresh backing and active valid books produces the exact Frozen five-share pair and existing capital admission.
2. The account read uses the retained gateway; no new connection or credential derivation occurs.
3. Historical startup backing cannot certify the decision after current account evidence changes.
4. Foreign BUY commitments, cash/allowance shortfalls and selected existing state reproduce existing backing rejection.
5. Nonempty local ownership, pending placement or fill evidence rejects the initial-only method.
6. PriceGate, filter warmup, Inactive, Depth Extreme and Fast Move reject through existing components.
7. Unequal current ticks are used independently.
8. Either CLOB minimum above five rejects without resizing; CLOB minima of five permit the pair despite a conflicting Gamma minimum.
9. Health loss, generation change, expiry or closure during account reads prevents eligibility.
10. Unprocessed public input prevents capture; healthy quiet input remains evaluable.
11. Capital evidence satisfies `FreeCash + ReservedCash + InventoryCost = $30`; the existing NORMAL reserve is preserved.
12. Returned evidence identifies its scope and outstanding execution gates. No result grants send authority.
13. Read failures and cancellation are sanitized; active reads are drained before resource closure.
14. Existing preflight, observe-only and predecessor behavior remains unchanged.
15. Required tests pass with zero attempted live network operations.

## Required Tests

Use real retained-session/account/filter/builder/gate composition with fake external transports and controlled clocks.

- Successful fresh empty-run admission and exact quote/capital evidence.
- Retained gateway identity and single-connection assertions.
- Startup snapshot remains historical; refreshed account changes control admission.
- Actual strict/exact open-order producer with fractional foreign BUY commitments.
- Independent cash and allowance backing failures.
- Selected account inventory/order rejection without mutation.
- Local owned-order, pending-placement and MATCHED/CONFIRMED evidence rejection.
- Production-wired filter warmup and predicate rejection cases.
- PriceGate equality and failure.
- Unequal ticks, tick change during account reads and CLOB/Gamma minimum disagreement.
- Public/user failure, generation change and market expiry during reads.
- Public backlog rejection and quiet valid input.
- No persistent reservation or processor/store mutation.
- Overlapping evaluations, cancellation and closure during reads, including repeated cancellation and gateway executor draining.
- Credential sentinels absent from errors/results/logs.
- Forbidden execution methods fail immediately if invoked.

Run targeted, affected runtime/account/filter/capital/quote regressions, predecessor regressions and the full suite when practical. Block socket connections and DNS; record attempted-network count.

## Observability Requirements

The immutable result exposes the existing component evidence and rejection reason. It must distinguish:

- Initial NORMAL eligibility.
- Evidence unready/rejected.
- Execution authorization, always false.

No additional periodic logging, counters or telemetry subsystem is required.

## Live Validation

Controlled fixtures must exercise the actual session method through current account capture and production-wired market inputs.

A later safe read-only invocation can inspect this initial decision without submitting an order. This iteration requires no live calls or credentials and proves no RDP economics.

## Explicit Non-Goals

- Order submission or live authorization.
- Later NORMAL cycles or nonempty inventory admission.
- REBALANCE admission or execution.
- FillWindow and quote-cycle controller.
- Order registration, reconciliation or close-barrier composition.
- Periodic account polling or reconnect recovery.
- Current-market tradability/time-to-close execution certification.
- Complete metrics or additional CLI modes.
- MICRO_LIVE_READY declaration.

## Risks

- An eligible decision can become obsolete immediately after return; future execution must revalidate.
- A retained gateway does not make old account evidence fresh.
- Generic market minimum metadata can disagree with actual token minima.
- Background reads must not outlive their owning session.
- Initial admission cannot be reused after a cycle begins.

## Deferred Findings

- Execution ownership, final submit guard and authoritative order lifecycle remain required.
- Subsequent NORMAL admission requires complete close evidence and confirmed nonempty accounting.
- REBALANCE and CanSafelyContinue remain uncomposed.
- Continuous account reliability and complete required metrics remain outstanding.

## Human Approval Required

NO.

This contract composes existing Frozen behavior for the initial empty run. It changes no strategy semantics, trading authorization or external interface.

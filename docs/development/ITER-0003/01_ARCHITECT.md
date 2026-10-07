# ITER-0003 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `69af7b4662c1425a362cb14233e6888b58af2345`.
- Local remote-tracking branch matches HEAD.
- Working tree contains only preserved untracked `research_outputs/`.
- Current stage: `ITER-0003` / `ARCHITECT`.
- ITER-0001 confirmed accounting and ITER-0002 NORMAL candidates are `CODE_READY`.
- ITER-0002 final PASS: `05_REVIEWER_REREVIEW_01.md`; archive receipt: `06_ARCHIVE_RECEIPT.md`; archived implementation: `6222102`.
- Latest documented full suite: 719 passed, 2 skipped, no exclusions.
- `MICRO_LIVE_READY`: NO. Real-money authorization: NOT_GRANTED.

Read active repository instructions, Frozen SOP, baseline, index and relevant completed iteration evidence. Inspected actual accounting, quote builder/tests, token-specific market-data/tick support, gateway safeguards and existing reconciliation.

The index’s verified HEAD identifies the preceding implementation archive; current HEAD additionally records its remote receipt and iteration initialization.

## Problem

RDP lacks the Frozen REBALANCE quote calculation.

Implement one prerequisite: extend the existing RDP candidate module to build the single deficient-side maker quote required by Frozen SOP sections 23–24.

## Evidence / Current Behavior

- `RDPInventorySnapshot` provides authoritative confirmed quantities and Delta.
- `rdp_quotes.py` implements NORMAL candidate construction with reusable immutable result, market/inventory validation and Decimal price helpers.
- NORMAL intentionally rejects `abs(Delta) >= 5`.
- Existing per-token market-data books provide current BestAsk and dynamic TickSize.
- Existing gateway supports forced post-only placement, but no RDP execution integration exists.
- Generic and FavoriteFirst quote policies introduce behavior incompatible with Frozen REBALANCE.

Frozen REBALANCE requires:

```text
abs(Delta) >= 5

Delta > 0 → buy NO only
Delta < 0 → buy YES only

Base quantity = 5 shares
Price = deficient-side BestAsk - current TickSize
Post-only required
```

Its objective is fast inventory recovery; cost, loss or unsuccessful filling is not an independent rejection/STOP condition.

## Existing Components Reused

- `RDPInventorySnapshot`.
- Existing `MarketMeta`, `Quote`, `Side.BUY`, `BookView`.
- `RDPQuoteDecision` and suitable existing numerical/input validation helpers.
- Existing accounting, quote and dynamic market-data fixtures.

## Existing Components Modified

- `src/polymaker/strategy/rdp_quotes.py`: add the focused REBALANCE builder.
- `tests/test_rdp_quotes.py`: add REBALANCE cases and retain NORMAL regressions.

Local extraction of directly shared validation is permitted only where needed by the added builder, preserving existing NORMAL behavior.

### Shared-Core Change

NO. Only the RDP-specific module changes.

### FL Behavior Impact

NONE.

### IM Dependency

NONE.

## New Components Required

No new production module or subsystem.

Add one pure builder to the existing RDP candidate module. Do not create a second result type or duplicated mutable inventory/execution state.

## Desired Behavior

Add a synchronous pure interface using the existing market, inventory, YES/NO book and explicit per-token tick inputs. Worker chooses its name.

Validate market/inventory identity and inventory consistency using semantics compatible with the reviewed accounting producer.

If `abs(Delta) < 5`, return no candidate with an explicit non-REBALANCE reason. At either exact threshold, REBALANCE is eligible.

Select the deficient token from Delta and produce exactly one existing BUY `Quote`:

```text
Delta > 0:
    BUY NO, quantity 5, price NO BestAsk - TickN

Delta < 0:
    BUY YES, quantity 5, price YES BestAsk - TickY
```

Use only the selected token’s book and tick for quote construction. The surplus-side quote inputs are unnecessary to this calculation; overall market-state reliability remains an independent runtime gate.

Apply the existing style of finite input, legal book, binary price and precision validation to the selected side. Reject missing/invalid/crossed/locked selected books; invalid ticks; off-grid selected ask/result; a resulting price outside `(0,1)`; or loss of the required exact price through float conversion.

The selected price must remain strictly below its ask. It may improve the best bid or equal it. Do not constrain it to BestBid or require BestBid minus a tick.

Validate venue minimum metadata. If the Frozen five-share quantity is below it, return no candidate with an explicit legality reason. Do not increase or shrink the quantity, including when Delta is much greater than five.

Do not apply NORMAL PriceGate, Book Filter or quantity skew. Negative LockedPnL, poor matched cost and other economic outcomes must not prevent the candidate.

Reuse `RDPQuoteDecision` with `post_only_required=True`. Its REBALANCE `required_gates` must identify:

- `CanSafelyContinue` with time, capital, market tradability and reliable state;
- CapitalGate;
- existing-order cancellation/reconciliation before replacement;
- live authorization.

Do not list NORMAL Book Filter or PriceGate as REBALANCE prerequisites.

The result remains a candidate, with no execution permission, state transition or STOP decision.

## Minimal Scope

Only:

```text
src/polymaker/strategy/rdp_quotes.py
tests/test_rdp_quotes.py
```

No accounting, gateway, market-data, engine, CLI, configuration, dependency or dataset changes.

No implementation of the REBALANCE loop, timing, cash gates, lifecycle or metrics.

## Invariants

- REBALANCE candidate eligibility uses `abs(Delta) >= 5`.
- Both exact threshold signs are eligible.
- Only the deficient side receives a quote.
- Quote quantity is exactly five shares.
- Price is exactly selected BestAsk minus its current TickSize.
- BUY and post-only are mandatory.
- No NORMAL gate or economic stop filter enters this builder.
- Invalid/unrepresentable selected inputs fail closed.
- No prices are clamped, snapped to a different target or substituted.
- No authoritative state mutates.
- Existing NORMAL behavior and FL/IM remain unchanged.

## Acceptance Criteria

1. Delta `+5` produces one NO BUY; Delta `-5` produces one YES BUY.
2. Greater magnitudes retain one five-share quote.
3. Values strictly inside both boundaries produce no REBALANCE candidate.
4. Selected prices use the correct current per-token tick and exact BestAsk offset.
5. A valid price above BestBid remains eligible.
6. NORMAL PriceGate failure does not prevent a legal REBALANCE candidate.
7. Negative economics do not prevent a legal candidate.
8. Selected-book, tick, grid, minimum-size and representation defects reject explicitly without quantity/price substitution.
9. Market/inventory mismatch or malformed inventory rejects.
10. REBALANCE gates and post-only requirement are explicit; no send/STOP authority is added.
11. Repeated reads preserve inputs.
12. Existing NORMAL tests, affected regressions and full suite pass within the approved diff.

## Required Tests

Add concrete cases for:

- Delta `+5`, `-5`, `+12`, `-12`, and just-inside values.
- Fractional confirmed accounting yielding REBALANCE Delta.
- Differing token ticks: selection uses only the deficient token’s current tick.
- Synthetic existing market-data tick change updates the next selected quote.
- Example: deficient book bid `0.40`, ask `0.45`, tick `0.01` → quote `0.44`, five shares.
- Quote equal to BestBid and quote improving BestBid.
- BestBid sum greater than one while the selected quote is legal: candidate remains available.
- Negative LockedPnL/WorstSettlementPnL with otherwise identical valid inputs: candidate unchanged.
- Missing/nonfinite/locked/crossed selected book.
- Invalid tick, off-grid selected ask, subtraction yielding zero.
- Tiny-tick Decimal failure and float-erased offset: explicit rejection.
- Minimum five passes; minimum above five rejects without size adjustment.
- Invalid market/inventory identity and inconsistent Delta.
- Immutable input captures for accepted/rejected calls.
- Real registered confirmed-fill snapshot integration.
- Existing NORMAL behavior preserved.

Run:

```bash
uv run pytest -q tests/test_rdp_quotes.py
uv run pytest -q tests/test_rdp_accounting.py tests/test_quoting.py tests/test_orderbook.py tests/test_marketdata.py tests/test_favorite_first.py tests/test_favorite_first_review.py
uv run pytest -q
```

Preserve exact commands/results and failures. Inspect status, relevant untracked files, diff/stat and `git diff --check`. Preserve historical assets and immutable audit evidence.

## Observability Requirements

Reuse the explicit candidate rejection reason.

Expose selected token, exact price, five-share quantity, post-only requirement and outstanding REBALANCE gates.

Update module/API descriptions so the common result accurately covers both candidate modes. Do not add runtime logs or metrics in this iteration.

## Live Validation

No live reads, submissions or cancellations are required.

After integrated readiness and human authorization, compare one supervised REBALANCE attempt with its confirmed Delta, deficient token, current ask/tick and actual post-only order. Fill timing and economic attribution require the later integrated runtime.

## Explicit Non-Goals

- `CanSafelyContinue` evaluation or parameter selection.
- CapitalGate and reservation/cash accounting.
- REBALANCE loop, repricing cadence or recovery transitions.
- Cancellation/reconciliation integration.
- FillWindow, Book Filter and cycle state.
- STOP logic or any MaxRebalanceSec rule.
- Runtime/CLI, telemetry or live authorization changes.
- External interfaces or data changes.
- Readiness declaration.

## Risks

- The shared result currently defaults to NORMAL-specific gates; REBALANCE must override them explicitly.
- Reusing NORMAL logic wholesale could incorrectly apply PriceGate, dual quantities or bid-based prices.
- Tight spreads legitimately produce a selected price at BestBid; rejecting it would contradict Highest Non-Crossing Maker behavior.
- Numeric repairs already reviewed for NORMAL must remain intact.

## Deferred Findings

CapitalGate, repair headroom, Book Filter, lifecycle, runtime safety, observability, single-market CLI and final readiness remain outstanding.

Runtime integration must preserve pending-exposure/reconciliation gates and approved official-interface isolation. Candidate availability never proves that trading may safely continue.

## Human Approval Required

NONE.

This iteration implements explicit Frozen REBALANCE price/quantity semantics without introducing policy, execution authority or shared-core changes. Real-money trading remains unauthorized.

# MI M3 Implementation Report

Status: P1 PRICE-BOUNDARY REPAIR IMPLEMENTED — AWAITING INDEPENDENT RE-REVIEW
Commit / working tree base: `57a9e9ce2f260e4015fedfd632771fcc37fd6b8d`
Reviewed predecessor baseline: M2B-0A and M2B-0B CLOSED on the base commit
Architecture reference: `docs/architecture/MI_M3_ARCHITECTURE.md`

## Scope implemented

A separate `mi-micro-live` command and one-market MI micro-live orchestrator. Exact BTC5m selection, target cap, candidate validation, explicit arm, Generic Gate revalidation, active cash reservation, post-only gateway placement, official ID enforcement, bounded observation, cancel/reconcile/release and fail-closed retention are implemented.

## Files added

- `src/polymaker/mi_micro_live.py`
- `tests/test_mi_micro_live.py`
- `docs/architecture/MI_M3_ARCHITECTURE.md`
- `docs/reports/MI_M3_IMPLEMENTATION_REPORT.md`

## Files modified

- `src/polymaker/cli.py`: independent command.
- `src/polymaker/mi_cash_reservation.py`: additive locked `is_active` check against current process authority.
- `README.md` and `docs/CURRENT_STATUS.md`: operating scope and handoff.

## Important implementation decisions

The command requires both `--arm` and an exact phrase after the candidate's market, outcome, quantity, maker price and reservation are shown. The M1 strategy is called with `max_abs_inventory=5`; its raw formula and M2A behavior are unchanged. Candidate action and outcome must agree. The existing gateway supplies order signing, official CLOB placement, post-only and ID response normalization. A partial fill causes immediate cancellation of the remainder. The order terminal state alone does not release cash: account-wide open orders, collateral and official positions must reconcile.

## Deviations from architecture

- NONE.

## Tests added

Offline tests cover market selection thresholds, target cap, missing/wrong reservation, one-order and one-fill limits, cutoff, no candidate with zero POST and clean release, no arm, maker price, action/outcome binding, official ID and singleton-list normalization, full fill, unfilled cancellation, immediate partial cancellation, unknown cancellation, account mismatch, process reservation revalidation, and fake end-to-end release.

## Tests executed / results

- M3 offline matrix: 29 passed.
- Focused M2B-0A, M2B-0B, FavoriteFirst, M1, M2A, engine and gateway regression: 325 passed.
- Final full pytest: 846 passed, 2 skipped.
- Ruff on touched source and test files: PASS.
- Mypy on touched source: PASS.
- `git diff --check`: PASS.
- Repository-wide `ruff check src tests` finds 100 existing errors in untouched files; `ruff check .` also enters pre-existing local `research_outputs/` and finds 982 total. No unrelated lint cleanup was attempted.
- No real order, cancel, or authenticated trade write was made.

## Known limitations / unresolved issues

Process-local reservation is not restart durable. Unknown placement or cancel outcomes require manual account inspection. Official Data API position lag can prevent automatic release, safely retaining reservation. An independent review and explicit human invocation are required before a real run.

## Safety / behavior impact

A new, explicit MI live capability exists behind Generic Gate and two-step arm. Existing FavoriteFirst, M1, M2A and closed M2B behavior is not changed. The additive reservation read method does not alter reserve/release semantics.

## Git diff summary

New M3 runtime, command, offline tests and documentation; one additive process-reservation read method.

## Handoff to Reviewer

Review the exact working tree, including untracked M3 files. Confirm no unbounded order path, no release after uncertain state, and no accidental real trade in tests. No PASS or CLOSED claim is made here.

## Repair after initial M3 Review NEEDS FIX (2026-10-05)

The independent Review in `docs/reviews/MI_M3_REVIEW.md` reproduced a raw `0.655` BUY candidate with WebSocket tick `0.001`, best ask `0.660`, and metadata signing tick `0.01`. The official SDK would sign `0.66`, touching the ask. That Review remains historical NEEDS FIX evidence; this section records the Worker repair, not a Review PASS.

### Scope implemented

- Added `normalize_signing_price()` in `execution/gateway.py`. It uses the installed official SDK's `ROUNDING_CONFIG`, `round_normal()` and `price_valid()` with the same formatted metadata tick that the gateway passes to `create_order()`. Unsupported, missing or invalid ticks fail closed. It also requires the result to lie on the signing tick.
- M3 validates maker safety and reserved notional against the normalized signing price. The returned `Quote.price`, candidate display, placement log, submit guard and `gateway.place()` all use that normalized value. The submit guard checks normalization idempotence and the current ask. The raw strategy best bid must remain unchanged; no MI strategy or target formula changed.
- The SDK's `create_order()` uses the supplied metadata tick when it is valid against its official minimum tick; otherwise it raises before POST. SDK rounding is idempotent for the normalized price. `post_only=True` remains enforced by the gateway.

### Files modified in this repair

- `src/polymaker/execution/gateway.py`
- `src/polymaker/mi_micro_live.py`
- `tests/test_mi_micro_live.py`
- `docs/architecture/MI_M3_ARCHITECTURE.md`
- `docs/reports/MI_M3_IMPLEMENTATION_REPORT.md`
- `docs/CURRENT_STATUS.md`

### Tests added and executed

Added the exact Review reproduction, safe normalization, signed price below/equal/above ask, missing/unsupported signing tick, coarser book tick, zero fake POST before place, and equality between validated price, gateway input and fake signing output.

- M3: 42 passed.
- Focused M2B-0A/M2B-0B/FavoriteFirst/M1/M2A/Engine/gateway: 325 passed.
- Full pytest: 859 passed, 2 skipped.
- Ruff on touched source/test: PASS.
- Mypy on M3 and other touched source excluding the whole gateway file: PASS. Whole-file gateway mypy retains one pre-existing error in `open_orders()` (now line 635); the new price helper has no mypy error.
- `git diff --check`: PASS.
- No real order, cancel or authenticated trade write was performed.

### Safety impact and handoff

Only M3 candidate-price-to-signing-price validation changed. Generic Gate, reservation semantics, FavoriteFirst, M1, M2A, clip, target cap, working-order limit and fill limit remain unchanged. Request independent re-review of the repaired working tree, especially the SDK tick/rounding contract and the crossing-price zero-POST tests. M3 is not CLOSED and no human micro-live run is authorized by this Worker report.

---

## Readiness Hotfix (2026-10-06) — IMPLEMENTED — AWAITING INDEPENDENT REVIEW

Status: readiness hotfix implemented; awaiting independent Review. No real order, cancel or
authenticated trade write was performed.

### Observed defect (real runs, twice)

```text
market_ws_subscribed
→ first usable book
→ CANDIDATE
→ gateway.place()
→ order_place_aborted_stale_connectivity
```

MI evaluated/attempted a candidate as soon as the first book was usable, instead of
waiting for the FavoriteFirst-equivalent feed/connectivity readiness for the current
market. The blocking-stdin arm was already removed; the abort was not caused by input.

### FavoriteFirst readiness reference (reused, not re-invented)

`src/polymaker/favorite_live.py` `_preflight` / `validate_preflight` and
`src/polymaker/engine.py` `arm_favorite_first` / `_can_submit_market` gate first real
writes on:

- `engine._market_books_fresh(cid)` → `MarketDataService.books_fresh(meta)`: both token
  books have snapshots from the **current** WS connection generation; `md.connected`;
  `snapshot_local_ts >= connected_since`; `snapshot_generation == connection_generation`;
  two-sided uncrossed books.
- `engine.md.metrics["queue_full"] == 0` (preflight `market_ws_ready`).
- `not engine._market_recovery_pending` (post-drop recovery).
- authenticated User WS connected and authorized (`favorite_live_user_ready`), and
  Fill Truth / reconciliation clear.

MI runs the same `MarketDataService` with the same market WS; it has no Engine and no User
WS (it uses `OfficialReadOnlyAccount` REST polling). It therefore reuses the same market
feed/connectivity generation + freshness semantics: `books_fresh(meta)` (which already
embeds `connected` / `connected_since` / `connection_generation`) plus the same
`queue_full == 0` ingest-backlog condition.

### MI change

- `src/polymaker/mi_micro_live.py`: added `market_feed_ready(md, meta)` (FL-equivalent
  readiness) and `_readiness_timeout_s()` (reuses FavoriteFirst `PREFLIGHT_TIMEOUT_S`).
- `run_market()` now: waits for `market_feed_ready` before building/evaluating a candidate;
  logs `waiting_for_feed_readiness` / `feed_ready`; re-establishes readiness if it is lost
  after a candidate is built; and treats a final-guard abort (zero POST) as "re-wait and
  re-evaluate", never reusing the stale candidate.
- The final `submit_guard` is preserved and now also uses `market_feed_ready`; its
  `order_place_aborted_stale_connectivity` path is unchanged. Because MI records the guard
  abort, a zero-POST stale abort no longer masquerades as `OFFICIAL_ORDER_ID_REQUIRED`; an
  unrecoverable stale state fails closed and reports `STALE_CONNECTIVITY`.
- New market: readiness is per-condition via `books_fresh(meta)`; a different
  `condition_id` cannot inherit a previous market's books/generation.

### New runtime flow

```text
subscribe → wait for FL-equivalent readiness → book fresh → evaluate → candidate
→ final connectivity/freshness recheck → gateway.place()
```

Readiness false ⇒ zero POST. No fixed sleep, no threshold loosening.

### Tests added

`tests/test_mi_micro_live.py`:
- `test_subscribed_but_not_ready_never_places` (book exists, candidate computable, readiness
  false → zero place / zero POST; result `FEED_READINESS_TIMEOUT`).
- `test_readiness_requires_connected_fresh_and_clear_queue` (connected + books_fresh +
  queue_full==0 conditions).
- `test_readiness_then_single_post` (readiness false → true → exactly one fake POST).
- `test_new_market_cannot_inherit_readiness` and
  `test_new_market_not_ready_gets_zero_post` (new condition_id re-establishes readiness).
- `test_final_stale_guard_still_blocks_post` (readiness passes, candidate built, connectivity
  turns stale at the guard → zero POST, `STALE_CONNECTIVITY`).
- `test_mi_arm_uses_no_blocking_input` (`--arm` only; `builtins.input` never called).
- One existing fake was extended with `books_fresh`/`metrics` to match the real
  `MarketDataService` interface.

### Tests executed / results

- `tests/test_mi_micro_live.py`: 49 passed.
- MI suite (`test_mi_micro_live`, `test_mi_live`, `test_mi_cash_reservation`): 95 passed
  (1 pre-existing executor-join warning in `test_mi_live`).
- FavoriteFirst/M1/M2A subset: `test_favorite_first`, `test_favorite_first_review`,
  `test_favorite_report`, `test_live_marketdata`: 71 passed, 2 skipped.
- Engine/gateway/execution/live_authorization/livetest: 94 passed.
- FavoriteFirst readiness/connectivity tests in `test_favorite_live.py` (preflight gate,
  arm readiness, user-WS submit guard, collateral/allowance): passed for the subset that
  does not hit the sandbox hang.
- Full pytest: reached ~723/868 with zero failures, then hit pre-existing sandbox hangs
  (`test_favorite_live.py::test_required_spender_follows_client_order_version_and_market_risk`
  and `::test_favorite_submit_authorization_is_bound_to_arm_identity` reproduce in isolation;
  both are in code paths untouched by this change).
- Ruff `check` on touched source/test: PASS. Mypy on `mi_micro_live.py`: PASS.
  `git diff --check`: PASS. (`ruff format --check` is not repo-clean pre-existing: 71 files.)

### Safety impact and handoff

Only MI startup readiness/pre-place retry behavior changed. The stale guard remains
enabled; no threshold, limit, reservation, pricing, strategy, Generic Gate or FavoriteFirst
logic changed. No real trading was performed. Request independent Review of the readiness
gating, the new-market reset, the final-guard zero-POST behavior and the no-stdin arm.

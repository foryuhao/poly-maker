# MI M3 — Single-Market Micro-Live Independent Review

Verdict: PASS — final independent Re-review (2026-10-05); initial NEEDS FIX history preserved below
Reviewed predecessor baseline: `57a9e9ce2f260e4015fedfd632771fcc37fd6b8d`
Reviewed commit / working tree: that HEAD plus the M3 tracked changes and untracked source, tests, architecture, and implementation report listed below; no commit or tag made.
Architecture reference: `docs/architecture/MI_M3_ARCHITECTURE.md`
Implementation report reference: `docs/reports/MI_M3_IMPLEMENTATION_REPORT.md`

## Scope reviewed

Read `src/polymaker/mi_micro_live.py`, `src/polymaker/cli.py`, the complete additive diff to `src/polymaker/mi_cash_reservation.py`, `tests/test_mi_micro_live.py`, the M3 documents, and the actual gateway, SDK order builder, Generic Gate, account reader, market data, BTC5m resolver and MI strategy call chain. `livecfg/config.toml` and `research_outputs/` were already local changes and were excluded from the M3 verdict. `git diff` alone does not include the new untracked M3 files; they were read directly.

Reviewed M3 source/test fingerprints (SHA-256):

```text
922ea19010c00d428f1ba3dd1b68050a6e3570c0b01c3e9a7f52b217ecb82990  src/polymaker/mi_micro_live.py
46da08f67e0f4b72dc590f6481870a921bafc421330f6db9bf0b3708ce249be2  src/polymaker/cli.py
31d5b85984fe66d36f02b4e4ec0811d2c768ceb47775c482a80f01a13919122a  src/polymaker/mi_cash_reservation.py
121d538d1b4caf9224b61c774de24374099a7f5dc776793c64b65ba8fc031e94  tests/test_mi_micro_live.py
```

## Findings

### P1 — BLOCKER: candidate tick can differ from the gateway signing tick

`candidate_quote()` checks `price % snapshot.tick_size == 0` and maker placement against the live book (`mi_micro_live.py:135-146`). `build_snapshot()` takes this tick from the WebSocket book (`mi_live.py`). The gateway signs with `meta.tick_size`, supplied as `PartialCreateOrderOptions(tick_size=...)` (`execution/gateway.py:211-221`). Neither M3 path checks equality of those ticks or validates the candidate price against the signing tick.

An offline probe supplied a valid MI book with WebSocket tick `0.001`, YES best bid `0.655` and best ask `0.660`, while selected-market metadata tick was `0.01`. MI produced a five-share BUY at `0.655`; `candidate_quote()` accepted it. The installed official SDK's `create_order()` uses `round_normal(..., ROUNDING_CONFIG[tick_size].price)`, yielding a signed price of `0.66`, equal to the ask. Its `price_valid()` accepted the original price because that check is a price range check. Thus the M3 pre-submit maker-price guard can approve a price different from the actual signed price. `post_only=True` remains a final venue protection, so this probe does not establish a taker fill. It does establish that M3 can send a crossing-priced signed request instead of denying invalid candidate-to-gateway tick evidence, contrary to the M3 maker-only and no-dangerous-autocorrection requirements.

Required fix: at the final write boundary, require consistent current venue tick evidence and validate the exact price the gateway will sign against that tick and current ask, rejecting any mismatch. Add an offline test with divergent WebSocket and metadata ticks, including a price that the SDK would round to the ask. Do not change the M2A strategy's normal target range.

### P0

- None found.

### P2 / test gaps

- The current M3 tests cover ordinary price/tick checks but not WebSocket tick versus gateway metadata tick drift. This is part of the P1 blocker, not a separate closure issue.

## Real-write path and limits

The one reachable M3 placement call is `cli.mi_micro_live()` → `run_micro_live()` → exact BTC5m resolver → `run_market()` → M1 `evaluate()` → `candidate_quote()` → candidate display → typed arm → `authorize_live_write()` → `ExecutionGateway.place([quote], ..., force_post_only=True, strict_placement=True)` → official SDK `create_order()` → `post_orders(post_only=True)`. There is one M3 `gateway.place()` call site and no loop back to candidate evaluation after it. Full fill or terminal cancel stops this market lifecycle. No M3 SELL, market-order, second-market trading, blockchain RPC, or third-party read/write path was found.

CLI without `--arm` exits before loading the runner. `--arm` alone requires the exact typed `ARM MI MICRO LIVE` phrase after candidate display; wrong or empty input denies, and EOF exits without calling place. The Gate and current identity are checked after arm and again in the gateway submit guard. An independent fake-client run produced one `create_order`, one `post_orders(post_only=True)`, an official singleton-list ID and a released reservation after full-fill reconciliation; changing identity after preflight produced zero signed orders and zero POSTs while retaining the reservation.

Startup `remaining < 180s` selects the next epoch before resolving market metadata or reserving cash. Once `run_market()` begins, there is no trading rollover. The MI config clamps raw `+20/-20` to `+5/-5`; the candidate requires five shares, BUY, price in `(0,1)`, notional within $25, current best bid below ask, and T−15 cutoff. The identified tick mismatch prevents certification of the exact maker price invariant.

## Cash / reservation audit

The only M2B-0B modification is `MICashReservationBook.is_active()`, a locked read against the existing process-global authority. It does not change official cash truth, the fixed $25 amount, shared funder pool, reservation creation, ownership, or release semantics. A separate-book probe denied a second $25 reserve against the same funder's $40, and forged ID, identity, market and RELEASED copies did not pass `is_active()`.

`run_market()` requires current identity, official empty account-wide open orders and balance, a bound ACTIVE $25 reservation, and submit-time revalidation. Full fill, confirmed cancel and confirmed partial cancellation require terminal order evidence plus empty open orders, collateral and position reconciliation before release. Cancel failure, unknown order lookup and unknown placement retain reservation. There is no unconditional release in `finally`.

## Tests and offline probes

- M3: 29 passed.
- Focused M2B-0A/M2B-0B/FavoriteFirst/M1/M2A/Engine/gateway: 325 passed.
- Full pytest: 846 passed, 2 skipped.
- Ruff on M3 touched source/test: passed.
- Mypy on touched source: passed.
- `git diff --check`: passed.
- Independent CLI fake-runner probe: missing `--arm`, wrong/empty typed input and EOF caused no runner arm approval; exact phrase with `--arm` approved the fake callback.
- Independent actual-gateway fake-client probe: full fill produced one five-share signed BUY and one post-only POST; changed identity produced zero POSTs.
- Independent cap, cutoff, startup-threshold, reservation-ID and shared-funder probes passed. Tick-drift probe reproduced the P1 failure.
- No real order, cancel, authenticated trade write, or external network trade call was performed.

## Safety / regression assessment

Generic Gate, FavoriteFirst, Engine, M1, M2A and gateway source are unchanged from the reviewed predecessor baseline. FavoriteFirst's $12 preflight remains unchanged. The M2B-0B additive read does not reopen its closed cash invariants. The P1 issue resides in the new M3 integration between live-book tick validation and gateway signing tick. Passing tests do not certify this untested price boundary.

## Untracked artifact audit

The reviewed M3 runtime, tests, architecture and implementation report are untracked. They must be included in a later complete checkpoint after repair and independent re-review. This Review document is itself a new untracked artifact. Pre-existing local `livecfg/config.toml` and `research_outputs/` remain outside M3 scope.

## Required fixes

1. Deny any candidate whose price/tick can differ from the exact official SDK signing price; prove maker safety under tick drift before `post_orders()`.
2. Add the divergent-tick regression and request independent M3 re-review of the repaired working tree.

## Deferred issues

Process-local reservation has no crash persistence by design. Real micro-live has not been run and cannot be authorized by this failed implementation Review.

## Initial Review Verdict

M3 IMPLEMENTATION REMAINS OPEN — maker-price validation can diverge from the SDK signed price.

M3 REVIEW FAIL.

## Final independent Re-review (2026-10-05)

Reviewed predecessor baseline: `57a9e9ce2f260e4015fedfd632771fcc37fd6b8d`.
Reviewed working tree: that HEAD plus the exact tracked/untracked M3 source, tests and documents present on 2026-10-05. No commit or tag was made. The pre-existing `livecfg/config.toml` and `research_outputs/` remain outside the M3 review scope.

Reviewed source/test fingerprints (SHA-256):

```text
99d1e31df2464ab2efe09af46d88bca000a8edab8de8c6a663ccf788e70c9e6a  src/polymaker/mi_micro_live.py
ac158cd495064607b44a95c9e1d223f3d5a2d6d325d20e5959640f279191e41c  src/polymaker/execution/gateway.py
46da08f67e0f4b72dc590f6481870a921bafc421330f6db9bf0b3708ce249be2  src/polymaker/cli.py
31d5b85984fe66d36f02b4e4ec0811d2c768ceb47775c482a80f01a13919122a  src/polymaker/mi_cash_reservation.py
7e209e293ddfec6fcb972a46100e8b0ea75ae1501b78a4e0a04f00a3b0679ac9  tests/test_mi_micro_live.py
```

### Prior P1 blocker closure

PASS. `ExecutionGateway.place()` passes `_tick_str(meta.tick_size)` to the official CLOB SDK `create_order()` options. The installed SDK resolves its official minimum tick, rejects a smaller supplied metadata tick before POST, and otherwise uses the supplied metadata tick. Its `create_order()` calls `round_normal(price, ROUNDING_CONFIG[tick].price)` before building the signed order. The new pure `normalize_signing_price()` uses the same tick formatting, `ROUNDING_CONFIG`, `round_normal()` and `price_valid()`; it rejects absent/unsupported ticks and any normalized result off the signing tick. M3 converts the raw Decimal candidate to the same float representation handed to `create_order()`. The returned normalized `Quote.price` is checked strictly below the book ask and against the $25 commitment. Before POST, the gateway submit guard rechecks the current book and normalization idempotence. `post_only=True` remains enforced.

Independent offline reproduction: WebSocket book tick `0.001`, raw BUY `0.655`, ask `0.660`, metadata/signing tick `0.01`. The official SDK rounding primitive gives `0.66`; `candidate_quote()` denies `MAKER_OR_TOKEN_INVALID`. A fake `run_market()` probe recorded zero `gateway.place()` calls and zero fake `post_orders()` calls. With raw BUY `0.645` and the same ask/ticks, M3 passed `0.64` to an actual `ExecutionGateway.place()` call backed by a fake official client. The fake client applied the SDK primitive and recorded signed price `0.64`, one `post_orders(post_only=True)` call, a five-share fill, reconciliation and released reservation. A second candidate was not reached. Signed price equal to ask, above ask, missing tick and unsupported tick all denied offline.

### Safety and regression assessment

Only a pure gateway helper was added; the shared gateway placement, response normalization and cancellation bodies are unchanged. FavoriteFirst order behavior, M1/M2A strategy, Generic Gate and M2B-0B reservation semantics are unchanged. M3 still has one `gateway.place([quote])` call site, one BTC5m market, BUY-only five-share clip, ±5 target cap, one working order, one filled clip, startup minimum 180 seconds, T−15 cutoff, explicit CLI `--arm` and exact typed arm. A full fill exits after reconcile; unfilled and partial orders require confirmed cancellation and reconciliation; unknown placement/cancel retains the ACTIVE reservation. All M3 market data, cash, position, order and cancel calls remain on official Polymarket HTTP/WebSocket/SDK paths. The new helper adds no network or blockchain interface and logs no secret.

### Tests and independent probes

- M3: 42 passed.
- Focused M2B-0A/M2B-0B/FavoriteFirst/M1/M2A/Engine/gateway: 325 passed.
- Full pytest: 859 passed, 2 skipped.
- Ruff on touched source/test: passed.
- Mypy on M3, CLI and reservation source: passed. A whole-file gateway mypy check reports only the pre-existing `open_orders()` assignment error (current line 635); no error occurs in the new helper.
- `git diff --check`: passed.
- Independent fake probes: crossing price zero POST; safe normalized price equal to gateway input and SDK-equivalent signed price; below/equal/above ask, missing/unsupported tick; one post-only POST followed by fill/reconcile/release. No real order, cancel, authenticated trade write or external trade network request occurred.

### Findings and required fixes

P0: none. P1: none. P2: none requiring a fix before implementation closure. Prior tick-drift test gap is closed. Required fixes: none for the reviewed implementation.

### Final verdict

M3 IMPLEMENTATION CLOSED — READY FOR HUMAN MICRO-LIVE, for the reviewed working tree only. This certifies implementation readiness, not an actual real-money order lifecycle. The complete reviewed files must be checkpointed before use; no real trading was performed in this Review.

M3 FINAL RE-REVIEW PASS.

---

# MI M3 — Readiness Hotfix Independent Review (2026-10-06)

Verdict: PASS
Reviewed predecessor baseline: reviewed M3 tree on `57a9e9ce2f260e4015fedfd632771fcc37fd6b8d` (+ P1 price-boundary repair)
Reviewed commit / working tree: uncommitted working tree; readiness hotfix touches only
`src/polymaker/mi_micro_live.py` and `tests/test_mi_micro_live.py` (both untracked). No commit/tag created.
Architecture reference: `docs/architecture/MI_M3_ARCHITECTURE.md`
Implementation report reference: `docs/reports/MI_M3_IMPLEMENTATION_REPORT.md` (Readiness Hotfix section)
Independent probes: `/tmp/review/probes.py`, `/tmp/review/probe_real_gateway.py` (offline, no real orders)

## Scope reviewed

Read the readiness code in `mi_micro_live.py` (`market_feed_ready`, `_readiness_timeout_s`, the
`run_market` loop) and the new tests. Independently confirmed
`favorite_live.py`, `engine.py`, `marketdata/service.py`, `orderbook.py`, `maker_inventory.py`,
`mi_live.py`, `mi_readonly_account.py` are all **unmodified** vs HEAD (`git diff --stat` empty),
and that `test_favorite_live.py` is not in this diff.

## FL readiness comparison

- FL relevant readiness (`favorite_live._preflight` / `arm_favorite_first`): `md.books_fresh(meta)`
  (via `engine._market_books_fresh`) **and** `not engine._market_recovery_pending` **and**
  `md.metrics["queue_full"] == 0`, plus FL-only User-WS / Fill-Truth gates.
- MI readiness (`market_feed_ready`): `md.connected` **and** `md.metrics["queue_full"] == 0` **and**
  `md.books_fresh(meta)`.
- Equivalent: **YES** for the market-feed/connectivity portion. `books_fresh` already encodes
  connected / `connected_since` / `connection_generation`. MI has no Engine and no User WS
  (it uses `OfficialReadOnlyAccount` REST polling), so the FL-only `_market_recovery_pending`,
  User-WS and Fill-Truth gates have no MI counterpart and are not applicable to a single
  reservation-gated MI order. MI is **not weaker** than the FL market-feed readiness; MI introduces
  **no new arbitrary readiness state** (no MI READY flag, no fixed sleep, no new timeout).

## Startup readiness audit

- Subscribed but not ready: **PASS** — probe A: `connected=True`, book present, candidate otherwise
  computable, `market_feed_ready=False` ⇒ `gateway.place`=0, `post_orders`=0, no `CANDIDATE` emitted.
- Ready transition: **PASS** — probe B: same fake with readiness True ⇒ exactly 1 POST; probe E shows
  real `books_fresh` False→True as snapshots arrive.
- Queue backlog: **PASS** — probe C: `queue_full>0` ⇒ not ready (and `connected=False` ⇒ not ready);
  enforced in the gate, not only logged.
- Connection generation: **PASS** — probe E on the **real** `MarketDataService`: gen1 fresh=True,
  after a generation bump (old snapshots retained) `books_fresh=False`, after gen2 snapshots
  `books_fresh=True`. MI readiness follows this.

## New-market audit

- Can B inherit A readiness? **NO** (probe D; and probe `test_new_market_not_ready_gets_zero_post`
  run_market(B) ⇒ 0 POST).
- Can B use A snapshots? **NO** — `books_fresh(metaB)` is false until B's own current-generation
  snapshots exist; MI readiness is per-`condition_id`.

## Candidate invalidation

Can a candidate built before readiness loss later be submitted? **NO** — probe F: a candidate is
built at price 0.64, readiness is lost at the final guard (0 POST), readiness returns, MI re-evaluates
(`build_snapshot` called again) and the single POST uses the **new** snapshot price 0.66, never the
stale 0.64 candidate. `ready=False; saw_stale=True; continue` re-enters the readiness gate before
any new candidate is built.

## Final guard

- Ready → stale before place: probe H with the **real `ExecutionGateway`** and fake client:
  `create_order`=1, `post_orders`=0, gateway logs `order_place_aborted_stale_connectivity`,
  MI result `STALE_CONNECTIVITY`. The startup gate did not replace the final guard; both layers are
  present, and the stale threshold is unchanged.

## Single-order regression

Can readiness retry produce >1 POST? **NO**. Gateway `submit_guard` runs before `attempted=True` /
`post_orders` at both call sites, so `guard_aborted=True` proves zero POST; MI retries only when
`guard_aborted and not placed`. A real POST (`placed` non-empty) makes MI settle and break; a
POSTed-but-rejected response (`placed=[]`, `guard_aborted=False`) raises `OFFICIAL_ORDER_ID_REQUIRED`
without retry; `PlacementUnknown` propagates. Single `gateway.place` call site; no second write path.

## M3 regression

Core limits all **PASS / unchanged**: BTC5m only, remaining≥180s, T-15 cutoff, BUY only, maker only,
`post_only=True`, clip=5, target cap ±5, working=1, filled_clip=1, $25 ACTIVE reservation, Generic
Gate (`authorize_live_write`), identity binding, signed-price maker validation, official order ID
required, ≤1 POST (enforced by `MicroConfig.__post_init__` and the single placement path).

## Tests

- `tests/test_mi_micro_live.py`: 49 passed.
- Engine/execution/gateway/live_authorization/livetest/favorite_first/favorite_first_review/
  favorite_report/live_marketdata/mi_cash_reservation: 192 passed, 2 skipped.
- `tests/test_favorite_live.py`: 58 passed, 5 deselected (the two pre-existing hangs).
- `tests/test_mi_live.py`: 19 passed (pre-existing 300s executor-join warning).
- Full suite: NOT COMPLETED — pre-existing unrelated hang. `test_favorite_live.py::test_required_spender_follows_client_order_version_and_market_risk`
  and `::test_favorite_submit_authorization_is_bound_to_arm_identity` hang in isolation
  (`timeout` EXIT=124) in a file **not modified** by this diff; the full run reached ~723/868 with
  zero failures before a research-writer hang. Not treated as an M3 blocker.
- Ruff `check` (touched), Mypy `mi_micro_live.py`, `git diff --check`: PASS.
- `ruff format --check` is not repo-clean pre-existing (71 files); not introduced here.

## Findings

P0: none. P1: none. P2: none requiring a fix before closure.

## Final verdict

M3 READINESS HOTFIX REVIEW PASS — READY FOR NEXT HUMAN MICRO-LIVE, for this reviewed working tree only.
No real order, cancel or authenticated write was performed.

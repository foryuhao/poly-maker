# M4A Research Telemetry — Technical Architecture

Status: ready for M4A.1 Worker implementation; no implementation or live deployment authorized by this document.
Git base: `fff2842` (`m3c-settlement-live-pass`).
Research authority: `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md`, especially sections 3, 5, 11–12.
Program contract: `docs/architecture/M4_ARCHITECTURE.md`.

## Objective, scope and non-goals

Capture the actual causal post-first-fill YES/NO market, owned-order, Fill Truth and FavoriteFirst path through T+300 as append-only, auditable research evidence. M4A is observational capture and audit. It does not create FutureLabels, execution probabilities, policy replay, Dynamic EV, scorer, optimizer or model-driven trades. No entry, sizing, inventory, settlement, auth or safety behavior changes. Current live order quantity may be five shares; every research quantity field remains generic.

## Inspected repository reality

- `favorite_live.py:run_favorite_first_live` commits one market per invocation, sleeps until `epoch + PERIOD_S` (300s), then finalizes and shuts down. There is no sequential multi-market supervisor. Observe through T+300 while this process/feed runs; interruption is censored, never secretly extended by a new process.
- `Engine` is one asyncio event loop. `MarketDataService` has its own bounded WS ingest queue and per-token `OrderBook`; `connection_generation` increments on reconnect. Research needs a separate queue, not a change to the feed's safety-on-overflow behavior.
- `MarketDataService._on_book/_on_price_change/_on_last_trade/_on_tick_change` apply parsed actual-token events. Tick changes currently log only; `Engine._refresh_book` can replace a book from REST without full replacement evidence in the journal.
- `UserEventProcessor.on_trade` owns accepted `(trade_id,maker_order_id)` economics, status progression, quarantine and REST discoveries. `StateStore` persists accepted fills/owned orders but not all as-known revisions. `Engine._favorite_economics` scopes first/pair fills to registered IDs; `Engine._on_fill` alone loses status/role/source detail.
- `Engine._recompute_locked` calls pure `evaluate_favorite`, then changes the effective target via readiness, risk, mismatch and reprice gates before `reconcile`; the existing `favorite_first_decision` journal row is change-only. It is not a periodic state record.
- `Journal.write` is synchronous line-buffered JSONL with `{ts,kind,data}`. `ts` can be exchange or local time; journal offset/checksum and local availability are absent. `UserStream` has connected state but no generation counter. `OrderBook` exposes full sorted levels and top views. No BTC reference feed was found.

## Dependency and module boundaries

```text
Existing live callbacks / authoritative state
             │ copy immutable, sanitized observations
             ▼
ResearchObserver (default OFF; no gateway/store write methods)
             │ bounded put_nowait; no serialization or file wait
             ▼
Dedicated research queue → ResearchWriter thread → research_data/
                                      │
                                      └→ read-only audit after close
```

Use a dedicated writer thread with a standard bounded thread-safe queue: existing callbacks run on the trading event loop and must not wait for file flush/fsync. Only immutable primitive-valued records cross the boundary; copied books/order facts are bounded to the required payload and prepared without network/disk access. The writer owns JSON encoding, files, checksums, flush and fsync. Observer interfaces receive copied data/callbacks, never `ExecutionGateway`, `StateStore` mutation, `UserEventProcessor` mutation or credentials. A null/default-off observer is inert. Research exceptions are caught at each integration seam and recorded in health counters; no exception reaches market processing, decisions, order placement/cancel, Fill Truth, reconciliation or settlement. Research cannot trigger a live wake/guard change.

Expected files by slice: new `src/polymaker/research/{__init__,schema,observer,writer,audit}.py`; narrow observation hooks in `marketdata/service.py`, `userstream/client.py`, `state/tracker.py`, `engine.py`, and possibly `favorite_live.py`; tests under `tests/`; `.gitignore` entry for `research_data/`. `execution/gateway.py` may receive observation-only request/response callbacks if engine-level wrapping cannot distinguish unknown placement; it must retain existing semantics. `strategy/favorite_first.py`, Fill Truth rules, settlement, wallet/auth and live config semantics are unchanged. No `model.py`, `scorer.py`, `optimizer.py` or `policy.py` in M4A.

## Exact observation seams

Each hook runs **after** the existing authoritative transition when recording accepted state; request hooks run immediately before existing awaited I/O. It copies data and has no control return value. "No" in the last column means the hook must preserve the existing live path.

| Existing file / function or class | Observation hook and copied evidence | Why causal / live behavior changes? |
|---|---|---|
| `marketdata/service.py:MarketDataService._connect_and_listen`, `_consume` | Connection up/down, generation, reader receipt monotonic time and corresponding wall time attached to queued frame; processed/lag metric. | Receipt precedes handling; generation prevents old frames crossing reconnect. No. |
| `marketdata/service.py:_on_book`, `_on_price_change`, `_on_last_trade`, `_on_tick_change` | Applied full book, **all** per-token deltas in frame, trade print and tick mutation, token/condition, exchange time, source message identity, copied levels/hash/tick, applied clock. Emit after mutation; invalid parse is quality evidence, not fabricated book. | Records state only when parser/application made it knowable. Both real token books are separate. No. |
| `engine.py:Engine._refresh_book` | `REST_BOOK` with complete bids/asks/hash/tick **only when drift branch applies replacement**, plus discovery/applied clock and token. | REST fetch completion is first knowledge; never backdate to the book's apparent exchange time. No. |
| `userstream/client.py:UserStream._connect_and_listen`, `_handle` | User connection up/down, **new observer-only user generation counter**, per-frame receipt and process clocks; no secrets or raw auth payload. | User WS can reconnect and trigger REST recovery; distinguish generations. No. |
| `engine.py:Engine._recompute_locked` around `evaluate_favorite`, target creation, `reconcile` | `BASELINE_DECISION`: exact pure snapshot/memory inputs, pure quote/state/trigger and then effective `desired`/`TargetQuotes`, suppressing guard reasons, reprice state, plan/no-op. The existing sparse journal remains unchanged. | Shows what was available before execution, and why pure policy differed from effective order intent. No. |
| `engine.py:_recompute_locked`, `_favorite_finalize_orders`, `_favorite_submit_ok`, `_on_market_ws_connection`, `_invalidate_fill_truth`, `favorite_halt_reason` | `SAFETY`/finalization state transitions: armed/feed readiness, blind/halt/mismatch, fill truth generation/readiness, first-finalized, pair-terminal and cutoff. Emit on changes and copy in snapshots. | These operational gates can suppress/delay PAIR; later reconciliation is not projected backward. No. |
| `engine.py:_recompute_locked` at `plan.to_place/to_cancel`, and `execution/gateway.py:ExecutionGateway.place/cancel` if needed | Sanitized submit/cancel intent and request correlation ID; after existing gateway result, ACK success/failure/**unknown**, order IDs, quote/role, and timing. Unknown POST is distinct from confirmed rejection. Observe asset-wide cleanup/finalization cancels too. | Intent precedes awaited I/O; result becomes known only after return/exception. No. |
| `engine.py:_recompute_locked` after `register_order`; `state/tracker.py:UserEventProcessor.register_order/on_order/quarantine_order` | Owned registration (`favorite_first_leg` = FIRST, `favorite_pair_leg` = PAIR), authoritative cumulative/remaining order state, terminal/cancel state, quarantine, role, IDs and source. | Registered ownership is the role authority; buffered pre-ACK fills become accepted only after registration. No. |
| `state/tracker.py:UserEventProcessor.on_trade` for WS and REST, plus `execution/filltruth.py:reconcile_owned_order*` outcome at Engine callers | Every accepted/advanced/retried/FAILED/quarantined revision with canonical fill key, source, status, applied/economic flag, role, price/qty and known-at. Record delayed REST discovery at REST completion. Reconcile completion/failure as separate readiness evidence. | Processor decides Fill Truth; observer copies the decision after it occurs. `Engine._on_fill` alone is insufficient. No. |
| `engine.py:Engine._favorite_economics`, `state/store.py:StateStore.owned_order/orders_for`, `marketdata/orderbook.py:OrderBook` views | Copy scoped accepted inventory, costs, active order status and top-5 YES/NO books into snapshots; compare with account position, do not substitute wallet quantity for strategy fills. | A snapshot is as-of the last locally applied research event, with missing data marked invalid. No. |

The Worker may implement equivalent narrower callback signatures, but must preserve these observation points. If a needed seam cannot be added without changing live semantics, stop and request Architecture revision. In particular, do not place research callbacks inside a gateway guard in a way that changes guard timing or result.

## Causal clock and sequence contract

Research Design section 5 defines exact v1 field names/types for `ResearchEvent`, `Episode`, `StateSnapshot`, nested BookState/WorkingOrder and manifests; use that as the schema authority rather than copying it here. Reject unknown fields, use finite decimal strings for prices/quantities/costs and `null` for unknown. No five-share structural assertion.

- `exchange_at_us`: parsed venue event time where present; nullable. It is never a local knowledge clock.
- `received_at_us`: wall timestamp sampled at WS reader enqueue or immediately after REST response; preserve the paired `time.monotonic_ns()` sample. The current market queue stores monotonic receipt only, so M4A.2 minimally adds paired wall receipt metadata. User WS needs the same stamp.
- `available_at_us`: wall timestamp after the event is parsed, accepted/applied and visible to the relevant live state (or after an I/O result returns). For a process-generated intent, stamp when generated. Sample `monotonic_ns` at the same boundary. It must be nondecreasing in observer enqueue order, allowing equal microseconds with ordered `event_seq`.
- `event_seq`: strictly increasing, session-local observer sequence assigned at the boundary before queue attempt; never reorder by exchange time. Every lost sequence is accounted by GAP/manifest counters. `connection_generation` is stream-specific; book `snapshot_generation` follows market reconnect or REST replacement.
- `knowledge_seq` on StateSnapshot: last **applied** observation sequence included; snapshot fields must be a consistent copy of state at that cutoff and no later event may appear. Scheduled time is distinct from actual `available_at_us`.

If a fill executes at exchange T0 but REST reveals it at local T+4, earlier snapshots retain the prior inventory. Its first accepted revision and Episode start use T+4 as knowledge time while retaining T0 for attribution. No historical backfill of earlier state. Existing strategy `first_fill_ts` may follow exchange time; research `first_fill_known_at_us` is separate. If a causal clock is unavailable, mark it null/invalid as the schema permits; do not fabricate one from journal `ts`.

## Episode and state lifecycle

Maintain a bounded approximately 20-second pre-fill ring of market mutations, quote exposure and clocked context for the committed condition; bound both age and memory. Entry order trials, including canceled/unfilled trials, are run-level events and never fake Episodes. The first **accepted positive FIRST-role** maker fill creates `episode_id = hash(condition_id, initial first_order_id)` as specified by Research Design; persist relevant ring context, append Episode revision 1, and immediately capture a FILL StateSnapshot. Additional FIRST/PAIR fill revisions, order/finalization/safety changes and terminal/censor changes append revisions or material snapshots, never overwrite the earlier as-known state.

Sample at a fixed 1-second schedule after first knowledge through T+300 while the existing session runs, plus material FILL/ORDER/SAFETY/FINAL samples. Continue after actual COMPLETE or T+270; post-cutoff snapshots are price diagnostics, not evidence that orders could be placed. Capture interruption/disconnect and mark affected coverage censored. A restart can reuse the deterministic episode key only when authoritative order identity is recovered; the current in-memory FavoriteFirst state is not generally restored, so an interrupted path remains censored unless a separately proven recovery continuity contract exists.

State includes actual YES and NO top-5 books/tick/validity, accepted FIRST and PAIR quantities/costs, `yes_qty`, `no_qty`, `paired_qty`, `unpaired_first_qty`, `unpaired_opposite_qty`, working orders and guard status. Cost basis comes from accepted fills only. Candidate quoted PAIR PnL is labeled a quote and is null when infeasible; M4A does not compute executable EV. The fixed FeatureVector keys from the Research Design remain schema fields: M4A may populate directly observed or strictly prefix-derived values, marks insufficient history/invalid coverage null, and leaves model/BTC-dependent values null. M4B owns the validated point-in-time feature table. Episode final outcomes remain unknown until evidence exists and may never be joined backward into snapshots.

## Writer, storage and failure model

Use `research_data/<dataset_id>/<session_id>/{manifest.json,events.jsonl,episodes.jsonl,states.jsonl,quality.json}`. `research_data/` must be Git-ignored in the Worker slice that first writes it. Session/dataset IDs, source code commit/config hash (effective FavoriteFirst defaults included), schema/payload versions, selected market/tokens, start/end, file hashes, event watermark, gap counts and close status belong in manifest. Do not capture private keys, wallet credentials, auth headers or secret config fields. `quality.json` is a derived read-only audit result; it is not source evidence.

Producer uses bounded `put_nowait`; no callback disk/network, flush, fsync or queue wait. Writer batch flush target <=1s and fsync target <=5s off the live loop. Clean close drains within a bounded shutdown budget and atomically finalizes manifest/checksums; an incomplete drain is declared, never silently discarded. On overflow/observer error, keep a contiguous lost-sequence count/range and reason in independent counters, emit `GAP` as soon as capacity returns, and put unresolved loss in manifest even if no further enqueue succeeds. Dependent snapshots/intervals become invalid. Crash inspection detects partial JSON tail, missing final manifest/checksum and last durable watermark; no automatic tail repair that makes data look complete. Writer has zero trading authority.

Do not rely solely on existing `Journal` as a byte-offset source: it does not expose offsets or stable rotation identity. Capture sanitized applied market payloads sufficient for book reconstruction in research events; include a raw-journal `SourceRef` only when exact offset/length and final checksum are proven. An empty source-ref list is explicit. Do not launch another market feed or modify journal semantics to supply references. Full WS book and REST replacement payloads, deltas and tick changes must replay the observed book; top-5 snapshots alone cannot reconstruct it.

## Authorized implementation slices and acceptance

| Slice | Exact scope and files expected | Acceptance evidence |
|---|---|---|
| M4A.1 Schema + Isolation Skeleton | `research/__init__.py`, `schema.py`, `observer.py`, `writer.py`; ResearchConfig default OFF; bounded queue interface and no-op path; near-zero `Engine`/wrapper integration only if needed to prove wiring. No real hooks or data claims. | Schema validates quantity-generic records; observer construction/failure cannot import or invoke execution; default-off controlled behavior unchanged. |
| M4A.2 Causal Event Capture | Narrow hooks in `marketdata/service.py`, `userstream/client.py`, `state/tracker.py`, `engine.py`, optional gateway observation seam; clocks/generation/sequences; applied WS/REST book, tick, trade, owned order, Fill Truth, baseline/safety events. | Delayed REST fill is known only at discovery; WS reconnect and REST replacement reconstruct; request/ACK/unknown states distinct; no changed order intent. |
| M4A.3 Episode + StateSnapshot | Pre-fill ring, episode revisions/identity, immediate/1s/material snapshots to T+300 in `observer.py` plus scheduling hook in `engine.py` or wrapper. | Partial/restarted/invalid episodes explicit; no fake unfilled episode; both books and accepted inventory are as-of knowledge_seq; post-COMPLETE observation persists. |
| M4A.4 Durable Writer + Failure Isolation | Complete `writer.py`, manifests/checksums/GAP/crash-tail, `research_data/` ignore rule and close integration. | Controlled overload/crash preserves trading traces and visibly invalidates capture; flush/fsync and queue bounds verified. |
| M4A.5 Audit + Acceptance Harness | `audit.py`, deterministic fixtures and relevant tests; read-only `quality.json`. | Sequence, identity, book/tick/inventory reconstruction, snapshot completeness, timing/isolation and real collection gate reported. |

Every Worker slice creates `docs/reports/M4A_<N>_IMPLEMENTATION_REPORT.md` with status **IMPLEMENTED — AWAITING REVIEW**; Worker cannot declare PASS. Every independent Reviewer creates `docs/reviews/M4A_<N>_REVIEW.md` with PASS or NEEDS FIX against a named commit/scope. P0/P1 block PASS; P2 may be explicitly deferred. After all five pass, create `docs/milestones/M4A_FINAL_REPORT.md` with actual gate result. Material architecture conflict stops the affected implementation and returns for Architect revision; no silent redesign.

## M4A engineering gate

- Isolation: controlled observer ON/OFF replay yields identical strategy decisions and order intents; injected research errors cannot propagate into trading.
- Causality/timing: strictly auditable sequence, correct `available_at`/knowledge cutoffs, no later evidence in earlier snapshots; callback p99 target <1ms and incremental controlled loop lag p99 <=5ms.
- Completeness: >=99% scheduled 1s snapshots during valid connected post-fill periods; any unexplained gap >2s fails. Disconnected/interrupted intervals are explicit, not counted as healthy coverage.
- Fill identity: every observed economic fill maps to registered owned FIRST/PAIR order, no duplicate economic application, and quantities/statuses reconcile with existing Fill Truth; quarantined/unresolved evidence remains visible.
- Reconstruction: initial full books + mutations reproduce sampled YES/NO top-5, tick and accepted inventory at declared sequence points within source precision; no complement-derived NO book.
- Coverage before operational readiness: >=20 real first-fill Episodes over >=3 market-days, from separately authorized existing runs. Synthetic fixtures test failure modes but do not count as economic episodes. Current single legacy episode is insufficient.

Failure in identity, clocks, mutation coverage, isolation or unexplained loss is M4A NO-GO for M4B evidence. The gate tests data trust, not economic edge or model performance.

## Open issues and next action

The current journal's missing byte offsets make raw SourceRef optional until independently verifiable; self-contained sanitized event payloads are required. User WS generation and paired wall/monotonic receipt clocks require minimal new observation metadata. Continuous multi-market collection, BTC feed, settlement attribution, exact M4D model and any live-control architecture are deferred. Next authorized action is **M4A.1 only**; no production/research Python was changed in this architecture session.

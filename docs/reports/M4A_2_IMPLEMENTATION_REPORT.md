# M4A.2 Implementation Report

Status: **IMPLEMENTED — AWAITING REVIEW**

Implementation baseline: `830240a` (`m4a1-pass`), clean working tree at start.
Previous milestone: M4A.1 PASS, `docs/reviews/M4A_1_REVIEW.md`.
Architecture reference: `docs/architecture/M4A_ARCHITECTURE.md` (program boundary: `docs/architecture/M4_ARCHITECTURE.md`).
Research reference: `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md`.

## Scope Implemented

Added an opt-in causal event adapter around the M4A.1 `ResearchObserver`, with applied market, REST book, owned order, Fill Truth, FavoriteFirst decision, effective quote, and safety evidence. The live `favorite-first-live --research` option starts the existing bounded observer/writer for the committed session. Default remains off. No Episode creation, ring, periodic StateSnapshot, label, model, or policy code was added.

## Changed Files

Added `src/polymaker/research/capture.py`, `tests/test_research_causal_capture.py`, and this report. Modified `marketdata/service.py`, `userstream/client.py`, `state/tracker.py`, `execution/filltruth.py`, `execution/gateway.py`, `engine.py`, `favorite_live.py`, `cli.py`, and `docs/CURRENT_STATUS.md`. M4A.1 Review was not edited.

## Actual Causal Hook Map

| Existing file/function | Event family | Causal seam | Data copied | Clock captured | Research method called | Behavior impact |
|---|---|---|---|---|---|---|
| `marketdata/service.py:_connect_and_listen`, `_consume` | CONNECTION | link state mutation / reader enqueue | stream, state, subscriptions, generation | local receipt wall+monotonic; applied wall+monotonic | `CausalCapture.emit` → `ResearchObserver.observe` | none |
| `MarketDataService._on_book`, `_on_price_change`, `_on_last_trade`, `_on_tick_change` | BOOK, DELTA, TRADE, TICK | after parser and per-token application; trade before existing flow callback | actual token, full parsed levels/hash/tick, individual changes, print | source exchange where parsed; reader receipt; applied clock | same | none |
| `UserStream._connect_and_listen`, `_handle` | CONNECTION, receipt context | link state / raw frame delivery | stream, state, generation; receipt only forwarded to normalized events | local receipt wall+monotonic; applied clock | same | none |
| `Engine._refresh_book` | REST_BOOK | only after existing drift replacement | full fetched bids/asks, hash, tick, token, market generation, research replacement count | REST completion receipt; applied clock; no fabricated exchange clock | same | none |
| `ExecutionGateway.place`, `cancel*` | ORDER_SUBMIT/ACK, CANCEL_REQUEST/ACK | after preliminary guard before signing/POST; after result/exception; before cancel I/O and after result | quote, order IDs, scope, shared request ID, ACK/rejected/aborted/failed/unknown outcome | local intent/result clocks | same | none |
| `UserEventProcessor.register_order`, `on_order`, `quarantine_order` | ORDER_STATE | after owned registration, WS state mutation or quarantine | canonical owned ID, FIRST/PAIR role from registry source, remaining/matched state | WS receipt and applied clocks where available | same | none |
| `Engine._refresh_token_orders`, `_reconcile_loop` | ORDER_STATE | after existing REST open-order replacement; extra Research state reads are isolated | applied per-token order set, role, grace policy | applied clock; REST source | same | none |
| `UserEventProcessor.on_trade`, `execution/filltruth.py:reconcile_owned_order_once` | FILL_REVISION | after Fill Truth decision; REST receipt sampled after response | trade+maker key, role, status, current economic acceptance/quarantine/readiness/disposition | exchange occurrence, WS/REST receipt, applied knowledge clock | same | none |
| `Engine._recompute_locked` | BASELINE_DECISION, SAFETY | after pure evaluation, after `reconcile` creates the plan, and at later submission suppression/result seams | exact pure snapshot/memory/config, pure quote, final plan/no-op, later suppression/result and current safety facts | local applied clock | same | none |
| `Engine._favorite_finalize_orders`, `_invalidate_fill_truth`, `_reconcile_owned_order` | SAFETY | after existing finalization/readiness mutations or reconciliation result | first/pair terminal, fill-truth generation and outcome | local applied clock | same | none |
| `favorite_live.py:run_favorite_first_live`, `cli.py:favorite_first_live` | observer lifecycle | committed market startup and wrapper cleanup | explicit session ID and default-off switch | no live callback clock | constructs existing observer/writer | none when disabled |

## Causal Clock Semantics

`exchange_at_us` is parsed venue occurrence time and remains nullable. `received_at_us` is sampled at market WS reader enqueue, user WS frame delivery, or immediately after the existing REST trade/book response. The paired receipt monotonic value is preserved as `payload.received_monotonic_ns` where available. `available_at_us` and top-level `monotonic_ns` are sampled only when the applied fact reaches `CausalCapture.emit`; availability is clamped nondecreasing inside the admission lock. For buffered pre-ACK WS trades, original receipt clocks and user generation survive buffering, while the accepted Fill Truth revision is emitted only after owned registration. A late REST fill retains its exchange time and receives a new local knowledge time; no earlier event is rewritten. Process-created intents have no source exchange clock.

## Event Sequence Semantics

`CausalCapture` serializes sequence assignment, record validation and observer admission with a short in-process lock. Sequence increases strictly across concurrent market, user, engine and gateway sources. A failed validation/admission can leave a missing sequence; M4A.4 owns persisted GAP and manifest accounting. The emitter and observer contain Research exceptions. Writer I/O is never inside the callback.

## Connection Generation Semantics

Market WS uses its existing generation counter, incremented after each successful subscribe; user WS now has an observer-only generation counter. CONNECTION UP/DOWN events and subsequent WS records identify the stream epoch. REST_BOOK carries the market generation and a research-only per-token replacement sequence. Existing live `OrderBook.snapshot_generation` was not changed because engine freshness guards use it as the market-WS generation; the REST_BOOK event itself marks the replacement for research reconstruction.

## Market Data Capture

Applied full YES and NO token books, per-token deltas, existing trade prints and tick changes are observed. No `NO = 1 − YES` research book, feed, polling or new network request was added. The full parsed book and each delta are copied into validated immutable Research event payloads, so later replay can distinguish a full replacement from incremental mutation.

## REST Replacement Capture

The existing drift branch emits REST_BOOK only after `book.apply_snapshot` using the response's complete bids/asks/hash plus current tick and replacement count. The REST response completion is receipt time. No exchange timestamp is invented. Unchanged REST reads emit no replacement event.

## Owned Order Lifecycle Capture

Gateway submit and cancel request evidence is separate from result evidence. A preliminary guarded abort before the Research submit intent has no submit event; a later signing-stage guard abort is paired with an `ABORTED` result under the same request ID. A returned placement with order IDs is ACK; explicit empty result after POST is REJECTED; `PlacementUnknown` is UNKNOWN. Cancel exceptions are UNKNOWN, not proof of rejection. Registration and authoritative WS/REST order state are separate events. FIRST/PAIR role is copied from `owned_orders.source` only after registration; pre-registration submit intent has unknown role. Asset/all cancellations carry their actual scope when individual order IDs are unavailable.

## Fill Truth Capture

`UserEventProcessor` alone decides accepted economics, duplicate application, status advance, FAILED and quarantine. Research reads the result after each decision. Acceptance now uses the same applied-key, excluded-key and effective-status predicate as `economic_fills_for_order`; FAILED is excluded. `(trade_id, maker_order_id)` is the canonical key; repeated status evidence is retained as revisions without new economic application. REST MINED/CONFIRMED discoveries use the processor's existing synthesized MATCHED acceptance path and emit knowledge-time revisions at discovery. Existing `favorite_first_leg`/`favorite_pair_leg` registry sources supply role. Each revision copies current Engine recovery, uncertain-order and quarantine guards as `fill_truth_ready`; standalone processors with no Engine readiness provider record null rather than guessing.

## Strategy / Effective Quote Capture

The pure FavoriteFirst snapshot, pre-evaluation memory, config, decision state and quote are captured after the existing `evaluate_favorite` call. `phase=ORDER_PLAN, stage=RECONCILED_PLAN` records the actual `reconcile` plan, including its target, placements, cancellations and no-op state. Separate `phase=EFFECTIVE_QUOTE` records identify the final no-op, cancel-only/uncertain, market-readiness/load-shed suppression, cancel-replace deferral or gateway placement result. The gateway's ORDER_SUBMIT/ACK events remain authoritative for the exchange attempt and acknowledgement; a reconciled plan alone is not proof of a POST. Existing sparse decision journal behavior is unchanged.

## Safety / Finalization Capture

SAFETY records current gate facts during FavoriteFirst recompute and existing first-finalized, pair-terminal, fill-truth invalidation and reconciliation results. These copy existing state; Research has no second safety machine. Repeated recompute safety facts remain repeated observations, not invented transitions.

## Provenance Handling

`Journal.write` writes line-buffered JSON but exposes no stable byte offset, record length, rotation identity or checksum. Attaching a claimed byte reference would be false; `source_ref` is therefore null. Research events contain sanitized applied payloads needed for reconstruction. Raw byte-offset provenance is deferred without redesigning the journal or modifying its trading path.

## Failure Isolation

`CausalCapture.emit` catches validation and observer exceptions; the existing observer also contains its own failures. Research-only market timestamp conversion and Research-only StateStore reads/payload preparation are now separately contained at their call sites, so they cannot interrupt market wake, REST resync or Fill Truth processing. No Research call changes a quote, order state or Fill Truth result. The optional writer runs on its existing thread. Gateway request/result hooks make no new exchange calls. Queue overflow remains nonblocking and is counted by M4A.1; durable GAP handling is deferred.

## Research Disabled Behavior

The default CLI path constructs no capture/writer. Engine passes `None` to its existing services, market frames retain the original three-field queue item, and no extra wall-clock sample or payload validation runs. An integration comparison of real paper-engine recomputes with no capture versus a disabled capture produced identical order intents. Research hook failure injection on a real recompute also preserved intents.

## Tests Added

`tests/test_research_causal_capture.py`: Engine constructor wiring; concurrent sequence admission; real per-token book/delta capture and reconnect generation; late REST fill knowledge time, sequential MATCHED/MINED/CONFIRMED revisions, FIRST/PAIR role, no duplicate economic quantity; pre-ACK receipt preservation; submit/ACK/cancel separation; REST replacement-only evidence; disabled engine parity and observation failure isolation; separate pure/effective FavoriteFirst evidence.

## Tests Executed

- `.venv/bin/pytest -q tests/test_research_causal_capture.py tests/test_engine.py tests/test_filltruth.py tests/test_marketdata.py tests/test_favorite_live.py`
- `.venv/bin/pytest -q`
- Targeted `.venv/bin/ruff check` on all modified Python files and Research package
- `.venv/bin/mypy src/polymaker/research`
- Targeted mypy over all modified production modules
- `git diff --check`

## Test Results

Focused tests and full suite passed; the final full-suite count and command outputs are recorded in the final validation section below. Targeted Ruff and Research-package mypy are clean. Targeted mypy over all modified production modules still reports 13 diagnostics in existing engine/tracker/gateway/filltruth/favorite_live code outside the new Research package; these are repository baseline type debt and were not repaired. No new Research-source diagnostic remains.

## Architecture Deviations

NONE. The `--research` flag is the narrow default-off live enablement seam; no M4A.3 lifecycle or M4A.4 durability behavior was added. REST replacement uses a research-only replacement sequence rather than changing the live OrderBook freshness generation.

## Known Limitations / Deferred Work

- M4A.3: Episode identity/start, pre-fill ring, periodic/material StateSnapshots and T+300 observation lifecycle are absent.
- M4A.4: persisted GAP recovery, manifest/checksum/fsync and crash audit are absent. Sequence gaps and queue health counters must be treated as incomplete evidence.
- M4A.5: collection/performance audit; the callback p99 and loop-lag targets have not been measured. Full-book validation under the admission lock needs measurement before a collection GO decision.
- Remaining Reviewer P2: applied DELTA/TICK source metadata, real reconnect-loop coverage, formal callback-latency measurement, and writer-close robustness remain deferred. Tests directly required for the P0/P1 fixes were added.
- Raw journal byte offsets cannot be proven with the current `Journal` interface; `source_ref` stays null.
- The existing gateway's scope-only asset/all cancel calls cannot identify every affected order at request time. Later authoritative order snapshots carry known state.
- Market/user source frames without a parsed venue timestamp retain null exchange time. No source timestamp is inferred from the journal.

## Safety / Live Behavior Impact

Live trading semantics changed: **NO**. Default-off decisions, intents, state transitions and external calls are unchanged. When explicitly enabled, Research only copies evidence and writes it through the existing isolated observer/writer; it has no execution authority. No settlement, reconciliation policy, wallet/auth, position sizing, inventory control or strategy evaluation rule changed.

## Git Diff Summary

M4A.2 remains an uncommitted working-tree implementation against `830240a`; the repair modifies only existing M4A.2 integration/Research code, its focused test file, this report and `CURRENT_STATUS.md`. The independent Review report was not modified by the Worker. See the final working-tree `git diff --stat` for cumulative counts.

## Final Validation

- Final new Research file after repair: 16 passed.
- M4A.1 skeleton test: 8 passed.
- Full `.venv/bin/pytest -q`: **561 passed, 2 skipped** in 7.77s.
- Targeted Ruff on all modified Python files and Research package: clean.
- Targeted mypy on Research package: clean, 5 source files.
- Repair-targeted mypy on Research, market data, tracker, engine and gateway: 9 diagnostics on existing non-Research expressions in tracker/gateway/engine; no new repair diagnostic. The broader pre-repair Reviewer check reported 13 diagnostics across additional non-Research modules.
- `git diff --check`: clean.

## Handoff to Reviewer

Independently re-review the six repaired P0/P1 findings and tests below. Treat output as causal raw event evidence only; no Episode or durable dataset certification is claimed. Next authorized action is independent M4A.2 re-review only.

## Review Repair — P0/P1

### P0-A Non-finite Timestamp Isolation

Reviewer finding: a parser-accepted non-finite market timestamp raised before the normal book wake.
Root cause: `exchange_us` ran outside the observer's exception boundary and called `int(inf)`.
Repair: non-finite or overflowing source timestamps map to null exchange time; `_observe` isolates only Research conversion/emission, preserving receipt and local availability clocks. The live parser and applied mutation are unchanged.
Tests: parametrized NaN, Infinity and negative Infinity applied-book wake test; all passed.

### P0-B REST Resync Research Failure Isolation

Reviewer finding: Research-only owned-order reads interrupted the second token's REST resync.
Root cause: `_research_order_snapshot` read StateStore outside a Research-specific guard; tracker observation had the same pattern.
Repair: order snapshot and tracker-only order/fill payload preparation are individually guarded and counted as Research failures. Authoritative resync/mutation exceptions remain outside those guards.
Tests: injected failing owned-order lookup during actual two-token `_refresh_token_orders`; both replacements completed.

### P1-A Fill Accepted Semantics

Reviewer finding: FAILED evidence said `accepted=True` while Fill Truth's economic list excluded it.
Root cause: the old payload checked applied/quarantined keys but omitted effective FAILED status.
Repair: accepted follows the processor's economic-fill predicate; quarantine evidence reflects key/order quarantine. No fill application or reversal rule changed.
Tests: MATCHED/MINED/CONFIRMED/FAILED progression and a contradictory quarantined fill; FAILED and quarantined revisions are unaccepted.

### P1-B Effective Quote Seam

Reviewer finding: `EFFECTIVE_QUOTE` was emitted from pre-reconcile `desired`.
Root cause: the hook preceded the final reconciler plan and later operational gates.
Repair: a separate `ORDER_PLAN` phase copies target, actual `to_place`/`to_cancel` and no-op. Only terminal no-op/suppression/defer/cancel/placement-outcome evidence uses the `EFFECTIVE_QUOTE` phase. ORDER_SUBMIT/ACK still decide whether an exchange action was attempted/acknowledged.
Tests: FavoriteFirst plan creation then non-null target/no-op, and a final submit-guard rejection with no ORDER_SUBMIT and null effective quote.

### P1-C Operation Request Correlation

Reviewer finding: request/result events could not be paired under concurrent operations.
Root cause: no per-operation Research correlation identity existed.
Repair: when Research is enabled, each submit/cancel operation gets a UUID request ID shared by intent and result, including aborted, failed and unknown paths. It is separate from exchange order/trade identity and is not allocated on the disabled path.
Tests: paper submit/cancel, live POST success/unknown, pre-POST signing failure and cancel success/unknown; pair IDs match and five live operations are distinct.

### P1-D Fill Truth Readiness

Reviewer finding: revisions lacked per-revision `fill_truth_ready`.
Root cause: readiness existed in Engine gates but was not copied at the Fill Truth observation seam.
Repair: Engine passes a read-only current-guard provider to the processor only when Research is enabled. Each revision stores its current result; standalone processors without a provider use null rather than infer from status. Earlier records remain immutable.
Tests: injected readiness transition false→true→false across four revisions, with earlier values preserved.

### Deferred P2

Source metadata, reconnect-loop coverage beyond the repaired paths, formal performance measurement and writer-close robustness remain deferred as in the independent Review. No M4A.3 or broad M4A.4 work was added.

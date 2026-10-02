# M4A Research Telemetry — Technical Architecture

Status: M4A.1–M4A.5 implementation PASS and checkpointed; acceptance preflight BLOCKED. Acceptance Evidence Revision below authorizes **M4A-A1 only** as the next Worker slice. No live run or M4B authorization.
Original Git base: `fff2842` (`m3c-settlement-live-pass`).
Acceptance revision Git base: `m4a5-pass` / `4d75a19bf4f4e0a967d711cb57f651453f2fdd30`.
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

Use `research_data/<dataset_id>/<session_id>/{manifest.json,events.jsonl,episodes.jsonl,states.jsonl,quality.json}`. `research_data/` must be Git-ignored in the Worker slice that first writes it. Session/dataset IDs, source code commit/config hash (effective FavoriteFirst defaults included), schema/payload versions, selected market/tokens, start/end, file hashes, event watermark, gap counts and close status belong in manifest. Do not capture private keys, wallet credentials, auth headers or secret config fields. The original design assigned `quality.json` to audit output; the accepted M4A.4 implementation uses it for immutable persistence-health evidence, with the manifest as the sole COMPLETE certificate. M4A.5 emits separate `audit.json` and `audit_report.md`. This revision adopts that reviewed separation; the new acceptance evidence below has its own artifact.

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

The original architecture authorized M4A.1 first; that historical sequence has completed through M4A.5. The current journal's missing byte offsets keep raw SourceRef optional until independently verifiable; self-contained sanitized event payloads remain required. Continuous multi-market collection, BTC feed, settlement attribution, exact M4D model and any live-control architecture remain deferred. The revision below defines the current acceptance work and supersedes the original next-action text.

## Acceptance Evidence Revision

Revision: AE-1, 2026-10-02.
Reason: **Real-Evidence Acceptance Preflight BLOCKED**.
Git base: `m4a5-pass` / `4d75a19bf4f4e0a967d711cb57f651453f2fdd30`.
Verified findings: `docs/reports/M4A_ACCEPTANCE_PREFLIGHT.md`.

This is post-implementation M4A acceptance hardening. M4A.1–M4A.5 PASS reports and tags retain their historical meaning. M4A.5 correctly refuses unsupported evidence, but the current live integration has a config-identity mismatch and lacks verifiable acceptance inputs. Fixing these issues does not implement M4B, change FavoriteFirst, or authorize trading. Only documentation changed in this Architect session.

### A. One canonical Research configuration identity

**Verified cause.** `favorite_live.effective_config` removes the deprecated, unused `probation_passive_offset_ticks` and renames `probation_max_s` to `first_leg_risk_window_s`. The wrapper hashes this public object using compact, sorted JSON. `Engine.attach_favorite_first` independently hashes `asdict(config)`, retaining both old keys and default JSON whitespace. Both use SHA-256, but on different bytes. `EpisodeLifecycle` copies the Engine digest into every Episode; `_episode_checks` correctly rejects its inequality with `manifest.strategy_config_id`. Neither relaxing this check nor rewriting captured IDs is permitted.

**Canonical contract `favorite-first-effective-v1`.** A pure helper in `research/identity.py` projects the **actual FavoriteFirstConfig instance passed to Engine**, including inherited defaults, to the following object:

| Canonical fields | Normalized type / source |
|---|---|
| `max_pair_loss_usd`, `normal_reprice_min_s`, `urgent_reprice_min_s`, `first_leg_qty`, `entry_start_s`, `last_new_entry_s`, `favorite_min`, `favorite_max`, `favorite_confirm_s`, `trailing_arm_usd`, `trailing_drawdown_fraction`, `forced_pair_start_s`, `hard_order_cutoff_s` | Finite float, from the same named dataclass fields |
| `first_leg_risk_window_s` | Finite float, from `probation_max_s` |
| `runner_passive_offset_ticks` | Integer; reject bool and non-integral values |

Exclude only `probation_passive_offset_ticks`: current code uses it for constructor validation but never for a trading decision. Continue validating the actual dataclass normally. Its future behavioral use, another strategy field, or another normalization requires a new identity contract version; do not silently omit new fields. Exclude credentials, paths, run IDs, clocks, current inventory, observed market prices/ticks and host details. Persist market context separately. All currently active strategy values are retained. Negative zero is normalized to positive zero; reject nonfinite or unexpected fields/types.

Canonical bytes are UTF-8 `json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)`, with declared float fields converted to float and the one integer retained as integer. `config_sha256` is the lowercase SHA-256 hex digest of exactly these bytes. This is a versioned Python/JSON contract, not an implicit cross-language floating-point standard.

The wrapper creates one immutable Research session context containing dataset/session IDs, normalization version, normalized object and digest at initialization. Pass this context to Engine; Engine passes the same identity to EpisodeLifecycle. Neither downstream component reconstructs or independently rehashes another config object. If a direct Engine caller enables Research, it must supply a context bound to its actual strategy config or use the same helper once at that construction boundary; it may not supply an arbitrary claimed digest. Identity/setup failure disables/degrades Research with explicit evidence and never changes live arming or strategy results. The auditor independently verifies the persisted normalized object using the same canonical encoder; it does not repair it.

For new live sessions:

```text
manifest.strategy_config_id == manifest.config_sha256
                            == every Episode.strategy_config_id
                            == acceptance_evidence.strategy_config_id
```

StateSnapshot has no direct config-ID field. Its validated dataset/session/Episode association resolves to that same identity; adding a redundant field is unnecessary. ResearchEvent similarly resolves through its session. No mutable configuration may drift under one session identity.

This identity covers FavoriteFirst strategy semantics. The acceptance envelope also records a separate `runtime_settings_sha256` over a sanitized, fixed allowlist of effective Engine scheduling, risk and execution guard settings, and `research_settings_sha256` over ResearchConfig excluding output paths. These are experiment bindings, not alternative strategy IDs. Include all numeric/bool Engine, Risk and Execution model settings used by the live run, including heartbeat, debounce, reconciliation, cutoffs, risk ceilings and rate/post-only settings; reject unexpected additions pending a schema update. Never serialize `Config`, wallet credentials, proxy URLs, environment variables, or Secrets wholesale. Market-dependent minimum quantity/tick remain market evidence, not config identity.

**Compatibility.** A1 needs no raw schema change: emit existing manifest/Episode v1 fields consistently. Existing v1 data remains immutable and is audited under its original contract; mismatched IDs continue to FAIL. Legacy synthetic fixtures with internally consistent string IDs may still test legacy integrity, but do not gain acceptance provenance. A2 adds a manifest version described below; no automatic upgrade, ID rewrite or retrospective LIVE attestation for v1 sessions. New integration tests must exercise wrapper context → Engine attachment → lifecycle → real writer → auditor rather than supplying matching fixture IDs manually.

### B. Evidence ownership, trust boundary and artifact

Choose **one new artifact name and envelope**, `acceptance_evidence.json`, version 1, with discriminated producer kinds `LIVE_SESSION` and `CONTROLLED_COMPARISON`. The two kinds occupy different locations and cannot substitute for one another:

```text
research_data/m4a/<session_id>/acceptance_evidence.json
research_acceptance/<comparison_id>/acceptance_evidence.json
```

The first is raw runtime evidence finalized with its session. The second is raw measurement evidence from the offline engineering runner and references already closed source sessions. Neither is an audit verdict; `research-audit` still writes derived reports outside raw evidence. `quality.json` stays persistence health only. Generated `research_acceptance/` is Git-ignored; reference hashes and results belong in durable reports, not bulk runtime data in Git.

| Evidence | Producer / trusted source | Persistence and schema | Auditor consumer / gate |
|---|---|---|---|
| LIVE attestation | Normal FavoriteFirst wrapper after existing preflight/arm, actual Engine and gateway mode, gateway results, owned registry and Fill Truth | Session acceptance v1 runtime context + causal event references, sealed by manifest v2 | Verify context and the order/fill chain; `live_attestation` |
| Callback duration | Research observation scopes on the actual live process; `perf_counter_ns` | Session acceptance v1 bounded histograms/counts; comparison ON run supplies recorded-peak measurement too | Recompute quantiles and completeness; `callback_latency` |
| Incremental loop lag | Reviewed offline comparison runner; identical recorded traffic, same environment, OFF/ON | Comparison acceptance v1 paired deadline/lag arrays, source hashes, experiment settings | Recompute paired increments and p99; `incremental_loop_lag` |
| Isolation | Same runner using actual Engine/policy paths with a non-network execution sink and controlled faults | Comparison acceptance v1 canonical decision/intent traces and case outcomes | Independently compare traces and required fault cases; `research_isolation` |

**Practical trust standard.** Trust the reviewed runtime/runner executing the declared source build under the operator's custody, with immutable original artifacts. Verify schema, producer kind, code/config/environment association, input hashes, measured counts and reinforcing execution facts. A typed `LIVE` label alone is insufficient. CLI assertion files, report summaries, file names and order-ID shape are not attestation. No `--live`, `--trusted`, test-fixture marker or caller p99 override can satisfy a gate.

This is not cryptographic proof against a machine/file owner who can replace the producer, fabricate all raw orders/measurements and recompute every hash. SHA-256 proves binding and accidental-change detection, not origin. That threat requires a separate signing/custody architecture and is outside M4A. Independent acceptance review checks the actual authorized invocation, build and artifact custody; record the resulting evidence hashes in the smoke/final review. Supplying an optional audit assertion without altering genuine raw evidence cannot promote a gate.

Common required envelope fields: `schema_version=1`, `producer_kind`, `producer_version`, `code_commit`, `source_tree_clean`, dataset/session or comparison identity, canonical strategy identity and normalization version, runtime/Research settings objects and hashes, start/end wall and monotonic clocks, measurement protocol version, clock implementation/resolution, sanitized execution-environment fingerprint, completion state and explicit missing/dropped/error counts. Environment records Python version, OS/CPU class, actual event-loop implementation and deployment profile; no host secrets or arbitrary environment dump. Collect Git/environment facts on the writer/runner side, outside live callbacks. Unknown or dirty provenance cannot establish acceptance; it must not block live trading.

Session envelope additionally contains runtime attestation facts, callback metrics, source event/Episode/state hashes and event watermark. It must **not** hash its final manifest, avoiding a circular hash dependency. Comparison envelope binds each input's final manifest and acceptance hashes, selected source sequence/time window, fixed workload definition/hash, all repetitions and raw lag/trace evidence. It never claims to be a real trading session or count as an Episode.

**Manifest evolution.** Define explicit DatasetManifest v2 while retaining the strict v1 parser. V2 preserves all existing fields and adds `config_identity_version="favorite-first-effective-v1"` and `acceptance_evidence={path:"acceptance_evidence.json", schema_version:1, sha256:<digest>}`. The descriptor may be null in OPEN/INCOMPLETE, but is required in COMPLETE. The original `file_sha256` inventory remains the three JSONL files; the evidence file has its own required descriptor and does not alter JSONL record counts. ResearchEvent, Episode, StateSnapshot and quality stay at their existing structural versions. Acceptance-specific SAFETY payloads carry `acceptance_version=1`; no economic/lifecycle state is redefined. Unsupported manifest/evidence versions fail closed. The old auditor must reject v2 as unsupported rather than silently ignore acceptance metadata.

Finalization order: close admission and preserve the existing causal seal/drain contract; finish accepted writes; freeze measurements; flush/fsync JSONL; calculate source hashes; write/fsync/atomically replace acceptance evidence off the live loop; persist quality; publish the final manifest using the reviewed M4A.4 finalization protocol. Evidence persistence failure forbids manifest COMPLETE for v2. A durably recorded *insufficient measurement* may coexist with raw COMPLETE, but its acceptance gate stays UNESTABLISHED. Never reopen admission to record a late performance or censor result. Missing/in-flight samples at the seal must be explicitly counted, not omitted. Preserve the documented final-rename durability limit; this revision does not promise a stronger crash protocol.

### C. Trusted LIVE provenance and eligible FIRST fills

The runtime context is created by `favorite-first-live`, not read from an audit file. After the existing preflight succeeds and the existing arm transition occurs, record a Research-only SAFETY observation `transition="ACCEPTANCE_ARMED"`, with acceptance version, entrypoint, Engine `paper` value, actual gateway `paper` value/backend kind, production venue identity, chain ID, canonical config ID and non-secret account identifier. Record only after the authoritative transition; no new condition may alter that transition. Failure to observe it invalidates acceptance evidence, not trading. Preflight-only runs never produce an armed attestation.

Use `account_id_sha256 = SHA256(canonical JSON [chain_id, lowercase public funder address])`; private key, signer secret, auth headers, API credentials/passphrases and signed request payloads never enter Research. The public identifier is for association, not proof by itself. Read public gateway properties after its existing connect path; introduce only a read-only primitive accessor if required. No new network request for Research attestation.

The session envelope seals `entrypoint`, actual modes/backend, account hash, armed-event sequence/availability, selected condition/tokens and source build. It also lists relevant order/fill event references; the auditor reconstructs them independently. The normal runtime must mark factory/replay/test substitutions as `SYNTHETIC` or `CONTROLLED_COMPARISON`; dependency-injected test engines cannot emit a qualifying LIVE context. Tests may construct malformed raw fixtures to exercise validation, but there is no production trust override.

LIVE verification requires all of:

1. Manifest v2 COMPLETE, valid bound envelope and raw integrity PASS, known clean reviewed runtime build, matching identities/settings, `entrypoint=favorite-first-live`, actual Engine and gateway `paper=False`, and supported production CLOB backend/endpoint identity. Endpoint names carry no credentials; v1 contract recognizes the configured production `https://clob.polymarket.com`, not an arbitrary URL labelled production.
2. A causal armed event matching the envelope and preceding the relevant order attempt.
3. Existing gateway `ORDER_SUBMIT` with `paper=False` and a correlated `ORDER_ACK` with `outcome=ACK` and the returned nonempty order ID; a matching owned FIRST/PAIR registration and quote/token association. The paper gateway's fabricated IDs/`paper=True` path cannot qualify. Order-ID syntax alone never qualifies.
4. Accepted positive BUY FIRST `FILL_REVISION` from existing USER_WS or REST Fill Truth, linked by `(trade_id,maker_order_id)` to that owned live order, not failed/quarantined; deterministic Episode identity and causal start; final eligible Episode and reconciliation status under the existing auditor.

Partial/late accepted fills use the same identity once, not extra Episodes. If an UNKNOWN placement is later recovered but the required trusted ACK/receipt chain is absent, its telemetry remains auditable and its LIVE eligibility is UNESTABLISHED under this minimum contract; do not invent an ACK. A no-fill run can verify runtime mode and produce useful metrics but supplies zero eligible FIRST-fill Episodes. Paper/synthetic runs are explicitly ineligible. Conflicting mode/identity or a fabricated/inconsistent chain FAILS provenance; absent evidence is UNESTABLISHED. Nothing changes Fill Truth economic acceptance or historical state.

The auditor never uses current venue state to repair historical evidence. Normal `research-audit` callers cannot change these results by supplying LIVE labels. A real run on the current `m4a5-pass` cannot satisfy this revised chain without A1–A3.

### D. Research callback latency measurement

Measure the complete **outer synchronous Research observation scope** at each existing integration seam: start immediately before Research-specific copying/projection begins, stop after schema validation, causal/admission lock work, synchronous lifecycle listeners/snapshots and failure accounting return. This includes payload work currently evaluated before `CausalCapture.emit`; timing only `queue.put_nowait` or the inner emit is insufficient. Exclude ordinary trading computation and awaited venue operations. Disk drain/flush/fsync belongs to the writer and is excluded. Measure scheduled Research snapshot callbacks separately as well as event callbacks; nested Research scopes contribute one outer duration, not duplicate pooled samples.

Use `time.perf_counter_ns()` at entry/exit, including failed/rejected attempts. Fix the small scope-label set in the protocol: market observation, user observation, order/fill observation, decision/safety observation, gateway observation, scheduled snapshot. Instrument all invocations (sampling stride 1); no random or only-successful sampling. The timer's final O(1) metric update is outside its own duration; disclose this and include timer/aggregation overhead in the controlled ON experiment, with no guessed subtraction.

Maintain bounded integer histograms: 1-microsecond bins from 0 inclusive to 10 milliseconds exclusive plus overflow, exact sample counts and maximum nanoseconds, per scope and aggregate. A bin reports inclusive integer-nanosecond bounds `[1000*k, 1000*(k+1)-1]`, not a falsely exact percentile; report nearest-rank p50/p95/p99 bounds and exact max. Compute rank `ceil(p*N/100)`. The 1ms threshold aligns with a bin boundary: PASS requires the p99 bin's inclusive upper bound <1,000,000ns; p99 at/above that boundary FAILS. Overflow cannot hide a slow percentile. No float rounding at the threshold. Retain attempted/completed/timed/dropped/invalid counts, measurement interval and zero-sample scopes explicitly.

Memory and metric update cost are fixed; no sorting, allocation proportional to traffic, disk/network or blocking queue operation inside callbacks. Freeze histograms consistently with the session seal. A1's identity fix must not touch this behavior. A2 must test that in-flight spans are either included before freeze or explicitly mark measurement incomplete; a stale clean metric snapshot is forbidden. A metric fault must be isolated and make measurement unavailable, never alter a quote or authoritative state.

A session callback gate requires at least **1,000 complete event-callback samples** spanning at least **30 connected seconds** during the armed interval, no missing/invalid measurement samples and p99 <1ms. Report other scopes even with fewer samples; they are not falsely certified individually. Scheduled-snapshot cost is reported separately and covered by loop-lag/isolation checks. Threshold failures with sufficient data are FAIL; missing/short/invalid measurements are UNESTABLISHED. Full acceptance additionally requires the ON measurement on the deterministically selected recorded peak workload below to pass the same p99 bound. This avoids a quiet whole-session aggregate standing in for peak traffic.

### E. Incremental loop lag: controlled OFF/ON experiment

Incremental means **added event-loop scheduling delay from enabling the complete Research path** under the same input workload and host/build/settings. It is not marketdata queue processing time, callback duration, or the difference between unrelated live sessions. No real-money OFF/ON A/B run is required or authorized.

Add one offline engineering runner, `polymaker research-acceptance-check <session-or-dataset> --output-dir research_acceptance`. It consumes closed raw sessions, runs the reviewed live observation/Engine seams with an isolated non-network gateway sink, and emits a `CONTROLLED_COMPARISON` envelope. It receives no wallet credentials and must reject attempted network/trading operations. This is an engineering workload replay, not M4C execution simulation, policy optimization, or profitability replay.

Deterministic source selection: from integrity-clean, LIVE-attested source sessions, choose the contiguous **30-second connected window with most persisted MARKET_WS/USER_WS input observations**, breaking ties by session ID then earliest start. Define windows on a 1-second grid within the captured source interval; record selected source sequences and counts. Include all same-condition source book/delta/tick/connection observations in that window and the prior full-book/tick prefix needed for valid initialization. The dataset auditor recomputes selection over every intended collection session; one easy session cannot stand in for a busier available window. At least 1,000 event-callback invocations are required. This establishes performance on the collection's recorded peak, not on all possible future traffic.

The runner drives actual market mutation and observation code with these inputs at recorded relative monotonic intervals. Its deterministic owned-order/fill, venue-response and clock scenarios are explicitly scripted and identical in OFF/ON, with a fixed initial state. Observed baseline decisions/order intents are **outputs to compare**, never injected to force equality. Where a sanitized recording cannot reconstruct an external input, use a disclosed fixed engineering scenario or return UNESTABLISHED; do not claim exact historical trading reconstruction. Synthetic controls never count as LIVE collection evidence. Replay ingress is scheduled at absolute offsets, not slowed to match processing capacity; record intended/actual dispatch, counts and any drops.

Measurement protocol:

1. Use the same Python/event-loop implementation, CPU/OS deployment profile, canonical strategy/runtime settings and reviewed runtime code for both arms. OFF bypasses Research; ON includes real capture, lifecycle, measurement, bounded queue, writer/filesystem and finalization. No different strategy/settings between arms. Record environment and Research settings so applicability can be checked.
2. Each pair uses the same five-second warm-up followed by the 30-second measured traffic window; warm-up is excluded explicitly. Run **three pairs**, arm order OFF/ON, ON/OFF, OFF/ON. Do not discard a slow valid repetition. Execution is sequential under comparable host load, with a fresh isolated state per arm; record failures and retry history rather than selecting favorable results.
3. Run a lightweight probe on the Engine loop at absolute 10ms deadlines. For slot `i`, `lag_i = max(0, actual_resume_ns - scheduled_deadline_ns)`. Record every deadline, including overdue deadlines at actual resumption; do not skip late slots or reset the grid. Keep arrays bounded to the configured window. Use the same probe in both arms.
4. Pair by repetition/workload-relative slot. `increment_i = max(0, lag_ON_i - lag_OFF_i)`. Compute nearest-rank p50/p95/p99/max from these integer-nanosecond differences. Do **not** subtract the two separately computed p99 values. Require all 3,000 slots per arm per pair; dropped inputs/samples, environment mismatch or unfinished arms are UNESTABLISHED. A bounded 120s wall timeout per arm ends a stalled run explicitly; it cannot certify success.
5. PASS requires each valid pair's incremental p99 <=5,000,000ns. Persist OFF/ON arrays and summaries, pooled diagnostics, dispatch statistics and ON callback histograms in the comparison envelope; the auditor recalculates all gates. ON callback p99 on this peak window must also be <1ms. A measured breach FAILS, not UNESTABLISHED.

Live session metadata references its environment but does not claim a per-session counterfactual OFF baseline. The comparison certificate applies only to matching code, strategy/runtime/Research settings and deployment profile, and to workloads no busier than the validated recorded window under this protocol. Different deployment settings/build or a newly busier source requires another comparison. Historical audit reads existing evidence and never reruns a wall-clock benchmark implicitly, preserving deterministic audit results.

### F. Isolation acceptance without real-money replication

The architecture and existing unit/fault tests are prerequisite structural evidence: Research owns no trading authority, failures are caught, admission is bounded and disk work is off the critical path. Preserve those tests. Final engineering isolation requires a persisted run of the reviewed comparison suite, not a caller's equality assertion or a textual pytest count alone.

For fixed inputs/logical clocks and identical scripted gateway responses, exercise actual FavoriteFirst/Engine decision and order-intent paths in OFF, normal ON and ON with controlled faults. The comparison runner's independent trace sink exists in **all** arms outside Research, so disabling or breaking Research cannot erase the reference. Compare canonical ordered traces: input/decision index, policy state/trigger, target/effective quote, ordered place/cancel intent with role/token/side/price/quantity, and resulting authoritative fill/order/inventory state. Normalize only declared nondeterminism (e.g. map generated request/order IDs consistently by creation index and preserve all uses); do not omit prices, quantities, intents, time-dependent decisions or errors. Persist full bounded canonical traces and hashes; the auditor computes equality instead of trusting `unchanged=true`.

Required cases cover FIRST and partial fills, PairStop/Runner/terminal transition, cancellation/late reconciliation, and these Research faults: observer/serialization exception, queue exhaustion, writer failure, and writer disk stall. Also preserve startup-disk and close/admission regressions. Fault injection is offline only and targets Research I/O/observer seams, never a real venue or live fill processor. The writer-stall case must show the workload/intent sink can finish while Research is held, with explicit degradation and bounded close after release/timeout. A sink mismatch, exception reaching the trading path, or live-path deadlock FAILS isolation. Missing required cases or truncated trace evidence is UNESTABLISHED.

The deterministic isolation mode fixes logical inputs/time; the real-time loop-lag mode measures scheduling separately. No demand for two independently traded markets to produce identical fills. Existing corner-case fixtures can supply rare scenarios not seen live, but may establish only engineering isolation. The dataset's verified real Episode count still comes exclusively from section C.

### G. Auditor authority and acceptance failure semantics

A3 extends `research-audit` to discover the runtime envelope via manifest v2 and accept a locator `--engineering-evidence-dir` for comparison bundles. A locator selects immutable evidence; it does not set a gate. Validate producer kind/version, all input hashes, source selection, original session LIVE chain, clean build/settings/environment, arrays/counts and canonical traces before recomputing metrics. Unsupported/missing contracts cannot establish a gate. The optional old `--real-sessions-file` and `--timing-file` remain informational or may be deprecated; never reinterpret them as trusted inputs.

| Condition | Required gate/result |
|---|---|
| Config/identity mismatch, malformed or hash-inconsistent required raw evidence, known in-session loss | Integrity FAIL; no collection eligibility |
| V1 session without the new envelope; no armed/live execution chain; no usable benchmark | Relevant acceptance gate UNESTABLISHED; do not mutate or upgrade the source |
| Explicit paper/synthetic origin | LIVE ineligible; zero eligible real Episodes, even if integrity is PASS |
| Contradictory LIVE claim/modes/receipts or impossible evidence | Provenance FAIL |
| Callback p99 >=1ms with complete sufficient samples | Callback latency FAIL |
| Incremental loop-lag p99 >5ms in any complete pair | Loop lag FAIL |
| Insufficient samples, missing required cases, missing measurement data or unsupported environment association | Relevant engineering gate UNESTABLISHED |
| Different decision/order-intent/authoritative-state traces or propagated Research failure | Isolation FAIL |
| Verified clean real collection below 20 unique eligible Episodes or 3 UTC market-days | Collection INSUFFICIENT; integrity need not fail |
| All required evidence established and thresholds met | Machine readiness PASS/READY, subject to independent final acceptance and explicit M4B authorization |

Keep separate integrity, LIVE eligibility, collection sufficiency, callback latency, loop lag, isolation and overall readiness fields. Any required FAIL implies overall FAIL/NOT READY. Any required UNESTABLISHED or insufficient collection implies INCOMPLETE/NOT READY. Collection counts only independently validated COMPLETE, Fill-Truth-COMPLETE, quality-clean Episodes in clean LIVE sessions; deduplicate by deterministic Episode ID, with duplicate/conflicting representations diagnosed rather than silently double-counted. UTC market-day continues to derive from `market_start_us`. Snapshot coverage >=99% and no unexplained gap >2s remain unchanged. Report excluded counts and evidence reasons. A dataset with unaudited/damaged candidate sessions cannot be declared clean by skipping them.

All evidence processing is offline/read-only; normal auditing needs no CLOB/Data API/network request. Validate paths remain within their declared evidence roots. Diagnostics redact secrets and remain bounded. Runtime and comparison measurement failure affects Research evidence only; it may not suppress submit/cancel, modify safety guards, change Fill Truth or interfere with settlement. No automatic approval of M4B or Dynamic Exit edge follows from a numerical report.

### H. Minimal slices, files, tests and reviewed baselines

| Slice | Authorized scope when reached / expected files | Required handoff gate |
|---|---|---|
| **M4A-A1 — Canonical Config Identity Repair** | New pure `research/identity.py`; narrow `favorite_live.py` and `engine.py` context plumbing; lifecycle consumes the existing ID; focused `tests/test_research_identity.py` and wrapper/Engine/writer/audit integration regressions | Reproduce the two exact old hashes, then prove every emitted Episode matches the actual manifest; active parameter change changes identity; type/alias normalization deterministic; mismatched artifacts still FAIL; no strategy/guard/price/quantity change |
| **M4A-A2 — Runtime and Controlled Acceptance Evidence** | New `research/acceptance.py` for strict envelope/context/metrics and `research/acceptance_harness.py` for the offline runner; `research/schema.py`, writer, observer/capture/lifecycle and narrow timing/provenance hooks in wrapper, Engine, gateway, tracker, marketdata and userstream; CLI runner and `.gitignore`; `tests/test_research_acceptance.py` and relevant integration tests | V2 evidence written by real writer lifecycle, metric/seal consistency, bounded overhead/memory, mode/receipt chain, no test override for LIVE, no secrets; deterministic OFF/ON/fault trace and quantile logic tests; no network/real orders in harness; freeze and test schema contract for A3 |
| **M4A-A3 — Acceptance Evidence Auditor Integration** | `research/audit.py`, CLI and audit tests; schema readers shared with A2 | V1 compatibility without retrofit; v2 hashes/identities/attestation; forged caller claims fail to promote gates; exact 1ms/5ms/99% boundaries; insufficient/invalid evidence and synthetic collections fail closed; recompute comparison traces, sample quantiles and peak selection; read-only deterministic outputs |
| **M4A-A4 — Single-Session Acceptance Smoke** | Operational evidence only after separate explicit live authorization; `docs/reports/M4A_A4_IMPLEMENTATION_REPORT.md`, independent `docs/reviews/M4A_A4_REVIEW.md`, status; generated raw artifacts remain outside Git | One existing minimum-scale FavoriteFirst session followed by integrity/provenance audit and offline comparison; acceptance prerequisites below satisfied before proposing multi-day collection |

No A1 permission to begin A2 automatically. Each A1–A3 slice requires Worker report → independent Review → repairs if needed → PASS → complete reviewed commit/tag/push verification. Use `docs/reports/M4A_A<N>_IMPLEMENTATION_REPORT.md` and `docs/reviews/M4A_A<N>_REVIEW.md`. First predecessor is **`m4a5-pass` / `4d75a19`**. Proposed additive corrective tags are `m4a-a1-pass`, `m4a-a2-pass`, `m4a-a3-pass`; later slices must verify and use the immediately preceding complete reviewed corrective baseline, recording its exact SHA. If a corrective baseline is needed, use that instead of a historical incomplete tag. Preserve all existing tags, including `m4a5-pass`; never relabel it as actual M4A readiness. Independent slice approval authorizes checkpointing, not the next live run.

A4 follows the same durable report/independent review/checkpoint discipline for the operational smoke evidence. A4 is a validation task, not permission to implement new behavior or retry live markets without authorization. Its eventual checkpoint records hashes/locations of generated evidence, never secrets or the raw collection itself. No M4A Final Report claiming readiness until the full real collection and engineering gates have independently passed.

### I. Single-session smoke gate and operational boundary

After A1–A3 PASS/checkpoints, perform a fresh read-only preflight of the exact corrective baseline. Then obtain explicit authorization for **one** existing controlled minimum-scale FavoriteFirst market. Current command surfaces, to be retained (not executed by this revision), are:

```bash
uv run polymaker favorite-first-live --config-dir livecfg --once --research
uv run polymaker research-audit research_data/m4a/<session_id> --output-dir research_audits
```

The wrapper retains its current selected-market process through market T+300; Research root is `research_data`, dataset `m4a`, and session ID is its UTC timestamp plus market slug. There is no multi-market supervisor or retry trading loop. `--preflight-only` may cancel selected-market orders under existing behavior; do not use it as a read-only evidence probe.

After the one closed session, run the **future** offline `research-acceptance-check` on its artifacts, then audit it with the generated engineering-evidence directory. A4's report must contain exact implemented commands, baseline, source/evidence hashes, metrics and limitations. Before recommending collection scale-up require:

- Canonical config identity and all raw integrity gates PASS; no secret, GAP or persistence failure.
- A real accepted FIRST-fill Episode and LIVE provenance established through the actual gateway/owned-order/Fill Truth chain; a no-fill or censored run is diagnostic, not a completed smoke.
- Runtime callback evidence present, complete and interpretable; sufficient session and recorded-peak callback samples passing <1ms.
- Valid controlled OFF/ON loop-lag evidence passing <=5ms, plus complete required isolation cases passing exact trace comparison. Missing/short evidence remains UNESTABLISHED and prevents scale-up.
- Material/periodic coverage and T+300 continuation auditable at the existing thresholds. A single Episode does not satisfy the final 20-Episode/3-day collection gate.

Any failed gate stops this acceptance attempt; diagnose/repair/review before retry. If the session has no fill, is legitimately censored or has too little traffic, report insufficiency and seek separate authorization for another smoke. Never manufacture fills, extend risk or change sizing to satisfy evidence quotas. Independent A4 review may recommend separately authorized multi-day collection only after this smoke gate passes; this document does not authorize it.

Final acceptance combines >=20 eligible real FIRST-fill Episodes over >=3 UTC market-days, clean integrity/cadence, per-session qualifying callback evidence and a matching controlled certificate for the collection's recorded peak. Re-run the offline comparison if later collection contains a busier window. Record engineering limits, excluded sessions and independent acceptance result. This establishes telemetry readiness only; there is no Dynamic EV edge conclusion.

### J. Current authorization and validation of this revision

Next authorized action: **Worker M4A-A1 — Canonical Config Identity Repair only**, against `m4a5-pass`. A2/A3 are specified for later separately authorized slices. A4 and multi-day real collection are not yet authorized. M4B remains unauthorized.

Architect validation: inspected actual wrapper/Engine hash expressions, schema, gateway ACK/paper branches, tracker accepted-fill projection, capture/seal/writer behavior, cadence/audit and CLI; computed both identities using pure in-memory expressions without creating Research artifacts. No production or test files changed; no test suite or live command was run. Documentation consistency and `git diff --check` are the checks for this architecture-only change. Runtime acceptance thresholds remain requirements, not measured results.

# M4A.2 Review

Verdict: **PASS**

The original NEEDS FIX review and its findings are retained below as historical evidence. The current verdict follows the re-review at the end of this document.

Reviewed baseline: `830240a` (`m4a1-pass`), the M4A.1 PASS checkpoint.
Reviewed working tree / commit: uncommitted M4A.2 diff against `830240a` on 2026-10-01; 12 Worker-changed files, 833 insertions and 44 deletions before this Review. No M4A.2 commit exists.
Architecture reference: `docs/architecture/M4A_ARCHITECTURE.md`; program boundary `docs/architecture/M4_ARCHITECTURE.md`; schema authority `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md` section 5.
Implementation report reference: `docs/reports/M4A_2_IMPLEMENTATION_REPORT.md`.

## Scope Reviewed

Read the relevant durable documents, the complete Worker diff, the new test file, and the live call paths from market/user streams through Engine, gateway, owned-order processing, Fill Truth, ResearchObserver and ResearchWriter. Inspected `Journal.write` for byte-offset provenance. Ran the full suite and targeted static checks, then performed read-only failure and evidence reproductions. No implementation code or test was modified.

## Changed File Audit

| File | Classification | Assessment |
|---|---|---|
| `docs/CURRENT_STATUS.md` | EXPECTED M4A.2 | Worker handoff status; this Review updates it below. |
| `docs/reports/M4A_2_IMPLEMENTATION_REPORT.md` | EXPECTED M4A.2 | Required report; several claims need correction after repair. |
| `src/polymaker/cli.py` | JUSTIFIED SUPPORTING CHANGE | Adds opt-in `--research`; default remains false. |
| `src/polymaker/favorite_live.py` | JUSTIFIED SUPPORTING CHANGE | Starts/closes the existing observer/writer for the selected run; default path skips initialization. |
| `src/polymaker/research/capture.py` | EXPECTED M4A.2 | Clock, sequence and observer adapter; external timestamp conversion is outside its exception boundary (P0). |
| `src/polymaker/marketdata/service.py` | EXPECTED M4A.2 | Applied book/delta/trade/tick and connection hooks; `_observe` calls unsafe conversion before the book wake (P0). |
| `src/polymaker/userstream/client.py` | EXPECTED M4A.2 | User reconnect generation and per-frame receipt context. |
| `src/polymaker/state/tracker.py` | EXPECTED M4A.2 | Owned-order and Fill Truth revision hooks; FAILED acceptance payload is false evidence (P1), and Research-only reads lack isolation (P0). |
| `src/polymaker/execution/filltruth.py` | JUSTIFIED SUPPORTING CHANGE | Adds REST response receipt clocks without changing reconciliation rules. |
| `src/polymaker/execution/gateway.py` | JUSTIFIED SUPPORTING CHANGE | Actual POST/cancel request and response seams, including unknown outcomes; missing request correlation IDs (P1). |
| `src/polymaker/engine.py` | EXPECTED M4A.2 | REST replacement, strategy/safety and order-state hooks; Research-only payload preparation can interrupt order resync (P0), and effective quote omits the final plan (P1). |
| `tests/test_research_causal_capture.py` | EXPECTED M4A.2 | Nine focused tests cover the main happy paths; misses the blocking failure and accounting cases. |

The 44 deletions are mostly replaced lines: the prior status text, imports/signatures, three-field market queue unpacking, direct calls now wrapped with observation, and the previous direct `evaluate_favorite(StrategySnapshot(...))` expression split into equivalent snapshot/evaluate statements. I found no unrelated strategy, reconciliation, settlement or sizing replacement hidden in the deletions.

## Findings

### P0

- **A Research-only timestamp conversion can break market processing.** `MarketDataService._on_book` applies a parsed book, then calls `_observe` before `_wake` (`service.py:233-240`). `_observe` calls `exchange_us(event_ts)` outside `CausalCapture.emit`'s exception guard (`service.py:287-293`, `capture.py:19-20`). The existing parser accepts `timestamp="Infinity"`; without Research, the frame is applied and wakes the quoter. With Research enabled, `int(inf)` raises `OverflowError` and the wake never happens. I reproduced both paths with the same book frame: disabled completed with one wake; enabled raised with zero wakes. The same conversion path applies to delta and trade events. Research input validation must be isolated without swallowing unrelated live errors.
- **Research-only order-state reads can interrupt live REST resync.** `Engine._refresh_token_orders` now calls `_research_order_snapshot` immediately after each authoritative `replace_open_orders` (`engine.py:1206-1213`). The helper performs additional `StateStore.orders_for` and `owned_order` reads outside the capture guard (`engine.py:1508-1521`). In a read-only reproduction where `owned_order` raises, the Research-disabled resync replaced both tokens and completed; Research-enabled resync raised after replacing only the first token. This can interrupt quarantine/finalization reconciliation. `UserEventProcessor._order_observe` and `_fill_observe` have the same unguarded Research-only `owned_order` pattern (`tracker.py:370-395`). Research payload preparation must be isolated from live transitions.

### P1

- **FAILED Fill Truth revisions claim an accepted economic fill.** `_fill_observe` sets `accepted` from `applied_fill_keys` and `quarantined_fill_keys` alone (`tracker.py:395-401`). The FAILED branch changes the stored status and quarantines the order but does not add the fill key to `quarantined_fill_keys` (`tracker.py:313-321`). I reproduced MATCHED then FAILED for one owned trade: authoritative `economic_fills_for_order` returned zero, while the FAILED Research event said `accepted=True, quarantined=False`. This violates the Research Design's fill payload semantics and could double-count or retain invalid economics downstream. Capture the processor's actual effective economic/validity state at each revision.
- **The labelled effective quote is emitted before the final order plan.** `engine.py:934-945` emits `phase=EFFECTIVE_QUOTE` from `desired`, before `reconcile(tq, live)`, `plan.is_noop`, cancel/replace verification, load shedding and the gateway minimum-size/final submit guard. The record can contain a non-null quote when no placement is planned or permitted. The accepted architecture requires the actual effective target together with plan/no-op and suppression evidence; the Implementation Report calls this an executable target. Preserve the target as its own fact and observe the later plan/intention seam without re-evaluating FavoriteFirst.
- **Order request and result events lack a shared correlation identity.** The Research Design section 5 and M4A architecture explicitly require a request correlation ID. Gateway submit events have quotes but no request ID; ACK/UNKNOWN events have outcome/order IDs but no common request ID (`gateway.py:220-251`). Cancel request/result pairs likewise lack one (`gateway.py:303-374`). With concurrent gateway calls and interleaved callbacks, event order alone cannot reliably pair an ACK/unknown result with its intent. Add a session-local request identity at the actual I/O attempt and carry it through every result path.
- **The required `fill_truth_ready` field is absent from FILL_REVISION payloads.** Research Design section 5 names it in the fill payload. Current events have status/accepted/quarantined, while engine SAFETY events sometimes report a readiness fact at another sequence. The latter is not a per-revision readiness value, especially across recovery. Record authoritative readiness where available, or explicitly mark unknown at the seam and document the limitation rather than silently omitting the field.

### P2

- The new tests manually assign market generations and call `_observe`; they do not exercise `_connect_and_listen` through disconnect/reconnect. The order lifecycle test uses paper placement plus a fake cancel, and does not check live POST unknown outcomes or request correlation. The FavoriteFirst test checks that two events exist, not that effective intent differs under no-op, load shedding or final guards. Add deterministic cases while repairing P0/P1.
- Applied DELTA events omit the source change hash/message identity, and TICK events omit source-native timestamp/old tick where present. The normalized mutations can replay levels, but the source linkage and audit precision promised by the architecture are weaker. Preserve available source metadata without raw auth payloads.
- A local synthetic benchmark of `CausalCapture.emit(BOOK)` with 1,000 bid and 1,000 ask levels measured about 1.0 ms median and 1.8 ms p99 over 100 calls. This does not prove production latency, but full-book validation runs under the capture admission lock on the live loop; M4A.5 should measure representative books against the architecture's callback p99 <1 ms target.
- Wrapper cleanup calls `engine.shutdown()` before `research_writer.close()` in one `finally` block. If shutdown raises, the Research writer close is skipped. The M4A.4 durability contract is deferred, but close should be attempted independently in the repair if this seam is touched.

## Causal Clock Review

`exchange_at_us` comes from parsed source timestamps for market/trade and is not used to assign sequence. REST discovery samples a receipt pair after `get_trades`/`get_full_book`, and `CausalCapture` samples local availability after applied mutation or result. A delayed REST fill therefore is not backdated in the normal path. `available_at_us` is clamped nondecreasing and `monotonic_ns` is sampled within the same ordering lock. Market WS receipt wall time is sampled at reader enqueue; its monotonic value is converted to a float for the existing queue and reconstructed as an integer, with submicrosecond precision loss. User WS stamps at frame delivery. The conversion P0 above means malformed but parser-accepted source time can break the live path.

## Event Sequence Review

One `CausalCapture` lock encloses sequence assignment, validation and `ResearchObserver.observe`, so successful queue admission cannot reorder between concurrent producers. Sequence is session-local and strictly increasing; the concurrent test exercises this. Failed validation or full/closed queue admission consumes a sequence. Queue counters exist, but no durable GAP/manifest yet, as permitted for M4A.2; missing tail sequences remain unprovable until M4A.4.

## Market / Book Capture Review

Hooks sit after the current per-token `OrderBook.apply_snapshot`, `apply_delta` and tick mutation. Separate subscribed YES/NO token books are used; no complement-price shortcut exists in the Research hooks. Trade prints use the existing parser and flow callback. Rejected parse results do not emit applied-book evidence. Full parsed WS levels and individual mutations are enough for level replay if no event is lost, subject to missing source hashes and the P0 isolation failure.

## REST Replacement Review

`Engine._refresh_book` emits REST_BOOK only in the existing drift branch after `book.apply_snapshot`; it copies full returned bids/asks/hash/tick/token plus market generation and a Research-only replacement count. Receipt is sampled just after the existing `get_full_book` response and availability after application. No extra REST request or changed replacement rule appears in the diff. The live book's snapshot generation remains the WS generation; REST_BOOK identifies the replacement separately. `Journal.write` exposes no stable byte offset or checksum, so null `source_ref` and explicit deferral are honest.

## Connection Generation Review

Market generation increments after successful subscribe; user generation increments after successful authenticated subscribe. Connection UP/DOWN and subsequent WS records carry their stream-specific generation. REST_BOOK carries current market generation. No cross-reconnect continuity is claimed. The current new test checks manually changed generation values rather than the actual reconnect loop; this is a coverage gap, not evidence that the loop is wrong.

## Order Lifecycle Review

Gateway submit intent is emitted after the final submit guard immediately before POST, and result events distinguish ACK, explicit rejection and unknown POST outcome. Cancel request and outcome events are separate, including scoped cleanup cancels. Authoritative registration/WS/REST state follows separately. The missing correlation ID is blocking, and the `EFFECTIVE_QUOTE` target is not the final placement plan. Paper cancel returns without a venue request, so it properly has no cancel ACK.

## Fill Truth / Fill Revision Review

The observer runs from `UserEventProcessor` after existing ownership, economics and status decisions; it does not independently parse raw user trades or apply fills. `(trade_id, maker_order_id)` remains the economic key, and MATCHED/MINED/CONFIRMED produce separate append-only revisions. REST discoveries retain exchange occurrence time and obtain a later local knowledge time. FAILED revision flags contradict the processor's economic truth (P1). Quarantined/REST recovery states need explicit per-revision readiness as described above.

## FIRST / PAIR Role Review

Role is read from `owned_orders.source` (`favorite_first_leg`/`favorite_pair_leg`) after registration. Pre-registration submit events contain no role; buffered pre-ACK fills are emitted only after registration and keep their original WS receipt time/generation. No role inference from price, timing or future state was found.

## Strategy / Effective Quote Review

The first BASELINE_DECISION copies the actual `StrategySnapshot`, prior memory/config and pure `evaluate_favorite` output. The second copies `desired` after several risk and repricing gates. It does not copy the reconciler's plan, no-op result, cancellation dependencies, load-shed decision or minimum-size/final guard result. It must not be interpreted as a final executable order intent until the P1 is repaired.

## Failure Isolation Review

`CausalCapture.emit` and M4A.1 `ResearchObserver.observe` catch failures inside their methods; the injected observer test covers that narrow case. Research-specific conversion and payload gathering happen before those guards in several live hooks. The two P0 read-only reproductions demonstrate Research-enabled failures escaping and changing live processing. The fix must isolate only Research work so genuine trading exceptions still follow the existing path.

## Default-Off / Live Behavior Review

The CLI flag defaults false; without it the wrapper does not start Research or pass Research kwargs to Engine. The default market frame queue keeps its three-field shape, and no Research clock/validation runs in the default reader branch. Existing paper-engine order intents matched in the Worker's non-Favorite comparison; the CLI help includes `--research`. This supports default-off parity for the exercised path. Enabled-path failures currently can interrupt trading (P0). No additional external feed, subscription or REST polling was added.

## Performance Risk Review

Research callbacks do no disk or network I/O and use the existing nonblocking queue. Full book payload validation recursively copies all levels under a shared admission lock; the synthetic benchmark above shows a plausible callback-budget risk for larger books. Additional StateStore lookups in active REST resync are a correctness/failure risk (P0) and may add latency. Formal p99/loop-lag acceptance belongs to M4A.5.

## M4A.3 / M4A.4 Boundary Review

No Episode creation, pre-fill ring, periodic StateSnapshot, T+300 Episode loop, FutureLabel, replay, EV model, scorer, BTC feed, sizing or inventory optimization was added. The wrapper starts the M4A.1 writer but does not add full GAP, manifest, checksum, fsync or crash recovery. No material M4A.3/M4A.4 scope creep was found.

## Tests Reviewed

Read all nine new focused tests. They meaningfully exercise normal sequence admission, two actual token books, delayed fill occurrence versus knowledge time, status progression, pre-ACK receipt retention, REST book replacement, some order events, and a real paper engine path with observer failure. Their missed cases are listed under P2; the suite's pass count does not validate FAILED accounting, enabled-path payload isolation, final order plan or request correlation.

## Tests Executed

- `.venv/bin/pytest -q`
- `.venv/bin/ruff check` on changed Research, live integration modules and the new test file
- `.venv/bin/mypy src/polymaker/research`
- Targeted mypy on Research plus all modified production modules
- `git diff --check`
- Read-only Python reproductions for non-finite market timestamp, Research-only order lookup failure, and MATCHED→FAILED fill evidence
- Read-only CLI help check and local synthetic callback timing sample

## Test Results

Full pytest: **554 passed, 2 skipped** in 7.40s. Targeted Ruff: clean. Research-package mypy: clean (5 source files). Broader targeted mypy: 13 diagnostics in 5 pre-existing non-Research code areas; inspection of the baseline diff shows the flagged expressions themselves were not introduced by M4A.2. `git diff --check`: clean before this Review. The three independent reproductions produced the P0/P1 results above. Passing existing tests do not override them.

## Implementation Report Accuracy

The report accurately identifies the changed files, default-off opt-in switch, normal-path late REST knowledge time, separate token books, null byte-offset provenance and M4A.3/M4A.4 deferrals. Its claims of complete failure isolation, accepted/quarantined Fill Truth revisions and an executable effective quote are false in the cases above. The hook map omits the Research-only StateStore read and the final plan gap. `Architecture Deviations: NONE` cannot be accepted as full compliance until the P0/P1 contract failures are repaired. The reported test count matches this Review's run but is insufficient evidence for the missing semantics.

## Required Fixes

1. Contain external timestamp conversion and all Research-only payload preparation/read failures at their observation seams. Add deterministic enabled/disabled failure-isolation tests for a parser-accepted non-finite timestamp and an order-state lookup failure. Keep trading exceptions outside Research observable.
2. Make each FILL_REVISION's accepted/quarantined/economic state agree with authoritative Fill Truth, especially after FAILED; capture or explicitly represent unknown `fill_truth_ready` at the revision. Add MATCHED→FAILED and quarantine/recovery regressions.
3. Give every submit/cancel I/O attempt a correlation ID shared by intent and ACK/rejected/failed/unknown result, including relevant scoped cancellation paths. Test overlapping requests and unknown POST.
4. Capture the actual effective target and subsequent reconciler plan/no-op/suppression evidence as distinct facts. Do not label a pre-plan desired quote as final executable intent. Test a non-null desired quote that produces no placement and a final guard/load-shed suppression case.
5. Update the M4A.2 Implementation Report with the actual fixes, revised hook map/deviations, test results and remaining limitations. Do not start M4A.3.

## Deferred Issues

P2 source metadata, reconnect-loop/unknown-outcome coverage beyond the required fixes, benchmarked callback latency, writer-close robustness and M4A.1's previously deferred P2s may be handled after the blocking repair or in their designated later slices. Raw journal byte-offset provenance and full GAP/manifest/crash durability remain explicitly deferred to the appropriate architecture decisions/slices.

## Final Verdict

**NEEDS FIX**. Two P0 live-path isolation failures and four P1 causal-evidence/contract failures block M4A.2 acceptance. This Review does not authorize M4A.3.

## Handoff

Next authorized action: Worker repairs only the P0/P1 findings above, updates `docs/reports/M4A_2_IMPLEMENTATION_REPORT.md`, and returns for independent M4A.2 re-review. Preserve `m4a1-pass` as the accepted baseline. The Reviewer did not modify implementation code or tests.

## Re-Review After P0/P1 Repair

Reviewed working tree / commit: uncommitted M4A.2 implementation and P0/P1 repair against `830240a` (`m4a1-pass`, current HEAD) on 2026-10-01. Cumulative pre-re-review diff: 13 files, 1,292 insertions and 44 deletions, including this already staged Review file. No M4A.2 commit exists yet.

Repair scope: `research/capture.py`, `marketdata/service.py`, `engine.py`, `state/tracker.py`, `execution/gateway.py`, focused Research tests and the canonical Implementation Report/status. Compared the cumulative changes with the original review audit; no new implementation module, feed, request path, strategy rule, Fill Truth mutation or Episode machinery was added by the repair.

### P0

NONE. Both prior enabled-path failures were independently reproduced again and no longer interrupt live processing.

### P1

NONE. The four prior causal-evidence defects are resolved at the actual engine, gateway and Fill Truth seams.

### P2

- The previous source metadata, real reconnect-loop coverage, callback latency and writer-close follow-ups remain deferred. The repaired paths did not worsen them. The final submit-guard test observes the terminal no-placement result but does not encode the particular guard reason; the plan and gateway events still distinguish planned from attempted work.
- `fill_truth_ready` is a copy of current Engine recovery, uncertain-order and quarantine guards. A standalone `UserEventProcessor` without that Engine provider records null. There is no separate durable Fill Truth readiness flag in the processor; consumers must retain the nullable meaning and must not infer it from status names.
- The full book payload still incurs validation under the capture lock; formal latency acceptance belongs to M4A.5. Raw journal byte offsets and durable GAP/manifest handling remain deferred as previously documented.

### P0-A Verification

`exchange_us` now returns null for non-finite or overflowing source seconds. `MarketDataService._observe` catches only Research conversion/emission failures after book application; normal journal and wake remain outside that catch. I replayed the original book frame independently with `NaN`, `Infinity` and `-Infinity`: each applied the 0.49 bid and generated one normal wake. Each Research BOOK retained null `exchange_at_us`, injected local receipt `123`, and a positive local `available_at_us`. No fabricated exchange time or live parser change was found.

### P0-B Verification

`Engine._research_order_snapshot` guards its additional StateStore reads and observation only; `replace_open_orders` and the two-token loop remain outside the guard. Tracker's Research-only role/fill lookups have equivalent narrow guards. I injected an `owned_order` read failure into the original two-token `_refresh_token_orders` path. Disabled and enabled runs both completed with `(YES orders, NO orders) = (1, 0)`; enabled Research counted one failure. Authoritative resync was neither swallowed nor interrupted.

### P1-A Verification

`UserEventProcessor.economic_fills_for_order` includes applied keys with a stored record, excluding quarantined fill keys and effective FAILED status. `_fill_observe` now uses those same conditions for `accepted`. Independently replayed MATCHED → MINED → CONFIRMED → FAILED for one owned trade: revision acceptance was `true,true,true,false`, and the authoritative economic-fill list was empty after FAILED. A contradictory fill produced a retained QUARANTINED revision with `accepted=false`. Research observes the processor's existing decisions; no application or reversal rule was changed.

### P1-B Verification

The pure FavoriteFirst record remains immediately after `evaluate_favorite`. A distinct `phase=ORDER_PLAN` record is emitted after `reconcile` with target, `to_place`, `to_cancel` and no-op evidence. `phase=EFFECTIVE_QUOTE` is emitted on the subsequent no-op, cancel/deferral, market/load-shed suppression or gateway placement-result branches. Independent real-engine paper runs produced (a) a non-null target with one placement, then a second recompute with a non-null target but `ORDER_PLAN.is_noop=true` and terminal `EFFECTIVE_QUOTE.quote=null`; and (b) a final submit guard returning false, with a non-null plan, terminal null effective quote, no ORDER_SUBMIT and no live order. These exercise the production reconcile/gateway path, not a renamed strategy value. A reconciled plan is not presented as proof of exchange submission.

### P1-C Verification

Gateway placement allocates a Research-only UUID after the preliminary guard and carries it from ORDER_SUBMIT through ACK, REJECTED, ABORTED, FAILED or UNKNOWN. Cancel-by-ID, asset, market-asset and all-order scopes similarly carry one ID from request through outcome. No ID is generated on the disabled path, and request IDs are separate from venue order/trade IDs. Independent local fake-client operations covered successful POST, unknown POST, pre-POST signing failure, successful cancel and unknown cancel: every intent/result pair shared its ID, all five IDs were distinct, and outcomes were `ACK,UNKNOWN,FAILED,ACK,UNKNOWN`. Failure before an exchange order ID was correlatable. No external call or retry logic was added.

### P1-D Verification

Engine wires a read-only readiness provider only when Research is enabled. It reads the existing fill-recovery, uncertain-order and processor quarantine guards at each revision; it does not infer readiness from MATCHED/MINED/CONFIRMED names. Independently toggled the provider across the four status revisions: payloads retained `false,true,true,false` in sequence after draining the immutable event queue. The earlier false record stayed false. REST receipt and exchange occurrence clocks remain separate; the existing late-discovery test passed.

### Regression / Scope

Reviewed the cumulative 13-file diff and the repair sections against the accepted architecture. The original 44 deleted lines remain the previously audited replacements, with no additional strategy, execution, Fill Truth or REST resync semantics removed by repair. The `--research` CLI default remains false; Engine constructs no capture when disabled, and the existing real paper-engine parity test still compares identical order intents. Research uses existing market/user/REST traffic; no new subscription, polling, BTC feed or authenticated call appears. No Episode lifecycle, periodic snapshot, label, replay, model, shadow scorer or M4A.4 durability expansion was added. Existing P2 issues remain P2.

### Tests Executed

- `.venv/bin/pytest tests/test_research_skeleton.py -q`: **8 passed**.
- `.venv/bin/pytest tests/test_research_causal_capture.py -q`: **16 passed**.
- `.venv/bin/pytest -q`: **561 passed, 2 skipped** in 8.13s.
- Targeted Ruff on Research plus all M4A.2 modified production/test Python files: clean.
- `.venv/bin/mypy src/polymaker/research`: clean, 5 source files.
- Targeted mypy on Research plus all modified production modules: 13 diagnostics in the same five non-Research files/unchanged expressions identified in the original Review; no repair-introduced diagnostic.
- `git diff --check`: clean before this documentation update.
- Read-only independent reproductions for non-finite book receipt/wake, two-token REST resync lookup failure, MATCHED→FAILED/quarantine and readiness revisions, FavoriteFirst no-op/final-guard outcomes, and live submit/cancel correlation across success/failure/unknown outcomes.

### Final Verdict

**PASS**. The two original P0 and four original P1 findings are resolved in the reviewed working tree. The retained P2 items are explicitly deferred. This certifies this uncommitted M4A.2 scope against `m4a1-pass`; any later implementation change requires its own review. Next authorized action is M4A.3 architecture-consistent implementation only.

## Later Checkpoint Metadata — 2026-10-01

This note records a Git checkpoint repair after the independent PASS above; it does not change that historical review verdict or assert a new review. The original `m4a2-pass` tag (`f5618de`) omitted eight integration files that were present in the working tree independently reviewed above. The reviewed code itself passed. The missing M4A.2 files were reconstructed in a separate worktree by copying the six unchanged M4A.2 integration files and removing only the distinct M4A.3 lifecycle blocks from `engine.py` and `favorite_live.py`; `capture.py`, focused tests and the M4A.2 reports already came from `m4a2-pass`. No M4A.3 code is in the reconstructed tree.

The reconstructed code reproduced the review's 561 passed, 2 skipped suite, all 16 focused causal tests, clean targeted Ruff, clean Research-package mypy, and the same 13 pre-existing broader targeted mypy diagnostics. The new `m4a2-reviewed-baseline` tag identifies the complete reconstructed reviewed checkpoint. The original `m4a2-pass` tag remains unchanged for historical integrity. Future M4A.3 review should use `m4a2-reviewed-baseline` as its implementation baseline.

# CONVERGENCE-0001 — Architect Report

## Status

DOCUMENT_PROPOSAL_ONLY

Implementation is **NOT AUTHORIZED**. Development remains paused. No Worker dispatch or ITER-0025 initialization is proposed.

## Inspected Baseline

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `7aa329e02bc5708bc5c2f5df956c9e5036085a07`, matching the local remote-tracking reference.
- Tracked tree clean. The new convergence audit and preserved `research_outputs/` are untracked.
- Read the active contract, Frozen SOP, baseline, iteration index, convergence scope, ITER-0024 RESULT and final review evidence.
- Inspected actual RDP runtime, CLI, market/account/user-stream paths, gateway, placement owner, lifecycle, accounting, capital, quote/filter modules and relevant tests.
- No source, test, configuration, Frozen SOP, audit or Git modifications were made.
- No network requests, orders, cancellations or production tests were run.

The existing implementation has substantial reviewed prerequisites. The missing delivery behavior is their composition into authorized submission, repeated NORMAL/REBALANCE operation, terminal handling and durable complete output.

The proposals below are complete document bodies. They do not authorize implementation.

## Proposal A — `docs/development/DELIVERY_GAPS.md`

```markdown
# RDP Phase 0 — Single-Market Delivery Gaps

## State and Authority

Status: PAUSED_FOR_DEVELOPMENT_CONVERGENCE.

Implementation baseline: 7aa329e02bc5708bc5c2f5df956c9e5036085a07.
Root: /home/ubuntu/poly-maker.
Branch: rdp-phase0-dev.
Latest archived implementation: ITER-0024, CODE_READY, final Reviewer06 PASS.
MICRO_LIVE_READY: NO.
Real-money authorization: NOT_GRANTED.
Implementation resume authorization: NOT_GRANTED.

This register covers the required initial delivery: one explicitly selected BTC 5-minute market inside poly-maker. It does not schedule implementation iterations or design a new platform.

Requirement authorities:

- docs/strategy/RDP_PHASE0_FROZEN.md.
- Approved decisions in ITER-0005/03_HUMAN_DECISION.md and ITER-0006/03_HUMAN_DECISION.md.
- AGENTS.md, especially §§4–6, 18–23, 29–38 and the Single Repository / Shared Data Rule.
- The program’s single-market deliverable, runtime safety, observability, delivery gate and final readiness requirements, preserved by the convergence audit.

Frozen strategy semantics control. Existing approved decisions settle the $5 NORMAL future repair reserve and cumulative absolute consecutive depth change. Neither decision is reopened here.

## Register Rules

IDs below are stable requirement references. They identify missing delivery behaviors, not proposed modules.

OPEN means the delivery acceptance condition is not met. Existing helpers or per-iteration PASS do not close an integrated behavior.

Each implementation contract must target one behavior under one ID. It may close that behavior or implement a necessary, explicitly justified prerequisite. A partial prerequisite must identify the still-missing acceptance condition; it does not close the ID.

Splitting an implementation step does not create a new delivery requirement. Add a requirement ID only when an authoritative source establishes previously omitted required behavior.

Safety defects that block an existing requirement remain blocking. They are not optional deferrals.

## Existing Reusable Evidence

These are implemented prerequisites, not declarations that the integrated runner is ready.

| Requirement supported | Actual implementation | Relevant actual tests |
|---|---|---|
| Exact BTC5m selection; official Gamma/CLOB metadata and HTTP books | rdp_live.py: slug parsing, bootstrap_rdp_market, read_rdp_market_status | test_rdp_live.py: exact-market/book GETs, identity/tradability rejection, origin validation, numeric precision, expiry |
| Official market WS, dynamic ticks, current generations and stale/failure handling | rdp_marketdata.py: RDPMarketDataService, RDPMarketDataSession | test_rdp_marketdata.py |
| Authenticated account/order reads, exact decimals, cash/allowance and selected-state checks | rdp_account.py; risk/rdp_capital.py: evaluate_initial_rdp_backing | test_rdp_account.py; test_rdp_capital.py: initial backing, foreign commitments, exact freshness and amount boundaries |
| Retained authenticated user stream and receipt provenance | rdp_userstream.py; existing UserEventProcessor | test_rdp_userstream.py: heartbeat/PONG, disconnect, expiry, redaction, repeated cancellation |
| Confirmed inventory and Frozen economic formulas | strategy/rdp_accounting.py: snapshot_inventory, RDPInventorySnapshot.market_pnl | test_rdp_accounting.py |
| NORMAL and REBALANCE quote formulas | strategy/rdp_quotes.py: build_normal_quotes, build_rebalance_quote | test_rdp_quotes.py: sizes, price/tick boundaries, deficient-side-only maker, minimum rejection |
| Frozen Book Filter and production input binding | strategy/rdp_book_filter.py; retained runtime book_filter_result | test_rdp_book_filter.py; runtime/filter composition tests |
| $30 projection, reservations, actual confirmed cost, $5 reserve | risk/rdp_capital.py: project_rdp_capital, evaluate_rdp_capital_gate | test_rdp_capital.py: partials, close proof, subsequent resting scope, reserve boundaries |
| Strict pair acknowledgments and per-token SDK signing | execution/gateway.py: opted-in place path and strict pair parser | test_execution.py: partial/unknown responses, ticks, post-only and guard denial |
| Initial capture and attributed acknowledgment ownership | execution/rdp_placement.py: RDPPlacementOwner | test_rdp_placement.py: complete/partial/unknown, observed IDs, buffered events, replacement blocking |
| Scoped cancellation/reconciliation and cancel/fill races | execution/rdp_lifecycle.py; runtime scoped close | test_rdp_lifecycle.py; placement close/executor drain tests |
| Initial pre-fill REST/FILL_WINDOW/CLOSE and original-receipt window completion | rdp_runtime.py: evaluate_initial_prefill, initial_fill_window, complete_initial_normal_cycle | test_rdp_placement.py: validity, confirmation races, window timing, close once and interrupted continuation |
| Coherent final initial-intent factual revalidation | rdp_runtime.py: revalidate_initial_normal_intent and retained bridge/capture helpers | test_rdp_placement.py: actual gateway pre/post-sign guards, bridge timeout, freshness, intent mutation and provenance |

At this baseline, the RDP CLI accepts preflight/observation modes and rejects an invocation without a supported read-only mode. No production RDP runtime call to gateway.place exists. The runtime does not compose build_rebalance_quote into an operating REBALANCE loop.

Existing accounting fields and structured decision logs are valuable evidence. They do not constitute the complete required metrics artifact.

## Finite Open Delivery Behaviors

### DG-001 — Authorized Initial NORMAL Submission

Status: OPEN.

Sources: Frozen §§4, 15–18; AGENTS §§31, 36; delivery execution/send-gate requirements.

Evidence: Initial admission, clean ARMED capture, factual revalidation and opted-in gateway placement exist. Admission still exposes HUMAN_LIVE_AUTHORIZATION and OPERATIONAL_TIME_SUFFICIENCY as outstanding. Factual validation fixes execution_authorized=false. No production submission owner connects them.

Missing behavior: An explicitly authorized initial submission must enforce all outstanding gates, retain pending capture before POST, make at most one strict post-only pair POST, and resolve typed acknowledgments/uncertainty into the existing owner without losing cancellation-race evidence.

Closure: Offline integration proves default denial causes no signing/POST; valid explicitly activated fake execution uses both current guards and per-token ticks; complete/empty/partial/unknown/error outcomes retain correct ownership; interruption drains the SDK operation before outcome resolution. Actual live authorization remains ungranted during development.

TimeAvailable is not already implemented. A contract introducing the send path must implement and test its operational budget check; it cannot accept factual validity or a caller’s unexplained “time safe” boolean as proof.

### DG-002 — Current Capital and Inventory Across Successive Orders

Status: OPEN.

Sources: Frozen §§2–11, 17–18, 26; approved $5 reserve; delivery inventory/accounting/capital gates.

Evidence: Pure projection handles confirmed costs, resting reserves and a prior closed subset. Initial admission requires an empty run; initial ownership/close composition is tied to one initial pair.

Missing behavior: Every subsequent NORMAL/REBALANCE order must use the same authoritative run inventory, confirmed acquisition costs and attributable current commitments. Fresh official account evidence must support current spendability without resetting the $30 budget or adopting foreign inventory.

Closure: Integrated consecutive-cycle tests preserve all prior confirmed fills/costs, account for partial remaining reservations exactly once, reject stale or contradictory account/order/fill evidence and enforce actual cash/allowance plus the Frozen capital model before each new order.

### DG-003 — Repeated NORMAL Operation in One Market

Status: OPEN.

Sources: Frozen §§14–22; single-market deliverable.

Evidence: complete_initial_normal_cycle finishes an already acknowledged initial pair. Initial admission/arming requires empty local scope. Completion does not submit or arm another cycle.

Missing behavior: After successful reconciliation with abs(Delta)<5, the same market runtime must re-enter NORMAL, apply inventory size skew and current gates, and operate another cycle without manual code intervention. Valid zero-fill quotes may rest; invalidation must close before requoting.

Closure: An offline market scenario completes more than one NORMAL cycle, including fractional residual inventory and changed dynamic ticks. No replacement is submitted before authoritative closure; no quote quota, zero-fill timeout or new stop condition is introduced.

### DG-004 — Operating REBALANCE and Return to NORMAL

Status: OPEN.

Sources: Frozen §§23–28, 34; delivery transition and risk-closure requirements.

Evidence: build_rebalance_quote and capital mode support exist. Initial close reports a descriptive REBALANCE classification. No RDP runtime calls the builder or operates its loop.

Missing behavior: At abs(Delta)>=5, stop dual NORMAL quoting and operate only the deficient side at BestAsk-TickSize, five shares, post-only. Repricing must follow existing cancellation/reconciliation ordering. Accepted fills update the same inventory. Recovery closes remaining orders and returns to NORMAL.

Closure: Integrated tests cover both deficiency signs, exact five-share boundary, partial fills, cancel/fill races, dynamic ticks, metadata minimum rejection, recovery, and continuation without a hard rebalance-duration/no-fill stop.

CanSafelyContinue must use actual time, capital, market and state evidence. Existing quote construction does not satisfy this gate.

### DG-005 — STOP and Operational Time Safety

Status: OPEN.

Sources: Frozen §§13, 26–27, 34; AGENTS §§31, 33, 36.

Evidence: Current routines fail closed and retain blocking on individual failures. They do not implement the complete runtime terminal action or CanSafelyContinue policy.

Missing behavior: New risk stops on TIME_EXHAUSTED, CAPITAL_INSUFFICIENT, MARKET_NOT_TRADABLE or STATE_UNRELIABLE. Attributable remaining orders pass through cancellation/reconciliation. Available confirmed inventory remains recorded when complete reconciliation is impossible.

Closure: Integrated NORMAL/REBALANCE failure scenarios produce the required terminal reason, zero subsequent new-risk sends, scoped cleanup and an honest complete/incomplete result. TimeAvailable is checked before new risk with enough configured operational budget for Order → Cancel → Reconcile. Operational deadlines never become MaxRebalanceSec or a no-fill stop.

Timing authority: Frozen §26 supplies the safety condition, not a numeric strategy cutoff. Existing routines accept positive close budgets and absolute operational deadlines; they do not certify sufficient remaining market time. Required implementation must document ordinary engineering budget selection and prove boundary behavior. A material strategy cutoff beyond this requirement would need human approval.

### DG-006 — Partial/Unknown Placement and Unreliable-State Termination

Status: OPEN.

Sources: Frozen §§18, 21, 26–27; delivery partial-fill, order-state, reconciliation and fail-closed requirements.

Evidence: Typed partial/unknown outcomes preserve attributed acknowledgments separately from merely observed IDs and retain replacement blocking. Scoped close can reconcile known attributed orders. No complete runner consumes these outcomes as terminal failures.

Missing behavior: The integrated runtime must withhold replacement/new risk, close only safely attributed scope, retain unresolved exposure/evidence and emit an actionable incomplete STOP result when authoritative outcome cannot be established.

Closure: Integrated failed POST, ambiguous/partial acknowledgment, unknown zero-ID, pending/FAILED/quarantined fills and unreliable reads never cause a retry POST, invented inventory, false capital release or foreign-order cancellation. Known attributed legs receive scoped cleanup; unprovable state stays blocked and explicitly reported.

Automatic heuristic adoption or rediscovery of unknown IDs is not required. Safe failure handling is required and cannot be deferred.

### DG-007 — Safe End of the Selected Market

Status: OPEN.

Sources: Frozen §§18, 21, 26–27; single-market flow through market end; delivery safe-termination requirement.

Evidence: Read-only observation ends and cleans up. Explicit initial completion closes on market expiry. There is no full trading runner owning all successive actions through termination.

Missing behavior: The integrated selected-market owner must disarm new risk, drain in-flight submission/read work, close/reconcile attributable remaining orders once and preserve final inventory/capital/completeness evidence. It must not select another market.

Closure: Offline expiry/cancellation tests during each operating phase prove no post-end new-risk POST, no detached SDK operation, scoped cleanup and durable honest final output. Unresolved closure must not be represented as successful completion.

### DG-008 — Official Resolution and Final Market Accounting

Status: OPEN.

Sources: Frozen §§12, 31, 35–36; AGENTS §38; required final MarketPnL/EndingCapital evidence.

Evidence: RDPInventorySnapshot.market_pnl computes a value for an explicitly supplied winner. No RDP runtime obtains an authoritative resolved winner or binds that result to durable market output.

Missing behavior: When authoritative official resolution is available, compute MarketPnL=WinningQty-C_total from the retained confirmed inventory and distinguish it from LockedPnL/WorstSettlementPnL. Before resolution, record pending status rather than invent a winner or realized capital.

Closure: Fake official resolved/unresolved/contradictory metadata scenarios produce correct final accounting or explicit pending/unreliable resolution. A resolved YES/NO result, settlement status and code/config provenance are durably traceable. No blockchain RPC, merge subsystem or synthetic cash realization is introduced.

Market end and market resolution are separate events.

### DG-009 — Complete Durable Required Metrics

Status: OPEN.

Sources: Frozen §§1, 22, 28–33, 35–36; AGENTS §§36, 38; required observability/capital-survival output.

Evidence: Accounting/projection fields and structured decisions exist. No RDP collector/report produces all required activity counts, rates, durations, economic attribution and capital extrema from the integrated run.

Missing behavior: Durable single-market output must reconstruct sends, fills, reconciliation, transitions, STOP, inventory/cost/capital and the metrics crosswalk below without creating another trading state machine.

Closure: Deterministic integrated scenarios produce every required field with documented units, event authority and denominators; zero observations and unresolved outcomes are explicit N/A/pending rather than fabricated zeros. Natural and REBALANCE attribution use confirmed economic snapshots. Reporting cannot alter execution authority or critical callbacks.

Definition checks remain blocking under this ID. Frozen specifies formulas for economics but does not explicitly specify every rate denominator, the precise NaturalCompletion classification, PriceConcession reference price or pre-resolution equity convention for drawdown. Resolve from existing authority where possible; request the smallest human definition if a materially different interpretation remains. Do not silently invent those definitions.

### DG-010 — One Integrated Runnable CLI Path

Status: OPEN.

Sources: Single-market deliverable and definition of runnable; AGENTS Single Repository / Shared Data Rule and §36.

Evidence: cli.py: rdp_live is explicitly read-only; preflight and observe commands are real, but there is no integrated operating strategy entrypoint.

Missing behavior: One documented RDP command inside poly-maker selects exactly one BTC5m market and connects official inputs, current gates, NORMAL, placement/lifecycle, REBALANCE, STOP, accounting, metrics and termination. Real-money execution requires deliberate authorization; the safe pre-live invocation remains usable without it.

Closure: Actual CLI integration tests traverse the complete fake market without manual state calls. A documented real command is safely exercised through its pre-live path after resume. Default/absent authorization sends zero orders. Existing read-only modes and FL remain compatible.

### DG-011 — Final Runtime-Path Validation Evidence

Status: OPEN.

Sources: AGENTS §36; program runtime safety verification and regression gate.

Evidence: Actual implementations and extensive guarded mocked tests exist. ITER-0024 final matching-tree evidence records 1,584 passed, 2 skipped; independent affected review records 601 passed. Those results do not prove every completed integrated path or a current read-only official connection.

Missing behavior: Final reviewed runtime must have targeted/affected/FL/full regression evidence and safely exercised official discovery, HTTP metadata/books, WS and authenticated read paths where required, while all create/cancel testing remains fake.

Closure: Final evidence identifies exact commands, commit, path, read-only versus mocked coverage, outcomes and any contract-accepted pre-existing exclusion. No claim of live execution or strategy edge is inferred. No real-money create/cancel validation is performed by agents.

Documentation-only revisions do not require repeating these production tests.

### DG-012 — Final Independent Readiness and Archive

Status: OPEN.

Sources: AGENTS §§16–22, 26, 29, 36–37; final readiness report and delivery audit gate.

Evidence: Iteration evidence is archived through ITER-0024. No final project-level readiness report/review exists; index correctly says MICRO_LIVE_READY=NO.

Missing behavior: Final Reviewer independently assesses the integrated target and all delivery acceptance conditions. Final readiness documentation includes actual command/configuration, flow, safety gates, tests, limitations, no deviations and no real-money trading. Reviewed state is pushed and exactly verified.

Closure: All required behavior IDs satisfy reviewed closure; final project Reviewer PASS exists; current index/readiness artifact is consistent; exact remote commit matches; one final rdp-phase0-dev→main comparison/PR references required evidence. Development stops awaiting human supervised validation. Merge and real-money authorization remain separate and ungranted.

## Safety and Integration Coverage Crosswalk

| Delivery requirement | Existing support | Remaining closure |
|---|---|---|
| Official HTTP discovery/selection; exact BTC5m; metadata | rdp_live bootstrap/status and tests | DG-010, DG-011 |
| Official market WS; dynamic TickSize; minimum size | retained market service, metadata validators, quote/gateway tests | DG-001, DG-003, DG-004, DG-010, DG-011 |
| Authenticated account/order/fill paths | account/user session; strict exact reads | DG-002, DG-006, DG-011 |
| Gated create and post-only | opted-in gateway and factual callback tests | DG-001, DG-004, DG-010 |
| Cancellation/reconciliation; partials; cancel/fill races | scoped lifecycle and processor tests | DG-002–DG-007, DG-011 |
| Stale public data, stale/unreliable order/fill state, changed generations | current health/freshness guards | DG-001–DG-007, DG-011 |
| $30 CapitalGate, FreeCash/ReservedCash/InventoryCost, $5 NORMAL reserve | capital projection/backing/gate | DG-001–DG-005, DG-009 |
| NORMAL, REBALANCE and STOP through one inventory | initial cycle and pure candidates | DG-002–DG-005, DG-010 |
| TimeAvailable and CanSafelyContinue | qualitative Frozen authority; existing operational deadlines | DG-001, DG-004, DG-005 |
| Unknown/partial outcome fail-closed, no foreign adoption/cancel | typed ownership and scoped close | DG-006 |
| Market end and drain | observation/initial owner cleanup | DG-007 |
| Market resolution and actual final PnL | supplied-winner accounting formula | DG-008, DG-009 |
| Durable activity/economics/capital/provenance | snapshots and structured logs | DG-009 |
| One application, shared data, no IM import/new runtime interface | current architecture and project constraints | All contracts; DG-010–DG-012 |
| Required tests, final independent review, immutable audit and exact remote | archived prerequisite evidence | DG-011, DG-012 |
| No autonomous real-money trading; stop at ready | current contract and pause | All IDs; DG-012 |

## Complete Required Metrics Crosswalk

Every row remains part of DG-009. Formula availability does not imply durable collection.

| Group / source | Required fields | Existing support / missing evidence |
|---|---|---|
| Activity: Frozen §§22, 32 | QuoteAttempts; ActiveCycles; ZeroFillQuoteAttempts; ActiveCyclesPerMarket | Attempt/confirmed-fill evidence exists; complete counters/report missing |
| Inventory: §§2, 5, 28, 32 | QY; QN; Delta; InventoryExposure; MaxAbsDelta; BalancedQty; ResidualYES; ResidualNO; ResidualQty; TimeToNormal; TimeInRebalance | Snapshot quantities exist; extrema/duration attribution missing |
| Cost: §§3, 6–7, 32 | CY; CN; C_total; InventoryCost; AvgYESCost; AvgNOCost; BalancedYESCost; BalancedNOCost; BalancedCost; ResidualCost | Snapshot formulas exist; durable complete run output missing |
| Residual/economics: §§8–11, 32 | LockedPnL; ResidualWorstLoss; ResidualBestPnL; WorstSettlementPnL; BestSettlementPnL | Snapshot formulas exist; lifecycle/report integration missing |
| NORMAL: §§29–30, 32 | FillRate; NaturalCompletionRate; PartialFillRate; NaturalLockedPnLDelta; NaturalWorstPnLDelta; AvgNaturalLockedPnLDelta; AvgNaturalWorstPnLDelta | Confirmed economic snapshots exist; classification/rates/attribution missing |
| REBALANCE: §§28, 30, 32 | RebalanceRate; RebalanceLockedPnLDelta; RebalanceWorstPnLDelta; AvgRebalanceLockedPnLDelta; AvgRebalanceWorstPnLDelta; PriceConcession; TimeToNormal; StopRate; StopReason | Builder exists; operating transitions and attribution missing |
| EV: §§30, 32 | LockedEVPerActiveCycle; SurvivalEVPerActiveCycle | Frozen weighted formulas exist; authoritative input aggregates/report missing |
| Final market: §§12, 31–32 | MarketPnL; TotalPnL; AvgMarketPnL; MedianMarketPnL; WorstMarketLoss; WinMarketRate | Supplied-winner formula exists; official resolution/output missing |
| Capital survival: §§1, 4, 31–32 | FreeCash; ReservedCash; InventoryCost; MinFreeCash; PeakReservedCash; PeakInventoryCost; EndingCapital; MaxDrawdown; WorstMarketLoss | Current projected components exist; extrema/final resolved values missing |
| Audit: AGENTS §38; readiness artifact | market identifier; timestamp; commit; strategy/config version; order/fill behavior; transitions; errors; artifact locations; resolution/completeness status | Partial structured evidence exists; complete durable market artifact missing |

For initial delivery, aggregates describe the observed single-market sample. A 288-market execution campaign is not required before MICRO_LIVE_READY. Fields requiring resolution or sufficient observations must preserve their availability status.

## Optional Work Explicitly Deferred

Not required for this delivery:

- Autonomous market rotation or a 288-market scheduler.
- Additional strategies, filters, states, predictive models or rebate optimization.
- Platform rewrites, generic execution frameworks or speculative internal interfaces.
- Heuristic unknown-order adoption/recovery after safe unresolved termination.
- Blockchain RPC, automatic merge/redemption or unofficial runtime services.
- Dashboards beyond required durable evidence.
- New research datasets or duplicated historical data.
- Establishing strategy edge from FL history or development tests.

Safe unknown-state handling, required metrics and final resolved accounting are not optional.

## Pause and Next Action

This register authorizes no implementation.

The next proposed contract is stored only at docs/development/CONVERGENCE-0001/NEXT_ITERATION_CONTRACT_DRAFT.md.

After independent document review and archival:
Development Status: PAUSED_FOR_DEVELOPMENT_CONVERGENCE.
Current Iteration: NONE.
Current Stage: AWAITING_EXPLICIT_HUMAN_RESUME.
MICRO_LIVE_READY: NO.
Real-Money Trading Authorization: NOT_GRANTED.

Await explicit human resume. Do not initialize ITER-0025 or dispatch a Worker from document approval.
```

## Proposal B — Minimal `AGENTS.md` Insertion

Insert the following text immediately after the existing sentence **“Real market evidence outranks model intuition.”** in §5, before the separator introducing §6.

This insertion supplements the existing minimum-problem rule. It does not replace strategy authority, safety, independent review or immutable evidence.

```markdown
## Delivery Convergence

Use docs/development/DELIVERY_GAPS.md as the finite register of missing single-market delivery behavior. It records requirements and closure evidence; it cannot change the Frozen SOP or approved human decisions.

Each implementation iteration must identify exactly one delivery behavior and its stable Gap ID. State the observable start condition, end condition, existing components reused and concrete acceptance evidence. New components or internal interfaces require a current necessity that existing code cannot satisfy by a smaller compatible change. New external runtime interfaces remain subject to §6 and human architecture approval.

Classify findings against existing requirements. A safety defect or defect blocking a required delivery behavior must be fixed within authorized scope or returned to Architect; it cannot be deferred merely to achieve convergence. Findings unrelated to required behavior are deferred without implementation. Genuine missing strategy authority still requires the existing human escalation procedure.

Reviewer PASS must identify either the delivery acceptance condition closed or a justified partial prerequisite. A partial prerequisite must name the still-open acceptance condition, explain why the prerequisite is necessary now, and leave the Gap ID OPEN. Helper completion alone is not delivery completion. Maintain Gap IDs rather than growing a speculative roadmap.

Retain serial Architect → Worker → Reviewer, independent actual-diff review, fail-closed safety, appropriate tests, immutable artifacts and Git archival. Documentation/report revisions alone do not require rerunning unchanged production tests; verify documents and protected-file state instead. Code, test, configuration or unresolved runtime concerns require appropriate implementation checks under §23.

CONVERGENCE-0001 is document-only. Its approval does not authorize implementation. After its archival remain PAUSED_FOR_DEVELOPMENT_CONVERGENCE, with no new implementation iteration or Worker dispatch, until an explicit human resume instruction. After resume, recheck the actual repository and draft applicability before assigning the next iteration.

At MICRO_LIVE_READY, stop development under §37. Do not continue optional refactoring or add features.
```

## Proposal C — `docs/development/CONVERGENCE-0001/NEXT_ITERATION_CONTRACT_DRAFT.md`

```markdown
# Next Iteration — Architect Contract Draft

## Status

DRAFT — IMPLEMENTATION_NOT_AUTHORIZED

Stored under CONVERGENCE-0001 only.
Iteration ID: UNASSIGNED.
Do not initialize ITER-0025 or dispatch Worker from this document.

Explicit human resume and a fresh repository/contract check are required before approval for implementation.

## Base Repository State

Root: /home/ubuntu/poly-maker.
Branch: rdp-phase0-dev.
Draft baseline: 7aa329e02bc5708bc5c2f5df956c9e5036085a07.
Latest archived implementation: ITER-0024, CODE_READY, final Reviewer06 PASS.
Development status: PAUSED_FOR_DEVELOPMENT_CONVERGENCE.
MICRO_LIVE_READY: NO.
Real-money authorization: NOT_GRANTED.

Shared-Core Change: NO.
FL Behavior Impact: NONE.
IM Dependency: NONE.

## Delivery Gap

DG-001 — Authorized Initial NORMAL Submission.

This draft proposes closing the initial submission behavior, not the integrated runner, subsequent cycles or all terminal handling. DG-002–DG-012 remain open.

The operational-time check is a necessary send-gate element. Its implementation here does not close the full CanSafelyContinue/STOP behavior under DG-004/DG-005.

## Problem

Actual initial admission, ARMED capture, coherent factual revalidation and strict pair placement exist, but no retained RDP operation connects them into one deliberately authorized initial submission and typed ownership result.

Start: a healthy retained single-market session with a freshly issued initial NORMAL admission and no prior run orders.

End: either a default-denied/no-send result, or one resolved initial placement attempt containing the safely attributed complete/empty/partial/unknown outcome after actual SDK work has drained.

## Evidence / Current Behavior

- cli.py: rdp_live exposes read-only modes only.
- rdp_runtime.py issues initial admission and arms capture.
- revalidate_initial_normal_intent checks current facts through both callback threads but grants no live authority.
- Admission retains HUMAN_LIVE_AUTHORIZATION and OPERATIONAL_TIME_SUFFICIENCY as outstanding.
- gateway.place supports strict post-only pairs, supplied per-token ticks, two guard locations and typed uncertain outcomes.
- RDPPlacementOwner resolves acknowledgments and preserves blocked unknown/partial capture.
- No production RDP gateway.place call exists.

Sources: Frozen §§4, 15–18, 26; AGENTS §§4–6, 31, 36; DELIVERY_GAPS DG-001.

## Existing Components Reused

- Retained public/user inputs and exact official initial account/status evidence.
- Actual initial admission and trusted provenance.
- Initial arming and existing processor pre-ack capture.
- Coherent initial intent revalidation and bridge.
- Existing gateway.place with strict placement, forced post-only, opted-in two-order pair and per-token ticks.
- Existing typed acknowledgment/PlacementUnknown evidence.
- RDPPlacementOwner.resolve and current shield/drain patterns.

## Existing Components Modified

Proposed scope after resume:

- src/polymaker/rdp_runtime.py: explicit default-denied initial submission operation, task ownership, combined send guard and teardown integration.
- tests/test_rdp_placement.py: actual composed submission and failure-race tests.
- tests/test_rdp_runtime.py only for necessary teardown/default-observer compatibility.

Small RDP-local helper extraction is permitted. No production shared gateway, processor, CLI, configuration, strategy or capital formula changes are proposed.

If actual implementation requires broader scope, return to Architect rather than silently expanding.

## New Components Required

One retained initial submission operation and a small immutable submission result describing no-send or the resolved existing attempt.

No new transport, reservation ledger, strategy state or general controller.

## Desired Behavior

An explicit operation defaults to denied execution. Only deliberate authorization for this exact market can activate the submission path.

With authorization, validated operational budgets and current factual evidence, arm capture before the single pair POST, pass immutable per-token signing ticks and combine factual revalidation with authorization, interruption and time gates at both existing guard locations.

Resolve the actual typed result into the existing owner. Retain uncertainty and safely attributed IDs without retrying.

No production activation is exercised by development agents. Authorized cases are tested only with fake official SDK/network infrastructure.

## Minimal Scope

1. Default/absent/invalid authorization rejects before signing or POST. Read-only callers do not acquire authority by observing eligible data.
2. Bind explicit activation to the retained session's exact selected market and current initial admission.
3. Require finite positive order-operation and close/reconciliation budgets. Do not assume OPERATIONAL_TIME_SUFFICIENCY is already satisfied.
4. Implement TimeAvailable from remaining market time and those operational budgets: new risk requires time for the order operation plus scoped Cancel → Reconcile. Reject at the boundary and recheck at both guard locations.
5. Treat budgets as operational safety controls, not MaxRebalanceSec, a no-fill timeout or a strategy performance cutoff. Select/record ordinary engineering defaults only when the eventual caller needs them; this API may require explicit budgets.
6. Reuse initial admission, arming and the current factual revalidation. Verify all outstanding send gates; factual validity alone cannot authorize execution.
7. Retain pending capture and one submission owner before yielding into gateway.place. Serialize competing admission/arm/submit/lifecycle actions.
8. Delegate once with strict placement, forced post-only, opted-in pair parsing and exact captured token ticks.
9. Combine current authorization, interruption, operational deadline and factual validation in the existing pre-sign/final guard. Do not read mutable strategy state from the SDK thread.
10. Shield and drain the placement operation through first/repeated caller cancellation or teardown. SDK completion may outlast its operational budget; retain ownership and honest uncertainty rather than detaching or retrying.
11. Resolve complete, clean authoritative empty, typed partial/unknown and unexpected-error outcomes using the existing owner. Preserve original typed attribution and buffered fill evidence.
12. Caller interruption after a POST must not discard a returned acknowledgment or turn an uncertain operation into a clean empty result.
13. Return immutable no-send or owned-outcome evidence. Do not automatically start a cycle, retry POST, requote or recover unknown IDs.
14. Existing CLI/preflight/observe defaults remain unchanged. There is no agent-authorized live run.

## Invariants

- At most one initial placement operation and one pair POST.
- Capture precedes POST; inventory remains confirmed-fill-only.
- Frozen prices, sizes, minimum metadata and $5 reserve are unchanged.
- Both send guards enforce current facts and outstanding authorization/time conditions.
- A budget deadline cannot forcibly undo a blocking SDK POST; ownership survives until it drains.
- No ambiguity is rewritten as clean rejection.
- Attributed acknowledgments and merely observed IDs remain separate.
- Unknown/partial state blocks replacement and preserves available evidence.
- Foreign orders/inventory are neither adopted nor canceled.
- Existing evidence objects remain factual and execution_authorized=false.
- No real-money action occurs in development tests.

## Acceptance Criteria

1. Default/absent/invalid authorization yields zero signing, POST and cancel calls.
2. Invalid budgets, insufficient remaining time or equality at the required budget boundary deny new risk.
3. Fake authorized valid execution makes one strict post-only pair POST with the captured per-token ticks and exact Frozen economics.
4. Stale/changed evidence or elapsed time during rate wait/signing denies the final POST.
5. Pre-ack buffered confirmation and typed complete acknowledgment register/replay through the existing processor exactly once.
6. Clean empty results release only when existing owner criteria allow; partial/unknown/error outcomes retain blocking and attribution.
7. First/repeated cancellation and teardown during signing/POST wait for actual fake SDK work, resolve its evidence and perform no retry.
8. Concurrent submission and conflicting runtime actions are rejected without a second operation.
9. Result/logs accurately distinguish authorization, no-send, attempted POST and unresolved outcome; no sensitive raw exception text.
10. Existing read-only CLI/default runtime and FL behavior remain compatible.
11. Targeted, affected, predecessor and practical full-suite checks pass for the reviewed implementation.

## Required Tests

Block/count real DNS/socket access before imports. Use existing fake clients and real executor fixtures.

- Default denial and invalid authorization.
- Exact operational-time boundary, clock invalidity, budget expiry during rate wait and signing.
- Actual fresh admission → arm → both guard locations → one fake successful POST → typed ownership.
- Dynamic tick, quote/filter, account/status freshness and generation changes before final guard.
- Complete, authoritative empty, partial, ambiguous response, POST exception and unexpected placement error.
- Pre-ack accepted fill replay and malformed/pending capture.
- First/repeated cancellation and teardown during actual fake executor signing and POST.
- No duplicate submit, no detached work, no unknown retry, no foreign adoption/cancel.
- Existing observation/CLI and lifecycle regressions.

Run narrow submission tests, affected runtime/placement/execution/account/capital/lifecycle tests, FL regressions and full suite when practical. Record exact commands/results and reviewed hashes.

These tests are required only after implementation resume. Do not run them for this document-only draft.

## Observability Requirements

Record selected market, attempt, captured request/ticks, authorization state, operational budgets/deadlines, guard rejection category, attempted-send status, attributed/observed IDs, resolution outcome and replacement blocking.

Use existing structured logging. Do not claim real-money trading occurred in fake tests or that factual validation alone granted authorization.

## Live Validation

None during this proposed implementation.

After full delivery and explicit human real-money authorization, the human supervises one BTC5m market. Until then, all create/cancel validation remains fake; safe official read-only checks belong to final path evidence.

## Explicit Non-Goals

- CLI live activation or complete integrated runner.
- Automatic cycle start or subsequent NORMAL/REBALANCE execution.
- Complete CanSafelyContinue/STOP controller.
- Heuristic unknown-order recovery.
- New ledger, formula, reserve or external interface.
- Reporting/metrics beyond evidence required for this operation.
- Real-money order/cancel actions.

## Risks

The existing gateway has asynchronous rate waiting and blocking signing/POST. The operation must retain one owner across all phases.

Authorization/time enforcement cannot be delegated to the factual validator implicitly.

An SDK operation may finish after cancellation or deadline. Its actual typed outcome must survive drain and resolution; a timeout does not prove no POST.

## Deferred Findings

The remaining delivery behaviors DG-002–DG-012 stay OPEN.

Safe partial/unknown terminal handling remains DG-006; this submission operation must already retain blocking and evidence needed by that handler. It cannot defer loss of attribution, detached SDK work or false replacement release.

No new strategy-semantic decision is required by the proposed budget-based implementation. If implementation discovers a material timing policy beyond Frozen TimeAvailable, stop and identify the smallest missing authority.

## Human Approval Required

Implementation resume: REQUIRED and not granted.

Real-money trading: NOT_GRANTED.

This is a reviewable draft, not APPROVED_FOR_IMPLEMENTATION. Document approval and archival do not authorize Worker dispatch, iteration initialization or execution.
```

## Rationale and Remaining Authority

The register separates existing reusable prerequisites from observable missing delivery behavior. Its crosswalk includes every specified runtime safety item and the complete Frozen metric inventory; it does not defer mandatory safety or accounting to obtain a shorter roadmap.

The next draft connects the reviewed infrastructure to one concrete missing behavior: default-denied initial submission ending in retained typed ownership. It explicitly implements the outstanding authorization/time gates rather than assuming the factual validator resolves them.

No new trading-semantic authority is needed to materialize these documents. Potential metric-definition ambiguities remain explicit closure checks under DG-009; the proposal does not resolve them by invention.

After independent document review and archival, the required state remains **PAUSED_FOR_DEVELOPMENT_CONVERGENCE**, awaiting explicit human resume.

# RDP Phase 0 — Single-Market Delivery Gaps

## State and Authority

Status: IN_PROGRESS.

Implementation baseline: 7aa329e02bc5708bc5c2f5df956c9e5036085a07.
Root: /home/ubuntu/poly-maker.
Branch: rdp-phase0-dev.
Latest archived implementation: ITER-0024, CODE_READY, final Reviewer06 PASS.
MICRO_LIVE_READY: NO.
Real-money authorization: NOT_GRANTED.
Implementation resume authorization: GRANTED by explicit human resume; ITER-0025 requires a fresh formal Architect Contract.

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

## Resume and Current Action

CONVERGENCE-0001 remains immutable reviewed historical evidence. Human explicitly approved it and resumed development from27908548acbf595e4abdc7282252185d4163e267. The archived next contract remains a DRAFT; Architect must recheck actual code and freeze ITER-0025 before Worker.

Current target: DG-001 authorized initial NORMAL submission. AllDG-001–DG-012 currently OPEN. No gap closes automatically from an iterationPASS; only reviewed full closure or explicit still-open prerequisite evidence updates this register.

Development Status: IN_PROGRESS.
Current Iteration: ITER-0025.
Current Stage: ARCHITECT.
MICRO_LIVE_READY: NO.
Real-Money Trading Authorization: NOT_GRANTED.
Main Merge Authorization: NOT_GRANTED.

Serial Architect→Worker→Reviewer and automatic repairs/next minimalrequirement-linked iterations are authorized after formal contract approval. Stop only for genuine reservedhuman decision or independently reviewedMICRO_LIVE_READY. No real orders/cancels, strategyextensions, optionalsafetyrelaxation or optionalfeatures.

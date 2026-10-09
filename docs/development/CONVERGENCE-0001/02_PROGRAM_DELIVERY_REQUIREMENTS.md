# CONVERGENCE-0001 — Existing Human Delivery Requirements

Source: the human RDP Phase0 autonomous development program, §§5–7,17–26,28, together with active AGENTS.md §§29,31,36–38. This is a durable requirement cross-reference for the convergence review, not a new strategy specification. Frozen SOP controls semantics; approved ITER0005/0006 decisions remain unchanged.

The target is one single-market runnable strategy inside the existing poly-maker application, from /home/ubuntu/poly-maker, explicitly selecting exactly one BTC5-minute market. No second application, manual code edits between states, data duplication or IM import.

Integrated flow required: official discovery/selection → official market data and metadata → RDP state/NORMAL → order lifecycle/fills/reconciliation → REBALANCE → STOP → accounting/metrics/logs → market end. Runtime market, account, risk and execution interfaces only official Polymarket HTTP/WebSocket; no new runtime dependency/interface without reserved human authority.

Before readiness independently verify: official HTTP and WS paths; BTC5m selection; dynamic tick and minimum metadata; authenticated account/order reads; gated order-create and post-only; cancellation/reconciliation; partial fills and cancel/fill races; stale market and unreliable order/fill state fail-closed; CapitalGate; NORMAL/REBALANCE/STOP; accounting; metrics/logging; safe market termination.

Frozen states, confirmed-fill inventory, capital/accounting formulas and cancellation ordering remain authoritative. Starting capital30 dollars; fixed5-dollar future-repair capacity in NORMAL from approved decision. Required economic distinctions and Frozen §32 observability must be durable enough to reconstruct the run, including capital survival extrema and final market outcome when resolved. Historical FL data is infrastructure evidence, not RDP edge.

Required metrics follow Frozen §§22,28–32 and the human observability list: activity, confirmed inventory/extrema/durations, acquisition and balanced/residual costs, natural/rebalance economic deltas and rates, PriceConcession/STOP evidence, Locked/Worst/Best settlement PnL, cash/reservations/costs, capital extrema/final capital/drawdown/loss. No extra strategy filters or predictions. Definitions absent from current authority must not be silently invented.

Required regression gate: targeted and affected integration tests, predecessor regression, full suite; exclusions only if proven pre-existing and accepted by project contract. Runtime-path checks may use official read-only live validation where already supported. No real-money order/cancel to prove readiness; all execution tests fake/offline. Do not weaken any send gate.

Readiness audit: immutable specialist evidence for implementation, archived PASS iterations/current index, final independent project-level Reviewer PASS, final readiness report, reviewed state pushed and exact remote verified. Single explicit command and required configuration must actually reach safe pre-live path; document expected flow/safety/tests/limits, Frozen and external deviations NONE, real-money trading NO, recommended first human-supervised BTC5m validation.

At MICRO_LIVE_READY stop development and await human live authorization. Per AGENTS§29 create one final comparison/PR rdp-phase0-dev→main referencing baseline, Frozen SOP, index, final Reviewer and readiness artifact; no merge authority. First live validation and economic proof are human-supervised after readiness, not required autonomous trading.

This convergence task remains documents-only. It neither authorizes implementation nor schedules288 markets. After archival state PAUSED_FOR_DEVELOPMENT_CONVERGENCE, await explicit human resume; no ITER0025 or Worker dispatch.

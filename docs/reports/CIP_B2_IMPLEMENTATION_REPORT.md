# CIP B2 Implementation Report

Status: **IMPLEMENTED — AWAITING REVIEW**
Commit / working tree base: `97ab0f9` on `m4-dynamic-ev`, with unrelated pre-existing modified and untracked work preserved.
Reviewed predecessor baseline: `m4a-a1-pass` (`97ab0f9`) for the current Git tree. The local B1 research script and output are the frozen strategy comparator, but have no independent reviewed checkpoint.
Architecture reference: User's B2 specification and `docs/research/CIP_B2_RESEARCH_DESIGN.md`; the M4 production architecture is unchanged.

## Scope implemented

Implemented a research-only two-phase replay over the same 32 execution-replayable BTC5m markets. T0–T150 invokes the existing B1 gamma reconciler. At exact T150 it records a pre-cancel inventory and order snapshot, then permanently switches to buy-only, maker-only reduce-only control through T300. It records phase-specific fills, capital blockage, quote persistence, overshoot diagnostics, final pool economics, and five full representative after-fill paths.

## Files added

- `research_tools/cip_b2_two_phase_backtest.py`
- `docs/research/CIP_B2_RESEARCH_DESIGN.md`
- `docs/research/CIP_B2_BACKTEST_20261003.md`
- `docs/reports/CIP_B2_IMPLEMENTATION_REPORT.md`
- Ignored local results: `research_outputs/cip_b2_20261003/{summary.json,market_results.csv,orders.csv,fills.csv,inventory_paths.csv,phase_transitions.csv,action_audit.csv}`.

## Files modified

- NONE by B2. All pre-existing production, config, test, status and other research changes remain untouched.

## Important implementation decisions

- `TwoPhaseReplay` subclasses B1's research-only `PoolReplay` and calls its gamma `reconcile` before T150. It inherits R0's book reconstruction, available-time event order, fill predicate and order latency. The replay compares every B2 pre-T150 submitted order and fill against a fresh B1 prefix.
- The exact T150 snapshot is taken after lifecycle advancement to T150 and before requesting phase-switch cancellation. Cancel-pending orders remain reserved and fill-eligible until the inherited cancel latency expires.
- Reduce-only keeps a safe resting lighter-side quote even when best bid moves. It requests cancellation of heavy-side orders, places only the side opposing current nonzero net, stops permanently at zero net, and terminates at the B2-specified T300 market end. This differs from B1's T240 pre-close cutoff because B2 expressly specifies T150–T300 reduction.
- Existing full-order trade-through fill semantics were retained. The implementation handles arbitrary positive fill sizes and sign flips but does not infer partial fills from public print size.
- Repeated capital-blocked decisions are reported as decision events, with blocked duration integrated over replay time. The action audit records contemporaneous BBO, validity, phase, inventory and reason codes.
- Materiality thresholds were frozen in the research design before interpreting the B2 output; no parameter or policy tuning followed the result.

## Deviations from architecture

- NONE from the B2 research specification. This independent historical ablation does not revise the production M4 architecture.

## Tests added

- NONE. The requested full backtest and direct invariant checks served as validation.

## Tests executed

- `python3 research_tools/cip_b2_two_phase_backtest.py` completed all 32 markets and produced seven result files.
- Audited exact market and settlement-label coverage, 32 exact T150 transition times, monotone permanent REDUCE_ONLY phase, every B2 pre-T150 order/fill against B1, zero risk-increasing reduce-only submissions, zero BBO-only reprices, valid-book cancellation reasons, $25 capital bound, and official winner cash-flow identity.
- Checked each of 265 fills against inherited strict price and post-activation timing predicates; checked that 265 fill path rows, 32 switch rows and 32 final rows are present.
- Direct in-memory scenarios exercised a heavy-side fill while cancel was pending and a legal 5-share reduce fill taking net from +2 to −3. The respective overshoot counters and subsequent side selection behaved as specified.
- Checked Python syntax, tracked diff whitespace, and new B2 file whitespace.

## Test results

All executed validations passed. Pre-T150 B1 order/fill prefixes match exactly. `BBO_ONLY_REPRICE=0`; no heavy-side new reduce order, phase reversal, data error or $25 violation was found. B2 recorded 238 gamma-phase fills, 27 reduce-phase fills, 540 final matched shares, 245 final residual shares, +$3.0267 GammaPnL, −$72.4467 ResidualPnL and −$69.42 TotalPnL. The result is classified **TWO_PHASE MODEL SUPPORTED** under the design's frozen materiality rules, pending independent review. Full metrics and market paths are in `docs/research/CIP_B2_BACKTEST_20261003.md`.

## Known limitations / unresolved issues

- R0's inherited maker fill rule fills the whole remaining clip on a qualifying public SELL print and has no queue-position or public-print-size model. The synthetic overshoot and sign-flip checks show code behavior, not observed frequency in the 32 markets.
- Only 19 of the 32 execution-replayable markets overlap the stricter M4A integrity-PASS subset.
- B2's reduce window ends at T300, whereas frozen B1 ceased new exposure at T240. B1 comparison is a whole-policy comparison, not an isolated same-cutoff attribution.
- B1 and B2 local research artifacts are untracked and await independent review; no reviewed PASS checkpoint is claimed.

## Safety / behavior impact

Live trading semantics changed: **NO**. The script reads only repository-local captured official data and cached official settlement labels, writes ignored local research output, calls no external interface and sends no order.

## Git diff summary

Four new B2 research-only source/document files and seven ignored generated result files. No B2 change to production code, configuration or tests; pre-existing working-tree changes were preserved.

## Handoff to Reviewer

Independently inspect exact T150 sequencing, the B1 gamma-prefix comparison, 27 reduce-phase fill paths, heavy-side cancellation with latency, reduce-only placement sides, capital reservation and all action reasons. Verify the seven output files against the result report and review the B1 T240 versus B2 T300 comparison limitation. No Worker PASS or checkpoint tag has been created.

# Dynamic EV exit research: design and repository assessment

Status: proposed design; no implementation or trading authorization implied.
Inspected 2026-10-01, repository HEAD `fff2842`.
Scope: BTC5m FavoriteFirst v0.3, research only, maximum 5 shares per outcome.

## 1. Recommendation

Build M4A: an optional observer that records point-in-time market state and accepted fill evidence, independently of strategy decisions, through market end. Reuse the current per-token books, parsers, owned-order registry, Fill Truth, journal, and run reports. Start with 1-second snapshots plus event capture. Do not fit a model yet.

The inspected live journal and database contain **one FavoriteFirst first-fill episode**, with two persisted maker fills: first leg 5 at 0.64 and pair leg 5 at 0.36. Its gross complete-set result is $0 before costs/rebates. This is a reconstruction fixture, not evidence of profitability or enough data for calibration. A large number of book messages does not increase the independent episode sample size.

Two modeling distinctions govern the design:

1. PAIR starts a specified maker execution procedure. It does not instantly realize the quoted complete-set PnL. Nonfill, partial fill, delayed cancellation, and residual settlement belong in its value.
2. The best future price on a recorded path is a hindsight opportunity label. It is not the value achievable by an online stopping policy. Continuation models must learn returns from a feasible, nonanticipating policy.

## 2. Repository assessment and evidence

Paths are relative to the repository. Line references describe the inspected commit.

| Component | Current implementation and reuse | Research implication |
|---|---|---|
| Baseline policy | `src/polymaker/strategy/favorite_first.py:42,145`: frozen config, memory, snapshot, pure `evaluate` | Import the pure policy for replay; pin code/config hashes. Do not rewrite its decisions. |
| Live defaults | `src/polymaker/favorite_live.py:67`: $0.50 emergency, Runner offset 2 ticks, normal/urgent reprice 0.75/0.30 seconds | Capture effective configuration per run, including inherited defaults. |
| Lifecycle | `favorite_live.py:476,661`: one committed market per invocation; waits to T+300, reconciles and shuts down | Capture until T+300 even after actual COMPLETE/T+270. No sequential live orchestrator needs to be added for research. |
| Decision integration | `src/polymaker/engine.py:835`: snapshots, strategy evaluation, operational guards and repricing | Snapshot the actual inputs and resulting desired quote as an observational record. Pure `evaluate` alone is not the complete live behavior. |
| Fill economics | `engine.py:605`, `src/polymaker/state/tracker.py:111`: accepted fills scoped to registered first/pair order IDs | Reuse `economic_fills_for_orders`; do not infer strategy fills from account positions or public trades. |
| Fill lifecycle | `tracker.py:183`: `(trade_id, maker_order_id)` identity; MATCHED accepted provisionally; status progression, quarantine/FAILED exclusion, REST recovery | Preserve both as-known revisions and final validity. Never rewrite past features with later reconciliation. |
| Persistent ledger | `src/polymaker/state/store.py`: `owned_orders`, `fills`, positions and order log | `owned_orders.source` distinguishes first/pair. `fills` has a composite-encoded trade key but no separate accepted-at timestamp/status history. It is insufficient alone for causal replay. |
| Fill Truth | `src/polymaker/execution/filltruth.py`, existing processor and reconciliation results | Read existing acceptance/completeness evidence only. No alternative fill ledger or reconciliation implementation. |
| Books | `src/polymaker/marketdata/service.py:39`: separate real books for both subscribed outcome tokens; `OrderBook` sorted levels and `BookView` | Use both actual token books. The old YES-canonical module docstring does not describe this service's current per-token usage. Do not synthesize NO as 1−YES. |
| Public events | `marketdata/parse.py`: book, price changes, trade price/size/side, tick changes | Existing feeds support L2 and trade-flow features. Exact queue position and true market-wide cancellation rates are not observable. |
| Existing estimators | `strategy/estimators.py`: volatility, flow and markout | These describe prediction-market prices, not BTC spot. Prefer causal token-specific windows for the first research feature set; shared market flow mixes tokens. |
| Journal | `src/polymaker/journal.py`: `{ts,kind,data}`; market/user frames and orders out | Raw source payloads and file order are reusable. `ts` mixes exchange timestamps and local action times; local receipt/availability is missing. |
| Sparse decision logs | `engine.py:901`: writes only when selected state/quote/quantity/peak fields change | Cannot treat these as 1-second observations or infer no movement between rows. |
| Missing mutation evidence | `engine.py:1427`: REST book replacement; `marketdata/service.py:237`: tick-size update | Neither replacement levels nor tick changes are currently written as complete journal events. Both are minimally required for exact new replay. |
| Run artifacts | `favorite_report.py`, `favorite_live.attribution_report`: `raw.log`, `events.jsonl`, `run.json`, `chat_report.md` | Reuse identities, configs, accepted fill paths, triggers and engineering results. Final rebates/settled PnL intentionally remain unknown absent evidence. |
| Resolution/settlement evidence | `logs/settlement/*/sweep.json`; `settlement_sweep.py` exposes condition ID and payout vector | Offline evidence join only. A wallet-level redemption is not automatically attributable to this episode. No settlement changes. |
| BTC reference feed | No BTC spot/reference/open feed found in `src`, `tests`, `config`, or `livecfg` | BTC momentum/reference-distance hypotheses remain unanswerable initially. Null fields, explicit availability flag; no new feed in M4A. |
| Dependencies | Python 3.12, Pydantic, SQLite, existing packages | NumPy/pandas/SciPy/sklearn/Arrow absent in inspected venv. M4A/B need no new dependency. Add a research-only sklearn extra at M4D if justified. |

### Baseline semantics that must be reproduced

- Favorite is the larger of the two token midpoints; this is a market-price feature, not a calibrated probability estimate.
- Entry starts at T+30 and stops at T+180; first entry at own token best bid after 2-second confirmation within [0.60,0.70].
- During the risk window there is no pair quote. A working first-leg remainder can continue/reprice under the existing rules.
- Emergency triggers at quoted total lockable PnL <= −$0.50. Otherwise at 20 seconds, first orders must be finalized; positive quoted PnL enters RUNNER, nonpositive enters PAIR_STOP. Partial quantities below venue minimum can become unpairable.
- Aggressive pair quote is legal tick-rounded `opposite ask − tick`. Runner quote is another 2 ticks below that, not necessarily opposite best bid.
- Trailing arms at peak >= $0.05 and triggers at a 50% drawdown from that peak. Strategy peak tracking starts in the relevant pair/Runner states; the research all-post-fill peak is a separate field.
- T+240 selects forced maker pairing; it cannot guarantee a fill. T+270 currently removes all quotes, including pair quotes. This is stronger than simply forbidding additional first-leg risk. Residual inventory can remain to settlement.
- Engine safety/readiness/repricing/finalization gates can suppress or delay a pure-policy quote. Record them for baseline fidelity; do not redesign them.
- The $0.50 quote-based trigger is not a guaranteed maximum realized loss. Maker-only execution cannot enforce an instantaneous cash-loss bound. Any separate hard loss control stays outside the model; capture its existing configuration without inventing a new threshold.

### Bounded local data audit

`journal/live.jsonl`: 2,850 book messages; 211,455 price-change messages; 1,422 public trade messages; 12 FavoriteFirst decision rows; 5 orders-out batches; 8 user-order messages; 6 user-trade messages; zero malformed JSON lines. Its source timestamps span 2026-09-30 14:40:30.693–14:50:01.715 UTC across event kinds.

Only one condition ID occurs in FavoriteFirst decisions. Seven rows are post-fill; their maximum consecutive gap is 8.8965 seconds. `state.db` contains four owned orders (two first-leg, two pair-leg), all for that condition, and two maker fill rows. The log also contains a book-drift correction and reconciliation/anomaly events. Therefore historical event-time reconstruction is exploratory, not exact as-known replay.

There is no `logs/runs/` directory in this checkout, although current code supports those artifacts. The legacy log and journal are the available run evidence. `journal/paper.jsonl` exists but was not audited for real-fill research and must not be pooled as real execution evidence. Existing settlement records for the episode's condition include resolved payout vector [1,0]; aggregate redemption amounts must not be counted as strategy profit.

Profile method: stream the entire live JSONL with `json.loads`, count by `kind`, group `favorite_first_decision` by full `data.cid`, select post-fill rows by nonnull `first_leg_side`, and compute adjacent timestamp gaps. Database was opened with SQLite URI `file:state.db?mode=ro`; queries were `SELECT source,count(*),count(distinct condition_id) FROM owned_orders GROUP BY source`, `SELECT side,price,size,is_maker,ts FROM fills ORDER BY ts`, and `PRAGMA table_info(...)`. Only metadata/economics were used; no credentials were read. This audit measures the inspected files, not every possible external run archive.

## 3. State, action and economic value

### State and knowledge time

`s_t = (x_t, qY_t, qN_t, CY_t, CN_t, orders_t, phase_t, constraints_t)`.
`x_t` contains the causal market/path features below; quantities/costs are known accepted strategy fills; `orders_t` includes outstanding quantities and pending cancel/finalization states. Include market elapsed time and time to both order cutoff and resolution. This is an approximate sufficient state, not a claim of a perfectly Markov market.

Decision time uses local **available-at** time. Exchange execution time is retained for economic attribution and latency analysis. A fill first discovered by REST at t+4 cannot appear in the feature history at t. Both clocks and a monotone sequence are required.

Initial candidate actions:

- `HOLD`: wait Δ=1 second without starting the pair protocol; reconsider using only newly available information. Existing first-leg acquisition follows the fixed acquisition contract described below.
- `PAIR`: commit to protocol `P1`: finalize any first-leg orders through the modeled existing rules, submit residual-sized post-only opposite BUY at legal ask−tick, reprice no faster than 0.30 seconds with cancel acknowledgment/late-fill handling, and continue attempts until complete or T+270. Venue minimum and inventory guards always apply. No taker or SELL.
- `INELIGIBLE`: a research output when inputs are invalid or the required state is unsupported. This does not issue a trading instruction.

P1 is an offline execution specification, not a new live executor. Once committed, v1 does not optimize cancellation/reversion to HOLD; pending and partially filled orders evolve under P1. This restriction makes the first model a meaningful stopping problem. A later generalized controller would need an expanded action/state model.

### Three distinct economic quantities

For a held first-leg quantity q at actual VWAP p, no previous pairs:

`theoretical_mid_pair_pnl = q × (1 − p − opposite_mid)`.

`quoted_pair_pnl = q × (1 − p − legal_opposite_maker_quote)`.

Neither asserts execution. For existing partial pairs, add actual gross PnL on paired quantity and apply the hypothetical quote only to the unpaired remainder. Quote PnL is null when a legal quote/book is unavailable; feasibility of submitting the residual is a separate flag.

Executable value is expected **whole-episode cash-equivalent PnL** under P1, including all possible outcomes of the attempt. With q matched first-leg shares, J eventual pair fills `(z_j,a_j)`, residual `r=q−Σz_j`, first-side payout Y, actual first cost C, rebates R and attributable costs F:

`Π = Σ_j z_j(1−a_j) + rY − C + R − F`.

Generalize to both residual sides and nonbinary payout vectors:

`Π = paired_qty + residual_yes×payout_yes + residual_no×payout_no − all_buy_costs + R − F`.

Merged or redeemed quantities are accounted once. Complete sets worth $1 are economic receivables before settlement, not an assertion of received cash. Report settled cash separately. Unknown outcome/costs remain unknown, or produce clearly labeled bounds/sensitivity scenarios.

Let K_e be the estimated joint execution/market transition law for execution scenario e. Define:

`Q_PAIR,e(s) = E_K_e[Π | s, commit to P1 now]`.

`Q_HOLD,e(s) = E_K_e[V_e(S_(t+Δ)) | s, HOLD]`.

`V_e(s) = max(Q_PAIR,e(s), Q_HOLD,e(s))` while both actions are allowed.

At/after T+240 only P1 is allowed for pairable residual; at/after T+270 no new order is allowed and pending orders/cancel outcomes resolve under existing rules, followed by residual payout at resolution. Pending cancellation is not instant disappearance. Unpairable residuals retain settlement exposure. Safety overrides restrict the admissible action set independently of EV.

The same first-leg sunk cost appears in both Q values and cancels in their difference. Do not subtract it twice in a recursion. No artificial time penalty in the initial risk-neutral objective. If a capital/risk penalty is later used, specify its units and value in advance and report both penalized utility and raw dollars.

Expectations use the empirical real-world distribution conditional on an owned maker fill, not an option-pricing risk-neutral measure. Even an unbiased market price before order placement need not remain unbiased conditional on our maker order being filled. That selection effect is a hypothesis to measure, not an assumed edge or assumed loss.

The simple mixture `p_fill × quoted_pnl + (1−p_fill) × 0` is invalid: nonfill has a state-dependent residual value, and fill probability correlates with adverse movement. Model joint fill quantity, time, execution prices and residual path.

Shadow rule: `PAIR` if `Q_PAIR >= Q_HOLD + m(s)`, else HOLD, subject to eligibility and hard overrides. `m>=0` is a dollar-valued hurdle for choosing PAIR; a positive margin biases this inequality toward HOLD. It is not inherently a risk-conservative rule. Use uncertainty to report ABSTAIN/INELIGIBLE when the sign is unresolved; select any tie rule/margin on validation only. The live baseline remains the fallback throughout this milestone.

### Partial first fills and the counterfactual contract

Episode means one first-leg acquisition cycle, starting at its first accepted positive maker match, not a new episode for every partial trade. Preserve every fill revision and first-order finalization time.

Two explicitly separate evaluation panels avoid silently assuming the final 5 shares existed at the first match:

1. **Fixed-inventory exit panel (first modeling panel):** evaluate states once acquisition is terminal and the accepted quantity is known, with no active pair order. The common initial state is observable at that time. Track the delay from first match. Immediate Pair at this landmark is named `immediate_at_finalization`, not immediate-at-first-fill. Full first matches can have a near-zero delay, but still require authoritative terminal state.
2. **All-first-fill panel (required for the complete A–F comparison):** at the first known match, seed only then-known quantity. Simulate the unchanged baseline acquisition/replacement policy until each candidate commits to pairing, then simulate first-order cancellation/finalization and possible late fills. Do not reuse actual subsequent first fills after a counterfactual cancel. All candidate policies share this acquisition/execution simulator. Replay A retains its actual 20-second acquisition behavior. Immediate B requests pairing at the first known match but may wait for finalization/minimum size.

Capture all partial and failed episodes in M4A. Report panel coverage and excluded/unsupported counts. Until acquisition/cancel simulation is validated, only the first panel supports model comparisons; there is no claim of superiority for the complete first-fill population. Fixed-inventory labels at arbitrary later states freeze the quantity known at that state and are explicitly diagnostic, not whole-episode replay results.

## 4. Architecture and milestones

```text
existing books + journal + owned fills/orders + baseline decisions
                              |
                     optional read-only observer (M4A)
                              |
                  research events + 1 s state records
                              |
              episode builder / causal features / labels (M4B)
                              |
             common execution simulator + policy adapters (M4C)
                              |
            chronological estimation + stopping model (M4D)
                              |
           independent shadow scorer -> decision records (M4E)
                              |
                       evidence review (M4F)
```

There is no arrow from research/scoring to the execution gateway, reconciler, settlement service, or strategy memory.

| Stage | Deliverable | Gate |
|---|---|---|
| M4A | Versioned capture, source manifest, observed Episode/StateSnapshot records and quality audit | Data capture acceptance criteria in section 12; no economic edge requirement |
| M4B | Deterministic episode reconstruction, point-in-time features, opportunity labels, censoring report | Source/quantity reconciliation, no lookahead, complete comparable horizons; small samples explicitly descriptive |
| M4C | A–E replay adapters, execution scenario manifest, observed-vs-simulated checks | Common simulator, residual valuation, acquisition contract and uncertainty disclosed; no “executable” claim on quote-only data |
| M4D | Model 0 conditional statistics; small supervised models; restricted stopping layer F | Walk-forward predictive improvement and out-of-sample policy comparison beyond simulation noise |
| M4E | Read-only score process consuming immutable snapshots, emitting ModelDecision | Causal feature parity, latency/availability audit, no trading capability, supported-state coverage |
| M4F | GO / NO EDGE / INCONCLUSIVE report | Conservative-assumption results, meaningful precision and risk criteria; any live control is a separately authorized milestone |

## 5. Exact durable schema proposal, version 1

Serialization: append-only UTF-8 JSONL in research-owned files; Pydantic models in future implementation with unknown fields rejected. No migration of `state.db`. Derived tables can initially be separate SQLite tables with JSON nested columns; Arrow/Parquet is optional later.

Types: `D` = finite decimal string, parsed as Decimal for accounting; `F` = finite float for statistical features; `I` = integer; `B` = boolean; `S` = string; `T` = UTC Unix microseconds as integer; `N` suffix means nullable. Null means unknown/unavailable, never zero. Quantities are shares, prices USD/share, PnL/costs USD, durations seconds unless suffixed `_us`/`_ns`. No NaN/Infinity. Every field below is required; nullable values may be null. Enum values are explicit except provenance reason strings. All arrays can be empty. No credential or wallet fields.

Common to each of the five objects: `schema_version:I=1`, `dataset_id:S`, `record_id:S`, `created_at_us:T`, `source_refs:SourceRef[]`. `SourceRef={path:S,sha256:SN,offset_bytes:I,length_bytes:I}`; hash is finalized on closing the source file. Dataset manifests carry code commit, config content/hash, feature/label/policy/execution versions, file hashes, capture period and split boundaries. Every version ID resolves to an immutable manifest, not an undocumented arbitrary string.

### Episode

Grain: one acquisition cycle; append revisions, PK `(episode_id,revision)`; only latest revision is used for final outcomes, as-of revision used for history.

```text
episode_id:S                 # hash(condition_id, initial first_order_id); stable across restart
revision:I                   # increasing; never overwrite
revision_known_at_us:T
run_ids:S[]                  # process invocations; restart does not duplicate episode
market_slug:S
condition_id:S
yes_token_id:S
no_token_id:S
market_start_us:T
market_end_us:T
strategy_version:S
strategy_config_id:S
first_side:enum(YES,NO)
first_order_id:S
first_fill_key:S             # canonical encoding of trade_id + maker_order_id
first_fill_exchange_us:TN
first_fill_known_at_us:T
first_match_price:D
first_match_qty:D
first_leg_finalized_at_us:TN
first_leg_final_qty:DN
first_leg_final_cost_usd:DN
first_leg_final_vwap:DN
first_order_ids:S[]
pair_order_ids:S[]
actual_pair_start_us:TN
actual_pair_complete_us:TN
actual_exit_trigger:enum(NONE,EMERGENCY_STOP,PROBATION_FAILED,TRAILING_STOP,TIME_FORCED,OTHER)
actual_terminal_state:S
actual_paired_qty:DN
actual_residual_yes_qty:DN
actual_residual_no_qty:DN
actual_gross_pnl_usd:DN
actual_fees_usd:DN
actual_rebate_usd:DN
actual_settlement_cost_usd:DN
actual_net_pnl_usd:DN
actual_cash_received_usd:DN
payout_yes:DN
payout_no:DN
resolution_known_at_us:TN
resolution_source_ref:SourceRef|null
capture_start_us:T
capture_end_us:TN
capture_status:enum(OPEN,COMPLETE,CENSORED,INVALID)
fill_truth_status:enum(PROVISIONAL,COMPLETE,UNRESOLVED,INVALID)
quality_reasons:S[]
```

First-match fields describe the initial acceptance; a later invalidation appends an INVALID revision, not a deleted episode or silently changed first timestamp. All acquisition fills live in event evidence. Episode totals/outcomes are forbidden as feature-join columns except the initial facts available at the snapshot's cutoff.

### StateSnapshot

Grain: one actual captured state per episode/scheduled tick or material event. PK `snapshot_id`; unique `(episode_id,session_id,snapshot_seq)`.

```text
snapshot_id:S
episode_id:S
session_id:S
snapshot_seq:I
sample_kind:enum(PERIODIC,FILL,ORDER,SAFETY,FINAL)
scheduled_at_us:TN
available_at_us:T
monotonic_ns:I
knowledge_seq:I              # last applied observer event included
episode_revision:I
market_elapsed_s:F
seconds_to_end:F
seconds_to_forced_pair:F
seconds_to_order_cutoff:F
since_first_known_fill_s:F
since_first_exchange_fill_s:FN
first_side:enum(YES,NO)
first_qty:D
first_cost_usd:D
first_vwap:DN
pair_qty:D
pair_cost_usd:D
pair_vwap:DN
yes_qty:D
no_qty:D
paired_qty:D
unpaired_first_qty:D
unpaired_opposite_qty:D
first_finalized:B
pair_terminal_authoritative:B
baseline_state:S
baseline_pair_trigger:SN
baseline_peak_quoted_pnl_usd:DN
baseline_quote:Quote|null
effective_quote:Quote|null   # after existing engine guards/reprice throttle
working_orders:WorkingOrder[]
yes_book:BookState
no_book:BookState
market_connected:B
user_connected:B
market_generation:I
user_generation:I
fill_truth_ready:B
order_state_resolved:B
safety_reasons:S[]
tick_size:D
venue_min_order_qty:D
theoretical_mid_pair_pnl_usd:DN
quoted_pair_pnl_usd:DN
candidate_pair_quote:Quote|null
pair_submission_feasible:B
all_postfill_peak_quoted_pnl_usd:DN
drawdown_quoted_pnl_usd:DN
execution_estimate:ExecutionEstimate|null
features:FeatureVector
coverage_valid:B
quality_reasons:S[]
```

Nested types:

```text
Quote={token_id:S, side:enum(BUY), price:D, qty:D}
WorkingOrder={order_id:S, role:enum(FIRST,PAIR), quote:Quote,
  submitted_at_us:TN, acknowledged_at_us:TN, remaining_qty:D,
  cumulative_fill_qty:D, lifecycle:S, cancel_requested_at_us:TN,
  cancel_acknowledged_at_us:TN, terminal_authoritative:B}
Level={price:D,qty:D}
BookState={token_id:S,bids:Level[],asks:Level[],levels_recorded:I,
  best_bid:DN,best_ask:DN,best_bid_qty:DN,best_ask_qty:DN,
  mid:DN,spread:DN,exchange_at_us:TN,received_at_us:TN,
  applied_at_us:T,book_seq:I,snapshot_generation:I,
  source:enum(WS,REST),valid:B}
ExecutionEstimate={execution_model_id:S,protocol_id:S,scenario:enum(OPTIMISTIC,BASE,CONSERVATIVE),
  fill_horizon_s:F,p_any_fill:FN,p_complete_fill:FN,expected_fill_qty:FN,
  mean_first_fill_latency_s:FN,ev_pair_now_usd:FN,
  ev_lower_usd:FN,ev_upper_usd:FN}
```

BookState retains top 5 levels on each side; full snapshots/deltas and REST replacements are reconstructable from source events. Do not compute deeper depth from a top-5-only record. Invalid or absent books use null scalars/empty levels and `valid=false`, not a fabricated empty valid market.

### FutureLabel

Grain: one snapshot × horizon × target execution scenario. PK `(snapshot_id,horizon_s,label_version,execution_model_id,protocol_id,scenario,inventory_contract)`.

```text
label_id:S
episode_id:S
snapshot_id:S
label_version:S
horizon_s:enum(1,3,5,10,20,30,60)
requested_end_us:T
observed_end_us:T
label_status:enum(COMPLETE,CENSORED,INVALID)
censor_reason:SN
inventory_contract:enum(FROZEN_ASOF,FULL_ACQUISITION_REPLAY)
frozen_first_qty:D
frozen_first_cost_usd:D
scenario:enum(QUOTE_ONLY,OPTIMISTIC,BASE,CONSERVATIVE)
execution_model_id:SN
protocol_id:SN
opposite_mid_at_h:DN
opposite_bid_at_h:DN
opposite_ask_at_h:DN
opposite_maker_quote_at_h:DN
min_opposite_maker_quote:DN
max_quoted_pair_pnl_usd:DN
min_quoted_pair_pnl_usd:DN
quoted_mfe_usd:DN
quoted_mae_usd:DN
quoted_hit_0:BN
quoted_hit_005:BN
quoted_hit_010:BN
quoted_hit_020:BN
quoted_loss_below_minus030:BN
quoted_worsen_by_010:BN
quoted_worsen_by_030:BN
quoted_worsen_by_050:BN
time_to_quoted_nonnegative_s:FN
nonnegative_event_observed:BN
nonnegative_followup_s:F
oracle_best_full_pair_price:DN
oracle_best_full_pair_pnl_usd:DN
oracle_full_pair_found:BN
pair_now_value_usd:FN
hold_reference_value_usd:FN
reference_policy_id:SN
execution_draw_count:I
execution_mc_se_usd:FN
terminal_payout_yes:DN
terminal_payout_no:DN
actual_episode_pnl_usd:DN
```

`pair_now_value` and `hold_reference_value` are feasible protocol/policy rollout values to the common terminal endpoint, not the oracle maximum. They are null until M4C. For QUOTE_ONLY, execution/oracle fields are null and draws=0. Unknown outcomes do not become losses or wins. Survival time is nullable; event flag plus followup distinguishes no hit from missing observation.

### ModelDecision

Grain: one model version/scenario decision per snapshot. PK `(snapshot_id,model_id,scenario)`.

```text
decision_id:S
episode_id:S
snapshot_id:S
model_id:S
feature_version:S
execution_model_id:S
protocol_id:S
scenario:enum(OPTIMISTIC,BASE,CONSERVATIVE)
training_cutoff_us:T
input_knowledge_seq:I
scored_at_us:T
score_latency_ms:F
ev_pair_now_usd:FN
ev_continue_usd:FN
ev_difference_usd:FN        # pair minus continue
difference_lower_usd:FN
difference_upper_usd:FN
interval_coverage:FN       # e.g. 0.95, with method in model manifest
decision_margin_usd:F
preferred_action:enum(PAIR,HOLD,ABSTAIN,INELIGIBLE)
confidence_status:enum(CALIBRATED,UNCALIBRATED,OUT_OF_SUPPORT,INVALID)
support_episode_count:I
override_reason:SN
baseline_action:S
mode:enum(SHADOW)
```

Confidence is not an invented percentage. Record interval method/calibration and support. Every shadow record remains observational, including a hard-override annotation.

### PolicyReplayResult

Grain: one episode × policy × execution scenario × seeded simulation draw × panel. Aggregate metrics are derived separately. Unique key includes dataset/version IDs.

```text
replay_id:S
episode_id:S
policy_id:S
policy_config_id:S
execution_model_id:S
scenario:enum(OPTIMISTIC,BASE,CONSERVATIVE,OBSERVED)
panel:enum(FIXED_INVENTORY,ALL_FIRST_FILL,OBSERVED_BASELINE)
seed:I
draw_index:I
split:enum(TRAIN,VALIDATION,TEST,UNASSIGNED)
status:enum(COMPLETE,CENSORED,UNSUPPORTED,INVALID)
reason:SN
initial_snapshot_id:S
first_pair_attempt_us:TN
first_pair_fill_us:TN
pair_complete_us:TN
pair_attempted:B
complete_pair:B
forced_pair_attempted:B
emergency_triggered:B
stop_trigger:enum(IMMEDIATE,FIXED_TIME,PNL_TARGET,TRAILING,MODEL,PROBATION_FAILED,EMERGENCY,TIME_FORCED,NONE)
first_qty:D
first_cost_usd:D
paired_qty:D
residual_yes_qty:D
residual_no_qty:D
pair_cost_usd:D
maker_fill_qty:D
submitted_order_count:I
orders_with_fill_count:I
filled_order_first_latencies_s:F[]
unfilled_order_followup_s:F[]
rejected_order_count:I
cancel_count:I
gross_pnl_usd:DN
fees_usd:DN
rebate_usd:DN
settlement_cost_usd:DN
net_pnl_usd:DN
net_pnl_lower_usd:DN
net_pnl_upper_usd:DN
mae_quoted_usd:DN
mfe_quoted_usd:DN
single_leg_exposure_s:F
inventory_risk_usd_seconds:F
peak_inventory_risk_usd:D
pair_fill_latency_s:FN
ev_pair_at_decision_usd:FN
ev_continue_at_decision_usd:FN
decision_snapshot_id:SN
decision_id:SN
counterfactual_value_from_decision_usd:FN
event_trace_ref:SourceRef
```

Actual observed baseline uses no invented execution probabilities. Its `OBSERVED` row is an audit comparison, not a substitute for simulating A under the same assumptions as B–F.

### Supporting event envelope and manifests (necessary provenance)

The five analytical objects cannot replace event evidence. Capture an immutable research event envelope:

```text
ResearchEvent={schema_version:1,session_id:S,event_seq:I,condition_id:S,
  episode_id:SN,kind:enum(BOOK,DELTA,TRADE,TICK,REST_BOOK,CONNECTION,
    ORDER_SUBMIT,ORDER_ACK,CANCEL_REQUEST,CANCEL_ACK,ORDER_STATE,
    FILL_REVISION,BASELINE_DECISION,SAFETY,GAP),
  exchange_at_us:TN,received_at_us:TN,available_at_us:T,monotonic_ns:I,
  source:enum(MARKET_WS,USER_WS,REST,ENGINE,OBSERVER),
  connection_generation:IN,source_ref:SourceRef|null,payload:object}
```

Market payloads retain the existing parser's source message, no credentials. REST_BOOK uses `{token_id,bids:Level[],asks:Level[],book_hash:SN,tick_size:D}`. FILL_REVISION uses `{trade_id:S,maker_order_id:S,role:enum(FIRST,PAIR),token_id:S,price:D,qty:D,status:S,accepted:B,quarantined:B,fill_truth_ready:B}`. Order payloads use WorkingOrder plus event's quote/role and request correlation ID; failed or unknown ACK must be explicit. GAP payload gives lost sequence range if known, count and reason. CONNECTION records stream and connected flag. BASELINE_DECISION references the original fields/config; SAFETY records existing reason/state. Use existing raw journal references where complete; add payloads only for missing evidence and sanitized normalized order/fill facts. Final manifest enumerates every event payload version and source file checksum before builder acceptance.

## 6. Feature specification

`FeatureVector` is a fixed-key JSON object with the following exact expansions. Numeric features are nullable F, flags nullable B, and categorical `first_side`/`favorite_side` are nullable YES/NO. Null accompanies missing history or invalid coverage. Metadata, raw books, costs and working orders live in StateSnapshot; these derived feature names are the model allowlist. All windows are backward-looking `(t−w,t]` by availability time, on a 1-second grid for price differences. A full valid window is required. Warm-up comes from existing subscribed pre-fill history.

Source codes: S=accepted strategy state; B=actual token book; E=observed execution/public-event history; D=causal derivation; X=unavailable external BTC feed. Frequency “1s/event” means persisted each second and on material fill/order/safety changes. Unless marked otherwise, live means computable from the existing source with the proposed capture. No eventual outcome, final VWAP or future fill may enter.

| Exact feature name(s) | Definition / source | Frequency; live | Leakage or interpretation risk |
|---|---|---|---|
| `first_side` | Accepted first token mapped by market metadata; S | Event; yes | Never infer from eventual inventory |
| `first_fill_price`, `first_fill_qty` | Initial accepted match price/quantity; S | Initial event; yes | Not final acquisition VWAP/size |
| `first_vwap`, `first_qty`, `first_cost_usd` | Accepted cumulative first economics known now; S | 1s/event; yes | REST discoveries enter only when known |
| `since_first_fill_s` | Time since first local acceptance; S | 1s; yes | Exchange timestamp is separate |
| `market_elapsed_s`, `time_to_end_s`, `time_to_forced_s`, `time_to_cutoff_s` | t−start, end−t, max(0,start+240−t), max(0,start+270−t); S | 1s; yes | Do not infer actual resolution publication time |
| `yes_qty`, `no_qty`, `paired_qty`, `unpaired_qty` | Accepted scoped economics; S | 1s/event; yes | Recompute on simulated fills during replay |
| `theoretical_pair_pnl_usd`, `quoted_pair_pnl_usd` | Section 3; D | 1s/event; yes | Quote is not obtainable execution |
| `executable_pair_ev_usd` | Validated P1 model expectation; D | 1s; M4D+ only | Null in M4A; train-fold predictions only for training features |
| `peak_quoted_pair_pnl_usd`, `drawdown_from_peak_usd` | Maximum through t; peak−current; D | 1s/event; yes | Causal research peak; not eventual max or baseline memory peak |
| `{yes,no}_{bid,ask,mid,spread}` | Actual token touch, midpoint and ask−bid; B | 1s/event; yes | No complement-derived book |
| `{yes,no}_{bid_qty,ask_qty}` | Displayed touch sizes; B | 1s/event; yes | Depth is resting competition, not maker fill supply |
| `{yes,no}_{bid_depth5,ask_depth5}` | Sum quantities of first 5 occupied levels; B | 1s/event; yes | Explicit definition avoids ambiguous existing BookView band depth |
| `{yes,no}_imbalance5` | (bid_depth5−ask_depth5)/(sum), null if sum=0; B | 1s/event; yes | Coherent per-token book required |
| `{yes,no}_imbalance_change_{1,3,5}s` | Imbalance(t)−imbalance(t−w); D | 1s; yes | No centered smoothing or backfill |
| `{yes,no}_bid_velocity_{1,3,5}s` | (bid(t)−bid(t−w))/w, USD/share/second; D | 1s; yes | Warm-up and gap masks |
| `{yes,no}_spread_change_{1,3,5}s` | Spread(t)−spread(t−w); D | 1s; yes | Same |
| `pair_quote_price`, `pair_quote_qty` | Candidate ask−tick and residual; S/B | 1s/event; yes | Candidate may be infeasible at venue minimum |
| `pair_quote_distance_bid_ticks`, `pair_quote_distance_ask_ticks` | (quote−bid)/tick, (ask−quote)/tick on opposite token; D | 1s/event; yes | Use contemporaneous tick |
| `pair_order_age_s`, `pair_cancel_pending` | Existing pair order ACK age and pending cancel flag; E | 1s/event; yes | Null age if none; actual orders cannot leak into another policy's state |
| `queue_ahead_proxy_qty` | Opposite displayed bids at prices >= candidate, subtract only identified own displayed quantity; B/E | 1s/event; proxy | Unknown hidden orders, uncertain own presence; not true rank |
| `own_fill_qty_10s`, `own_order_cancel_count_10s` | Accepted quantities and confirmed own cancellations in trailing 10s; E | 1s/event; yes | Simulated own history must replace actual history in replay |
| `{yes,no}_sell_trade_qty_{1,5,10}s`, `{yes,no}_buy_trade_qty_{1,5,10}s` | Sum public prints by parsed aggressor side; E | 1s/event; proxy | Verify side semantics; dedupe; prints may not be exhaustive |
| `maker_fill_intensity_10s` | Opposite sell-print shares/10 seconds; E | 1s/event; proxy | Label as potential maker demand, not own fill rate |
| `opposite_bid_depletion_10s` | Sum negative displayed size changes at tracked bid levels /10; E | 1s/event; proxy | Depletion combines trades/cancels; not cancellation rate |
| `second_leg_fill_probability_3s` | P1 probability of full remaining fill within 3s; D | 1s; M4D+ only | Null until calibrated; distinct from probability of any fill |
| `favorite_side`, `favorite_price_now` | argmax/magnitude of current two mids; B | 1s/event; yes | Not an asserted true probability |
| `favorite_price_at_first_fill` | Last valid as-known favorite mid at initial acceptance; B | Initial event; yes | Do not choose a later nearest observation |
| `held_mid_change_since_fill` | Held token mid now minus held token mid at initial acceptance; D | 1s; yes | Keep held token fixed if favorite switches |
| `held_mid_mfe`, `held_mid_mae` | Max/min of held-mid change through t, signed; D | 1s; yes | Expanding past only |
| `held_mid_change_{1,3,5,10,20}s` | Held midpoint(t)−midpoint(t−w); D | 1s; yes | Polymarket momentum, not BTC return |
| `held_mid_reversion_5s` | Held midpoint minus mean of 1s midpoints over preceding 5s; D | 1s; yes | Indicator only; no claim that reversion exists |
| `{yes,no}_book_age_s` | t−last applied book mutation; B | 1s; yes | Quiet book alone does not prove broken connection |
| `market_processing_lag_ms`, `data_valid`, `btc_available` | Existing ingest metric + observer quality; S/E | 1s/event; yes | Must distinguish processing/receipt time and channel health |
| `btc_log_return_{1,3,5,10,20}s` | log(spot(t)/spot(t−w)); X | Future 1s; no | All null in M4A; unavailable source is not zero momentum |
| `btc_realized_vol_20s` | sqrt(sum of squared 1s log returns over 20s), unannualized; X | Future 1s; no | Time synchronization required |
| `btc_acceleration_3s` | log return over latest 3s minus preceding 3s; X | Future 1s; no | No future interpolation |
| `btc_open_distance_bps` | 10,000×(spot/reference_open−1); X | Future 1s; no | Requires verified market reference source and definition |
| `btc_direction_persistence_5s` | abs(sum sign of five 1s returns)/5; X | Future 1s; no | Valid only with five available returns |

Schema expansion is literal: `{yes,no}` yields two keys and `{1,3,5}` three. No other FeatureVector keys are permitted. `data_valid`, `btc_available`, `pair_cancel_pending` are B; sides are enums; all other features F. Core v1 fitting uses a small subset (quantity, first VWAP, age, time remaining, quoted PnL, opposite spread, imbalance, recent midpoint change); the remainder supports diagnostics/ablations rather than mandatory high-dimensional fitting.

## 7. Exact labels and opportunity interpretation

Horizons H={1,3,5,10,20,30,60} seconds start at snapshot availability t. Frozen-inventory diagnostic labels use quantity/cost known at t, never later acquired quantity. Price paths use valid books as actually applied; endpoint values are the last available valid state at or before t+h, with continuous observed connection/book coverage. A quiet, connected book can persist; never interpolate across disconnection or carry an old generation into a new connection before resnapshot.

- Endpoint labels: opposite midpoint/bid/ask/legal ask−tick at t+h.
- Within-window quoted labels: minimum legal maker quote and max/min quoted whole-lot pair PnL over `(t,t+h]`. MFE=`max(0,max_future_pnl−pnl_t)`; MAE=`min(0,min_future_pnl−pnl_t)`. The current state is the zero-excursion anchor.
- Threshold labels: any quote PnL >= $0, $0.05, $0.10, $0.20; any quote PnL < −$0.30; any fall <= current PnL−$0.10/$0.30/$0.50. Dollar thresholds refer to that actual lot; also report per-share opportunity distributions.
- Time-to-nonnegative: first future hit of >=$0; if already nonnegative, zero. Right-censor time-to-event when observed followup ends; do not label it as failure.
- `oracle_best_full_pair_*`: enumerate starts on the 1s grid, with a fixed-quote probe resting until t+h or T+270, using the specified execution scenario. Count a full pair only when simulated filled volume covers the frozen residual; include matched partial-pair cost already incurred. Minimize full-pair VWAP across those starts. This is an execution-assumption-specific hindsight bound on that probe family, not a guaranteed price or an attainable policy return. No full pair gives null price and false `oracle_full_pair_found`, never zero price.
- Feasible value targets: P1 initiated now versus wait h seconds and follow a predeclared reference policy, both simulated to the same terminal endpoint including nonfills/resolution. These generate value-regression targets. At h that crosses forced time, waiting is truncated by the forced action, with that intervention recorded; do not relabel it as unconstrained h-second holding.

Keep price-diagnostic labels through T+300 separate from executable opportunity windows ending at T+270. A requested full price horizon that extends past market end is CENSORED, not a shorter horizon with the original name. Missing sequence, invalid book, interrupted capture or unresolved relevant fill history also censors/invalidates the affected labels. Report valid counts by horizon and reason. Event-level quotes, not just 1s samples, supply within-window extrema; do not claim subsecond extrema from sampled data alone.

FutureLabel contains outcomes; it is never the feature table. Predicted P10/P50/P90 are estimates across conditional future realizations. A single episode supplies one label, not three predicted quantiles. For prices, low quantiles are favorable; for PnL they are unfavorable.

## 8. Execution assumptions and common replay

All policies use the same exogenous recorded event path and seeded execution draws, separate simulated inventory/order state, pinned metadata, time constraints and costs. A counterfactual order cannot consume the same public print twice within one simulation. Across policies, each replay is a separate world.

Only genuinely exogenous operational events, such as a recorded market-feed disconnect, can be shared directly. Fill Truth readiness, pending cancellation, reconciliation latency and inventory-dependent guards must follow each simulated policy's own order/fill history. Copying actual A's recovery flags into B–F can create both impossible fills and artificial halts. Unsupported recovery states are marked as such rather than guessed.

Post-only buys that cross at modeled arrival are rejected; they never become taker fills. Polymarket documents this behavior in its [order documentation](https://docs.polymarket.com/trading/place-orders). The [public market stream](https://docs.polymarket.com/market-data/realtime-data) provides L2 and last-trade events, not guaranteed queue rank for our hypothetical order. Verify print coverage and side semantics against owned fills before trusting volume depletion.

| Scenario | Queue and fills | Latency / cancellation |
|---|---|---|
| Optimistic | Zero unobserved queue ahead; compatible opposite-token sell print at/below resting limit needed; fill bounded by unique print volume | Low observed latency quantile; still nonzero arrival, post-only check, cancel acknowledgment and partial fills |
| Base | Join behind displayed competition; reduce ahead-volume by compatible trades; use cancellations only with a calibrated queue-depletion assumption | Train-period median conditional latency and observed rejects/cancel outcomes; preserve adverse-selection dependence |
| Conservative | Join behind full displayed competing volume plus prespecified buffer; no cancellation credit, no touch-only fill, no fill across feed gaps | Train-period high latency quantile, explicit cancel races and lost priority after repricing |

Before calibration, these are sensitivity scenarios with named provisional parameters, not estimated executable probabilities. Do not invent a 70% fill rate. No-fill/settlement and additional queue/latency stress cases supplement the three. Conservative fill assumptions are not necessarily a mathematical lower PnL bound: a missed losing pair can improve settlement PnL. Report the worst policy result across credible joint scenarios, not merely the lowest fill fraction.

A small no-market-impact assumption is required even for 5 shares; compare order size against displayed/printed liquidity and flag episodes where material. Do not infer executions from ask touching a hypothetical bid, displayed ask volume, midpoint crossings, or cancellations alone. Handle paired-token/cross-token prints only after proving equivalence; otherwise do not double count them. Deduplicate using available source identifiers and ordering without collapsing distinct identical trades. Ambiguous coverage produces scenario uncertainty, not invented exact volume.

Use actual resting order trials to calibrate fill/no-fill, quantities, latency, rejects and post-fill markout conditional on quote aggressiveness, queue proxy, time remaining, spread and volatility. Include unfilled/canceled orders as censored competing outcomes. Calibration applies only where those trials provide support. The current four owned orders cannot support a fitted execution model. Actual fill prices/times are evidence for A, not guaranteed fills for hypothetical B–F orders.

Rebate value is zero in the initial model scenario; **actual rebate remains null** until attributed evidence exists. Preserve gross, modeled net and actual net separately. Maker rebates depend on filled liquidity and market program allocation, with mutable rates; do not multiply gross notional by a presumed fixed rebate. See the [official rebate explanation](https://help.polymarket.com/en/articles/13364471-maker-rebates-program). Existing fee metadata and costs are versioned as-of; uncertainty receives sensitivity bounds. Resolution joins may use existing settlement artifacts; do not call settlement actions from research.

### Policy registry

| Policy | Exact first comparison |
|---|---|
| A: FavoriteFirst v0.3 | Reuse pinned `evaluate`, config and memory; implement a research adapter for observed engine readiness/reprice/finalization semantics. Simulate own fills and quote state under each execution scenario. Check against actual state transitions separately. |
| B: Immediate Pair | Request P1 at first known fill in all-first-fill panel; `immediate_at_finalization` is separately named in fixed-inventory panel. |
| C: Fixed Time | Request P1 at first known fill + {3,5,10,20,30,60}s; finalization and T+240 can constrain actual attempt. Landmark versions explicitly use landmark clock. |
| D: PnL Target | Request P1 at first legal quoted pair PnL >= {0,0.05,0.10,0.20} USD; no hit -> forced time. A target triggers an attempt, not a locked profit. |
| E: Simple Trailing | Track causal quote PnL peak; arm at $0.05 and trigger 50% drawdown, else force at T+240. No passive pair during HOLD. A's Runner passive quote remains in A. |
| F: Dynamic EV | Section 3 comparison with margin fixed on validation, causal model features and supported state only; no model-driven change to acquisition or safety limits. |

For B–F, the initial research comparison retains the existing $0.50 emergency guard in a common safety wrapper; this measures improvement within the current envelope. Separately predeclare offline-only `heuristic_replacement` variants where the 20s/emergency/trailing triggers are absent but true engine safety and T+240/T+270 remain. This separate panel tests the full unification hypothesis without mislabeling the existing quote trigger as a guaranteed absolute-loss bound. Do not mix panels in rankings. A always means exact original v0.3.

At the forced deadline an action can fail, remain unfilled or leave a subminimum residual. Carry these to economic settlement; never assume a forced pair completed. Account for any cancel-pending fills received after cutoff according to modeled exchange execution/ack times. Complete-pair and forced-pair metrics are separate.

## 9. Modeling and validation

### Progression

**Model 0:** equal-episode-weighted conditional distributions with count/intervals by first price bucket ([.60,.62), [.62,.65), [.65,.68), [.68,.70]), first-fill age, time remaining and current quoted PnL. Use a small set of predeclared landmark ages for descriptive comparisons so long episodes do not dominate. Empty/small cells remain unsupported. Inspect markouts, recovery and tail worsening under all execution assumptions.

**Model 1:** start with ridge regression of feasible `wait_h_then_reference` minus `pair_now` rollout value, using a small standardized feature set and prespecified h (initially 1s for stopping, plus 10s for diagnostics). Include simple time/price buckets to capture known deadline effects. Add logistic full-pair/profit events or quantile regression for opportunity distributions only when sample counts support them. A shallow boosted tree is a challenger after the linear model demonstrates useful signal. No RL or deep-learning dependency.

**Model 2:** a limited regression-based stopping policy. Start from a fixed feasible reference exit; use out-of-fold reference/updated-policy rollouts to estimate continuation, then perform a small predeclared number of backward policy-improvement passes. Pending P1 orders use the fixed execution protocol. Validate the resulting causal policy on later complete episodes. This follows the principle of estimating conditional continuation payoffs in [Longstaff–Schwartz](https://escholarship.org/uc/item/43n1k4jb); it does not turn a hindsight maximum into an attainable EV. Independent fitting/evaluation is essential because same-path regression decisions can introduce lookahead bias ([Woo, Liu and Choi](https://arxiv.org/abs/1810.02071)).

Until that layer is validated, call the prediction `value_of_waiting_under_reference_policy`, not optimal EV. Counterfactual values are simulator outcomes, not directly observed causal truth. Observational policy logs cannot identify unsupported queue/execution behavior; more complicated models do not repair this limitation.

### Chronological separation

- Oldest 60% episodes train; next 20% validation; newest 20% test. These fractions are a starting design, not viable splits for the current one episode.
- Group all snapshots, partial fills, restarts, simulation draws and labels from the same condition/episode together. Group contiguous market blocks/days for inference and walk-forward testing.
- Define each sample's information interval `[earliest_feature_time, latest_label_time]`. Purge any cross-split overlaps; embargo at least max lookback (20s initially) plus max price horizon (60s), extended to actual terminal rollout/resolution availability where needed. A training label must have been available before the evaluation model's training cutoff, including delayed resolution.
- Fit scaling, imputers, feature selection, queue calibration, latency distributions, probability/interval calibration, policy margins and thresholds using train/validation only. Nested expanding-time folds, never random cross-validation of snapshots.
- Keep a final untouched chronological holdout. If test results motivate revisions, retire that holdout and collect a new later one. Report every prespecified policy and unsuccessful variant.
- Use paired episode return differences and a moving-block/bootstrap over contiguous market blocks/days. Resample all draws/snapshots of an episode together. Report number of episodes, filled orders, market-days, horizon-valid labels, effective support and exclusions; snapshot count is not inferential N.
- Do feature ablations: current PnL only; + time/first price; + book imbalance/depth; + market-price momentum; BTC group only after a verified feed exists. Evaluate incremental out-of-sample error and policy value.
- Predefine the economically meaningful difference. Proposed planning value: $0.02 net per 5-share-equivalent episode, with actual-lot dollar results also shown. Determine needed N from pilot block variance and desired precision/power, not a magic minimum of live trades. A rough independent-episode estimate is `N≈(z*sd(paired_difference)/precision)^2`; autocorrelation increases N. Freeze the final procedure before test inspection.

## 10. Metrics and research questions

For each policy/panel/execution scenario and chronological period, compute episode-weighted mean, median, strict win/loss/zero rates, mean conditional positive/negative PnL, minimum, and P5/P10/P50/P90/P95. Use complete economic outcomes, not only paired winners. Unknown outcomes appear as counts and bounds and are not silently dropped from denominators. Report gross and modeled net with explicit cost assumptions.

- MAE/MFE: minimum/maximum signed change in quoted liquidation-by-pair proxy from initial snapshot until simulated exposure ends; label as quoted, not cash drawdown. Separate path-wide opportunity excursions from policy-specific exposure excursions.
- Exposure seconds: integral of `1[unpaired_qty>0]` from first known fill until pair completion or market end; an unpaired settlement retains exposure through T+300. Report the exchange-time version separately when reliable.
- Complete-pair probability: complete pairs / eligible first-fill episodes, not / filled pair orders.
- Forced/emergency probability: episodes where that trigger started an attempt / eligible episodes. Also report forced completions and residuals independently; reasons can occur sequentially.
- Maker fill probability: acknowledged maker orders receiving any accepted fill / all acknowledged maker orders, with rejects separately; pair-completion probability conditional on attempt additionally reported. Show canceled-order censoring and size-weighted fill fraction without summing replacement sizes into a fake target quantity.
- Pair latency: first actual/simulated pair fill−first pair attempt and full completion−first pair attempt, plus noncompletion count and survival curve. Do not present only successful fast fills as overall latency.
- Inventory risk at u: first-leg cost basis of unpaired shares plus any opposite residual cost basis (unpaired shares can lose their whole cost). Report `sum(net PnL)/sum(peak inventory risk)` as net dollars per dollar at risk, and `sum(net PnL)/sum(risk-dollar-seconds)` as exposure-efficiency companion. Mean of episode ratios is a distinct metric. Do not replace capital at risk with quote drawdown.
- Calibration: on held-out states, compare predicted Q_PAIR, Q_CONTINUE and their difference to same-state feasible simulated rollouts under the matching scenario/protocol. Bin predictions, show count, mean error/MAE, calibration slope, prediction-interval coverage; probability targets use Brier/reliability plots, quantiles use pinball loss/coverage. Limit/weight repeated states per episode. Separately check predictions against observed own-order fill/latency where the actual action matches. These checks validate the execution model only within observed support.

| Economic question | Analysis supported by the framework |
|---|---|
| 1. Adverse selection? | Held-token midpoint markout `q*(mid_(fill+h)−fill_price)` and movement-only `q*(mid_(fill+h)−mid_fill)`; matched contemporaneous eligible-but-unfilled quote controls, time/price strata, block intervals. Conditional markout alone is association, not causal proof. |
| 2. Future opposite prices? | Seven endpoint distributions plus separate quoted and execution-scenario opportunity distributions. |
| 3. Losing-leg recovery? | Condition on quote PnL<0; quote-hit and executable simulated full-pair recovery rates, censoring included. |
| 4. Near-zero to profit? | Predeclare near-zero as |quoted PnL|<=$0.025 per 5-share equivalent; estimate future threshold hits and feasible-policy gains. |
| 5. Loss worsening? | Worsening-by thresholds, terminal tails and paired waiting-vs-immediate differences. |
| 6. Why 20s? | Compare prespecified 3/5/10/20/30/60s policies and state-conditioned value; no default economic status for 20s. |
| 7–8. Predictors/entry price? | Time-split feature ablations and price-bucket support/uncertainty, with PnL/time confounding controlled. |
| 9. BTC momentum? | Initially unavailable; cannot answer from token momentum. Revisit only with a verified reference feed. |
| 10–12. Imbalance/time/PnL? | Nested feature groups against PnL-only/time-only references, out of sample. |
| 13. Dynamic advantage? | Paired A–F episode differences under common simulator and panel. |
| 14–15. Robust meaningful edge? | Conservative queue/latency/nonfill/adverse-selection stresses, zero-rebate case, attributed costs and confidence intervals. |

## 11. Unresolved assumptions and leakage traps

1. **Execution identifiability:** public L2/prints do not reveal our queue rank or all cancellations. A base model needs many supported own-order trials; shadow intentions create no new real fill evidence.
2. **Residual valuation:** settlement outcome is necessary even though outcome prediction is not the main target. Offline known payout is a label only; online residual EV needs an auxiliary calibrated payout estimate or explicit bounds. A midpoint proxy must be labeled as an assumption and stress-tested.
3. **Availability time:** exchange event time, receive time, processing time and REST discovery differ. Re-sorting all sources by exchange time manufactures foresight. Historical journals lacking these clocks cannot be promoted to exact causal replay.
4. **Path continuation:** stop trading does not mean stop observing. Ending capture at actual pair completion causes informative censoring and removes evidence for longer alternatives.
5. **Own-action contamination:** subtract identifiable own displayed volume where possible; replay fills, queue, costs, peaks and execution features from each simulated policy. Do not feed actual future pair fills to all policies.
6. **Acquisition selection:** final first quantity/VWAP and successful-completion status are future information at initial partial fill. Landmark and all-first-fill panels must remain distinct; never generalize the landmark model to unsupported partial states.
7. **Peak leakage:** use prefix maxima, backward windows and immutable feature cutoffs. Future extrema belong only in labels. Within-window oracle maxima are not continuation returns.
8. **Censoring vs losing:** no quote, no fill, disconnected feed, no observed outcome and an actual loss are different states. Complete-case-only PnL can hide the worst episodes. Show exclusions and bounded sensitivity.
9. **Market/period contamination:** adjacent markets, restarts and many states of the same path are correlated. Split by time/condition and purge overlapping information intervals.
10. **Baseline fidelity:** replay code/config from the correct version and include operational delays. The one historical episode predates some current report artifacts; do not assume every current implementation detail matches that run.
11. **Costs/rebates:** neither presumed zero actual costs nor fully attributed wallet settlement is justified. Record unknowns and publish explicit modeled scenarios.
12. **Scope of result:** research is conditional on FavoriteFirst entry selection; it cannot establish unconditional BTC prediction skill or entry edge. Absence of supported statistical advantage is INCONCLUSIVE until precision supports a NO EDGE conclusion.

## 12. Exact M4A scope and gate to M4B

### Smallest implementation proposal

New future modules: `research/schema.py` (typed contracts), `research/observer.py` (immutable capture), `research/writer.py` (separate bounded writer), and `research/audit.py` (read-only validation). No modeling/scoring dependency and no order-capable object accepted by these modules. Add a research config flag defaulting off and a manifest per session. M4B modules (`builder`, `features`, `labels`) follow only after capture acceptance.

Minimal integration points:

1. At existing market ingest, stamp receive/processing clocks and session sequence, preserve connection generation, reference raw market events, and capture tick-size events. Record full initial/resubscribe snapshots. Observe REST book replacements at the existing mutation point with replacement levels. Do not alter feed/parser/refresh behavior.
2. At existing owned-order/fill acceptance and status-change points, copy sanitized identity/economics, source, acceptance/quarantine/finalization evidence and known-at time. The observer consumes existing truth; it never decides whether a fill is real. Record successful/failed/unknown submission/cancel acknowledgment separately from intent.
3. Capture market metadata/config/code hash at selection. Maintain a 20-second pre-fill ring of market and quote-exposure context; persist the brief context on first fill. Keep run-level summaries of entry order trials including no-fill/canceled trials for execution/adverse-selection comparisons, without manufacturing no-fill episodes.
4. On first accepted positive first-role fill, append Episode revision and immediate state. Persist StateSnapshot every 1 second plus fill/order/safety/finalization events until T+300. Continue after COMPLETE/HALTED/T+270 while the existing process/feed runs. Interrupted processes write a censored terminal record; no new supervisor, live session or external price feed is launched.
5. Use a fixed bounded queue with immutable records and an independent file writer; no disk/network wait in the trading callback. Catch observer errors, disable/drop research capture with explicit GAP evidence and health counters. Research failure must not cancel/place orders or suppress existing safety logic. Record flush watermark and incomplete tail; newline JSON alone is not a durability guarantee. Batch flush at <=1s and fsync off the trading path at <=5s, with final checksum/manifest on close; a crash tail is marked incomplete on next inspection.
6. Reuse existing raw journals rather than create another market feed. Research files store references plus the missing clock, identity, mutation and snapshot evidence. Resolve references and hash closed source files; never rely on a path that rotation can silently overwrite.
7. Add only meaningful checks: accepted-fill identity/status revisions, partial fill and delayed REST discovery, book reconstruction including correction/tick/reconnect, prefix-only features, continuation after pair completion, restart identity, queue overflow/crash censoring, and enabled-vs-disabled unchanged strategy decisions/order-intent traces. Test observer overload and bounded latency. No tests that simply assert constant schema fields.

Proposed storage: `research_data/<dataset_id>/<session_id>/{manifest.json,events.jsonl,episodes.jsonl,states.jsonl,quality.json}`. Keep generated data out of Git; design/code alone tracked. Append episode revisions, never mutate old state records. `FutureLabel`, `ModelDecision`, and `PolicyReplayResult` schemas are reserved; M4A emits no fake values for them. `execution_estimate=null` and BTC fields null in M4A.

### GO from M4A to M4B (engineering/data gate)

All numeric quality thresholds below are proposed acceptance budgets, not claims about measured current performance:

- **Isolation:** observer-off and observer-on deterministic playback produce identical policy decisions and order intents. Injected observer/write failures do not propagate to trading. No observer imports/calls to execution or settlement APIs. Existing strategy/Fill Truth regression checks pass.
- **Timing:** both token books and owned-event records have causal sequence and available-at time; receive/exchange time distinguish missing values. No snapshots use a later sequence. Research callback p99 under 1ms on recorded peak traffic; incremental loop-lag p99 <=5ms in the controlled comparison; overload generates explicit gaps.
- **Completeness:** >=99% of scheduled 1s snapshots captured during normal connected post-fill coverage; unexplained gaps >2s are forbidden. Every detected drop/reconnect/REST replacement is represented. Invalid intervals are retained/masked, never forward-filled to qualify.
- **Truth and identity:** every observed first/pair economic fill maps to registered order role and episode; no duplicate economic application. Accounting reconciles to existing accepted fills at the source precision. No unexplained discrepancy; unresolved/quarantined cases remain INVALID/UNRESOLVED.
- **Reconstruction:** initial snapshots plus events reproduce sampled top-5 books, ticks and current inventory exactly at recorded sequence points (within declared source numeric precision). The legacy episode is only a smoke fixture where available data permits; its missing receipt/correction evidence is documented, not invented.
- **Coverage:** demonstrate the new format on at least 20 first-fill episodes across at least 3 market-days before calling collection operationally ready; use only separately authorized existing runs. This is a modest collection gate, not statistical power. Partial fills, late acceptance, failed status, cancel race, reconnect, post-COMPLETE capture and interruption require deterministic fixtures if absent in real episodes; synthetic fixtures never count toward economic sample size.
- **Endpoints:** complete paths continue to market end after actual exit. Every interrupted path records censor reason. Unknown resolution is explicitly retained and supports bounds; it blocks terminal-return labels for that row, not clean quote-label generation for other horizons.
- **Reproducibility:** immutable manifests/config/source hashes, schema validation, deterministic episode keys, duplicate/status audits, and quality counts by horizon can be independently rerun. No secrets are captured.

NO-GO for declaring M4A ready if identity, causal clocks, book mutation coverage, observer isolation or unexplained data loss fails. Continue repairing capture; do not compensate by adding a model. M4B tooling may be developed against fixtures earlier, but outputs stay experimental until this gate passes. The current one legacy episode does not pass the collection gate.

### Later M4F decision gate (research only)

Advance toward a separately reviewed live-exit proposal only if all prespecified conditions hold: positive held-out mean incremental net PnL versus A and the strongest simple comparator selected on validation; block-adjusted 95% lower confidence bound above zero and point improvement above the predeclared economic materiality threshold; no dependence on optimistic fills or speculative rebates; conservative/scenario-stressed advantage remains; tail loss/MAE, residual probability and exposure do not breach predeclared risk budgets; useful calibration on supported states; directionally consistent gains in multiple chronological folds; adequate support/precision and an independent shadow run with no control authority.

Risk noninferiority default: no worsening of P5 PnL, probability of residual inventory, or mean exposure relative to the validation-selected comparator, allowing only prespecified uncertainty tolerances before test evaluation. Worst observed loss is reported but cannot alone establish safety. If these criteria are underpowered, return INCONCLUSIVE and specify the required additional evidence. If sufficiently precise results rule out the materiality threshold across credible execution assumptions, return NO EDGE for this policy class. Neither outcome warrants increasing complexity just to improve in-sample results.

## 13. Design handoff

- **Mathematical formulation:** a constrained stopping decision to initiate a maker-only pairing protocol, valued over joint fill/path outcomes and residual settlement; compare expected feasible policy values, not quoted prices or hindsight extrema.
- **Exact data schema:** five versioned objects in section 5, fixed feature allowlist in section 6, seven-horizon labels in section 7, with explicit revisions, causal clocks, source provenance, nulls and censoring.
- **Exact M4A scope:** optional immutable telemetry, 1s plus event capture through market end, missing mutation/acceptance/order timing evidence, separate durable writer and data-quality audit. No model-driven trading, infrastructure redesign or BTC dependency.
- **Primary risks/leakage traps:** unknowable queue rank, delayed fill discovery, post-exit censoring, partial-acquisition counterfactuals, oracle targets, own-order contamination and time-period overlap.
- **M4A→M4B GO / NO-GO:** advance on demonstrably faithful, isolated and sufficiently complete capture; no edge claim required. Current historical records are suitable for an initial fixture only.

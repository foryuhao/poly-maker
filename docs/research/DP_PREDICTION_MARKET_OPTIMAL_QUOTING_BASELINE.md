# Prediction-market optimal quoting baseline for BTC5m Dynamic Pairing

Research date: 2026-10-03. Status: **REQUIRES MODEL EXTENSION** for an exact, operational BUY-only and maker-only decision engine. This is a model-selection report, not an implementation authorization or profitability claim. The previous maker-fill-proxy and edge-slack reports remain historical records; the historical public-event fill proxy remains invalid for own fills. No trading code, order, API integration, or backtest was changed. Repository base: `97ab0f9` (`m4a-a1-pass`) with pre-existing unreviewed working-tree changes. The independent M4A acceptance gate remains blocked in `docs/CURRENT_STATUS.md`.

Primary source: Dominik Feil and Max Nendel, **“Optimal Market Making in Prediction Markets,”** arXiv:2607.17991v1 (20 July 2026), [full 42-page paper](https://arxiv.org/pdf/2607.17991). It is a theoretical preprint with illustrative simulation, **not** a Polymarket empirical calibration. Classical references: [Avellaneda and Stoikov (2008)](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf) and [Guéant, Lehalle, and Fernandez-Tapia (2013; preprint 2011)](https://arxiv.org/pdf/1105.3115). Venue facts below use [official Polymarket concepts](https://docs.polymarket.com/concepts/prices-orderbook), [positions and tokens](https://docs.polymarket.com/concepts/positions-tokens), [order API documentation](https://docs.polymarket.com/trading/place-orders), and the [audited BTC5m market's own rules](https://polymarket.com/event/btc-updown-5m-1790937900). Academic and venue links are methodological references; runtime data remains official Polymarket HTTP/WebSocket only.

## A. Executive conclusion

**REQUIRES MODEL EXTENSION.** Feil–Nendel is the closest identified established prediction-market quoting framework: its two complementary claims, bounded belief price, binary settlement, terminal settlement-risk penalty, and inventory-aware bid/ask output match the *economic* core of DP. The identities `q=YES−NO` and `X=spendable cash+NO` transform terminal wealth and inventory risk **exactly**. Yet the paper's state `(t,p,q,X)` does not determine spendable cash or accumulated matched pairs, while BUY-only Polymarket orders lock cash as matched inventory grows. Its quote control is `[0,1]²`, without a live BBO/tick/post-only feasible set; a projected quote is not the paper's verified optimum. These are operational state and admissibility gaps, not just unknown coefficients. Its uncalibrated execution-intensity functions are an additional independent blocker. The paper is a strong theoretical reference but cannot yet serve as an exact live BTC5m DP decision engine. [Feil–Nendel §§2–4](https://arxiv.org/pdf/2607.17991); [Polymarket post-only order rules](https://docs.polymarket.com/trading/place-orders).

## B. Exact paper model

**Original contract, state, and belief.** One binary claim pays `Y∈{0,1}` at resolution just after fixed `T`. A second complementary claim is recognized in the paper, but its quoted-order problem is reduced to **one claim with short selling allowed**. The pre-resolution information filtration is `F_t`; `p_T∈(0,1)` and `P(Y=1|F_T)=p_T`. Its martingale market belief/price is `p_t=E[p_T|F_t]=P(Y=1|F_t)`. The paper **assumes market efficiency to identify belief with market price**; it does not estimate belief independently from an order-book mid. A real-valued latent belief `L_t` is mapped by a strictly increasing `f:R→(0,1)`: `p_t=f(L_t)`. The original theory allows suitable `f`, drift and diffusion; logistic `f(x)=1/(1+e^{-x})` is an example and the numerical choice. The dynamics are

```text
dL_t = μ(t,L_t) dt + σ(t,L_t) dW_t,
μ(t,x) = -½ σ(t,x)² f''(x)/f'(x),
dp_t = ς(t,p_t) dW_t,
ς(t,p) = f'(f⁻¹(p)) σ(t,f⁻¹(p)).
```

The drift choice makes `p` a bounded martingale. Under the logistic choice, `L=log(p/(1-p))` and `ς(t,p)=p(1-p)σ(t,logit(p))`. The process remains inside `(0,1)` before resolution; the binary payoff is revealed after `T`. [Feil–Nendel §2.1.1, Example 2.1](https://arxiv.org/pdf/2607.17991).

**Controls, cash, and inventory.** At every time the maker controls YES bid `πᵇ_t∈[0,1]` and YES ask `πᵃ_t∈[0,1]`; a fill has fixed size `Δ>0`. Order size is **not optimized**. `Nᵇ,Nᵃ` count fills, `q` is signed YES exposure on finite grid `{-Q,-Q+Δ,…,Q}`, and `X` is the one-claim cash ledger:

```text
dX_t = Δ πᵃ_t dNᵃ_t − Δ πᵇ_t dNᵇ_t,
dq_t = Δ dNᵇ_t − Δ dNᵃ_t.
```

There is no gross YES/NO pair count, spendable-collateral constraint, limit-order queue, or independent order-size control. Cash can be any real number in the mathematical problem. [Feil–Nendel §2.1.2](https://arxiv.org/pdf/2607.17991).

**Execution arrivals.** Independent Poisson random measures generate bid and ask counts with predictable intensities `Λᵇ(t,p,πᵇ)·1{q<Q}` and `Λᵃ(t,p,πᵃ)·1{q>−Q}`, independent of the Brownian driver. The general theory requires bounded continuous intensities, twice differentiable in quote price, `∂πΛᵇ>0`, `∂πΛᵃ<0`, and a curvature condition ensuring unique Hamiltonian maximizers. It does **not** derive fills from FIFO or public L2. The numerical section assumes, rather than empirically calibrates, the following specific form (`h(t)=(e^{ξt/T}−1)/(e^ξ−1)`, `j(t)=(e^{κt/T}−1)/(e^κ−1)`):

```text
A(t,p) = [A₀ + A₁ h(t)] √(p(1−p)),
k(t) = k₀ + (k₁−k₀) j(t),
Λᵇ(t,p,π) = A(t,p) [2π/(π+p)]^ν exp[-k(t)(p−π)],
Λᵃ(t,p,π) = A(t,p) [2(1−π)/(2−π−p)]^ν exp[-k(t)(π−p)].
```

The paper explicitly says the empirical functional form of prediction-market order intensity has not been systematically studied and Table 1 values are chosen for qualitative illustration, **not contract calibration**. Its numerical parameters include `T=1`, `(σ₀,σ₁,σ₂,η)=(0.6,0.3,0.1,3)`, `(A₀,A₁,ξ)=(100,150,2)`, `(ν,k₀,k₁,κ)=(1,35,50,1.5)`, `(γ,γ_T)=(0.004,0.001)`, `Q=100`, and `Δ=10`. Reusing these on 300-second BTC5m would be unjustified even before venue differences. [Feil–Nendel §§2.1.3, 4.1, Table 1](https://arxiv.org/pdf/2607.17991).

**Objective and terminal condition.** The maker maximizes expected terminal wealth, with separate running mark-to-market variance and terminal binary-settlement variance penalties:

```text
sup_π E[X_T + q_T Y + Φ(p_T,q_T) − γ∫₀ᵀ q_s² ς(s,p_s)² ds],
Φ(p,q) = −γ_T q² p(1−p),     γ,γ_T > 0.
```

`q²ς²` is the instantaneous variance rate of marked inventory `qp`; `q²p(1-p)` is the conditional variance of `qY` immediately before revelation. This is **not** CARA utility or a guaranteed positive-Pair-Edge objective. Since `E[Y|F_T]=p_T`, write full value `Υ(t,p,q,x)=x+qp+V(t,p,q)`. The paper's reduced HJB, retaining its definitions, is

```text
0 = −V_t − ½ς²V_pp + γq²ς²
    −1{q<Q} Hᵇ(t,p; [V(t,p,q)−V(t,p,q+Δ)]/Δ)
    −1{q>−Q} Hᵃ(t,p; [V(t,p,q)−V(t,p,q−Δ)]/Δ),
V(T,p,q)=Φ(p,q),
Hᵇ(t,p;z)=Δ sup_{π∈[0,1]} Λᵇ(t,p,π)(p−π−z),
Hᵃ(t,p;z)=Δ sup_{π∈[0,1]} Λᵃ(t,p,π)(π−p−z).
```

The HJB outputs the unique pointwise maximizers for bid and ask from neighboring-inventory value differences; it has no universal simple closed-form quote. Proposition 3.2 characterizes the maximizers by inverse monotone first-order maps, with endpoint cases; Theorems 3.3/3.5 verify the policy and solution under stated regularity. Numerically the paper solves the reduced HJB backward using an implicit-Euler time grid, second-order price differences, fixed-point iteration across inventory, and quote-grid maximization, then interpolates `V`. Its 10,000-path **synthetic** comparison reports mean PnL 12.39 versus 12.47 for a myopic baseline and lower PnL standard deviation 10.34 versus 28.11; this is not observed Polymarket profitability. The objective does not guarantee that a YES/NO pair acquired through separate quotes costs less than $1. [Feil–Nendel §§2.2–4.4 and Appendices A–D](https://arxiv.org/pdf/2607.17991).

## C. What is prediction-market specific

Feil–Nendel replace a Brownian price on an unbounded real line with a bounded conditional-probability martingale, a latent belief transform, an explicit `Y∈{0,1}` settlement, a `q²p(1-p)` terminal risk term, and quote/intensity boundary behavior at 0 and 1. Their numerical intensities depend on market belief and time as well as quote distance. [Avellaneda–Stoikov](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf) uses a continuous mid-price, CARA terminal-wealth utility, and exponentially quote-distance-dependent arrivals; [Guéant–Lehalle–Fernandez-Tapia](https://arxiv.org/pdf/1105.3115) adds bounded inventory and tractable HJB reductions. Those classical formulas do not supply binary settlement risk or the YES/NO cash transformation. Feil–Nendel remains the primary reference; its prediction-specific features do not cure its missing venue execution and funding states. [Feil–Nendel §§1.2–2](https://arxiv.org/pdf/2607.17991).

## D. YES/NO and BUY-only transformation

Let `C` be **spendable** cash, `Y_s` YES shares, `N_s` NO shares, and `ω∈{0,1}` the YES settlement result. (The paper uses `Y` for the binary outcome; share subscripts here avoid collision.) A YES share pays `ω`, a NO share pays `1−ω`. Define

```text
q = Y_s − N_s,              X = C + N_s.
W_T = C + Y_s ω + N_s(1−ω) = X + qω.
W_t^mark = C + Y_s p + N_s(1−p) = X + qp.
```

For `Δ` shares, a BUY YES at `b` changes `(C,Y_s,N_s)` by `(-Δb,+Δ,0)` and `(X,q)` by `(-Δb,+Δ)`, **exactly** the paper's bid fill. A BUY NO at `r=1−a` changes it by `(-Δ(1−a),0,+Δ)` and changes `(X,q)` by `(+Δa,−Δ)`, **exactly** the paper's SELL YES at `a` in transformed wealth/inventory coordinates. Similarly SELL NO at `s` and BUY YES at `1−s` both produce `(ΔX,Δq)=(-Δ(1−s),+Δ)`. These are exact accounting/payoff identities for a binary exhaustive pair at equal quantity and complementary price. `W_t^mark` uses the theoretical complementary values `p` and `1−p`, not two executable book quotes. The identities do not prove identical order execution. [Feil–Nendel §2 opening and §2.1.2](https://arxiv.org/pdf/2607.17991); [Polymarket complete-set and complementary matching rules](https://docs.polymarket.com/concepts/prices-orderbook).

Concrete counterexample to **operational** equivalence: start with `C=10`, no tokens, buy 5 YES at 0.50 then 5 NO at 0.50. Actual state is `(C,Y_s,N_s)=(5,5,5)`, `q=0`, `X=10`; the one-claim paper ledger after buy YES then sell YES is `(X,q)=(10,0)`. Both have the same terminal wealth, but only **5** is spendable cash in the BUY-only account. Repeating matched BUY pairs consumes more free cash while paper `q` stays zero and `X` remains 10. A gross pair count or spendable collateral state is needed if capital can bind or complete sets cannot be instantly converted to cash at no cost. The present runtime does not have live automatic merging; current preflight explicitly checks collateral balance. Order books may mirror complementary prices, and Polymarket can match complementary BUY orders to mint shares, but a SELL of held YES and a BUY of NO have different token delivery, collateral use, and potentially matching routes/priority. Fees, tick alignment (`1−a` must be a legal NO tick), execution times, and available depth can break a trade-by-trade operational equality. [Polymarket positions](https://docs.polymarket.com/concepts/positions-tokens); [Polymarket order matching](https://docs.polymarket.com/concepts/prices-orderbook); [post-only rule](https://docs.polymarket.com/trading/place-orders).

## E. DP inventory mapping

For physical inventory, `M=min(Y_s,N_s)`, `R_Y=max(Y_s−N_s,0)`, and `R_N=max(N_s−Y_s,0)`. Settlement wealth is `C+M+R_Yω+R_N(1−ω)`. The paper's signed inventory is **`q=Y_s−N_s`**, not YES shares or NO shares alone. `q>0` is residual YES; `q<0` is residual NO (economically short YES plus collateral). Balanced `q=0` eliminates both running `q²ς²` and terminal `q²p(1−p)` risk. The deterministic matched portion is embedded in transformed `X=C+N_s`; the paper does **not** separately track it as physical `M` or available collateral. Its bid/ask inventory skew favors lowering positive `q` and raising negative `q`, which can resemble buying the complementary leg after an initial fill. It does not prescribe an entry seed, a named PAIR/REPAIR phase, complete-set formation, or a target gross pair count. [Feil–Nendel §§2, 4.3](https://arxiv.org/pdf/2607.17991).

## F. BTC5m variable mapping

“Observed” here means in the **existing persisted local data**, not merely available in an API. Book values are official Polymarket L2 observations; the paper's true conditional belief is a stronger theoretical object.

| Original variable | Exact paper definition | BTC5m mapping | Persisted? / official source | Status |
|---|---|---|---|---|
| `T,t` | Fixed revelation horizon and current time | Market start/end and time remaining; a 300-second named window | Slug, run reports, manifest timestamps; official market metadata | Exact market clock within recorded timestamp limits; revelation may follow close |
| `Y` (outcome) | Binary payoff revealed after `T` | BTC5m UP/YES or DOWN/NO settlement per market rule | Local run outcome often unsettled; official Polymarket resolution later | Officially observable later; often missing from capture |
| `p_t` | `P(Y=1|F_t)`, martingale, identified with efficient market price | YES belief proxy from contemporaneous YES book midpoint; compare NO complement | Both official books persisted | **Proxy**, not verified true belief or martingale |
| `p_T` | Strictly interior conditional YES probability just before revelation | Last pre-close market belief proxy | Local books exist; true conditional probability not observed | Proxy / theoretical assumption |
| `L_t` | Real latent information process with `p=f(L)` | `logit(p_proxy)` only if logistic `f` selected | Derived from proxy | Conditional algebra; latent aggregate information missing |
| `f,g=f''/f'` | Bounded monotone transform and regularity ratio | Logistic example/theoretical choice | No directly measured transform | Model specification, not direct observation |
| `σ(t,L), ς(t,p)` | Latent and price diffusion coefficients | Estimate from official YES book price path after declaring price proxy/time scale | 20 long local market captures | Estimable only **conditional on diffusion model**, pilot unstable |
| `q` | Signed one-claim inventory, grid step `Δ` | `YES−NO` accepted inventory | Accepted fill revisions, episode snapshots (11 fill markets); baseline position from official account API exists in runtime but is not recorded in each manifest | Exact for captured accepted trade deltas; full account baseline not always reconstructable locally |
| `X` | One-claim cash ledger with sell credit and buy debit | `spendable collateral + NO shares` | Fill cash flows local; current spendable balance read in runtime preflight, not persisted in these research sessions | **Missing** full local starting/current ledger |
| `Δ,Q` | Fixed fill size and signed inventory bound | Intended quote size and residual-inventory bound | Run config has 5-share minimum/first size; individual fills can be partial | `Δ=5` is a chosen control size, but paper's full-size jump assumption is not exact; `Q` preference/control |
| `πᵇ,πᵃ` | YES bid and ask quote controls in `[0,1]` | BUY YES at `πᵇ`; BUY NO at `1−πᵃ` under transformation | Actual accepted quotes and both token books | Exact price algebra; control feasibility only proxy until post-only, tick and cash checks |
| `Λᵇ,Λᵃ` | Own bid/ask execution intensities conditional on time, belief, quote | Own BUY YES/BUY NO accepted-fill hazard as functions of quote state | 74 ACKed orders, 27 fills; public L2/trades also present | **Missing calibrated functions**; public events invalid as own-fill labels |
| `γ,γ_T,Φ` | Running risk preference, terminal risk preference, terminal penalty | Decision-maker risk preferences; `−γ_Tq²p(1−p)` conditional on belief proxy | Not market fields | Exogenous preferences / model formula |

For a candidate `p_proxy`, YES mid and `1−NO mid` should be compared on a causally aligned book; the displayed UI price can instead use last trade for wide spreads. YES/NO BBO pairs are distinct executable prices and cannot be silently substituted for the paper's frictionless `p`. No external BTC spot or custom directional fair value is introduced. [Feil–Nendel §2.1.1](https://arxiv.org/pdf/2607.17991); [Polymarket displayed-price rule](https://docs.polymarket.com/concepts/prices-orderbook).

## G. Complete parameter audit

| Model input or coefficient | Paper role | Classification for BTC5m | Current evidence / dependency |
|---|---|---|---|
| `T`, elapsed `t`, actual BBO/tick | Horizon and venue admissibility inputs (`BBO/tick` are **not** original model states) | **Directly observable** | Official market metadata and WS/HTTP books; persisted 20-market pilot. |
| `Y`, token IDs, actual account YES/NO, actual spendable balance | Settlement/account facts; last two needed by BUY-only mapping | **Directly observable through official APIs**, only partly persisted | Token IDs/books in manifests, accepted deltas/snapshots; local baseline balances and final resolutions incomplete. |
| `p_t`, `p_T`, `L_t` | True conditional belief, terminal belief, latent belief | **Missing as exact variables** | Mid and logit-mid are proxies; no observed conditional probability or independent latent signal. |
| Choice of `f`; numerical logistic transform | Belief-to-price functional form | **Exogenous/model specification** | Logistic is paper's numerical choice, not a venue fact. |
| `σ(t,L)` / numerical `σ₀,σ₁,σ₂,η` | Belief diffusion / volatility shape | **Estimable from existing passive data only conditionally; more passive markets for credible fit** | Official book price paths exist but are tick-discrete and short; martingale/diffusion assumptions untested. |
| `Λᵇ,Λᵃ`; numerical `A₀,A₁,ξ,ν,k₀,k₁,κ` | Execution intensity level and price/time/belief shape | **Requires own-order data** | Existing 74 strategy-selected ACKed orders/27 fills do not identify quote-distance functions; more public L2 cannot repair this. The paper gives illustrative, uncalibrated numbers. |
| `Δ` | Fixed shares per jump | **Exogenous/control, constrained by official minimum** | Local minimum/order size 5; partial fills in actual records violate exact jump size. |
| `Q` | Maximum absolute signed inventory | **Exogenous/control** | Could be set by risk preference, cash and order limits; not statistically identified from books. |
| Initial `q₀`, `X₀` | State at start | `q₀` **partly reconstructable**; `X₀` **missing locally** | Runtime can request official positions/balance, but capture does not certify complete start cash/holdings for each session. |
| `γ,γ_T` | Running and terminal risk aversion | **Exogenous/preference parameters** | Paper's Table 1 values are illustrative and depend on horizon, size and cash units. |
| `Φ(p,q)` | Terminal settlement-risk penalty | **Specified by model**, belief dependent | Formula exact under paper assumptions; numerical value depends on proxy `p` and chosen `γ_T`. |

**Execution dependency cannot be hidden.** Both Hamiltonians multiply the incremental value by `Λ(t,p,π)`. Level, quote-distance slope, time/belief dependence and bid/ask asymmetry therefore change the optimum; the paper's numerical analysis explicitly changes spread and skew when liquidity sensitivity changes. An arbitrary uncalibrated intensity can illustrate a policy, but **cannot justify an operational “optimal” BTC5m quote or historical replay**. The 27 authenticated fills confirm some outcomes of strategy-selected orders, not a controlled range of quote distances. The old public-trade/L2 historical fill proxy failed against own fills and remains excluded. This task does not estimate a replacement fill model. [Feil–Nendel §§2.1.3, 3.1, 4.1–4.3](https://arxiv.org/pdf/2607.17991); [historical own-fill validation](DP_M0B_OWN_FILL_LABEL_VALIDATION.md).

## H. Existing data sufficiency

Read-only inspection of current files: `research_data/m4a/` has **30 sessions, 26 BTC5m slugs, and 5,751,850 manifest-recorded events**. The same pilot inclusion rule as the historical DP studies yields **20 distinct complete, zero-manifest-gap/loss, ≥50,000-event markets**, totaling **5,750,067 recorded events**, with named market starts from 2026-10-02 10:25 through 18:45 UTC, non-contiguously. Both YES/NO books have full `BOOK` snapshots, `DELTA` updates, BBO/depth and timestamps; `REST_BOOK` and tick changes occur. Eleven usable sessions have nonempty `states.jsonl` with first-fill episodes and YES/NO quantities. The authenticated order audit finds **74 accepted maker orders, 27 accepted fill events in 11 markets, including 12 FIRST and 15 PAIR fill events**. Run reports carry market IDs, window times, selected ticks and order/fill summaries. The latest DP edge-slack study found all 12 causal opposite books at FIRST fills had a one-tick spread; this describes observed book feasibility, not a fill prediction. [Local historical dataset audit](DP_M0_MAKER_FILL_RESEARCH_DESIGN.md); [own-fill audit](DP_M0B_OWN_FILL_LABEL_VALIDATION.md); [edge-slack audit](DP_M0B2_EDGE_SLACK_STUDY.md).

**Sufficiency judgment:** sufficient to inspect the economic YES/NO transformation, check causal book/tick feasibility at recorded states, describe a market-price proxy, and construct accepted inventory deltas. Insufficient for the paper's exact live state or empirically justified optimal quotes: initial spendable cash/account baseline and verified terminal outcome are not consistently persisted, the true conditional belief is unobserved, quote-distance arrival functions are not identified, and the paper's `[0,1]²` action set does not encode post-only/collateral constraints. An official runtime account read can supply a current balance, but this historical capture cannot reconstruct each past balance without assumptions. No historical hypothetical fill replay is authorized.

## I. Maker-only and tick compatibility

The original paper describes bid/ask liquidity provision, but its formal controls merely lie in `[0,1]²`; they are **not bounded by contemporaneous best opposite quotes** and can in principle be marketable on an actual CLOB. It has no current BBO in the HJB state and no post-only rejection transition. Polymarket's official post-only flag rejects a crossing order, while a legal price must follow the market tick and minimum size. [Paper §§2–3](https://arxiv.org/pdf/2607.17991); [Polymarket orders](https://docs.polymarket.com/trading/place-orders).

Given observed YES best ask `a_Y`, NO best ask `a_N`, and tick grids, paper output would have to satisfy `πᵇ<a_Y` for BUY YES and `1−πᵃ<a_N` for BUY NO, plus valid ticks and available collateral. If opposite books mirror exactly, `a_N=1−b_Y` reduces the NO condition to `πᵃ>b_Y`, but that equality is a venue/book state to verify, not a paper assumption. With a one-tick spread there is no strictly inside-spread maker price. **Compatible with a simple per-order admissibility constraint as a control *filter*, but not the paper's exact optimality claim:** optimizing the HJB over a BBO-dependent discrete feasible set requires modelling that evolving feasible set (and rejection/funding state); merely rounding or clipping the continuous solution is a different policy. Nearest-tick rounding may cross; favorable-side rounding (bid down, ask up) preserves price direction but changes the objective; direct maximization of the paper Hamiltonian over legal ticks is the mathematically faithful *instantaneous* alternative, yet still omits future book/cash dynamics. No rounding rule is selected here.

**BUY-only conclusion:** payoff and signed-inventory economics map exactly via BUY NO `=1−πᵃ`; spendable funding and possibly execution-route distributions do not. Thus BUY-only is economically compatible under unlimited collateral or instantaneous costless complete-set conversion, and **not operationally exact** under the observed finite-collateral, no-auto-merge runtime. Maker-only is feasible as an execution constraint but not already solved by Feil–Nendel's verified control problem.

## J. Five-minute expiry behavior

BTC5m has a 300-second named event window; the [audited market rule](https://polymarket.com/event/btc-updown-5m-1790937900) resolves UP when the specified BTC/USD TWAP over that window is at least its initial reference, otherwise DOWN. The paper's `T=1` numerical examples are normalized and cannot be copied to seconds without scaling diffusion and intensity parameters. It assumes a strictly interior `p_T` immediately before a separate Bernoulli revelation; whether BTC5m trading close, reference availability, and official resolution fit that boundary is **unverified**. It is especially unsafe to treat `p_T` as a known true probability merely because the market is about to close.

Exactly from the paper's terminal condition, as `t↑T`, `V(t,p,q)→−γ_Tq²p(1−p)` under its regularity. Its two inventory-difference inputs then approach

```text
zᵇ(T,p,q)= γ_T(2q+Δ)p(1−p),
zᵃ(T,p,q)=−γ_T(2q−Δ)p(1−p).
```

Because optimal bid decreases with `zᵇ` and optimal ask increases with `zᵃ`, positive `q` pushes the bid lower and ask lower near expiry: discouraging added YES while encouraging a reduction of YES exposure; negative `q` reverses this. Running risk has less time to accrue, but terminal settlement risk remains, so the net time trend is **not universally monotone**. In the paper's *illustrative* numerical specification, spread generally narrows over time as assumed order-flow sensitivity rises, then widens near settlement around `p≈0.5`; inventory skew can strengthen near expiry because there is less time to unwind, while risk effects fade as `p→0` or `1`. These implications are conditional on paper parameters and no maker/funding constraint; they do not prove an executable late “REPAIR” quote on BTC5m. [Feil–Nendel §§2.2, 3.1, 4.3](https://arxiv.org/pdf/2607.17991).

## K. Structural comparison with reported public-account behavior

The user reports prior qualitative observations of FlippingSharks, sixx7, x-MoneyForWhiskas, and VidarX: both-side accumulation, BUY-heavy activity, fast opposite-side acquisition, and balancing. No account-level history for those accounts was found in the inspected repository research documents or local dataset; this report does **not** independently verify those observations, fit the accounts, or claim that they use the model. **Theory:** if signed `q` is positive after a YES buy, Feil–Nendel tends to favor the ask-side action, which transforms economically to buying NO; after a NO buy it tends to favor buying YES. **Inference:** this can resemble second-side pairing and balance-seeking, while repeated matched accumulation is invisible to `q` and `X` as a spendable-cash constraint. The model does not itself imply a BUY-only preference, rapid sequencing, profit-positive pairs, or the observed accounts' motives. [Feil–Nendel §4.3](https://arxiv.org/pdf/2607.17991).

## L. Blocking issues

1. **Core state/control gap:** physical matched pairs consume collateral under BUY-only execution, but `(X,q)` cannot recover free cash or gross pair inventory; paper's action set omits live BBO/tick/post-only feasibility. The exact wealth transformation does not fix these control constraints.
2. **Uncalibrated own-execution intensities:** the paper's `Λᵇ,Λᵃ` are theoretical/illustrative. Current selected own-order sample does not identify the required quote-dependent functions, and public L2/trades are invalid own-fill substitutes.
3. **Belief and expiry assumptions:** true conditional `p_t`, diffusion shape, and strictly interior pre-revelation `p_T` are unverified for this five-minute TWAP market; official YES mid is only an observable proxy.

No PnL or policy-replay conclusion follows. The first issue determines the **REQUIRES MODEL EXTENSION** classification even if every numerical coefficient could be calibrated. If a future established model covers the missing finite-collateral and maker-action state, its intensity requirement would still need independent evidence.

## M. Recommended next action

**Identify and evaluate one published prediction-market stochastic-control model with explicit finite collateral/complete-set inventory and post-only quote admissibility before authorizing any replay or trading implementation.**

# 固定算法与字段字典

schema_version=RDP_RISK_SHADOW_V2。所有 timestamp、*_utc、*_time、*_at 数值时间字段
为 UTC Unix seconds；*_monotonic 为该进程单调秒，*_ms 为毫秒。
所有价格单位 USDC/share，库存单位 share。
数值 0=ZERO；字符串 UNKNOWN=无法计算；字符串 N/A=不适用，JSON 不使用 NaN。

## 通用与采样

market_slug、condition_id、session_id 关联市场/真实 Session；纯观察 session_id=N/A。
每条记录 timestamp 为产生记录的本地时间，observer_sequence 为全文件共同的 FIFO 序号。
shadow_dropped_event_count_so_far 为该条入队前累计丢弃数。
市场开始使用官方 eventStartTime，endDate=start+300，与 slug epoch 严格一致。
elapsed_sec=snapshot_utc−market_start_utc；remaining_sec=max(0,300−elapsed_sec)。
slot_index=0..299；scheduled_snapshot_time=start+slot_index；actual_snapshot_time 为实际采样时间；
snapshot_delay_ms=(actual−scheduled)*1000。slot_validity=OBSERVED/MISSED。
MISSED 不回填；产生相应 UNKNOWN 记录。T300 只有 OBSERVATION_END 事件，不计基础槽位。
checkpoint 在 30/60/120/160/180/210/240/270 为 true。summary.checkpoints 保存真实槽位证据。

## 行情

yes_token_id/no_token_id 来自官方 Up/Down 映射。YES/NO 分别保存 best_bid、best_ask、
mid、spread、quote_source_timestamp、local_receive_timestamp、quote_age_ms、quote_validity、
official_tick_size、event_id。顶层 official_tick_size 为两侧 tick 字典。
完整合法盘口要求 0≤bid≤ask≤1。Mid=(bid+ask)/2，Spread=ask−bid。
quote_age_ms=(snapshot−实际接收时间)*1000；≤5000ms VALID，>5000ms STALE；缺失 MISSING；
错误身份 INVALID_IDENTITY；本地时钟不可靠 CLOCK_RESET。读取缓存不刷新接收时间。
官方事件时间只用于审计，不参与本地 freshness，也不假设两种时钟同步。
p_yes=有效 YES Mid；NO 不填补缺失 YES。p_yes_from_no=1−有效 NO Mid。
两侧有效且接收时间差≤1s 时 crosscheck_error=abs(YES Mid−(1−NO Mid))。
差异>0.02：LOW_CONFIDENCE，不生成价格依赖的有效风险；否则 VALID（仅近同步）。
接收时间差>1s：ASYNCHRONOUS、error=UNKNOWN；无有效 NO：NOT_AVAILABLE。
quote_validity 为主 YES 概率口径及 crosscheck 门禁；各侧有效性独立保存。
quote_gap_reason 为对应失败或 SLOT_MISSED。过期原始价格只供审计，不用于实时风险。

## 库存

q_yes/q_no 只来自权威执行快照；delta=q_yes−q_no。
inventory_side=YES/NO/NEUTRAL，库存未知则 UNKNOWN。
inventory_validity=VALID/UNKNOWN；inventory_updated_at 是该执行事实对 Observer 已可见的时间；
actual_strategy_state 来自实际事件，未知时 UNKNOWN，交易结束 STOPPED。
confirmed_fill 不代表 Shadow 成交，不允许事后修改旧库存或旧风险。

## VOLATILITY_V1

真实 YES 更新按市场时间 1 秒桶，仅选各桶最新有效点，保留真实接收时间。
重复投递 fingerprint 去重；没有更新的秒不产生价格点；真实更新但 Mid 不变允许 dp=0。
在观察时刻以前已经可见的点中选 [t−30,t]；两端都必须在窗口内。
dp=p_i−p_(i−1)，dt=本地单调接收时间差；仅 0<dt≤5 且相同连接 generation 的区间有效。
V_hat=sum(dp²)/sum(dt)，sigma_hat=sqrt(V_hat)。不年化，不使用收益率。
区间数≥10、有效时间≥20s、最新 YES Age≤5s 才 VALID，否则 INSUFFICIENT_DATA。
缺失或陈旧价格使用 MISSING_PRICE/STALE_PRICE。跨重启不拼接；时钟跳变重启估计并记 SHADOW_ERROR。
vol_interval_count、vol_valid_duration_sec、vol_gap_count、vol_latest_quote_age_ms 描述有效覆盖。

## VOL_RISK_V1

H=min(30,remaining_sec)。r_vol_proxy=Delta²×V_hat×H，volatility_sd=sqrt(r_vol_proxy)。
单位 USDC²/USDC。risk_model=FIXED_INVENTORY_LOCAL_VARIANCE_PROXY_V1。
假设未来 H 秒库存和局部方差率不变，不是完整 HJB 或校准预测。
有效零净库存方向风险为 0，即使 sigma_hat 未知；非零库存且 V_hat 未知则 UNKNOWN。

## REALIZED_VOL_RISK_V1

r_vol_realized=sum[区间起点 Delta²×dp²]，采用上述相同一秒桶价格点。
仅累计已经结束的桶区间，最后一个仍开放的桶不提前纳入累计。
两端库存有效且区间内确认库存状态未变才计算；确认变化保守排除整个区间。
INVENTORY_TRANSITION_GAP、PRICE_GAP、INVENTORY_UNKNOWN 按 realized_gap_reasons 计数。
迟到至已经冻结桶的事件不改写历史累计，标记 PRICE_GAP。
realized_interval_count、realized_valid_duration_sec、realized_gap_count、
realized_inventory_transition_count 描述市场内累计覆盖。
无任何有效区间时 UNKNOWN；有缺口时 PARTIAL；否则 OBSERVED_INTERVALS_ONLY，
不宣称覆盖未知的市场起始/结束边界。这是方向平方价格暴露代理，不是账户 PnL。

## SETTLEMENT_RISK_V1

p=YES Mid。r_settle=Delta²×p×(1−p)，settlement_sd=sqrt(r_settle)。
WinProb=p（Delta>0）、1−p（Delta<0），LoseProb=1−WinProb。
Delta=0：概率 N/A，市值、风险和兑付变化均为 0。
FavoriteSide=YES/NO/TIE，FavoriteProbability=max(p,1−p)。
InventoryAligned：非零库存与 Favorite 相同 true，不同 false；零库存/TIE 为 N/A。
N=abs(Delta)，InventoryMarketValue=N×WinProb；TerminalWinValue=N，TerminalLoseValue=0；
WinPayoffChange=N−InventoryMarketValue；LosePayoffChange=−InventoryMarketValue；
ConditionalDownsideLoss=InventoryMarketValue，是失败条件下损失，不是预期损失。
expected_terminal_payoff_change=WinProb×WinPayoffChange+LoseProb×LosePayoffChange≈0。
这是公式一致性，不表示 Maker 收益为零。结算方差不能与 r_vol_proxy 直接相加。

## 假设研究

inventory_source=HYPOTHETICAL，virtual_delta_plus5=5，virtual_delta_minus5=−5。
virtual_plus5/minus5 的 r_vol、r_settle、settlement_sd、win_prob 直接复用上述算法。
实际库存字段仍 UNKNOWN，不产生虚拟成交、虚拟实际 PnL 或实际决策对照。

## SHADOW_TIME_POLICY_V0

仅真实 REBALANCE、可靠非零库存、有效对应侧盘口才生成候选。
Delta>0 BUY NO，Delta<0 BUY YES；Delta=0 NONE。
ShadowSize=当前 ActualIntendedSize；没有真实数量则 UNKNOWN。
0≤T<60：PASSIVE、5 tick；60≤T<150：PASSIVE、2 tick；150≤T<300：ACTIVE、0 tick。
ShadowQuote=对应 BestBid−offset×动态官方 tick。合法性复用当前 maker 约束口径：
tick≤price≤1−tick、tick 网格、price<ask、数量网格/官方最小数量及 1 USDC 名义金额。
非法候选不截断，UNDETERMINED/UNKNOWN；TICK_UNKNOWN 或 INVALID_CANDIDATE_QUOTE。
同时保存 candidate_bid_0tick/1tick/2tick/5tick 及各自 validity。
HoldCandidate 只有 T≥240、非零 VALID 库存、VALID 概率、Favorite≥.95 且 aligned=true 时为 true；
可靠但不满足为 false；关键输入未知为 UNKNOWN。不覆盖候选，不发送交易指令。

比较字段包含真实 intended/submitted/ACK/confirmed_fill 与 Shadow 候选，分别追加生命周期 stage。
PriceDifference=ActualIntendedPrice−ShadowQuote；ticks=差价/官方 tick；相同数量时 usdc=差价×size。
不可比较 UNKNOWN。正数仅表示实际报价更高，不表示收益或一定能成交。
固定 difference type：SAME_QUOTE、PRICE_DIFFERENCE、SIZE_DIFFERENCE、SIDE_DIFFERENCE、
ACTUAL_NO_QUOTE、SHADOW_UNDETERMINED、INSUFFICIENT_MARKET_DATA、INSUFFICIENT_INVENTORY_DATA、
INVALID_CANDIDATE_QUOTE。Tick 缺失的 reason_code=TICK_UNKNOWN。

## Summary

expected_snapshot_slots=300；snapshot_slot_record_count 为生成记录总数；observed_snapshot_slots 为
及时实际观察槽位数；valid_yes/no_snapshot_slots 为各侧有效槽位数。
market_data_gap_count 为主概率无效槽位数，WS connection/queue 详情在 market_events/ws_metrics。
shadow_dropped_event_count 为有界写盘队列拒绝总数；writer_error_count、shadow_error_count 分别统计。
volatility/inventory/settlement_risk 的 valid_count、unknown_count 分开统计；
actual settlement risk 无库存为 UNKNOWN，hypothetical 文件仍可能有效。
rebalance_decision_count/shadow_comparison_count 包括初始计划及真实修订，生命周期追加不重复计决策。
same_quote_count、price_difference_count、undetermined_count 为真实决策对照计数；
hold_candidate_count 为每秒实际库存研究标签为 true 的槽位数。
observed_market_duration_sec 是实际观察进程覆盖的市场时长，不由缺失槽位补写推导。


## 单命令集成补充（2026-10-14）

rdp-sm-run --shadow 自动传递执行身份与 Journal 路径；不更改模型公式或策略参数。
附加模式中 risk_snapshots 除基础槽位之外，可以有 record_type=DECISION 的风险记录。
其 risk_snapshot_id 唯一绑定 decision_id；timestamp/decision_timestamp 为实际决策的源时间，
observed_at 为生成关联记录的时间。market_input_snapshot_timestamp 标明已接收行情输入槽位，
inventory_source_timestamp 标明该决策之前的权威窗口库存源时间。
不改写已经保存的原始槽位，也不使用决策之后的确认成交。
summary 的槽位有效性计数仍仅统计300个基础槽位；written_records 包括事件风险记录。
自动附加每20ms只读轮询，单轮最多256 KiB；无需等待子进程完成来关闭真实交易资源。

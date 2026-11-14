# 公共信号与执行约定 v1

```json
{
  "document_type": "signal_common_contract",
  "version": "maker-discovery-common-v1",
  "clock": "available_mono",
  "round_window_ms": 500,
  "round_min_hit_shares": "0.5",
  "round_min_depth_fraction": "0.0001",
  "stable_recovery_min": "0.50",
  "stable_continuous_hold_ms": 100,
  "support_guard_recovery_min": "0.40",
  "input_lateness_max_ms": 50,
  "per_token_coverage_bucket_ms": 250,
  "wait_cap_ms": 3000,
  "minimum_life_at_submit_ms": 400,
  "entry_market_age_ms": [15000, 240000],
  "cancel_market_age_ms": 240000,
  "order_budget_usdc": "5.00",
  "primary_anchor_price": ["0.20", "0.80"],
  "execution": "guarded-maker-v1",
  "partial_fills": "aggregate_by_order_id"
}
```

## 1. 数据、时钟和价格

仅使用公开 L2 深度、可判别主动方向的逐笔成交、token/market 映射、tick、数量精度和行情质量信息。Up 与 Down 分开建状态；直接与互补镜像成交去重后才可计量。本轮研究默认只用该 token 的直接成交；若适配器需要镜像，另记映射和去重版本，不能双计。

`received` 为输入事件原接收时刻，`available` 为事实真正可供策略使用的时刻。轮次统计窗口用 received，候选确认、失效及发送用 available，按 `(available, input_seq, subindex)` 串行处理。必须先处理当前输入造成的失效，再发布候选或准入订单。水位未过窗口终点不确认；确认晚于终点 50ms 标为 UNKNOWN。闭区间/开区间约定：窗口 `[start,end)`；候选 `[confirmed,deadline)`；到期等号一律失效。

每 token 每个 250ms 桶须有可证明的有效输入覆盖；缺失/断线/序列缺口/不可解释方向标 UNKNOWN，不把无数据当零成交。完整快照重建后换 generation，旧状态全部终止。档位价格和数量用 Decimal/tick 整数，禁止浮点相等判断。锚档消失即结束，不用换档后价格填回原观察。价格上移/下移本身不确认方向。

## 2. 新候选共用的真实受击轮次 R

对同一 token、同一 side、固定原价档 p：BID 只收主动 SELL，ASK 只收主动 BUY。第一笔合格方向的直接成交开启 500ms 轮次；第一笔的前置盘口数量 `Q0>0`，期间最低数量 `Qmin`，窗口终点数量 `Qend`，窗口内该价主动成交量 `H`。

`L=Q0-Qmin`；`hmin=max(0.5,0.0001*Q0)`。只有 `H>=hmin` 且 `L>=hmin` 才合格。`u=H/Q0`，`d=L/Q0`，`r=clip((Qend-Qmin)/L,0,1)`。分母为零不合格，静态厚墙或净新增挂单不能代替真实冲击。

**新候选稳定轮**：合格且末尾连续 100ms 的恢复率 >=0.50（以当时已知最低量计算；出现新低重新计时），关闭时 r>=0.50。此定义比旧适配器的 0.4/0.2 滞回保持更严格，不能无标记复用旧 `stable` 布尔值。实现可从原始路径重算；只有旧布尔值时只能做近似诊断，不能计为本版通过。弱轮不要求稳定，按各 SOP 的 r/d 条件判定。

相邻轮必须不重叠、同档同 generation，后一轮第一笔成交发生于前一轮事实已可得之后。对比轮要求指定的归一化冲击比 `u2/u1` 与深度损失比 `d2/d1`；跨 Bid/Ask 的这些比值只是归一化强度对照，不能声称两侧绝对流量相同。

## 3. 固定支撑 S 与等待检查

稳定 Bid 轮确认于 c 时生成支撑，原锚 `P`、`Qmin`、`L` 固定。维持数量底线 `F=Qmin+0.40*L`。逐笔深度检查 `Q(P)>=F` 且原档仍存在；任何一次违反即终止，不允许同一消息后续回补掩盖中间跌破。后续合格 Bid 轮失败也终止。S 的寿命由单信号给定，默认最多 8 秒；新好轮可产生新证据，但不延长既有候选的 deadline。

这是一条**发出撤单请求的规则**，不能消除撤单在途成交。成交与失效同一不可排序时间戳须标 `ORDERING_UNKNOWN`。已发送订单在取消确认前仍可能成交；所有这种成交进入全量经济账，另标失效后成交，不从 PnL 中删除。

## 4. LW：疑似净撤供（沿用 M6 观测量，非逐单撤单事实）

以出现首次深度减少前的 Ask 前三档固定集合 A，初始总量 `A0`。`loss=max(0,A0-sum(Qask(A)))`；累计直接 BUY 成交 `B` 的范围是 `[start-500ms, now]` 且价格在 A；`residual=max(0,loss-B)`。

在 750ms 形成窗口内，`loss/A0>=0.30`、`residual/A0>=0.20`、`residual/loss>=0.50` 连续保持 100ms 且有前 500ms 完整覆盖，确认 LW。其原生有效期为确认后 750ms。原 Ask 最优档必须持续存在；失去原档结束这次结构。

当前维护：`loss>0`、`residual/A0>=0.10`、`residual/loss>=0.25`，否则供给恢复而失效。M13/M15 若使用归档 LW 作为形成历史，会明确冻结 A/A0/start，并在自己的有限窗口内重算累计 B 和维护条件；绝不把已过期 LW 冒充当前有效 LW。累计量从历史前沿保存，不从只有 3 秒的缓存反复求和而漏掉早期成交。L2 无法分辨逐单撤回、增加和同时成交，名称只能是“疑似净撤供”。

## 5. Maker 执行 E：guarded-maker-v1

对买入某 outcome token：确认后以信号原锚 P 下 post-only 买单，须 `P<=best_bid` 且 `P<best_ask`、P 档有量、tick/精度合法、账户资金足够。**若原锚不可合法挂单则跳过，不把价格降到别处后仍声称原档承接。** 报告实际到达时 post-only 是否被接受；拒单不算成交。

每订单预算最多 5 USDC，按交易所最小量和精度向下取整；不足最小量跳过。每策略版本/市场/token 最多一次提交（被拒也占次数），每个证据 root 最多一次，禁止多个重叠情景拼成交易数。市场开盘 T15 起、T240 前准入；发送时距离信号截止和 T240 均至少 400ms。

撤单决定时间为 `min(signal_deadline, confirmed+3000ms, market_open+240000ms)`，或任一硬失效更早出现时。最早确认起计时，不从重试/下单回执重新开始。不改价、不补单。下单与撤单都用冻结的到达延迟模型；记录 submit、ack、cancel_request、cancel_ack、fill。

主撮合仅可使用已验证的公共成交驱动、FIFO queue 模型，前置可见队列不能当作零；未知撤单归属不能自动全算在前方。直接成交穿过一 tick 是独立敏感性情景，须冻结数量/部分成交模型，不能因一笔极小穿价自动填满任意大订单。发单即成交只作信号价格诊断。主模型、穿价、假设成交分别记账；正式 24h 开始前须冻结具体执行适配器与参数。

成交持有到官方市场结算。失效后竞态成交仍记完整经济结果，另外统计信号有效期间成交占比。没有行情证据的订单是 UNKNOWN，不是确定零成交。不能只挑“有效期间的赢家”来计算总策略收益。

## 6. 必须输出的审计字段

`market, token, strategy_id, version, sop_hash, common_hash, implementation_hash, generation, root_id, candidate_id, component_ids, input_seq, received, available, confirmed, deadline, anchor_price, component_deadlines, Q0, Qmin, H, u, d, r, guard_floor, quality_status, end_reason, submit_id, order_id, execution_model, fee_status, fill_before_invalidation, settlement_source`。

没有足够数据计算字段应明确 UNAVAILABLE，不能补零。成交后才可补充结果字段，任何 settlement 信息不可回流信号生成。

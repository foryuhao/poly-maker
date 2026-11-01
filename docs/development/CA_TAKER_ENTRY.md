# CA Maker / Taker 开仓切换

CA 检测器、确认门槛、形成时长、信号有效期、单次消费，以及 `ca_entry_direction`
的方向选择保持原样。执行配置默认 `maker`，只有显式选择 `taker` 才会吃卖一。

在用于 CA 的 `config.toml` 中设置：

```toml
[rdp_sm]
normal_entry_mode = "ca_v1"
ca_entry_execution = "taker" # "maker" 恢复原有被动买入

[rdp_sm.ca_taker]
order_type = "FOK"          # FOK 全成或撤；FAK 允许部分成交，余量立即取消
max_slippage_ticks = 0       # 0：最高买价为准备时的卖一；可增加允许跨过的 tick 数
max_price = "0.99"          # 价格上限，超过则不提交，不截断为另一种挂单
entry_price_ceiling_enabled = true # 开启 CA 开仓价格过滤；false 关闭
entry_price_ceiling = "0.90" # 达到或超过此价格，跳过建仓（0.90 本身也跳过）
```

在已有的对应配置表中设置这些参数，不要在同一 TOML 文件重复声明同一个表。
原有 CA observation、仓位、风控、TP 等配置仍需保留。无需修改全局
`execution.post_only`；TP、BA 和其他策略路径依然强制 Maker。

`ca_entry_direction` 不随执行方式改变：`higher_bid` 仍买两边 Bid 较高的一侧；
`signal` 才买 CA 信号同侧。要保留过去的方向选择，就保留当前值。
Taker 与 `ca_pullback.enabled=true` 不兼容，配置加载时会拒绝。

## 下单和失败语义

- 使用真正的 `postOnly=false`、FOK/FAK 限价单；不会用 GTC 模拟 Taker，
  未成交部分不会留在盘口。
- 价格在签名前冻结为当时 Ask 加允许滑点；超过 `max_price` 或合法价格范围则跳过。
  `entry_price_ceiling_enabled=true` 时，冻结价格还必须严格低于 `entry_price_ceiling`；
  默认阈值为 0.90，包含滑点后的价格。关闭此开关仍受 `max_price` 限制。
  此过滤只用于 CA Taker 开仓，不限制 Maker TP 或 TP 到期后的 Taker 配对退出。
  提交前再次核对原 CA、方向、盘口、资本和价格上限。更优 Ask 可以成交，超过上限不追价。
- 延续原有按份数计算的开仓数量。低于市场份数或 marketable BUY 金额要求时不自动扩仓。
- 每个 CA 只在真实 POST 边界消费一次。拒绝、未成交、超时均不自动重新发一笔；
  只有未实际提交的候选可以沿原有规则重新准备。
- 在发送前持久化签名订单 ID 和含费用的风险上界。超时后按同一 ID 核对，
  UNKNOWN 不当作未成交，不依赖空持仓或取消回执释放风险。
- 交易所的 FOK 未全成或 FAK 无撮合回包可能同时带 `success=true`、签名订单 ID 和
  明确的 `order couldn't be fully filled`／`no orders found to match with FAK order` 错误。
  仅在原生 POST 记录的 FOK／FAK 类型与该错误完整匹配、ID 精确一致、
  成交金额／状态／成交及交易哈希均无接受证据，并且后续完整订单页、成交页无矛盾时，
  才记为 `REJECTED`、释放本笔预留。不会等待一个被 FOK kill 的订单永久出现在详情接口。
  页面失败或缺页仍保留 UNKNOWN；迟到的真实订单或成交会撤销拒绝证明，重新核对。
- FAK 部分成交按实际数量进入 TP/残仓流程；不足 Maker TP 最小数量时保留真实残仓，
  不为了 TP 再补开仓。

## 成交、费用和 TP

Taker 只按本次签名订单的精确 `taker_order_id` 归属，不能借用对手方的 Maker ID。
同 token 的 SELL 对手单和互补 token 的 BUY 对手单均校验经济关系；多档撮合按
对手方各条成交的价格、数量计算真实加权成本。WS 早于 ACK 会先缓存，再按签名 ID 入账；
REST 和执行缓存使用同一归属逻辑。重复状态更新不重复增加仓位。

FOK／FAK BUY 的签名授权量是美元支出预算。`price=0.83, size=5` 签名本金为
4.15；若撮合改善到 0.81，交易所可买到 5.123457 份，订单详情仍可能报告
`original_size=5, size_matched=5.123457`。这不是超额花费，也不修改原签名或登记数量。
只有精确登记的 CA Taker BUY ID 可接受这种详情形态；普通 Maker／SELL 的份数上限不变。
每笔及累计成交本金按 6 位美元精度核对签名预算（最多 1 micro 精度差），逐腿价格不能
高于签名限价，本金加实际手续费不能超过预留总上界。WS、REST、缓存与最终收口使用
同一规则；越界、身份冲突、迟到矛盾仍会阻断。

Maker TP 使用核实后的实际开仓份数（按原 0.01 份精度补齐），而不是截断到签名中的
名义 5 份。开仓回包丢失后，仅在相同签名 ID 的成交、订单终态和实际账户完整证明后，
才恢复该轮 TP／到期退出；原 CA 不重复开仓，恢复不会重置从首次观察成交起算的兜底期限。

开仓准备期读取官方 CLOB market-info 的费用参数，缺失或不支持时不提交。
手续费按协议参数和每条撮合记录计算并按五位小数舍入；若官方记录提供 `fee_usdc`，
还需与协议计算和已预留上界核对。费用来源参数另记日志，不伪称来自订单 ACK。
成交记录保留实际 VWAP、`liquidity_role=TAKER`、`principal_usdc`、`fee_usdc`。
金额和仓位最终仍经过原有订单、成交、持仓及资本校验。

TP 继续用原有 Maker 对侧买入，目标依据**含开仓费的成本**，保留原
`profit_per_share`、TP TTL、撤单和到期结算流程。例：实际买入 0.52，
费用率 0.07、指数 1 时每份成本约 0.53747；若目标净利润为 0.05，
对侧 TP 上限约 0.41253，按 0.01 tick 向下取 0.41。

## 验证与启用边界

离线测试：

```bash
PYTHONPATH=src:tests .venv/bin/pytest -q tests/test_rdp_sm_ca_taker.py
```

测试使用真实 SDK 签名、原生 POST 权限、Mock HTTP、确认成交和完整收口路径，
禁止真实网络/交易。覆盖两侧、FOK/FAK、部分成交、执行缓存、Maker TP、费用、
价格变化、UNKNOWN 和同信号防重复。没有历史完整盘口，未声称回测 Taker 收益。

配置仅在新进程启动时读取，不热切换运行中的实盘进程；启动、停止由操作者执行。

## TP 到期后 Taker 退出

`[rdp_sm.ca_matched_tp] taker_exit_on_expiry = true` 开启到期配对退出；
默认 `false`，继续保留到期撤 TP、残仓持有到结算的行为。此模式要求 TP
已启用且 `repair.ba_enabled = false`，防止 BA 提前接管同一轮库存。

TP 仍在开仓实际 MATCHED 后挂出，原 `ttl_sec`（当前 30 秒）从 TP 首次 POST
开始计算。如果未能挂出 TP，则以首次观察到开仓成交加 `ttl_sec` 作为退出时间。
TP 完整成交时，该轮已配对，不额外发 Taker。部分成交和撤单竞争中的成交均先核对。

到点先撤 TP，并等待开仓、TP 的终态、完整成交集合及实际库存核对通过；随后
按本轮剩余数量及当前卖一，计算对侧 Taker BUY FAK，覆盖原仓。全部订单继续为 BUY。
每次退出签名本金为 `max(1, ceil(剩余原仓份数 × 当前卖一 × 100) / 100)` USDC，
先把所需金额向上补齐美分，再保留最低 1 USDC，不为凑整美分把份数放大到整数。
例如剩余 5.069442 份、卖一 0.27，仅签 1.37 USDC，名义份数为 5.0740；
剩余 5 份、卖一 0.19，仅签 1 USDC，名义份数为 5.2631；
卖一 0.01 时仍签 1 USDC，可买 100 份，配对原仓后留下 95 份反向仓位。
该最小必要补量可超过普通 `max_order_size`，不修改其他路径的数量限制。
资金和额外反向仓位仍由原来的合仓／到期结算流程处理。

退出价冻结为准备时卖一，提交前必须仍等于当前卖一；报价改变则放弃该签名，重新准备。
直接按美分预算构造 FAK BUY，使用 SDK V2 签名器；签名名义份数按预算除以卖一后
向下保留 4 位小数，细 tick 也不产生 5／6 位份数精度拒单。实际本金上界始终是签名
美分预算，不能改用截断后的名义份数乘卖一；费用预留、WAL 和成交核对均使用该预算。
只有待买侧存在有效、当前且
tick 已核验的卖盘才下单；该侧缺少买盘不阻止 BUY，空卖盘、断流和错误盘口仍会等待。
两侧 Taker 手续费均进入成本和 PnL，WAL 风险上界、余额及 Session 预算也包含退出费。
即刻 BUY 可能因价格改善获得更多份，费用预留按美元支出上界计算，不能继续按名义
份数估算。实际手续费始终按撮合明细单独入账；本金加费用不得超过预留总上界。
开仓前还需确认预算覆盖整轮开仓、最坏退出价格、最后一笔最低金额补量及费用。

30 秒是启动退出的时间，不能保证交易所在第 30 秒完成。撤单、成交确认、网络、
深度仍可能延迟或阻止退出。每次 FAK 后先完成订单终态、完整成交集合和实际库存核对；
明确拒绝、未成或部分成交后，只对未覆盖的原仓数量重新按当前卖一准备，最小重试间隔
为 `max(0.1, repair.review_sec)`，直到覆盖原仓或遇到不可执行条件／市场提交截止。
单次 FAK 无撮合不是整轮退出的终点：严格 kill 证明通过后，释放该单预留，以新的
签名 ID、最新卖一与最低金额要求继续退出。失败的完整订单／成交页、真实迟到成交、
金额或状态字段不明时，仍不允许把旧单当成未成而直接补第二笔。
撤 TP 后遇到 `SYNC_CACHE_NOT_READY` 等暂时读失败时，同一轮到期退出继续持有执行权，
等待完整订单／成交／账户核对恢复，不能返回普通循环后丢失退出任务。只有全部旧单均
已终态并且账户证明完整，才释放已排空的网络 batch，让正常 PAUSED→PROBE 规则恢复，
再准备本轮下一笔 FAK；不会重置网络熔断或 TP 期限。对冲后的确认读取暂时失败也继续
核对同一订单，不重复买入。到市场提交截止仍有不可接受原仓或无法核实状态，记录
`CA_EXPIRY_EXIT_FAILED`，禁止把该轮计为成功退出。
不会为了消除补量产生的反向仓位，再次反向对冲而形成来回补量。
完全覆盖且无多余仓位记 `PAIRED`；有额外反向仓位记 `HEDGED_WITH_EXCESS`，同时记录
`original_unhedged_shares`、`hedged_shares`、`extra_reverse_shares` 和
`extra_reverse_cost_usdc`（含按比例分配的退出费）。`safe=true` 不表示净仓位为零。
已完整核对的净残差 ≤0.01 份属于可接受尾差，记录 `WITHIN_RESIDUAL_TOLERANCE` 和
真实残差，不为 0.005737 份之类尾差补买最低 1 美元，也不把它当作退出失败。
订单 UNKNOWN、账实不符、资金不足或到截止仍有原仓时，保留全部不确定风险、记录
`CA_EXPIRY_EXIT_FAILED` 并阻止新订单，不再把未覆盖的原仓当作正常退出。

`config/config.toml` 的开仓方向现为 `ca_entry_direction = "signal"`：
只买出现有效 CA 的同侧，开仓执行继续为 Taker；`taker_exit_on_expiry = true`。

# M4 实盘 — 2026-11-15

本版本在2026-11-14实盘代码快照上收录M4执行、巡检与含费盈亏统计的更新。
发布分支：`codex/m4-live-release-20261115`。

## 执行规则

- 保留信号生成质量检查，取消锚定价过滤。每次真实POST前检查签名限价：`0.50 <= price < 0.95`，包括首单、重试、补单及10秒后追价。上下限在`config/m4-live/execution.toml`配置。
- 前10秒保持原锚定价加当时1tick；满10秒后，每次按新鲜同token Best ask定价。首次新鲜Best ask越出POST价带即持久放弃该信号；价格回来、重启或待确认匹配失败释放资金都不能恢复该信号的新买入。
- 价格在价带内，其他原因继续依原规则重试至T240；UNKNOWN/ACK先核对，SETTLING保留预算并继续核对，只有最终CONFIRMED计成交。
- BUY-only FAK，保留本金USDC分精度、份数0.1步长、原最小数量规则，并满足交易所BUY本金至少1USDC。已确认且不足1USDC的余款记DUST。
- 保留5USDC信号计划预算、10USDC单场风险上限、方向名额、官方tick、网络、资金、停机及T240保护。实际价格改善的增量份数及费用完整记账。
- 历史场恢复原settings与指纹。同场重启保留旧规则，下一新场采用新配置。

## 加载证据

运行时须核对PID、启动时间和以下字段；源码哈希本身不能证明加载：

```text
post_price_band_policy=POST_SIGNED_LIMIT_BAND_V1
post_minimum_price=0.50
post_maximum_price=0.95
signal_anchor_price_filter=false
post_price_band_retry_policy=ABANDON_ON_BEST_ASK_BAND_REJECTION_V1
fak_buy_minimum_policy=EXCHANGE_PRINCIPAL_GE_1_USDC_V1
```

`taker_plan`保存相同价带及重试策略，`taker_price_band_abandonment`保存不可变的原始信号计时和新鲜报价终止证据。

## 巡检与盈亏

实盘巡检改为北京时间每小时第20秒触发，仍逐个五分钟市场核对信号、真实POST、UNSENT和最终确认成交。
`m4_taker_coverage.py`逐信号核对过去12小时覆盖；`ops/m4_taker_pnl.py`读取本执行器query-only SQLite，仅对已有CONFIRMED成交的精确市场读取公开官方最终结果，计算实际本金及费用后的累计盈亏与最大回撤。

## 验证范围

本次GitHub推送按用户要求未重新运行测试。推送前已有记录：M4完整离线回归887项通过，506个历史账本在只读源的隔离备份上恢复通过，settings及指纹保持一致。
实盘已核验新运行标识；15:55场首个新规则信号在信号后10.441秒因新鲜Best ask0.14越界而持久终止，零准备、零POST。

仅发布代码、配置、静态测试和运维规则。运行账本、订单流水、凭据、停机标记及生成的研究产物保留本机；推送操作不变更实盘服务。

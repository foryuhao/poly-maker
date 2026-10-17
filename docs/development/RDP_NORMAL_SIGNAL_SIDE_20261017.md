# NORMAL 仅信号侧挂单（2026-10-17）

按用户要求，将当前 `obi_v1` NORMAL 从同时提交 YES/NO 两侧买单改为只提交信号侧的一张买单。

- `VALID_YES`：只买 YES，挂 YES BestBid。
- `VALID_NO`：只买 NO，挂 NO BestBid。
- NONE / INVALID：不提交 NORMAL 订单。
- 最终检查不允许方向或报价改变后继续发送旧候选；活动订单遇到新鲜有效的反向信号立即撤单，
  同向或 NONE 保留原单，不自动翻向、改价或加挂另一侧。具体规则见 [挂单存续更新](RDP_NORMAL_ACTIVE_ORDER_20261017.md)。
- 候选仍可预签 YES、NO 两个互斥方向，实际激活仅发送匹配信号的一个候选，且每个候选只有一单。
- 提交数量沿用该侧已有的基础数量、库存 Skew、Favorite/TargetDelta 和硬上限；未提交侧计划数量为零。
  双侧配对的 TargetDelta 二次调整不适用于单侧订单，避免访问或重建不存在的另一侧。
- 只对实际提交侧检查价格、Tick、最小股数、1 USDC 最小金额和资金承诺。
  另一侧低价不足最小金额，不再否决合法的信号侧订单；信号侧本身非法仍跳过，不切换到另一侧。
- OBI 计算仍要求有效的双侧盘口，下单前 100 ms 盘口/信号年龄和新网络门控继续生效。
  NORMAL 已发单后，仅盘口超过 100 ms 不再触发撤单；原信号起算 2 秒截止时间不变。
- 每连续方向区间一次 POST、原信号起算 2 秒期限、T120 NORMAL 截止继续生效。
- 确认成交后，按现有库存差额决定下一阶段；达到 REBALANCE 条件时，继续买入缺口侧，使用原有 POST 起算 3 秒期限。
  这意味着之后的 REBALANCE 仍可能买入最初信号的另一侧。
- Maker-only / post-only、未知订单核实、关闭屏障、资金限制和 Merge 沿用现有流程。
- 无信号参数的旧 `legacy` 报价调用保留其双侧语义；两份当前运行 TOML 均已选择 `obi_v1`，下次启动直接采用本规则。

## 日志

Session 增加 `normal_order_policy=SIGNAL_SIDE_ONLY`，NORMAL sizing 记录 `order_policy` 与 `normal_signal_side`。
最终执行记录中，单侧订单的 `pair_price_sum` 为 null，避免误认为存在完整配对价差。
如果存在有效信号但没有该方向的合法候选，记录 `NO_LEGAL_SIGNAL_CANDIDATE`。
终端原有单订单展示会显示实际提交的 YES 或 NO；历史日志不作改写。

## 验证

离线覆盖 YES / NO 两个方向的真实 SDK 签名及 POST 正文仅含一个正确 token，原始期限、同方向一次与拒单不重试；
验证未挂单侧为零、对侧金额不足不阻断合法订单，以及非零 Favorite/TargetDelta 配置不会重建另一侧。
两个方向确认成交后，分别验证进入缺口侧 REBALANCE，且 T120 之后仍按原 3 秒期限修复。

相关回归：264 passed（35.94 秒）。使用 MockTransport 和测试钱包，未启动或重启实盘。

Dust/行情边界回归：28 passed（0.33 秒）；小于 0.1 与恰好 0.1 股分别验证两个信号方向。
将 `caad7ad1e9f74afeba5b07240a53267e:w1` 的最终盘口快照输入新报价函数：
`VALID_YES → YES 0.44 × 5`，`planned_no_size=0`。这是离线报价重算，不代表重新下单或全场收益回测。

最终全量回归：`PYTHONPATH=src:tests .venv/bin/pytest -q --disable-warnings --maxfail=5`，
1778 passed、2 skipped（155.96 秒）。修改涉及的 Python 文件通过 Ruff，`git diff --check` 通过。

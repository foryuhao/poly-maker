# RDP-RISK-002 Risk Shadow Mode v2

基线：[当前冻结基线](../RDP_SM_CURRENT_BASELINE.md)。Shadow 在独立进程运行。
本次单命令集成仅改 CLI 与市场入口的可选启动组合；真实策略、执行、库存账本和 Merge 保持原样。
Shadow 没有交易 Gateway 对象、签名、账户配置或 Merge/RPC 初始化。
默认不运行；`--enable` 是显式开关。每次命令观察一个指定 BTC5m 市场，不新增交易调度。

## 使用

现在支持单命令开启真实 RDP-SM 与只读 Shadow：

```bash
uv run polymaker rdp-sm-run --shadow
# 原有配置参数保持可用：
uv run polymaker rdp-sm-run --config-dir livecfg --shadow
```

`--shadow` 默认关闭。开启后自动绑定本次发现的 slug、condition/token IDs、真实 Session ID
及当前配置的执行 Journal，不需要手动复制这些参数。交易还是原有真实交易模式。
CLI 不带该参数时保持原调用路径。启动提示会显示 Session ID、子进程 PID 和启动日志路径：
`research_outputs/rdp_risk_shadow/launch_logs/<session_id>.log`。
实际数据仍写入下方按市场划分的研究目录，每条记录的 session_id 对应本次真实执行。

Shadow 在独立子进程内观察官方 WS；子进程不继承密钥、API Secret 或账户配置环境。
只读 Journal 每20ms轮询，启动时从头过滤本次 Session，能够等待 M1 创建 Journal。
子进程复核父进程的 condition/YES/NO token 身份，冲突时记录 SHADOW_ERROR 并停止 Shadow。
市场已开始才启动 CLI 时，早期未观察槽位会明确记录 MISSED；不会虚构 T0 覆盖。
实际交易提前退出不会终止 Observer，它独立运行至同一市场 T300；无需延长真实执行 Session。

每秒库存仍直接复制已安全关闭窗口的权威结果，未知订单/成交期间保守标记 UNKNOWN。
REBALANCE 决策紧接上一窗口结束时，允许使用 Journal 中在该决策之前已经确认的最新窗口库存，
关联此前最多1秒的已观察行情槽位。合并风险保存为独立 DECISION 风险记录，原槽位不回填。
DECISION 行区分 decision_timestamp、observed_at、inventory_source_timestamp 及市场输入时间；
不能把其数量与原槽位的 UNKNOWN 库存混为同一记录。Tick 更新若在决策前已收到则使用新 Tick；
若只能看到决策之后的 Tick，保守返回 UNKNOWN，不用未来 Tick 修正历史候选。

这一自动启动组合已经离线验证；本次未运行带真实订单的 CLI 验证。
详细结果见 [单命令集成报告](SINGLE_COMMAND_RESULT.md)。

默认关闭（无网络、无文件写入）：

```bash
.venv/bin/python -m polymaker.risk_shadow
```

纯行情观察，建议在市场真实开始前启动：

```bash
.venv/bin/python -m polymaker.risk_shadow --enable \
  --slug btc-updown-5m-<官方市场开始epoch> \
  --output-root research_outputs/rdp_risk_shadow
```

使用现有 GammaClient 公共 `/markets` 获取元数据，核验 `eventStartTime`、`endDate`、
slug、condition/token/outcome/tick。`startDate` 是挂牌时间，不能当作 T0。
使用现有 MarketDataService 官方 market WS，独立实例复用已有解析器和盘口缓存。
不读取 `.env` 或账户认证配置，不改变执行引擎的 HTTP 行情来源。
不存在实际库存时全部实际库存/实际库存风险为 UNKNOWN；session_id=N/A。
研究情景单独存储，标记 HYPOTHETICAL，固定 Delta=+5/-5。

已另外获得交易授权的 Session 可以在另一终端只读附加：

```bash
.venv/bin/python -m polymaker.risk_shadow --enable \
  --slug btc-updown-5m-<官方市场开始epoch> \
  --journal /绝对路径/现有账户执行Journal.jsonl \
  --session-id <实际Session-ID>
```

该命令不启动交易，交易命令/授权保持原有流程。Observer 的生命周期仅由指定市场 T300
决定，交易 STOP、窗口关闭或暂停不会结束 Observer，也不会重新启动交易。
Journal 只以 rb 打开。连接时从当前文件结尾开始，不把之前的记录回填为本轮实时知识。
每20ms有界读取最多 256 KiB，部分行缓冲最多 4 MiB。

实际库存直接复制 `sm_window_result` 的已确认库存（已扣验证 Merge），仅安全关闭时
视为 VALID；`sm_run_result` 仅 COMPLETED 且安全关闭时复制最终权威库存。
下单意图、成交状态及 Merge 事件使当前库存 UNKNOWN，直到新的权威执行快照。
不从成交自行重建库存账本；确认成交事件可以关联订单，但不直接更新库存。

`sm_strategy_window` 的真实 REBALANCE 及 `sm_quote_refreshed` 的实际修订报价均记录。
每个修订有独立 decision_id，window_id 关联原执行窗口。实际已提交/ACK/确认成交以
追加记录关联 decision_id/order_id，不能将 Shadow 候选写成实际成交。
仅使用决策前已保存、至多早 1 秒的市场/风险槽位，标记 LAST_PRIOR_SLOT_MAX_1S。
若没有该证据，或已知库存与实际决策 Delta 不一致，则对照为 UNKNOWN。
原始决策时间、Journal 时间和 Observer 接收时间分别保留。
因此这是保守的只读 Journal 关联，不宣称拥有成交确认在引擎内部生效的精确时间。
本轮实际实盘附加尚未验证；不得为获得对照数据自行下单。

## 文件与可复现性

输出目录：`research_outputs/rdp_risk_shadow/<market_slug>/<observer_run_uuid>/`。
各市场使用全新模型及文件。相同市场可以有不同 Observer run，互不覆盖。

- market_events.jsonl：真实接收 BOOK/DELTA/TICK/CONNECTION；官方时间、接收 wall/monotonic、
  处理可见时间、事件 fingerprint、连接 generation；BOOK 保留盘口档位，DELTA 保留更新。
- market_snapshots.jsonl：300 个基础槽位；迟到超过槽位范围的记录为 SLOT_MISSED，价格 UNKNOWN。
- risk_snapshots.jsonl：实际库存和固定算法风险；没有实际库存时 UNKNOWN。
- hypothetical_risk.jsonl：仅 OBSERVATION_ONLY 的固定 ±5 研究情景。
- execution_events.jsonl：附加模式的显式白名单执行事件，不存原始 API 响应、签名或认证字段。
- decision_comparison.jsonl：真实 REBALANCE 的意图对照及实际订单生命周期追加记录。
- shadow_errors.jsonl：SHADOW_ERROR；写盘失败同时记录 stderr 和内存错误计数。
- market_summary.json：槽位覆盖、检查点、有效性、丢弃/错误、研究决策计数及输出路径。

写盘队列容量 4096，put_nowait 永不等待；单独线程序列化/写盘。每条记录有
observer_sequence 和累计丢弃计数；序号缺口可审计。关闭最多等待 5 秒并报告是否排空。
WS 自带有界队列容量 2048，Observer 消费与交易进程完全分离。

无丢弃/错误的纯观察数据可离线重放全部 300 槽位：

```bash
.venv/bin/python -m polymaker.risk_shadow_replay \
  research_outputs/rdp_risk_shadow/<market_slug>/<observer_run_uuid>
```

重放不访问网络，按 observer_sequence 重建已可见行情，比较市场、风险及假设风险字段。
附加模式未实现第二套库存账本，故此重放命令只接受纯行情观察数据。

## 验证

```bash
.venv/bin/python tests/rdp_sm_m0_offline.py tests/test_risk_shadow.py -q
.venv/bin/python tests/rdp_sm_m0_offline.py tests/test_rdp_sm*.py \
  tests/test_filltruth.py tests/test_gateway_positions_v2.py tests/test_settlement_sweep.py \
  tests/test_execution.py tests/test_state.py tests/test_userstream_parse.py \
  tests/test_favorite_live.py tests/test_marketdata.py tests/test_orderbook.py \
  tests/test_research_causal_capture.py tests/test_risk_shadow.py -q
```

测试使用既有 pytest、隔离网络/DNS/凭证的 offline runner；完整结果见 RESULT.md。
没有校准 Risk→Tick 转换，没有交易参数优化，没有反事实成交/PnL 推断。

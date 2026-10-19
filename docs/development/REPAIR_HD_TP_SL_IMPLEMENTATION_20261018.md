# HD / TP / SL 开发版实现状态

历史记录：本页为2026-10-18首阶段实现。2026-10-19用户已确认数值参数，完整规则已继续落地；最新状态以[2026-10-19实现记录](REPAIR_HD_TP_SL_IMPLEMENTATION_20261019.md)为准。

日期：2026-10-18。策略版本：`hd_tp_sl_v1`。已进入代码实现；**当前不具备完整实盘运行条件**，没有启动实盘或切换现有运行配置。

## 已实现

- 独立纯策略 `repair_policy.py`：默认HD，单向HD->TP->SL或HD->SL；TP/SL不回退。实际配平可直接完成，不要求经历SL。
- 每秒收益判据采用episode浮动PnL/Q0与从第一笔原仓成交起的持有时间。收益率参数默认未知，不使用演示数字填生产值。
- TP首次有效挂出后固定价等待；`N=max(1,ceil(sigma_1s/tick))`，使用市场真实tick。新TP缺少sigma或目标价格不合法时报告阻塞，不截断价格或凑投机数量。
- SL新挂最新ask-tick；同一SL内落后至少1 tick时追价，同价或更积极时保留队列，不向下改价。
- 进入SL时强制撤销旧状态订单；关闭屏障解决实际成交/在途后才重新计算数量并重挂。不因新单不可行保留旧TP；无法重挂则保持SL并报告阻塞。
- T120 NORMAL截止沿用现有守卫；新控制器独立实现T180强制SL，不等待新行情或sigma暖机；T240停止新POST并撤余单，Merge沿用已有关闭屏障。
- Buy Only、post-only GTC、session15 USDC、真实Delta限额5股、0.1股配平容差、最低量/金额、部分成交、合法反向尾仓与实际仓位容错继续生效。真实超限时可接受资金允许的部分限额恢复，不因不能一次配平而放弃可行恢复。
- 行情底座新增逐次有效BBO变化历史，不再只依赖每秒sigma桶；每个历史点都经过因果判定，短暂逆向变化即使在复核前恢复也不会被抹去。
- episode保存模式、TP价格、峰值、因果窗口及参数快照，并写入本session journal；同一episode重建控制器可恢复锁存状态。首次成交没有可信原生时间时使用明确标记的LOCAL_FIRST_TRADE_OBSERVATION，不伪称准确撮合时间。
- 最终POST守卫检查模式、时间、当前Maker/tick/数量/Delta和执行事实，不再要求双侧BBO指纹完全不变。新控制器不做旧Q求解，也不在准备后额外等下一条行情；一致的SDK tick缓存不再每次强制清空。
- 新日志记录模式、触发依据、候选价量、下一复核时点、KEEP/CANCEL/撤后重挂及阻塞原因；人类日志不依赖旧Q字段。

## 代码与配置

|文件|职责|
|---|---|
|`src/polymaker/repair_policy.py`|纯路径策略、状态快照/恢复、TP/SL报价及配置预检|
|`src/polymaker/rdp_sm_path_repair.py`|经济账/行情适配、逐事件路径、实际订单复核与约束|
|`src/polymaker/rdp_sm_lifecycle.py`|复用签名/WAL/POST/撤单/关闭屏障，独立时间唤醒|
|`src/polymaker/rdp_sm_execution.py`|第一笔成交时间来源、原始成本及episode订单归属|
|`src/polymaker/rdp_sm_marketdata.py`|带代际和时间戳的秒内BBO变化历史|
|`src/polymaker/config.py`|显式策略选择及可空研究参数|
|`src/polymaker/rdp_sm_entry.py`、`rdp_sm_loop.py`|新控制器组合；参数/验证未满足时在实盘开仓前阻止启动|
|`src/polymaker/cli.py`、`rdp_sm_logging.py`|只读配置检查与分模式日志|

`rdp_sm.repair.policy`默认仍为`ev_legacy`，当前config/config.toml与livecfg/config.toml均未修改。选择`hd_tp_sl_v1`才进入新控制器；不把旧命令继续运行旧模型解释为新版已上线。

只读检查：

```sh
uv run polymaker rdp-sm-repair-check
```

该命令只读取本地配置，输出参数、声明的校准状态、所选策略和未满足原因，不调用交易所，不输出凭据。返回2表示配置/策略尚不满足检查条件。当前实际检查为`policy_selected=false`、`parameters_complete=false`、`ready_for_live=false`。

新配置路径为`rdp_sm.repair.path`，参数为`tp_profit_rate`、`shock_window_sec`、`shock_z`、`drawdown_per_share`。四个数值默认均为None，状态为UNCALIBRATED。旧EV数值配置保留历史兼容，不参与新路径策略。

## 明确未完成的实盘条件

1. **参数及策略校准。** r_TP未给出历史回放优选值。开发版路径SL采用“短窗不利变化/事前sigma超过门槛，或价格贡献的每原始股累计回吐超过门槛”的可配置候选结构；观察窗口和阈值都未校准。没有设置0.03/share、30%回吐等生产默认。
2. **完整闭环与执行实证。** 当前测试使用明确注入的参数和虚拟交易所；还需新政策完整session回放、长挂TP成交与撤换延迟敏感性、样本外收益检验。旧日志缺少准确原生撮合时间、部分最终赢家和新长挂政策覆盖，不能凭价格触及制造反事实成交。
3. **恢复及数据质量的运行验证。** 已提供本episode快照恢复；现有进程级fence仍执行原有撤单/保留未决证据，不自动接管旧session继续发REPAIR。不能声称已实现进程崩溃后的无缝续跑。局部观察时间代理下的分段价格贡献、时钟修正、重连和实际日志负载也需运行验证。

`repair_readiness`的scope明确为参数与声明回放验证的配置预检；它不是对真实成交、全链路恢复或盈利能力的认证。REPLAY_VALIDATED和validation_reference需要真实研究结果支持，不能手填标记当作通过实盘验收。目前缺参数或验证状态时，生产入口与生产loop均返回REPAIR_NOT_READY_FOR_LIVE，NORMAL也不会先开仓。

## 验证记录

- 新纯模型及真实SDK/网关/日志/对账链路测试使用虚拟HTTP/时钟；没有真实下单。
- 全量回归首轮：1958 passed、2 skipped，216.54秒。
- 执行/截止/Merge定向回归：101 passed，18.78秒。
- 最终改动后的纯策略、新控制器、完整离线loop选择、CLI/人类日志及配置兼容复验：66 passed，15.47秒。
- 新文件Ruff检查通过；git diff --check通过。

主要覆盖：浮盈逆向冲击、事前sigma、慢跌累计回吐、不使用沉没成本触发SL、交易PnL与价格变化分离、TP收益严格大于门槛、TP固定价、真实可变tick、不可行价格、重连代际、部分成交撤重挂、新单最低量、反向尾仓不往返、T180无行情/无sigma、新策略实盘启动阻止及只读诊断。

最终定向复验命令：

```sh
PYTHONPATH=tests .venv/bin/python -m pytest -q \
  tests/test_repair_policy.py tests/test_rdp_sm_path_repair.py \
  tests/test_rdp_sm_cli_logging.py tests/test_config_aliases.py
```

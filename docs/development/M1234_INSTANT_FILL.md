# M234 发单即成交模式

2026-11-08 实现。入口：`ops/m1234_sim_instant_fill.py`；持续服务启动器：
`ops/m1234_sim_instant_fill_start.py`。

该模式验证新框架**通过准入并发单的信号**：在原 SUBMIT 时刻，以原挂单价、原数量
立即假设全量成交，持有至官方结算。独立记录 `ASSUMED_FILL`。`trades-only` 和
`cancel-half` 继续记录 `MODELLED_FILL`，用于验证执行条件和排队结果。

## 数据与记账口径

- 只覆盖 M2、M3、M4。候选信号不等于发单：未通过准入的候选不会自动变成假设成交。
- 固定沿用 `trades-only` 的 SUBMIT 集合，只取一个情景，避免重复计算两个排队世界。
  不改变策略门槛、发单价格、数量、方向限次或准入时窗；不复刻旧 study 的准入逻辑。
- 模型成交时刻等于原 SUBMIT 时刻，等待时间为零。撤单、超时、排队量和实际成交量
  不限制这条假设账本。它是执行假设下的信号基准，不是可实现成交或盈利的证明。
- 每个市场、每个策略独立记账，初始资金沿用源 ACCOUNT_OPEN；提交时立即扣除
  `价格 × 数量`，无挂单预留。现金始终等于初始资金减累计成本加已结算兑付。
  结算后 inventory 保留累计买入份额作为历史证据，标为 `SETTLED_HISTORICAL`，
  不可再次计入剩余资产。
- 只接受与源市场 slug、condition ID、token IDs 一致的官方结算；未确定结果保持待结算。
  盈利、胜率和 ROI 只统计已结算订单，均为未计手续费、返佣的毛值。
- 新账本不将假设成交反馈给源策略。当前 M234 在 SUBMIT 时锁定每市场每方向的准入次数，
  不因该模式产生更激进的后续发单。未来若引入成交驱动的再入场、跨市场资金或 CARA 决策，
  需要另外建立独立策略状态，不能直接把这份固定发单集合当作其仿真。
- 信号按市场、策略、候选 ID 去重；有正数 MODELLED_FILL 的源订单才计入排队成交数。
  同一订单多次部分成交只计一单。源订单未决状态在执行对照中单独保留。

## 持续运行

在工程根目录执行：

```bash
PYTHONPATH=src .venv/bin/python ops/m1234_sim_instant_fill_start.py
```

默认读取 `research_outputs/m1234_sim/monitor/control.json` 中的当前模拟器身份，
创建独立的连续 systemd 用户服务，每 15 秒读取一次已提交的源审计。
首次启动会补齐本轮此前发单；重启从各市场审计水位继续，不重复成交或结算。
模型成交没有延迟，显示延迟包含源审计提交等待和最多一个读取间隔。

输出默认放在 `research_outputs/m1234_sim/signal_baseline/<源运行目录名>/`：

| 文件 | 用途 |
|---|---|
| `plan.json` | 模型版本、实现及结算校验器哈希、源运行身份、发单集合口径 |
| `ledger.sqlite3` | 独立假设成交与官方结算审计、每市场处理水位 |
| `latest.json` | 信号、发单、假设成交、待结算、收益与排队执行对照 |
| `report.md` | 便于阅读的实时汇总，含北京时间 |
| `launch/launch.json` | 连续服务名称、启动命令与启动结果 |
| `launch/m1234_sim_instant_fill.py` | 本轮冻结的模式代码 |

启动器将服务和目录写入控制文件的 `signal_validation` 字段，保留原有字段。
它**不会开启已关闭的聊天巡检，不创建定时器，也不重启源模拟器**。
源数据以只读 SQLite 事务读取；新服务仅允许 AF_UNIX 套接字，不连接交易端点。
原运行的源代码哈希不受 `ops/` 增量模式影响。

自定义源运行时同时指定服务与目录：

```bash
PYTHONPATH=src .venv/bin/python ops/m1234_sim_instant_fill_start.py \
  --source-run /absolute/path/to/simulation-run \
  --source-unit polymaker-m1234-sim-RUN.service \
  --output /absolute/path/to/separate-baseline
```

历史运行的一次性计算：

```bash
PYTHONPATH=src .venv/bin/python ops/m1234_sim_instant_fill.py \
  --source-run /absolute/path/to/simulation-run \
  --output /absolute/path/to/separate-baseline
```

输出不能放在源运行目录中。已有输出绑定模型实现和源运行身份；更新模型须使用新输出。
一个输出只允许一个写入者。连续服务在源运行完成后退出；源异常退出时保存 PARTIAL 状态，
不将中断拼接为连续 24 小时。停止该模式可按 `launch.json` 中的 unit 执行
`systemctl --user stop <unit>`，不影响原模拟器。

## 完整性与验证

缺库、读取错误、身份不一致、源无效市场或源运行失败均显式报告；无效市场从统计排除，
保留已有原始和派生成交证据。PARTIAL/UNAVAILABLE 状态的数字不能当作完整结果。
每市场的成交、结算和审计水位在同一事务落盘，避免重启重复记账。

```bash
PYTHONPATH=src:tests:. .venv/bin/python -m pytest -q \
  tests/test_m1234_sim_instant_fill.py \
  tests/test_m1234_sim_metrics.py tests/test_m1234_sim_watch.py
```

覆盖立即全量假设成交、候选去重、只读已提交事务、重启和部分成交去重、跨市场资金与
订单 ID 隔离、官方结算与待结算、错误 token/市场/运行身份、预算守恒、无效数据排除。

# M234 主执行版本

用户确认的主规则：有效信号出现后，按信号锚价与发单时 best bid 的较低值挂买单；每笔预算 5 USDC；每市场、每策略、每方向最多一次提交。挂出后不改价、不重挂、不因信号结束、ASK 恢复或买一变化撤单。T240 撤剩余量，已成交份额持有到官方结算。

## 代码与边界

- `ops/m234_sim_main.py`：主策略及共享行情 Engine 扩展。原始 M234Policy 和两个 M1 执行版本继续作为基线，两个新增 broker 为 `main/trades-only`、`main/cancel-half`。
- `ops/m234_sim_main_replay.py`：使用原实验的固定市场清单、原始行情、冻结源码及水位回放；检查旧 broker 的完整快照。
- `ops/m234_sim_main_validate.py`：独立核对原始候选事件、预算、定价、T240 撤单、审计成交及收益，导出逐方向配对表。
- `ops/m234_sim_main_forward.py`、`ops/m234_sim_main_start.py`：冻结源码与扩展，先检查回放证明，再切换公共行情模拟服务。只替换当前 M1234 公共模拟，不触及旧 M1 实盘、M2 study 或 dashboard。
- `ops/m234_sim_main_metrics.py`：分别汇总主版本和基线，避免重复计算订单。M1 两个执行版本共享显示于两种视图，不能相加。
- `ops/m234_sim_main_instant_fill.py`：在主版本 `main/trades-only` 的已提交审计上做发单即成交对照；这是已发单群体，并非所有候选信号，也不反向影响源策略。

发单前仍需要原始有效候选、可用观察、合法且新鲜的本地盘口、交易所价格/数量精度及资金。取消旧版“锚价必须仍有可见买量”的入场条件：新报价允许在当前历史盘口不存在的价位排队。数量按选定限价重新计算，受精度影响实际名义金额不超过 5 USDC。拒单也消耗该市场/策略/方向的一次提交机会。

T240 使用原有官方时钟上界判定；当时钟误差上界覆盖 T240 时发出撤单请求，保留原 200ms 生效延迟，途中仍可部分成交。行情缺失使撮合结果无法确认时仍记录未决风险；这不是额外策略撤单，也不能当作确定未成交。原有真实公开成交量消耗队列、部分成交、post-only 到达检查、成交归因及结算经济规则不变。

## 回放与统计

```bash
PYTHONPATH=src:ops .venv/bin/python ops/m234_sim_main_replay.py \
  --source-run research_outputs/m1234_sim/forward/24h-20261108T154229-66fde6 \
  --cohort research_outputs/analysis/m234_lifetime_20261108_v3 \
  --output research_outputs/analysis/m234_main_20261108_v1 --workers 2

PYTHONPATH=src:ops .venv/bin/python ops/m234_sim_main_validate.py \
  --output research_outputs/analysis/m234_main_20261108_v1
```

固定样本为北京时间 2026-11-08 15:45–20:35 的 58 个有效、已官方结算市场，其中 42 个完整窗口、16 个未完成 T15 前预热。保留原始文件 SHA256、水位和快照；未拼接多次中断来声称连续运行通过。

主版本更改入场定价，发单群体也可能改变，因此不要求主版本与基线的发单价格、数量及候选完全相同。要求原始检测器的候选输出相同、原基线全部 broker 快照相同；逐笔配对键为市场/策略/情景/方向。

确认成交率为有正数 MODELLED_FILL 的订单数除以提交订单数。部分成交只计一单。未决订单单列，可能与已确认部分成交重叠。已知毛 PnL 为确认成交数量乘以官方结算单价与买价之差；未验证手续费和返佣不计。未决剩余量的可能盈亏范围不是统计置信区间，不把已知部分收益当成确定的策略总收益。不同情景及版本账户独立，不相加成组合。

## 连续运行与日志

持续采集目标为 288 个五分钟市场，保留 87300 秒上限和现有队列大小。8 GiB 输出预算加 4 GiB 磁盘空闲保护；资源边界触发时如实记录，不声明完成 24 小时。

`ops/m234_sim_main_storage.py` 仅把重复 `SIGNAL/UNAVAILABLE` 诊断逐条无损写入 `unavailable-diagnostics.jsonl.gz`，包含输入序号、诊断序号和原 payload；已提交计数写入 SQLite metadata。其他审计和所有原始公共行情保留。标准报表合并压缩诊断计数；主版本报表身份包含执行策略版本。不会修改或删除旧运行文件。

新运行基于冻结原版本，仅更新报表的模式识别和诊断计数支持；交易引擎、检测器和撮合器的底层源码保持原版本。扩展模块及源码均在运行 `launch` 下存档并校验。后续回放新录制应使用该运行归档的 `m234_sim_main_forward.py --mode replay` 和同一 `--source-root`。

`monitor/control.json` 保留 `enabled:false`，不恢复用户已经停止的对话巡检，不创建定时器。切换运行使用新目录和新运行身份，旧基线目录与手动替换原因均保留。

## 本次完整回放结果（58 市场）

所有原始候选与旧基线完整快照一致，独立审计校验 PASS。以下按独立排队情景统计，部分成交计一个订单。

| 版本 | 情景 | 策略 | 信号 | 发单 | 已确认成交单 | 成交率 | 未决单 | 已知结算毛 PnL (USDC) |
|---|---|---|---:|---:|---:|---:|---:|---:|
| baseline | cancel-half | M2 | 68 | 43 | 0 | 0.00% | 1 | 0.0000 |
| baseline | cancel-half | M3 | 89 | 41 | 0 | 0.00% | 3 | 0.0000 |
| baseline | cancel-half | M4 | 55 | 27 | 0 | 0.00% | 3 | 0.0000 |
| baseline | trades-only | M2 | 68 | 43 | 0 | 0.00% | 1 | 0.0000 |
| baseline | trades-only | M3 | 89 | 41 | 0 | 0.00% | 3 | 0.0000 |
| baseline | trades-only | M4 | 55 | 27 | 0 | 0.00% | 3 | 0.0000 |
| main | cancel-half | M2 | 68 | 43 | 13 | 30.23% | 28 | -44.9242 |
| main | cancel-half | M3 | 89 | 41 | 9 | 21.95% | 25 | -5.9317 |
| main | cancel-half | M4 | 55 | 27 | 5 | 18.52% | 21 | -5.3663 |
| main | trades-only | M2 | 68 | 43 | 4 | 9.30% | 33 | -17.7334 |
| main | trades-only | M3 | 89 | 41 | 0 | 0.00% | 30 | 0.0000 |
| main | trades-only | M4 | 55 | 27 | 2 | 7.41% | 22 | -4.5011 |

本样本逐方向配对结果：主版本与基线发单候选和限价均相同，因此观察到的成交变化来自挂单保留逻辑。新版本仍会在未来盘口下按 min(anchor, best bid) 定价。

主版本未决订单的首个原因均为 `DIRECT_TRADE_SOURCE_TIME_UNCERTAIN`：公开成交的源时间不足以确定归属。不同情景的同一信号不能相加；未决可能与已确认部分成交重叠。更长挂单时间扩大了这种不确定性暴露。当前已知收益多数为负，不能用增加的确认成交数推断策略有效，也不能把未决剩余量收益当成零。

42 个完整窗口的子样本结果、各未决原因、逐方向配对和可能剩余盈亏边界均保留于下列证据；16 个非完整窗口没有冒充完整窗口。

- `research_outputs/analysis/m234_main_20261108_v1/comparison.json`
- `research_outputs/analysis/m234_main_20261108_v1/validation.json`
- `research_outputs/analysis/m234_main_20261108_v1/paired-orders.csv`
- `research_outputs/analysis/m234_main_20261108_v1/diagnostics.json`

定向测试：主策略及原撮合回归 92 项通过；存储/报表/指标相关 22 项通过（含重叠测试，不相加作独立总数）；发单即成交对照 15 项通过；冻结生产协调器完成 12 个离线窗口，真实订单 0，资源清理无错误。实际新录制格式回放得到一致的完整状态。详细记录位于 `research_outputs/analysis/m234_main_validation/checks.json`。

## 持续模拟部署

北京时间 2026-11-09 00:35:29 建立新运行，00:39:56 接入 00:40 首个市场。首窗完成 128009 帧，T15 前预热、观测通过 T300、有效性 VALID；随后已进入 00:45 市场。首窗主版本全部策略撤单均核对为 T240（含原有时钟误差上界及撤单延迟），未恢复提前撤单条件。

- 模拟服务：`polymaker-m1234-sim-main24h-20261109t001539-11c2cb.service`
- 新运行：`/home/ubuntu/polymaker/research_outputs/m1234_sim/runtime/dbeca9af1e4a0a34/research_outputs/m1234_sim/forward/main24h-20261109T001539-11c2cb`
- 发单即成交对照：`polymaker-m234-instant-fill-main-a02ce5230b30.service`，源群体为 `main/trades-only` SUBMIT。
- 启动验证：新运行 `launch/deployment-validation.json`。首窗结束时保留了 `WS_DISCONNECTED` 原始关闭事件，不将其抹去；市场完整性为 VALID。
- 旧运行以 `MANUAL_STOP` 退出，清理错误为空、无无效市场，替换原因另存于旧运行 `launch/main-policy-replacement.json`；原终态仍为 FAIL/MANUAL_STOP，不伪装 24 小时完成。
- 当前仅确认启动及首窗切换通过；24 小时连续采集仍在进行。`monitor/control.json` 仍为 `enabled:false`。

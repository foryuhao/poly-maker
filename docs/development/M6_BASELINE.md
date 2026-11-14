# M6 独立基线

2026-11-13 用户授权开发，未授权本轮启动。M4 基线历史任务已结束；M4 实盘不属于本次变更范围。

## 已确认合同

- 仅 M6「撤供留托」：当前有效 RF_BID 与 LW_ASK 重叠；使用此前 24 小时 v7 测试的冻结信号实现，不调阈值。
- 原信号锚定价 `0.02 <= p < 0.20`，配置为 `config/m6.toml`。该配置与 M4 的配置无继承、覆盖关系。
- 挂价沿用 M4 基线的原锚定价，不使用原研究执行器的 `min(anchor, best_bid)` 改价。
- 每笔模拟风险 5 USDC，每场总占款 10 USDC；每方向一个已 POST 独立机会；数量向下取 0.1 shares，满足平台 Maker 最小量。
- 新单窗口为 `[T15,T240)`；保留现有首次 POST 至少 400ms 的剩余信号预算。信号过期、行情质量、名额、预算等跳过原因必须记录。
- 新口径：POST 后至本场 T240（含），同 token 有效 Best ask 不高于原锚定价减 POST 时 1 tick，才按原 POST 价格和数量全量模拟成交。ACK 只是提交应答。触价盘口数量不限制本约定模型的模拟成交数量。
- 无有效触价证据时记录未成交、模拟撤单及诊断，完整且官方已结算场次收益为 0。行情缺口本身不排除完整已结算场次。
- 旧口径：同一批 POST 立即按原价原量全量模拟成交。仅正式结果确认后记入收益；不完整、缺失、未结算、待观察窗口单列。
- 收益从新基线首场的初始 0 独立累计，不拼接 M4 或旧 M6 研究收益；手续费、返佣均不计入此 Maker 基线模型。

此模型与旧 M6 研究的「直接 SELL 穿价、数量封顶、部分成交」不同，不能把曲线差异直接归因为信号变化。

## 隔离边界

入口为 `python -m polymaker.m6_baseline.run`，只有基线模式，无 `--live`。不加载真实钱包配置，不提供真实下单、账户成交或仓位查询。订单签名使用一次性虚拟密钥；最终 POST 为本地模拟应答。公共行情和官方结果只经公共 GET/公共 WS 读取。

所有新运行数据位于 `research_outputs/m6_virtual/`；锁、会话、账本、结算、统计起点都独立。停止标记仅为 `runtime/trading-control/m6-baseline-stop.json`，不修改或删除任何 M4、人工或余额保护标记。

为避免修改 M4 实盘正在依赖的模块，M6 在独立包内保留基线执行、记账、观察、结算的隔离副本；不改 M4 源文件，不做运行时 monkeypatch。中性行情传输和 SDK 检查组件以只读导入复用。虚拟 SDK 的 `m4_v2` 字符串是既有执行接口枚举，不能据此认定加载了 M4 信号。

`_signal_v7/` 为冻结信号资产，原文件逐字节保留，不格式化或修改。原 v7 无按策略选择接口，适配层只允许创建 M6 campaign；原始完整检测器与适配器的候选以 token、锚定价、确认/失效时刻、RF/LW 证据时间核对，排除进程本地事实 ID 差异。源路径与 SHA256 见 `research_outputs/analysis/m6_baseline_development/provenance.json`。后续更新冻结资产必须重新核对语义，不能静默跟随 research 目录变化。

## 入口与报告

离线检查（无网络、无账本创建）：

```bash
.venv/bin/python -B -m polymaker.m6_baseline.run --project-dir /home/ubuntu/polymaker --check
```

服务模板：`ops/polymaker-m6-baseline.service`。本次仅交付模板，不安装、不启用。后续获用户启动指令后，从下一完整市场开始采集；同场重启恢复原账本，保留方向名额和占款，不重置观察窗口。运行范围和配置指纹写入首场的 `statistics-scope.json`。

快照与出图（已有采集数据后运行）：

```bash
.venv/bin/python -B ops/m6_pnl_snapshot.py --project-dir /home/ubuntu/polymaker --output REPORT_DIRECTORY
.venv-m4-report/bin/python -B ops/m6_pnl_render.py --project-dir /home/ubuntu/polymaker --directory REPORT_DIRECTORY
```

输出 `summary.json`、`validation.json`、逐场覆盖 CSV 与 `m6-pnl-waterfall-dual-cumulative-drawdown.png`。蓝色触价曲线、橙色假设成交曲线；初始 0 计入回撤，最大回撤用峰谷菱形及实线箭头，灰色窗口断线。

## 验证与边界

定向测试：

```bash
PYTHONPATH=tests/m6:src .venv/bin/python -B -m pytest -q tests/m6/test_m6_baseline.py
```

历史原始输入一致性回放：

```bash
.venv/bin/python -B ops/m6_baseline_replay.py --market-directory SOURCE_MARKET_DIRECTORY --output EVIDENCE_DIRECTORY
```

证据位于 `research_outputs/analysis/m6_baseline_development/`。其中 `report-smoke/` 的收益与图片使用明确标记的合成数据，仅验证报表计算和显示，不能作为策略收益证据。

本次离线验证不等同于新 M6 基线连续运行 24 小时通过，不证明 WS/公共 API 今后的可用性。启动、运行期限和巡检计划留待用户安排；旧 M4 小时巡检不自动恢复。

## 小时巡检（2026-11-14 用户授权）

独立 `polymaker-m6-hourly-watch.timer` 每小时北京时间整点后20秒运行
`ops/m6_hourly_watch.py`，只读核对 M6 账本与日志、生成双口径图并排队到授权本聊天。
状态与控制位于 `research_outputs/m6_hourly_watch/`，`control.json` 记录目标会话。
巡检不修改交易参数、不自动启停任何交易服务，不访问 M4 数据或真实交易接口。
完整链路试验按既定条件自动停止后，巡检识别其 completion.json，不误判为故障。
停止巡检：禁用对应 timer，并将该目录 control.json 的 enabled 置 false。

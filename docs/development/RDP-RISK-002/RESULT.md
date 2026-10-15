# RDP-RISK-002 Risk Shadow Mode v2 — RESULT

日期：2026-10-14。完成冻结基线记录、独立只读 Observer、固定风险算法、只读执行对照、
离线测试及一个真实 BTC5m 市场观察。没有修改任何既有 src/tests 文件，没有 Git commit/reset、
没有真实订单、撤单、Merge/Redeem、签名、账户认证、交易参数优化或自动下一轮研究。

## 交付状态

```text
BASELINE_BRANCH: single-market-pilot
BASELINE_COMMIT: a728c4db3d01dab2fddb2b9ac162c9c5bf4bb3a1
BASELINE_APPROVED: YES
MERGE_EXISTING_BEHAVIOR_ACCEPTED: YES
MERGE_MODIFIED: NO
RPC_USED_BY_SHADOW: NO
RPC_COMPLIANCE_ASSERTED: NO
SHADOW_CODE_READY: YES
OBSERVATION_ONLY_READY: YES
EXECUTION_ATTACHED_READY: YES — OFFLINE_VERIFIED; LIVE_NOT_RUN
TRADING_BEHAVIOR_CHANGED: NO
SHADOW_TRADING_PERMISSION: NONE
SHADOW_DEFAULT_ENABLED: NO
EXTERNAL_INTERFACE_ADDED: NO — 仅复用既有官方 Gamma HTTP / market WS
FULL_TEST_RESULT: 1317 passed, 11 failed（首次全仓离线测试；下面说明原基线复现）
RELATED_REGRESSION_RESULT: 1002 passed, 0 failed
SHADOW_TEST_RESULT: 44 passed, 0 failed（最终源码）
MARKETS_OBSERVED: 1
MARKET_SNAPSHOT_COVERAGE: 300/300 槽位；YES VALID 241/300，NO VALID 241/300
RISK_SNAPSHOT_COVERAGE: 300/300 记录；假设库存文件 300/300
T240_VALID: YES — 双侧完整盘口及概率有效
T270_VALID: NO — 记录按时生成，官方盘口缺档，MISSING
VOLATILITY_VALID: 199
VOLATILITY_UNKNOWN: 101
SETTLEMENT_RISK_VALID: actual=0；hypothetical +5/-5 各241
SETTLEMENT_RISK_UNKNOWN: actual=300；hypothetical +5/-5 各59
REALIZED_VOL_COVERAGE: N/A — 无实际库存；实时 r_vol_realized=UNKNOWN，有效时长0
SHADOW_DECISION_COUNT: 0（真实观察）
ACTUAL_COMPARISON_COUNT: 0（真实观察；离线关联已测试）
HOLD_CANDIDATES: 0（无实际库存，不伪造真实 HOLD）
NEXT_VALIDATION_STEP: 人工审查本批数据；附加模式只在另行获得交易授权后观察，不自行下单
```

## 修改文件

全部是新增文件；当前 HEAD 未变化。

- docs/development/RDP_SM_CURRENT_BASELINE.md
- src/polymaker/risk_shadow_model.py
- src/polymaker/risk_shadow.py
- src/polymaker/risk_shadow_replay.py
- tests/test_risk_shadow.py
- docs/development/RDP-RISK-002/README.md
- docs/development/RDP-RISK-002/ALGORITHMS_AND_FIELDS.md
- docs/development/RDP-RISK-002/TEST_MATRIX.md
- 本 RESULT.md 与 evidence/ 中的测试输出、公开观察保护脚本、回执、示例及 SHA256 清单。

[运行命令与附加模式说明](README.md)、[字段字典与冻结公式](ALGORITHMS_AND_FIELDS.md)、
[验收测试映射](TEST_MATRIX.md)、[实际示例日志](evidence/example_logs.jsonl)。
旧 SOP、交易执行、Gateway、RiskJournal、Merge、库存权威账本及多市场调度均未修改。

## 测试结果及原有失败

相关回归：**1002 passed**，包含 RDP-SM、确认/执行/状态、仓位、结算、Favorite Live、
MarketDataService、盘口、因果捕获及 Shadow。最后新增严格 0.02 概率交叉核验边界测试后，
Shadow 单独测试为 **44 passed**；最后更改为 Decimal 精确阈值及流式重放均经 Shadow 测试和
真实数据重放验证。Ruff、compileall、git diff --check 通过。

更广全仓离线测试：**1317 passed, 11 failed**，不宣称全仓 PASS。
10 个失败为既有 CLI 日志混入 stdout 导致 JSONDecodeError：
CLI logging 6 项、entry 1 项、inventory wait 1 项、tick recovery 2 项。
使用 git archive 导出批准 commit 到独立临时目录，原始代码完整测试同样复现这 10 项：
**1280 passed, 10 failed**。没有 checkout/reset 当前工作树。

剩余 config_aliases 失败为既有 CLI 默认读取工作区 .env，被 offline runner 的凭证保护拒绝，
并非到达真实结算调用。在原始基线临时副本中添加不含凭据的空 .env，并使用同一保护运行该
测试组，复现完全相同的拒绝：**15 passed, 1 failed**。没有读取或复制真实凭证。
本轮仅记录这些已有问题，未修改原日志、配置或交易代码。

所有上述离线运行：**0 network/DNS attempts；workspace credentials denied**。
测试 baseline_01 将所有既有 src/tests 文件与批准 commit 逐字节 SHA256 核验相同。
完整输出保存在 evidence/：full-tests.txt、baseline-full-tests.txt、
baseline-credential-test.txt、related-tests-final.txt、shadow-tests-final.txt。

## 真实观察

市场：`btc-updown-5m-1791953700`。
Condition：`0xf9b17acf0f33daa687ec83eac9cdafd0fae9bbfce9670c8a3baf2e3f6f5988d7`。
实际市场时间：2026-10-14 04:55:00–05:00:00 UTC（上海 12:55–13:00）。
官方 eventStartTime/endDate 与 slug epoch 核对，未用挂牌 startDate 或 Observer 启动时间替代。
Observer 在市场前订阅，前市场真实更新可用于 T0 的30秒历史窗口。

研究输出目录：
`/home/ubuntu/poly-maker/research_outputs/rdp_risk_shadow/btc-updown-5m-1791953700/b7d7bcee3fa34644b0d683e7ea7d2243/`。

- 市场/实际风险/假设风险各 300 条，槽位 0–299 全部按时；最大采样延迟约 2.083ms。
- 完整市场观察时长 300 秒；全部八个检查点存在；T180/T210/T240 有效。
- YES/NO 完整有效盘口各 241 个槽位；6 个 STALE，53 个 MISSING。
- 波动率 199 VALID；42 INSUFFICIENT_DATA、6 STALE_PRICE、53 MISSING_PRICE。
- 实际库存 300 UNKNOWN，实际结算/库存风险不伪造；假设 +5/-5 结算风险各 241 有效。
- market_events 339881 条；WS received/processed 各171927，queue_full=0。
- shadow_dropped_event_count=0、writer_error_count=0、shadow_error_count=0，队列排空成功。
- 进程清空继承环境，禁止打开工作区 .env/.aws，禁止 execution Gateway、Merge、
  settlement、web3、eth_account 模块初始化；DNS 仅允许既有官方 Gamma/market WS 主机。
- 不访问账户，不提交真实订单，不调用 RPC。保护脚本及回执保存于 evidence/。

T240：YES bid=.96、ask=.97、Mid=.965；NO bid=.03、ask=.04，
本地 Age 约17.478ms，近同步 CrossCheckError=0。

T270：YES bid=.99、ask=UNKNOWN；NO bid=UNKNOWN、ask=.01。
虽然最新更新 Age 约12.269ms，但盘口缺档，因此 MISSING；p、波动率和假设风险 UNKNOWN。
这属于官方 WS 当前盘口不完整，不是停止采样或交易停止导致的观察生命周期中断。
未使用上一有效盘口或最终结果补齐。

离线流式重放上述原始行情事件，按 observer_sequence 恢复已知盘口与真实接收时间：
**300/300 槽位，市场/风险/假设风险字段 PASS，mismatches=[]，无网络**。
结果在 evidence/live-replay.json；文件和最终源码 SHA256 在 evidence/artifact_manifest.json。
研究原始文件保留在既有 Git 排除的研究目录，不改写为“更完整”的历史数据。

## KNOWN_LIMITATIONS

1. T270 盘口缺档，不能宣称该检查点风险有效。完整观察代表300个真实槽位及明确缺失状态，
   不代表任何时刻官方双边盘口都完整。没有新增 HTTP 补行情或第三方接口。
2. 执行附加只做离线验证，真实观察没有交易 Session；不能宣称真实成交库存/报价对照已实盘验收。
   读 Journal 每秒轮询，未知状态保守排除；只能用决策前至多1秒的已保存研究槽位。
3. 实际累计波动暴露在本次纯观察中 UNKNOWN，不具备实际库存覆盖。
   原始 realized gap 计数包含订阅预热和保守排除的已冻结桶迟到事件诊断，不能当作300秒内
   实际账户暴露或完整市场库存风险。没有从未来确认成交重建库存。
4. 原始事件文件约381MiB，独立 writer 有界且零丢弃；离线重放流式处理，避免一次加载全部事件。
5. 全仓11项既有测试/凭证隔离失败如上；没有顺手修复。相关交易回归通过、原源码字节不变。
6. 当前 Merge 后端既有 Blockchain RPC 事实已记录并人工接受其行为；本轮不声称其接口合规，
   Shadow 没有初始化/调用该路径。
7. TIME_POLICY_V0 为未校准实验对照；未提交候选不假定成交，HOLD 不是交易指令，未研究盈利能力。

本轮停止；等待人工审查真实日志，再决定后续验证或风险定价研究。

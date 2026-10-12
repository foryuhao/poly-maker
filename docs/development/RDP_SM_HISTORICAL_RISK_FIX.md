# RDP-SM Historical RiskJournal False Block Fix

日期：2026-10-12（Asia/Shanghai）。角色：Worker。项目：`/home/ubuntu/poly-maker`。

基线：当前工作分支 `fl-restored-97ab0f9`、HEAD `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`，包含既有 CLI Logging 与未跟踪 SM 文件。既有工作区修改全部保留。冻结边界：用户给定 RDP-SM v1.8；未用旧 Phase0 文档替代或修改 SOP。

参考：[Historical Risk Diagnosis](RDP_SM_HISTORICAL_RISK_DIAGNOSIS.md)。Live Trading：FORBIDDEN。

## 结果

```text
ROOT_CAUSE: CONFIRMED
FIX: CODE_READY
SAFE_ZERO_TRADE_SESSION: PASS
UNRESOLVED_RISK_FAIL_CLOSED: PASS
HISTORICAL_MATCHED_RESERVE: UNCHANGED
M2 SAFETY: PASS
TESTS: 605 PASS / 0 FAIL / 0 SKIP; 1 preserved deselection
FL REGRESSION: PASS
SHARED CORE CHANGES: NO
NEXT ACTION: Independent Targeted Review
```

`RiskJournal` 现在在同一账户文件锁内检查完整历史事件与状态，只接纳有严格证据的已安全关闭、零交易尝试 Session。非空文件本身不再是拒绝理由；无法证明无风险的历史仍以 `HISTORICAL_SM_RISK_REQUIRES_MANUAL_REVIEW` 拒绝。没有恢复旧 executor、订单或库存，也没有实现交易 Session 的自动重启恢复。

## 修复范围与判定规则

只修改 `src/polymaker/rdp_sm_execution.py` 中的 `RiskJournal` 和 `SMWindowExecutor.open` 的历史一致性检查，新增一个专项测试文件。

历史扫描在 exclusive `flock` 后、当前 Session 记录及任何下单之前执行。读取文件与加锁文件的 device/inode 必须一致；保留 append 模式、现有 flush/fsync 和账户级文件名。`sequence` 从已验证的历史行数继续计数，避免新 Session 的 Journal 引用从零重置。

当前支持的安全历史形态刻意收窄为连续、完整的三行事件链：

```text
sm_session -> sm_close_barrier -> sm_run_result
```

可以包含多个这样的 Session。必须同时满足：

- JSON UTF-8、行终止、对象结构和全部既有字段有效。重复 JSON key、NaN/Infinity、缺字段、未知字段/kind、空白行、截断、缺结尾、重排或跨 Session 包装 ID 矛盾均拒绝。
- Session ID 唯一，内外 Session ID 一致；账户 Funder 匹配文件账户，历史 Signer/signature type 一致，记录中的 authority 与既有类型语义一致；同一 slug/Market ID 的跨 Session 映射不能冲突。新启动也不能复用已有 Session ID。
- `preflight_observed_order_ids=[]`、`preflight_observed_trade_ids=[]`，pagination failure 为 null。
- 完全没有 placement intent、ACK、UNKNOWN、order、trade、strategy/window 或其他交易事件。即使 intent 表示 `post_started=false`、订单明确拒绝或后面出现 SAFE 标签，也保持阻断。存在下单尝试的 Session 不属于这个最小修复的恢复范围。
- 关闭事件为 `SAFE_TO_CONTINUE`，且 `proofs={}`、`confirmed_fills=[]`。最终结果也必须为 SAFE，所有窗口/成交/库存/Delta/pairs/残余/PnL 字段为零，窗口和 skipped quotes 为空，平均成交成本为空。不能只靠零提交计数或 SAFE 标签通过。
- Session、关闭和最终结果的时间顺序有效。仅接纳当前可直接证明的 `BOOK_SIDE_MISSING + BLOCKED` 或 `MARKET_CUTOFF + COMPLETED` 零交易结果；未知、风险性或矛盾停止状态拒绝。更多零交易事件形态（例如 quote-skipped）暂不放行，证据不足时保持 Fail-Closed。

旧 Session 的起始余额字段只用于历史记录一致性校验，不作为当前资金授权。沿用既有 float 预算减法口径，避免 `1.57 - 1.2` 的正常浮点尾数被误判；历史保留成本的合计和逐 Trade ID 对比仍使用 Decimal。

## 真实诊断 Session 验证

Session：`78f615c16be44892874bb33b30e44fd5`。

历史文件：`/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d.jsonl`，第 1–3 行，2244 bytes。

使用当前生产 `Journal` / `RiskJournal` 原始类的 AST 定义在临时副本中运行构造器，无 app/SDK 导入，无凭据读取，无网络，无订单客户端。实际输出：

```text
ACTUAL_SESSION_NONBLOCKING ['78f615c16be44892874bb33b30e44fd5']
sequence 3
reserve 10.150
TEMP_COPY_UNCHANGED True
ORIGINAL_UNCHANGED True
```

真实文件诊断前后 SHA-256 均为：

```text
7caf4e9530487523043485e50b92cd3491cbc4a13555051c4dbbc8b976956948
```

T01 另以 public dummy account/market 的等价完整记录验证生产入口：实际 M1/M2/M3 组合重开历史，新的 M2 snapshot 遇到外部 HTTP fixture 的缺 bid，结果仍是 `BOOK_SIDE_MISSING`、零提交、零成交、SAFE；新三行只追加到旧三行之后，再次 Journal 重开也通过。没有在真实账户上运行 CLI 或 executor。

## Historical MATCHED Reserve

真实历史行中的三个 Trade ID/成本保留为：

| Trade ID | Status | Retained Cost |
|---|---|---:|
| `cba74eeb-a251-4ac2-abfb-f9679f7b3521` | MATCHED | 3.250 USDC |
| `38287f2c-d6a8-4b1a-8d4d-a3d5d5495a00` | MATCHED | 3.50 USDC |
| `461cd979-8859-4b5f-9336-90fff7fc3564` | MATCHED | 3.400 USDC |
| 合计 | 不改写状态 | **10.15 USDC** |

Journal 检查逐 Session 验证外部历史成本与该 Session 的总预留一致，逐 Trade ID 去重；同一历史交易不能在下一 Session 中消失或变更金额。`redeem` 必须保持 `NOT_VERIFIED`，不把这些记录当作该 Session 的 confirmed fills。

`SMWindowExecutor.open` 在创建 executor/记录新 Session 前，将保留 Trade IDs/成本与**既有 AccountRiskPolicy 通过新鲜 M1 风险检查产生的历史证据**逐项对照，且当前已计入的 reserve 不得低于历史合计。没有 policy、Trade ID 缺失/成本变化、仅返回 CONFIRMED 而旧预留未解除、当前 reserve 减少或账户身份冲突均阻断；此补丁不增加任何释放逻辑。

没有修改 AccountRiskPolicy 的市场、时间、token、账户归属、相关订单/持仓验证，也没有新增资金计算公式。启动后继续使用其原有 sticky pending evidence 和 M2 snapshot 的原有预留逻辑。历史成本未重复相加，交易状态未伪造，Journal 记录未改写。

T10 的真实生产组件/MockTransport 集成验证使用三笔 BUY 成本 `3.25 + 3.50 + 3.40`：M1 session reserve、所有 M2 snapshot historical reserve 均为 10.15，当前预算来自 fixture collateral 动态计算。该 fixture 的 `36.064169 - 10.15 = 25.914169` 仅作为测试断言；生产代码不包含这些诊断金额。已有订单承诺仍在此历史预留之外被原 M2 计算保留。

历史订单终态、Redeem 和真实最新资金余额没有新增 live 验证；保留预留与原来的 NOT_VERIFIED 证据边界。

## Required Tests

| 要求 | 本轮证据 | 结果 |
|---|---|---|
| T01 真实等价完整零交易历史 | 等价完整 triplet + 生产入口重开 + 原始文件临时副本 | PASS |
| T02 多个完整安全 Session | 多 Session 重开、追加旧字节不变、sequence 连续；浮点口径反例 | PASS |
| T03 已提交未确认订单 | ACK/post_started=true/order ID 事件拒绝 | PASS |
| T04 UNKNOWN placement | UNKNOWN 事件拒绝 | PASS |
| T05 未解决成交/承诺 | MATCHED、CONFIRMED trade、LIVE order、非零 capital、观测风险及冲突状态拒绝 | PASS |
| T06 损坏/不完整 | JSON/UTF-8/重复 key/nonfinite/字段/顺序/缺闭合等反例 | PASS |
| T07 SAFE 但订单未知 | SAFE triplet 中 UNKNOWN/proofs/未知订单结果仍拒绝 | PASS |
| T08 零成交有尝试 | INTENT、ACK、UNKNOWN、已提交 REJECTED 全部拒绝；不恢复交易 Session | PASS |
| T09 安全与风险混合 | 混合历史与跨 Session 市场/账户冲突拒绝 | PASS |
| T10 10.15 reserve | 重复历史不重复计费；M1/M2 完整保留；新鲜缺失/变化/CONFIRMED/no-policy 拒绝 | PASS |
| T11 M1/M2/M3 安全 | 既有 Type2、M1/M2 与 M3/handoff suites | PASS |
| T12 CLI Logging/FL | 既有 CLI Logging/Entry、选定 FL suites | PASS |

最终验证统一通过既有 `tests/rdp_sm_m0_offline.py`：清除继承环境、dummy 配置、临时工作目录、禁用 socket/DNS、拒绝 workspace `.env`/`.aws`。集成测试只替换外部 HTTP/时间与观测边界，执行实际 Journal/Gateway/SDK/Tracker/FillTruth/SM 逻辑；MockTransport 中的 POST/DELETE 是离线 fixture，未连接真实交易所。

| 最终批次 | 实际 Passed | Failed / Skipped | Deselected | Runtime |
|---|---:|---|---:|---|
| 新 Historical Risk 专项 | 82 | 0 / 0 | 0 | 2.73s |
| CLI Logging + Entry + M3 + M3-H01 | 78 | 0 / 0 | 0 | 116.34s |
| M1/M2/Type2 + 选定 FL | 445 | 0 / 0 | 1 | 19.42s |

三个最终进程全部 exit 0，共 **605 个不同测试 PASS，0 FAIL，0 SKIP，1 preserved deselection**。每个进程输出 `M0 offline guard: 0 network/DNS attempts; workspace credentials denied`。原始历史临时副本验证独立于上述 pytest 数量，不重复计入。

保留既有 deselection：`test_cli_conflict_stops_before_settlement_preflight` 在预期异常前发现 workspace `.env`，与 credential guard 不兼容，未为运行它开放真实凭据。它不计为 pass/skip；没有声称整个无关 FL 全量测试通过。

完整命令前缀：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c "import tempfile,runpy,sys,os; tempfile.tempdir='/dev/shm'; sys.path.insert(0,os.path.abspath('tests')); runpy.run_path('tests/rdp_sm_m0_offline.py',run_name='__main__')"
```

各批次参数：

```text
tests/test_rdp_sm_historical_risk.py -q --tb=short

tests/test_rdp_sm_cli_logging.py tests/test_rdp_sm_entry.py tests/test_rdp_sm_m3_loop.py tests/test_rdp_sm_m3_h01_handoff.py -q --tb=short

tests/test_rdp_sm_type2_account.py tests/test_rdp_sm_m2_h01_pagination.py tests/test_rdp_sm_m2_h01_commitment.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_execution.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -k 'not test_cli_conflict_stops_before_settlement_preflight' -q --tb=short
```

日志：`/tmp/rdp-sm-history-fix-targeted-final.log`、`/tmp/rdp-sm-history-fix-cli-m3.log`、`/tmp/rdp-sm-history-fix-m1-m2-fl.log`。

Ruff `--no-cache` 检查修改源文件与新测试 PASS；`git diff --check` PASS。

## Safety 与变更清单

修改既有文件：`src/polymaker/rdp_sm_execution.py`。新增：`tests/test_rdp_sm_historical_risk.py`、本报告。

开工前记录 184 个 src/tests/docs/config/livecfg 文件的 SHA-256。将当前 source 中的 RiskJournal 类、两行 executor-open 历史检查及模块说明还原到开工基线后，整个源文件 hash 与开工记录严格一致（使用外部已保存旧 RiskJournal AST 源片段辅助比对，未写回工作区）。因此其余生产代码字节全部一致，包括：

- NORMAL / REBALANCE 和 make_quotes。
- M2 snapshot、committed、每单资金/订单检查、$30 上限。
- 3 秒 FillWindow、deadline、cancel buffer、取消和关闭屏障。
- confirmed-only fills、Type2 `_execution_identity`。
- CLI Logging、RunResult/WindowResult schema。
- 共享 FL Gateway、Tracker、FillTruth、Journal、StateStore、配置、Engine。

最终文件指纹：

| 文件 | SHA-256 |
|---|---|
| `src/polymaker/rdp_sm_execution.py` | `5eb49ddbf6f53d5c30cfe2e0928c71a903fbbf7c60c77c9a83299d0e1aec7af5` |
| `tests/test_rdp_sm_historical_risk.py` | `93733b2fb68db8dda30565bcd6ccba7d220dd49a7fc79f5b9e95612111a10ac7` |

184 个既有文件中只有上述 SM execution 源文件变更，没有删除既有文件。仅新增专项测试和本报告；原诊断报告、CLI Logging、所有既有测试及 SOP/config 文件不变。

现有测试均未改写，旧 lock/restart 测试仍通过：该测试只有 session-open 记录、没有完整 run result，因此属于“不完整历史”而继续拒绝。真实 Journal 不删除、不清空、不改写、不迁移；新 Session append 到同一账户文件。没有真实订单/撤单、Merge/Redeem、资金释放、SOP 修改、Git commit 或 GitHub push。

NEXT ACTION：**Independent Targeted Review**。建议复核完整性/冲突反例、真实原始 triplet、跨 Session reserve 一致性、锁与 append、冻结 M2 逻辑和离线测试结果。Worker 本轮不宣称独立 Review 已执行或提供 live 启动授权。

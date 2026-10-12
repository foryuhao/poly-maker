# RDP-SM NORMAL 双边批量挂单修复

日期：2026-10-12，Asia/Shanghai。项目：`/home/ubuntu/poly-maker`。基线：当前工作分支及已有 CLI Logging / Historical RiskJournal 修复。

```text
FIX: CODE_READY
NORMAL: YES + NO in one batch POST — PASS
IMBALANCE / existing REBALANCE: one short-side order — PASS
FILL_WINDOW: 3 seconds, unchanged
UNKNOWN / PENDING FAIL-CLOSED: PASS
HISTORICAL_MATCHED_RESERVE: UNCHANGED
TESTS: 628 PASS / 0 FAIL / 0 SKIP; 1 preserved deselection
FL REGRESSION: PASS (selected baseline suites)
SHARED CORE CHANGES: YES — additive Gateway options only
INDEPENDENT REVIEW: NOT REQUESTED, per user instruction
LIVE TRADING: NOT EXECUTED
NEXT ACTION: user-owned MicroLive after existing historical trade risk is reconciled
```

## 问题与行为变化

旧实现对 NORMAL 的 YES、NO 分别调用 `Gateway.place`，先提交 YES，再在同一个 3 秒窗口内重新执行市场/账户/订单/成交/资金检查，最后才尝试 NO。第二次检查可能耗尽共享窗口，导致只有 YES 真正提交。SM transport 和 Gateway 默认响应解析也都只支持单笔归属。

已改为：两侧一起做新鲜检查与总资金准入、一起记录 durable intent、一起保留全部承诺，然后通过现有 Gateway 和官方 SDK 发出**一个 `POST /orders`，body 中含两笔 GTC / Maker / Post-only BUY**。两笔订单共享同一个 request hash 和 `T0 + 3s` deadline，不再在两侧提交之间执行任何账户/市场往返请求。

用户所称 IMBALANCE 对应现有 `REBALANCE` 状态：只提交需要补齐的一侧，仍然一次请求。未改变原有 `abs(Delta) < 5` 的 NORMAL 分类、REBALANCE 短侧选择、报价价格/数量、资金上限、收尾余量或状态名称。

官方 [Place Orders 文档](https://docs.polymarket.com/trading/place-orders) 支持在一次请求中提交多笔订单，也明确每个结果可能独立接受或拒绝。因此这里保证同一批请求包含两侧，不能声称交易所对两侧原子接受或同时成交。使用当前已安装的 `py-clob-client-v2==1.0.2`，未升级 SDK 或依赖；实际 SDK serialization / signatures / HTTP boundary 由离线集成测试验证。

## 实现与安全处理

- `ExecutionGateway.place` 增加可选的逐 token `tick_sizes` 与 `placement_parser`。不传参数时保持原默认行为与原单笔解析；SM 显式传入这两项。批量两侧可能具有不同 tick，不能用 YES 的 tick 对 NO 签名。
- `ExecutionClobClient._post` 校验整个 1–2 笔请求的 postOnly/GTC、BUY、token、funder、signer、signature type、精确金额与份额。全部校验通过后才标记两个 `post_started`，记录相同完整 body hash，调用一次官方 SDK POST。已有请求开始后禁止再次 POST。
- M2 为整组成本做现有 $30 和当前 spendable-budget 检查；必须能支付整组才能提交，资金只够一侧时两侧均不提交。所有完整承诺及 intent 都在 POST 前建立，不依赖订单列表及时出现。
- 复用 Tracker 的 `arm_pending_many` 捕获 ACK 前的两侧成交事件，逐侧注册所有可靠 ACK，保留现有确认、终态和关闭核对逻辑。
- 正常收到两侧 ACK 时共享 3 秒窗口，按已知订单 IDs 取消并分别核对。延迟 ACK 不重置 deadline；过期后只做清理。调用方被取消时仍等待同一个在途请求，登记并清理所有已知订单。
- 一侧明确 REJECTED 时不再等待完整窗口，立即清理接受侧，并按原 M2 关闭证据决定是否安全。REJECTED 不计作确认成交。
- 一侧 ACK、另一侧 UNKNOWN 时保留可靠 ACK 的归属并取消核对它，未知侧全额预留，窗口 Fail-Closed，不重试。整个 HTTP 请求超时、响应数量缺失/多余、错误 envelope、重复 IDs 或无法归属的结果，保留无法证明已解除的全部承诺。
- 即使 ACK 都可靠，trade 仍为 MATCHED/MINED 或订单/经济事实矛盾时，继续使用原关闭屏障拒绝；不将 pending 成交记入 confirmed inventory。
- CLI 在两侧实际提交且 request hash 相同时显示 `Order Submission: YES + NO together (one batch request)`，并保留逐侧 ACK/REJECTED/UNKNOWN 输出。

Batch raw response 仍完整保存到既有 Placement 的 `raw_response`（该字段原类型为 Any），现在通常为数组；不改 RunResult/WindowResult schema。CLI 对嵌套数组中的凭据继续递归脱敏。

## 验证证据

新增 `tests/test_rdp_sm_batch_placement.py`，23 个用例。测试使用真实 SDK 签名、Gateway、M2/M3、Tracker、FillTruth 和 Journal，仅替换外部 HTTP / 时间与观测边界。

主要证据：

| 验证内容 | 实际结果 |
|---|---|
| NORMAL 真实 SDK wire request | 1 次 HTTP POST，body 含 YES、NO 2 笔；两侧 request hash 相同 |
| NORMAL → REBALANCE → NORMAL | 3 次 POST 的订单数分别为 **2 → 1 → 2**，中间只补 NO 短侧 |
| 模拟慢账户/市场 GET | 全部检查发生在共同窗口之前；两侧同请求，POST 到取消之间无 GET |
| 两种 tick | YES 使用 .01，NO 使用 .001；NO @ .479 × 5 签名金额为 2395000 micro-units |
| 成对资金不足 | balance 或 allowance 仅 3 USDC 时，4.85 USDC 整组请求零提交 |
| POST 前预留与 Journal | 两条 intent 均存在；首次外部 POST 已预留完整 4.85 USDC |
| 单侧明确拒绝 | 仍只有一次成对请求，立即取消已知接受侧，不重试 |
| 单侧 UNKNOWN | 保留并清理另一侧可靠 ACK，未知侧成本仍保留；下一窗口不能提交 |
| 整体超时 / 短或长响应 / 重复 ID / 错误类型 | Fail-Closed，保留整组成本，零确认成交 |
| 交换两侧响应 IDs | 原订单身份/经济事实核对拒绝，不虚构库存或释放承诺 |
| MATCHED pending | 零 confirmed inventory，关闭拒绝，相关未确认成本保留 |
| 延迟 ACK / 调用方取消 / ACK 后 Journal 失败 | 只发一次整组请求，所有已知订单被清理，不发第二个请求 |
| 时间窗口与截止线 | 到期或剩余关闭余量不足时整组零提交；deadline 始终 T0+3 |

既有测试按新合同更新了顺序提交相关断言：`posts` 仍统计单个订单，新增 `post_requests` 统计实际 HTTP 请求，避免把“2 个订单”误当“2 个 POST”。预算不足现在是零提交；整个批量请求超时现在是两侧结果未知；旧“第二侧慢读”反例改为验证取消在 deadline 发生、关闭读被阻塞时仍受管理。未删掉风险回归测试。

最终分三批执行，全部 exit 0：

| 批次 | PASS | FAIL / SKIP | Deselected | Runtime |
|---|---:|---|---:|---|
| 新 Batch + M2 / remediation / commitment / pagination / Type2 / Historical Risk | 204 | 0 / 0 | 0 | 19.24s |
| CLI Logging + Entry + M3 + M3-H01 | 78 | 0 / 0 | 0 | 78.03s |
| M1 / token identity + 选定 FL | 346 | 0 / 0 | 1 | 4.60s |
| 不重复合计 | **628** | **0 / 0** | **1** | |

每批均使用既有 `tests/rdp_sm_m0_offline.py`，输出 `M0 offline guard: 0 network/DNS attempts; workspace credentials denied`。工作区真实 `.env`/`.aws` 禁止读取，继承环境清除，socket/DNS 禁用，SDK POST/DELETE 只进入 MockTransport。保留既有排除 `test_cli_conflict_stops_before_settlement_preflight`，该例在预期异常之前发现 workspace `.env`，没有为它开放真实凭据。

命令前缀：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c "import tempfile,runpy,sys,os; tempfile.tempdir='/dev/shm'; sys.path.insert(0,os.path.abspath('tests')); runpy.run_path('tests/rdp_sm_m0_offline.py',run_name='__main__')"
```

三个最终参数集：

```text
tests/test_rdp_sm_batch_placement.py tests/test_rdp_sm_m2_execution.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_h01_commitment.py tests/test_rdp_sm_m2_h01_pagination.py tests/test_rdp_sm_type2_account.py tests/test_rdp_sm_historical_risk.py -q --tb=short

tests/test_rdp_sm_cli_logging.py tests/test_rdp_sm_entry.py tests/test_rdp_sm_m3_loop.py tests/test_rdp_sm_m3_h01_handoff.py -q --tb=short

tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -k 'not test_cli_conflict_stops_before_settlement_preflight' -q --tb=short
```

最终日志：`/tmp/rdp-sm-batch-m2-final.log`、`/tmp/rdp-sm-batch-m3-final.log`、`/tmp/rdp-sm-batch-fl-final.log`。Ruff `--no-cache`、`git diff --check` PASS。

## 保护范围与文件清单

AST 比对开工源副本确认以下定义完全不变：`make_quotes`、M2 `_snapshot`、`committed`、`confirmed`、`_verify_close`、`_verify_order`、`verify_close`、`_cancel_known`、`_execution_identity`、`RiskJournal._read_history`、`verify_retained_reserve`，以及 Gateway 默认 `_parse_place_response`。AccountRiskPolicy / M1 / FillTruth / Tracker / StateStore 源文件也保持原字节。

修改生产文件：

- `src/polymaker/rdp_sm_execution.py`：批量 transport 校验、逐侧解析/归属、整组执行窗口。
- `src/polymaker/execution/gateway.py`：仅 additive per-token tick 与显式 parser 参数，默认 FL 路径不变。
- `src/polymaker/rdp_sm_loop.py`：NORMAL 双侧 / REBALANCE 单侧的 arity 断言。
- `src/polymaker/rdp_sm_logging.py`：批量/单侧提交标识。

修改测试：M2 execution / remediation / commitment、Entry、M3、M3-H01、CLI Logging。新增 Batch 测试及本报告。配置、冻结 SOP、所有历史报告、历史 Journal、已有 Historical Risk 修复测试、CLI 命令入口、共享交易/状态核心的其他文件未改动。

核心源文件 SHA-256：

| 文件 | SHA-256 |
|---|---|
| `rdp_sm_execution.py` | `ca52eb038d98020904ad223c98cccbb18a7bdc483c9035dbcc33f0a7d8261590` |
| `execution/gateway.py` | `0bc06a326a024f37eaf950266355254f02ac96dab499d941e71ba9325ffdee4e` |
| `rdp_sm_loop.py` | `31007c2584ce92fc1c153a0bbc75e04f6a3c6fe0b326ee110813e6bc51797f0a` |
| `rdp_sm_logging.py` | `97eefaaffdb59ef1bc9021aaf5fda2ac120521f9a45417011e7cbfe02e2da4a6` |

没有实际下单、撤单、Merge/Redeem、释放资金、Git commit、GitHub push 或独立 reviewer 调用。

## 当前真实账户的测试前提

现有账户级 SM Journal 为 **17 行**，含用户此前启动的真实 Session `b9c6fc9ad7b54ee096aaf54695d6c5d4`。该 Session 有真实 UP 提交、MATCHED 证据和 INCOMPLETE / BLOCKED 最终结果。用户报告最终 UP 已成交；Journal 没有追加相应已确认安全关闭证明。本轮没有请求真实账户的最新订单/成交/持仓数据，也不把用户报告当作资金承诺已解除的证据。

本轮开工/完成 Journal SHA-256 同为：

```text
de37913b2290bb6f577b0fd4f419ee10a32e1b469e823902f81595a055e4018d
```

此前三条外部历史 MATCHED 的 **10.15 USDC 保守预留保持**。本轮只修挂单执行，不将已有真实交易历史当成“安全零交易 Session”，不删除、清空、改写或换文件绕过 Journal。已用当前 RiskJournal 原始 AST 定义对真实文件执行只读历史解析（未构造真实 writer、未导入 app/SDK、无网络），结果为 **BLOCKED**，解析前后原文件 hash 一致。因此只要启动到达 Journal 检查，这份真实账本仍会阻断重跑；原 INCOMPLETE Session 需要另行只读核清实际订单、成交、持仓与承诺，并形成可靠的风险处置证据。

用户已明确不需要独立 review，本轮按此执行。代码与离线测试完成；下一步由用户手动测试，在现有真实历史风险被妥善核清和处理后开展。

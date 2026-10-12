# RDP-SM M3-H01 — Independent Targeted Review

## 1. Baseline

- Review date: 2026-10-12, Asia/Shanghai.
- Project: `/home/ubuntu/poly-maker`.
- Branch independently verified: `fl-restored-97ab0f9`.
- HEAD independently verified: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`.
- Contract: RDP-SM v1.8 FROZEN; FL-first / Thin SM Orchestrator; MicroLive First.
- MicroLive authorization: **NO**.
- 本轮为独立 Reviewer 执行：未参与修复，未修改生产代码或既有测试。Worker 的 CODE_READY、各项 PASS 和 478 项通过均作为待验证声明处理。
- 入口工作区：既有 `src/polymaker/cli.py` tracked 修改（22 行新增）；32 个 untracked 文件，包括 18 份 development 报告、3 个 SM 模块和 11 个测试/离线 runner 文件；staged diff 为空。审查包括这些未提交交付文件。
- 已读取四份指定材料：`RDP_SM_M3_TRADING_LOOP.md`、`RDP_SM_M3_INDEPENDENT_REVIEW.md`、`RDP_SM_M3_H01_HANDOFF_FIX.md`、`RDP_SM_M2_H01_PAGINATION_INDEPENDENT_REVIEW.md`。
- 未检出独立落地的 v1.8 SOP 文件；遵守本轮冻结边界，没有以旧策略文档替代、编辑 SOP、恢复历史分支或开始 M4。
- 入口对 src/tests/docs/config/livecfg 下 173 个非字节码、非 .env 既有文件记录 SHA256，保存在工具会话内存中。最终比较见 Change Manifest。

关键审查文件 SHA256：

```text
src/polymaker/rdp_sm.py
f42a71d56b1fba7db15edaefd5f7f1d461cda84789fabf3d95711af891cbab9f
src/polymaker/rdp_sm_execution.py
9721e39244ae082bd74c27df7acf8bcd90a6c0c0e1a109cb4ba59428ce490eb4
src/polymaker/rdp_sm_loop.py
956fb291c235fb79fac00857b6e97f72a0d1ccd71c963da716c13eccb0988792
tests/test_rdp_sm_m3_h01_handoff.py
a9071de17e970da2e9bd3273d7f6a1c6e6d4c047e9a3ec1db53f18d56be8351e
```

## 2. Review Scope

唯一核心问题：M1 已观察的外部账户风险，是否在 M3 启动、等待和 M2 执行期间保留，且资金不足时阻止实际订单提交。

检查实际 M1 `_account` / `run_preflight`、SM SDK per-page observation、M3 waiting/startup、M2 factory/snapshot/placement/close、身份绑定和相关测试断言。只运行直接相关 NORMAL/REBALANCE、Type 2、M2-H01/H02、M0 containment 和 FL 回归；不重新审计报价算法、完整资金模型、订单生命周期或共享核心。

接受保守 BLOCKED，不要求自动恢复、新 Ledger、M4、Merge/Redeem 或新的账户管理系统。未执行真实交易、真实账户请求、外部 API、RPC、安装或凭证读取。

## 3. Original Counterexample

独立改编历史 Reviewer 报告中的 stdin reproducer，复用既有 HTTP/time fixtures，使用实际 Type 2 配置和生产 M1/M3/M2/Gateway/SDK。只对真实方法增加委托式计数与快照记录；不替换执行结果。

输入保持原反例：账户余额 $6；M1 首次观察 external LIVE BUY5@0.90，即 $4.50；随后 listing 遗漏该订单，但 targeted official order read 仍返回 LIVE；M3 NORMAL 首笔 BUY5@0.49，即 $2.45；若错误放行，潜在承诺为 $6.95，第二笔再增长至 $9.345。

| 独立诊断 | 后续 list reads 总数 | 成功连接的 Reader 数 | M2 保留风险 / 可花预算 | Gateway.place / POST / cancel | Window / Run |
|---|---:|---:|---|---|---|
| 从 T+60 启动 | 8 | 3 | $4.50 / $1.50 | 0 / 0 / 0 | 1 个 BLOCKED window；Run BLOCKED |
| 从 T+0 等到 T+15 | 23 | 18 | $4.50 / $1.50 | 0 / 0 / 0 | 1 个 BLOCKED window；Run BLOCKED |
| 从 T+0 等待后时钟到 T+60 | 9 | 4 | $4.50 / $1.50 | 0 / 0 / 0 | 1 个 BLOCKED window；Run BLOCKED |

三个序列的所有成功连接 Reader 均保留 `external`；所有实际 M2 snapshot 的 `execution_reserved_usd=4.5`、`basic_spendable_budget_usd=1.5`。实际 `Gateway.place` 未调用，HTTP order POST 未发生，无后续 window。停止原因为 `OFFICIAL_CAPITAL_INSUFFICIENT`，外部订单仍 LIVE，最终 Run 不是 COMPLETED。

独立执行既有 `test_original_preflight_risk_survives_waiting_and_missing_listings` 的三个参数亦验证相同事实，并检查实际持久化 session provenance。

**ORIGINAL COUNTEREXAMPLE: PASS。** 原 $6.95 / $9.345 错误提交路径已消除。

## 4. Actual M1 → M3 → M2 Evidence Flow

实际生产路径：

```text
SMTradingLoop.run(offline=True)
  → _ExecutionReads(cfg, risk_reads=previous)
  → real M1 run_preflight
      → Gateway.open_orders → SDK order pages
      → ExecutionClobClient._observe_order_page
      → retain exchange-ID rows / sticky fill observations
      → close M1 HTTP pool; observations remain on reader
  → each waiting reader validates identity and copies previous observations
  → SMWindowExecutor.open(..., risk_reads=last M3 reader)
      → fresh real M1 preflight seeded with M3 evidence
      → fresh M2 execution reader seeded with accumulated evidence
  → real M2 _snapshot
      → retained history + both fresh listings + local unresolved attempts
      → deduplicated reservation and fresh capital budget
  → real M2 execute_window → capital check before each Gateway.place
  → real M2 terminal / FillTruth / Tracker / Journal / close barrier
```

源码位置：`rdp_sm.py:338` 的账户读取；`rdp_sm_execution.py:148-231` 的分页观察；`:280-325` 的 Reader 交接；`:464-510` 的 M2 factory；`:572-737` 的 snapshot；`:1121-1142` 的逐笔资金与失败检查；`rdp_sm_loop.py:240-282` 的等待和 factory 交接。

交接的是现有 `observed_open_rows`、`observed_trade_legs`、`pagination_failure`；不传递旧 READY 授权、旧余额、SM ownership、确认库存或旧 close proof。下游仍重新读取资金和市场事实。复制前校验来源与目标账户，复制后释放 predecessor reference；没有新增资金 Ledger 或共享 FL 修改。

M1 当前读取 positions/orders/collateral，不独立扫描全部 trades。因此已验证的是 M1 **实际观察到的**订单及其 matched/associate 风险保留，而非新增完整成交发现能力；Reader 已有相关 trade legs 和 incomplete latch 也明确被复制。真正成交核对仍由 M2 完成。

**EVIDENCE HANDOFF: PASS。**

## 5. T+0/T+60 Verification

A. M1 SDK order-page 证据在 HTTP pool close 后仍存在，进入下一个等待 Reader 和 M2 资金计算。

B. M3 每次构造 Reader 都传 `risk_reads=startup_reads`；创建新连接没有重置已观察风险。

C. M2 fresh startup preflight 和最终 execution reader 均继承已累计证据；三个独立反例序列直接观察了全部成功连接 Reader。

D. 后续空 snapshot 只改变当前 listing，不删除 historical map，不构成终态证明。

E. 新 M2 order observations 与 M1 历史观察合并，实际额外订单回归验证累计 $5。

F. 交接校验 Signer、Funder、signature type；Type 2 资金账户身份没有退化为 Signer-only。

T+0→15→后续资金读取由既有完整市场 integration 覆盖；T+0→60 和直接 T+60 omission 由独立 stdin 诊断和定向用例共同覆盖。官方时钟和等待由 offline fixtures 驱动，实际 M1/M3/M2 执行。

**T+0/T+60 HANDOFF: PASS。**

## 6. Cross-Snapshot Risk Retention

| 要求 | 实际验证 | 结果 |
|---|---|---|
| 保留 Preflight LIVE | 首次之后全部遗漏；真实 snapshot 仍 reserve4.50 | PASS |
| 保留已观察 matched 风险 | `test_preflight_pending_fill_cannot_be_released_by_zero_fill_replica`：matched1/associate 后被 misleading cancelled0 替换，sticky flag 和4.50保留 | PASS |
| 新观察累计 | `test_m2_additional_commitment_does_not_replace_m1_evidence`：A4.50+B0.50，reserve5/budget1，零 POST | PASS |
| UNKNOWN 不释放 | targeted order timeout 用例及 M2 repeated LIVE/UNKNOWN 测试，最大承诺保留 | PASS |
| 遗漏不代表终态 | 三个独立 original schedules 与 M2 commitment/pagination 回归 | PASS |
| 分页失败不清空历史 | M1 page1 external 后 page2 timeout：map/latch保留，M2不开启；M2 order/trade partial-page回归亦通过 | PASS |
| 不完整状态不证明安全 | M1 BLOCKED；M2 persistent latch 阻止 snapshot、intent、POST及safe barrier | PASS |
| 外部/未确认风险不变库存 | 定向 pending 用例：SM confirmed为空；M2 MATCHED/MINED/CONFIRMED external-page回归不投射为owned库存 | PASS |
| 原可靠释放规则保留 | fresh zero-fill proof 和 owned partial-fill/cost 回归均通过 | PASS |

M1 incomplete pagination 定向用例：第一页面证据与 `OPEN_ORDERS_PAGINATION_INCOMPLETE` 实际存在；零 executor、零 window、零 placement/POST，Run BLOCKED。下游已有分页失败历史不会被后续成功空读解除。

**CROSS-SNAPSHOT RETENTION: PASS。**

## 7. Deduplication

`_observe_order_page` 用 exchange order ID 的 `setdefault` 保留首次经济条件，风险标记 sticky；trade legs 用 `(trade_id, order_id)` 去重。M2 snapshot 把历史、两份 fresh listing 和未解决 owned attempts 按 identity 合并；相同 ID 经济条件冲突会阻断。

实际定向 sufficient-funds 用例：余额 $7.40、同一个 external $2.45 在 M1/M2 重复出现；初始 reserve $2.45、预算 $4.95。第一笔 SM ACK 后 reserve $4.90，未因同时出现在 local/listing 中重复计入；第二笔 $2.395 可合法提交，共两个真实 MockTransport POST，安全关闭。

M2 duplicate order/trade、同 trade ID 多 owned maker legs、snapshot economics conflict 和 confirmed fill dedup 回归均通过。A+B 场景保留两个不同 ID，无新风险遗漏。

**DEDUPLICATION: PASS。**

## 8. Safe Commitment Release

`_external_no_fill_terminal` (`rdp_sm_execution.py:739-788`) 未改变为“空列表即释放”。被 omitted 的 external BUY 只有 fresh matching CANCELED/CANCELLED、matched0、显式空 associates、匹配 market/economics、完整无相关 leg 的 trades 查询、第二次 matching official order read 和 durable proof 才可零成交释放。

实际 `test_verified_no_fill_release_uses_existing_m2_proof`：历史 LIVE 后 fresh cancelled0；真实 order/trades 查询和 `sm_external_no_fill_terminal` journal 存在；reserve0、预算6，两个合法 MockTransport POST，COMPLETED/SAFE。历史 row 仍保留，external 不成为 SM ownership，也未被 SM cancel。

已观察任何 fill/external event、UNKNOWN、错误或不完整分页不能使用这个零成交释放路径。针对 external 已观察成交后，即使后续看似已完全确认，也可能持续保留完整 envelope；这是明确保守限制，不要求本轮提供自动恢复或更复杂 external partial-fill 释放。

Owned 订单的可靠释放和确认成本仍由原 M2 proof/FillTruth/Tracker 处理：终态和完整 confirmed economic set 一致后，只释放未成交部分，`committed()` 保留实际 confirmed成本。实际 partial-fill/cancel-race/duplicate-confirmations 与后续 $30 cap 用例通过；pending fill、late confirmation、LIVE contradiction 与 Cancel ACK 用例保持阻断。

**SAFE RELEASE: PASS。** 已证实存在可用的可靠释放路径，没有把全部历史承诺无条件永久锁定；无法确认时持续 BLOCKED 符合边界。

## 9. Run Status Correctness

M2 资金不足产生 BLOCKED window；M3 标记 failed 并退出循环，final cleanup 即使安全，也只返回 BLOCKED，不能重新继续或变为 COMPLETED（`rdp_sm_loop.py:215-225`）。无法证明最终 close 时返回 INCOMPLETE。

原反例三序列：window BLOCKED、Run BLOCKED、原因 `OFFICIAL_CAPITAL_INSUFFICIENT`、无进一步提交。最终 own-order barrier 为 SAFE_TO_CONTINUE 是“没有待关闭的 SM owned orders”的检查结果；它没有解除 external风险，也没有覆盖 Run BLOCKED。

等待直接跳到 T+240且尚未建立 M2：retained风险用例返回 BLOCKED/BLOCKED，原因 `PREFLIGHT_RISK_UNRESOLVED_AT_CUTOFF`，零订单；不再走无条件 COMPLETED shortcut。这一分支没有 M2释放证明，可能对已取消但尚未核实的历史观察保守阻断，属于可接受限制。

正常无风险 cutoff 与正常可验证关闭仍 COMPLETED。完整市场 integration 独立通过：69 windows（68 NORMAL+1 REBALANCE）、137 个本地 mock Maker POST、confirmed YES/NO=5/5、3 ActiveCycles、最后 mock提交T+219、T+240停止，final SAFE_TO_CONTINUE/COMPLETED。

**CAPITAL SAFETY / RUN STATUS: PASS。**

## 10. Type 2 Identity Binding

`_execution_identity` (`rdp_sm_execution.py:334-364`) 从当前配置 dummy key 派生 Signer；要求 chain137、SDK/Gateway/key Signer一致、配置/SDK/Gateway Funder一致、signature mode一致。Type0要求 Signer=Funder；Type2要求不同身份及 Funder=官方 SDK local deterministic derived Safe。实际资金查询 mode2、positions以Funder读取，实际mock签单 maker=Safe、signer=EOA、signatureType2。

交接另外要求来源/目标 SDK 的 Signer、Funder、mode全部相同。来源 cfg 的变更不能使来源 client被自动重标为新账户：两边都重新校验实际 SDK/Gateway身份，然后才复制风险。

现有7项Type2/Type0回归通过，覆盖正确映射、wrong funder、key/SDK funder/signature mutation、Type0。原反例三种等待序列均使用有效 Type2运行。

新增独立只读 stdin诊断在T+0看到external后，等待期间只修改配置Funder和fixture官方positions账户为另一个dummy地址：后续交接失败，只有第一次list read，原Reader仍保留external；未开启M2、零snapshot/window/Gateway.place/POST/cancel，Run BLOCKED/BLOCKED（M1_SESSION_NOT_READY）。结合既有key等待变更用例，证明没有跨账户导入。

`NOT_VERIFIED_FOR_LIVE` 标记保留；本地派生与离线签名不证明真实Safe当前权限。

**ACCOUNT IDENTITY / TYPE 2: PASS for offline integration。**

## 11. Relevant Regression

| 项目 | 独立执行的证据 | 结论 |
|---|---|---|
| M2-H01 | local ACK omission、historical commitment、UNKNOWN、sequential budget、partial-page risk、dedup/release回归 | PASS |
| M2-H02 | optional slow reads不先于deadline cancellation；held second-leg read时取消优先且任务drain；cutoff禁新提交 | PASS |
| M0-H01 | terminal后LIVE及official listing contradiction使旧proof失效并阻断后续订单 | CONTAINED |
| M0-H02 | late confirmed、status-only、cross-order set变化不能沿用旧空集证明；库存dedup | CONTAINED |
| M0-H03 | first/second UNKNOWN、duplicate ID、lookup failure保留最大风险、不重试、不继续window | CONTAINED |
| NORMAL / REBALANCE | 22项M3集成/报价/完整市场用例，包含NORMAL→REBALANCE→NORMAL→STOP | PASS |
| Type2 / Type0 | 7项实际SDK/Gateway离线集成 | PASS |
| M1/F01 | 64项startup/token identity | PASS |
| selected FL | 282项execution/filltruth/state/parser/marketdata/gateway/favorite_live/config回归 | PASS |

`git diff --quiet` 独立确认 shared execution/state/Journal/config/Engine/favorite_live source 与HEAD一致；tracked CLI差异是入口即有的22行新增，未在本轮变化。无要求之外的全面重审。

## 12. Independent Test Results

现有Python3.12.14 venv、既有guarded runner，未安装软件或改测试。由于 `/tmp` 写入出现 `Disk quota exceeded`，运行前仅在外部bootstrap中把 `tempfile.tempdir` 指向 `/dev/shm`。runner保持原状、原credential/network guards、temporary cwd、禁plugin autoload/cache、禁bytecode。

最终可复现命令（从项目根目录）：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c "import tempfile,runpy,sys,os; tempfile.tempdir='/dev/shm'; sys.path.insert(0,os.path.abspath('tests')); runpy.run_path('tests/rdp_sm_m0_offline.py',run_name='__main__')" tests/test_rdp_sm_m3_h01_handoff.py -q --tb=short

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c "import tempfile,runpy,sys,os; tempfile.tempdir='/dev/shm'; sys.path.insert(0,os.path.abspath('tests')); runpy.run_path('tests/rdp_sm_m0_offline.py',run_name='__main__')" tests/test_rdp_sm_m3_loop.py -q --tb=short

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c "import tempfile,runpy,sys,os; tempfile.tempdir='/dev/shm'; sys.path.insert(0,os.path.abspath('tests')); runpy.run_path('tests/rdp_sm_m0_offline.py',run_name='__main__')" tests/test_rdp_sm_type2_account.py tests/test_rdp_sm_m2_h01_pagination.py tests/test_rdp_sm_m2_h01_commitment.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_execution.py -q --tb=short

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c "import tempfile,runpy; tempfile.tempdir='/dev/shm'; runpy.run_path('tests/rdp_sm_m0_offline.py',run_name='__main__')" tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -k 'not test_cli_conflict_stops_before_settlement_preflight' -q --tb=short
```

| Suite | Executed | Passed | Failed | Skipped | Deselected | Runtime |
|---|---:|---:|---:|---:|---:|---|
| M3-H01 targeted | 11 | 11 | 0 | 0 | 0 | 3.45s |
| M3 integration | 22 | 22 | 0 | 0 | 0 | 65.32s |
| Type2 + relevant M2 | 99 | 99 | 0 | 0 | 0 | 17.22s |
| M1/F01 + selected FL | 346 | 346 | 0 | 0 | 1 | 2.38s |
| **Distinct pytest total** | **478** | **478** | **0** | **0** | **1** | |

另外4个独立stdin diagnostics全部通过（3个原反例时序+1个等待Funder变更），不加入478 pytest计数。独立诊断复用历史报告Section14脚本，改为安全预期、增加Type2与等待变体、委托计数Gateway.place和真实snapshot；未创建项目test文件。

首次内存盘bootstrap漏了脚本启动时自动附带的tests路径，导致targeted/M3/M2在collection时报ModuleNotFoundError，尚未执行测试。随后仅在外部bootstrap恢复该路径，完成上述最终运行；没有改source/test/assertion或放宽保护。collection失败不隐藏为通过，也不计入executed cases。

每个最终suite和独立diagnostic均报告 **0 network/DNS attempts; workspace credentials denied**。仅MockTransport响应和公开dummy key，零真实订单/真实cancel/真实外部请求。正常测试中的137等POST数字指本地mock边界，非真实交易。

1项deselection：`test_cli_conflict_stops_before_settlement_preflight`。独立查源码确认 `Config.load()` 在别名校验前 `load_dotenv()`，其调用文件定位可触及受保护workspace `.env`；保留既有排除，未读取该文件、未关闭credential guard。Deselected不是passed或skipped。

其他独立检查：Ruff check与format --check（no-cache）针对execution/loop/handoff tests通过，`git diff --check`通过，shared-core quiet diff通过。

**Worker的478 executed /478 passed /0 failed /0 skipped及1 justified deselection已独立核实。**

## 13. MicroLive Impact

M3-H01原资金安全blocker在本次范围内已消除；没有新的可复现错误下单、证据丢失或错误安全放行反例。

真实交易的实际前置阻断仍是：MicroLive授权为NO且M3 live入口明确拒绝；Type2当前Safe真实签单权限仍NOT_VERIFIED_FOR_LIVE；本轮没有完成真实接口准备验证。这些是已有真实交易前提，不构成本次离线H01修复FAIL理由。

保守限制：incomplete分页历史持续BLOCKED；external已观察fill/event后保留完整风险；pre-executor cutoff无释放proof时拒绝COMPLETED；没有跨进程恢复或自动恢复。均未发现导致错误下单，本轮不要求扩展功能。

## 14. Final Reviewer Decision

**PASS**

原反例由实际M1/M3/M2路径独立消除：已观察external4.50跨等待/reader handoff进入资金预算，后续空listing不能释放；资金不足时零Gateway.place/POST且Run BLOCKED。重复经济敞口计一次、新风险累计、可靠零成交释放可用、owned确认成本保留、Type2资金账户隔离正确。M2/M0/NORMAL/REBALANCE/Type2/selected FL回归通过，共478项pytest通过，零真实交易。

此决定仅针对M3-H01及直接相关回归，不授权MicroLive，也不声明真实Safe权限或真实接口已经验证。

## 15. Change Manifest

仅创建：

`docs/development/RDP_SM_M3_H01_INDEPENDENT_REVIEW.md`

报告创建前173个既有文件SHA256比较：changed0、added0、deleted0。报告创建后再次独立比较确认所有173个文件保持相同，changed0、deleted0，仅新增本报告。最终branch/HEAD、staged diff及git diff --check复核通过；16个必需报告章节齐全。

没有修改生产代码、既有测试、配置、历史报告、SOP或共享核心；没有删除项目文件。原tracked CLI22行差异和原32个untracked文件均保留，staged diff为空，branch/HEAD不变。初次baseline尝试仅在外部 `/tmp` 触发配额错误，之后baseline保存在工具会话内存；没有清理项目或外部历史材料。

**SHARED CORE CHANGES: NO。**

## 16. Next Minimal Action

**Architect Acceptance** of M3-H01 targeted fix。

不需要本轮再修复；不开始M4、不执行真实订单、不修改冻结SOP v1.8。真实交易准备仍需既有明确授权与权限/接口验证。

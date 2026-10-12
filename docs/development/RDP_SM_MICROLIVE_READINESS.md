# RDP-SM — MicroLive Readiness

## 1. Baseline

- Date: 2026-10-12, Asia/Shanghai.
- Project: `/home/ubuntu/poly-maker`.
- Required/current branch: `fl-restored-97ab0f9`.
- HEAD: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`，本轮前后不变。
- SOP: RDP-SM v1.8 FROZEN；FL-first / Thin SM Orchestrator；MicroLive First。
- MicroLive Authorization: **NO**，本轮未授予授权。
- 入口 Git status：既有 tracked `src/polymaker/cli.py` 22 行新增；33 个 untracked 交付文件（19 份 development 报告、3 个 SM source、11 个测试/runner）；staged diff 为空。实际工作区源码已检查，未以旧 FL HEAD 否认已验收成果。
- Origin 配置：`https://github.com/foryuhao/poly-maker.git`；upstream 为 `https://github.com/warproxxx/poly-maker.git`。只核验脱敏 remote 配置，没有修改 remote；由于 Readiness NO，未进入发布权限或远端 refs 验证阶段。
- 已读取指定 M1 Safe Session、M2 Execution Integrity、M2-H01 Pagination Independent Review、M3 Trading Loop、M3-H01 Independent Review；检查现有源码、测试、CLI 实际 help、Journal。
- M1/M2/M3 source 与 M3-H01最新修复和必要报告/测试均存在。本轮没有恢复、读取或复制历史 RDP 分支；没有清理未知文件。
- 对 src/tests/docs/config/livecfg 下 174 个既有非 .env、非字节码文件记录 SHA256，并在交付前核验。冻结 SOP、原报告、生产代码和既有测试未修改。

## 2. Accepted Milestones

按本轮用户确认的验收状态：

| Milestone / Finding | Status |
|---|---|
| M0 | CLOSED_WITH_FINDINGS |
| M1 / M2 / M3 | ACCEPTED |
| M2-H01/H02 | CLOSED |
| M3-H01 | CLOSED |
| M0-H01/H02/H03 | CONTAINED |
| Type 2 offline compatibility | PASS |

本轮复查没有否定上述离线验收。MicroLive Readiness 与离线交付验收不同：必须存在可在明确人工授权后启动的真实生产入口，并可靠核清实际账户风险。

## 3. Production Entry

**PRODUCTION ENTRY: FAIL。**

实际 CLI source `src/polymaker/cli.py:40-61` 只有 `rdp-sm-preflight`，调用 `run_preflight` 后输出 JSON；不调用 `SMTradingLoop`。只读、禁网络/真实凭证环境下通过实际 Typer `--help` 核验，exit0，存在 preflight 命令，没有 M3 trading/microlive runner 命令。

现有库级实际路径已通过离线生产组件集成验证：

```text
SMTradingLoop.run(offline=True)
  → actual M1 run_preflight with retained evidence
  → actual SMWindowExecutor.open
  → actual M2 snapshot / capital check / execute_window
  → production Gateway + installed official SDK
  → production Tracker / FillTruth / StateStore
  → durable RiskJournal and final reconciliation
```

M3 内没有直接 Gateway.place/cancel 路径，订单均经 M2。绑定一个精确 BTC 5m slug，无 next-market 调度；one-run flag 和 blocked-window exit 阻止自动重启/继续。

**AUTHORIZATION GATE: FAIL for readiness；default denial: PASS。**

`rdp_sm_loop.py:227-236` 的 `run()` 默认先要求 `offline is True`；`:115-125` 和每个 loop 的 `_offline_boundary` 还要求 sync/async HTTP 都是 `httpx.MockTransport`。真实 HTTP transport 无论人工是否授权都会返回 `M3_LIVE_EXECUTION_NOT_AUTHORIZED`。代码文档明确说明 future authorized MicroLive 还需要 separately approved live gate。

没有现成的人为授权输入能同时限定 exact market、one market、$30、BUY/Maker/post-only 并合法进入该真实 M3 runner。该阻断适用于所有账户类型，不是当前 Type2 identity verifier 的拒绝。

原 FL `run`、`favorite-first-live`、live tests、settlement CLI 路径仍存在；`run` 默认 `paper=False` 并启动旧 Engine。这些是保留的不同工作流，不能作为本次 SM Gate、M2单市场管控或授权入口的替代证明。未运行任何这些真实写路径，也未为了本轮隔离重构 FL。

最小后续工程范围：增加一个连接实际 M3 的独立 CLI 入口及默认关闭、绑定明确人工授权与指定单市场/$30约束的 live admission；保留现有 Type2身份、M1→M2证据交接和所有 M2 gates。不得把开启 offline 或替换 MockTransport当作真实授权。

## 4. Type 2 Account

**TYPE 2 ACCOUNT: PASS for local/current read identity and offline execution。**

2026-10-12 使用现有 `Config.load('config')`、SM `_ExecutionReads` 与原 FL Gateway/installed official SDK 重新连接实际账户。只在内存中加载授权只读检查所需配置，所有 SDK/stdout/logging 原始内容捕获、不输出、不写入项目文件。

| Item | Current observed result |
|---|---|
| Signer | `0x72EE...49d1` |
| Funder / collateral Safe | `0x2d73...9D5D` |
| Signature Type | 2 |
| Chain | 137 |
| Signer != Funder | YES |
| Configured Safe vs SDK-derived Safe | Exact MATCH，纯本地派生 |
| Config / Gateway / SDK signer-funder-mode | `_execution_identity` PASS |
| Position account | 官方 Data API `user=Gateway.funder` |
| Collateral account | authenticated SDK，configured signature_type2 |
| Open-order/trade account | 同一 SDK credentials 的 authenticated L2 GET |

Type2由当前 SM verifier允许正确 derived Safe，7项Type2/Type0实际离线签单/读取/变更拒绝回归全部通过；没有额外 EOA-only 或 Safe live-authority证明开关阻止正确Type2配置。未来获得授权后所需官方SDK签单能力已有离线证据，但当前所有模式的M3 live入口仍存在Section3通用硬阻断。

**TYPE 2 LIVE AUTHORITY: NOT_VERIFIED_FOR_LIVE。** 用户明确允许该状态，不将它列为本轮自动FAIL或独立blocker。没有RPC、Safe owner/threshold请求或真实订单权限探测。

## 5. Official Read-only Checks

官方只读基础查询：**PASS**；必要风险核清：**FAIL / NOT_PROVEN**；最终 **OFFICIAL READ-ONLY: FAIL for complete readiness clearance**。

本轮 fresh观察时间：2026-10-12 **04:52:50–04:52:52 UTC（12:52:50–12:52:52 Asia/Shanghai）**。不是复用此前历史余额，即使本次数字与历史相同。

| Read | Fresh observed result |
|---|---|
| Existing API credential derivation | 官方 GET成功；没有create或create-or-derive fallback |
| USDC balance | **36.064169 USDC** |
| Applicable normal exchange V2 allowance | **> $30**，约 `1.157920892373161954235709850E+71 USDC`；4个 allowance entries |
| Official order version | 2 |
| Open orders | **0** |
| Retained open-order evidence | 0，完整读取后的实际结果 |
| Positive positions | **0**，以正确Funder查询 |
| Authenticated trade rows | **230** |
| Trade states | **224 CONFIRMED / 3 FAILED / 3 MATCHED** |
| Nonterminal trade rows observed | **3 MATCHED**，不能解释为零 |
| Pagination | orders/trades各1页、SDK END_CURSOR=`LTE=`；positions1页、explicit next_cursor=null |
| Identity recheck after reads | PASS |

初次6个请求全部是GET，使用既有官方 `/auth/derive-api-key`、`/balance-allowance`、`/version`、`/data/orders`、`/data/trades`、`/v2/positions`。临时 HTTP request guard 限定 exact official host/path、GET-only、禁止redirect并设置15秒超时；未启用Gateway.connect、SDK credential creation、order executor、WS、RPC或第三方API。

针对3条MATCHED，随后在 **04:54:27–04:54:30 UTC** 用同一SDK重新读取account trades、以trade ID查询既有 `/data/trades`，并读取相关 `/data/order/<id>`。仅输出脱敏aggregate，不保存原始ID/交易数据/密钥/secret/passphrase。

| Historical match time UTC | Authenticated account role / relevance | Targeted trade recheck | Relevant order verification |
|---|---|---|---|
| 2026-03-23 17:35:46 | MAKER；有1个maker leg地址精确匹配当前Funder | 仍 MATCHED | GatewayReadError |
| 2026-03-23 17:05:44 | TAKER；authenticated account trade记录 | 仍 MATCHED | GatewayReadError |
| 2026-03-22 17:07:21 | MAKER；有1个maker leg地址精确匹配当前Funder | 仍 MATCHED | GatewayReadError |

这8个后续请求仍为GET。历史时间本身不能把非终态更新为已确认；空orders和空positive positions也不构成这些成交的终态证明。本轮没有声称它们必然造成当前具体金额的承诺，更没有虚构风险金额；实际问题是官方targeted状态仍非终态且相关订单无法可靠核验，尚不能证明这些关联当前账户的历史风险已解除。它们不能被静默丢弃以获得Readiness PASS。

**账户风险核清BLOCKER：需官方终态/可靠解释，或用已获授权的只读证据证明这些历史记录不会占用本次资金。** 不要求新ledger、自动恢复、RPC或通过真实交易尝试解除。

全轮实际账户诊断：14个官方GET、**0 HTTP writes /0 write attempts /0 real orders /0 real cancels**。读取失败保留为UNKNOWN，未转为订单/成交/余额零。三条非终态观察在诊断输出和本报告中持续记录。

## 6. Capital Safety

**CAPITAL SAFETY: PASS in current production offline paths。**

- M2 constant `CAPITAL_LIMIT=Decimal('30')`；逐笔检查 `committed()+new<=30` 和fresh `basic_spendable_budget_usd`，Gateway之前生效。
- M1已观察风险沿M3等待Reader→M2startup→execution reader交接，来源/目标Signer+Funder+mode一致后复制。真实snapshot按order ID合并历史/两次fresh listing/local UNKNOWN或ACK最大承诺。
- 原反例三个时序实际M1/M3/M2路径复测：$6余额、external$4.50、请求$2.45，reserve4.50/spendable1.50；**Gateway.place=0，POST=0，Run BLOCKED**，不只检查内部flag。
- 已看到matched/associate、UNKNOWN、分页错误不能释放；Cancel ACK不代表终态；同order ID不双计；M2新external风险累计。
- 完整官方no-fill终态/trades/复读proof释放路径仍通过；confirmed成本与未解决最大风险不混淆。
- Current actual funds read正确使用signature_type2和Funder关系；余额/allowance充足不能替代Section5未知成交风险核清。

M2-H01/H02、M3-H01和$30/UNKNOWN/partial-fill/cancel回归全部通过。未为Readiness新增资金模型或删除身份/风险checks。

## 7. Execution Safety

**MARKET TIMING / CLOSE BARRIER: PASS in tested production paths。**

既有回归验证300秒BTC市场身份、精确YES/NO uint256 token、dynamic tick、合法size、default FillWindowSec3、T+15开始、T+240停止新单及15秒close headroom。

NORMAL使用当前BestBid−各自dynamic tick；REBALANCE使用弱侧最高合法不跨ask的BestBid；base clip5；实际confirmed inventory由原M2/Tracker/FillTruth负责，MATCHED/MINED不成为confirmed库存。

全部订单走M2 Maker BUY/GTC/post-only，SDK transport核对实际signed经济条件；无SELL/Taker/Merge/next market。下一个window要求fresh mandatory close proof；cancel ACK后仍查official终态和完整confirmed经济集合；UNKNOWN/LIVE contradiction/late fill/pending stops next submission；异常退出不自动重启。

Full offline market独立复测：**NORMAL→REBALANCE→NORMAL→STOP；69 windows（68NORMAL+1REBALANCE），137个本地MockTransport POST，3ActiveCycles，confirmed5/5，first mock POST T+15、last T+219、STOP T+240，COMPLETED/SAFE_TO_CONTINUE**。这些不是实盘订单或盈利验证。

## 8. Journal

**JOURNAL: PASS for minimal first-session audit。**

检查实际RiskJournal：使用既有FL JSONL格式、每次flush+fsync、初始化directory fsync、Funder绑定文件和exclusive flock；历史非空文件拒绝自动新session。无需M4/database扩展。

新增只读stdin离线诊断（未新增项目test）：actual M1/M3/M2、一个window两笔mock BUY、YES confirmed2、截止停止，关闭后读回实际JSONL：**23条records**。

| Record kind | Count | Audit facts |
|---|---:|---|
| sm_session | 1 | session、slug、Signer/Funder/mode、初始risk provenance |
| sm_strategy_window | 1 | market/window、strategy、delta、quote economics |
| sm_placement_intent | 2 | attempt、market/window/token/outcome/BUY/price/size、record timestamp |
| sm_placement_ack | 2 | order ID、placement outcome、post_started、request_hash/raw response |
| sm_placement_result | 2 | actual placement result |
| sm_cancel_ack | 2 | 已请求order ID集合及ack结果、record timestamp |
| sm_trade | 2 | trade/order identity、status、fill economics |
| sm_official_reconciliation | 6 | official terminal order/trades及FillTruth结果 |
| sm_close_barrier | 3 | safety result、reason/commitment |
| sm_window_result | 1 | YES/NO inventory、capital commitment、barrier |
| sm_run_result | 1 | final status/summary |

所有record有wall-clock `ts`和同session ID；两笔intent在ACK之前，真实mock POST handler从文件读回确认intent已落盘后才接受提交。诊断最终confirmedYES2/NO0、capital_committed0.96、Run COMPLETED；仅2个mock POST，0network/DNS。

现有fsync故障、ACK后写失败、exclusive lock、historical restart拒绝、UNKNOWN/in-flight cancellation回归全部通过：写失败不能新增提交或报告safe；已有owned ID仍尝试清理、保留unknown。

边界：submission timestamp由intent/placement结果的ts界定，没有额外精确wire送达时刻字段；cancel集合和ack有记录，但进程在cancel返回前死亡时不一定有独立pre-cancel request record。已经有durable owned intent/ID且历史文件拒绝自动重启，无法确认时保持风险而非重下单；不阻断首次受控session，精细延迟观测DEFER。未读取或写入真实账户Journal，真实文件系统权限与启动目录仍需实际入口准备时复核。

## 9. Test Results

本轮重新独立执行，existingPython3.12.14 venv、publicdummy key、双MockTransport。使用既有runner保持credential/network guards，禁pytest自动plugins/cache/bytecode；临时数据放 `/dev/shm`，不触及此前有quota问题的 `/tmp`。

实际命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c "import tempfile,runpy,sys,os; tempfile.tempdir='/dev/shm'; sys.path.insert(0,os.path.abspath('tests')); runpy.run_path('tests/rdp_sm_m0_offline.py',run_name='__main__')" tests/test_rdp_sm_m3_h01_handoff.py -q --tb=short

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c "import tempfile,runpy,sys,os; tempfile.tempdir='/dev/shm'; sys.path.insert(0,os.path.abspath('tests')); runpy.run_path('tests/rdp_sm_m0_offline.py',run_name='__main__')" tests/test_rdp_sm_m3_loop.py -q --tb=short

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c "import tempfile,runpy,sys,os; tempfile.tempdir='/dev/shm'; sys.path.insert(0,os.path.abspath('tests')); runpy.run_path('tests/rdp_sm_m0_offline.py',run_name='__main__')" tests/test_rdp_sm_type2_account.py tests/test_rdp_sm_m2_h01_pagination.py tests/test_rdp_sm_m2_h01_commitment.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_execution.py -q --tb=short

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c "import tempfile,runpy,sys,os; tempfile.tempdir='/dev/shm'; sys.path.insert(0,os.path.abspath('tests')); runpy.run_path('tests/rdp_sm_m0_offline.py',run_name='__main__')" tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -k 'not test_cli_conflict_stops_before_settlement_preflight' -q --tb=short
```

| Suite | Executed / Passed / Failed / Skipped | Deselected | Runtime |
|---|---|---:|---|
| M3-H01 | 11 /11 /0 /0 | 0 | 4.33s |
| M3 incl full market | 22 /22 /0 /0 | 0 | 67.65s |
| Type2/M2-H01/H02/execution | 99 /99 /0 /0 | 0 | 19.31s |
| M1/F01 + relevant FL | 346 /346 /0 /0 | 1 | 6.57s |
| **Total** | **478 /478 /0 /0** | **1** | |

M1/F01为64项，selected FL为282项；Type2为7项、M2为92项。M0三个findings的实际containment回归保持通过。额外CLI help与23条Journal读回诊断通过，不加入478pytest计数。

每个offline suite输出 `0 network/DNS attempts; workspace credentials denied`。真实账户只读诊断是独立进程，不把实际凭证传入offline测试；测试没有authenticated real trading writes。全轮零真实订单、零真实取消。没有改测试强行PASS。

1项既有justified deselection：`test_cli_conflict_stops_before_settlement_preflight`；CLI的dotenv discovery会在expected ConfigAliasError前触及受保护workspace.env。继续排除，未关闭credential guard，未将deselected算作passed/skipped。

Ruff no-cache检查、git diff --check和shared-core quiet diff通过。原FL生产共享Gateway/Tracker/FillTruth/Journal/config/Engine/favorite_live没有被覆盖或修改。

## 10. Actual Blockers

| ID | Classification | Concrete evidence | Smallest next action |
|---|---|---|---|
| READY-B01 | BLOCKER | CLI只有rdp-sm-preflight，未连接真实SMTradingLoop | 后续明确授权一个SM CLI入口，复用现有runner/executor；本轮不实现 |
| READY-B02 | BLOCKER | M3.run只允许offline=True且双MockTransport；没有可在人工授权后启用且限定market/$30的live Gate | 最小live admission：default false、显式人工范围、one exact BTC市场/$30/BUY/post-only；保留全部原safety checks |
| READY-B03 | BLOCKER | fresh官方记录仍有3个MATCHED；两条有当前Funder maker leg，一条账户TAKER；targeted trade仍非终态，related order验证失败 | 先取得可信official终态/账户风险解释或证明历史记录对本次资金无风险的只读证据；不得把失败/陈旧时间/空列表当作解除 |

READY-B03是“必要账户风险未核清”，不是断言这些历史记录必然仍占用某个金额；本轮没有构造新资金模型或认定需要自动恢复。没有将Type2liveauthority未验证本身作为blocker。

## 11. Deferred Items

- Type2当前Safe实盘签单权限、第一笔真实Maker订单、真实fill和cancel延迟：NOT_VERIFIED，需要后续明确人工授权的MicroLive验证；不是本轮自动FAIL条件。
- Journal更精确的wire/cancel latency记录、完整M4、自动恢复：不妨碍已有durable intent/owned-ID/fail-closed与首次单session审计；不在本轮实现。
- Merge/Redeem、返佣、盈利能力、多市场：不属于首次MicroLive必需范围；不据此拒绝PASS，不开发。

无新增通用Executor/Ledger/数据库建议，无未复现理论问题充当blocker。

## 12. Final Decision

**MICROLIVE_READY: NO**

库级M1/M2/M3真实组件离线执行、Type2identity、资金限制、时间、close、Journal和478项回归均通过。现状仍缺少生产CLI→M3与未来可人工授权的真实transport入口，并且fresh相关成交风险无法可靠核清，所以不能标记READY_FOR_HUMAN_AUTHORIZED_MICROLIVE。

未授予或执行真实交易；不为获得PASS修改代码、降低安全标准或扩大开发范围。

## 13. Release Decision

**DO_NOT_PUSH**

**GITHUB_RELEASE: NOT_PUSHED**

用户GitHub发布授权以Readiness YES为条件，本轮未满足。没有暂存、commit、checkout/create release branch、annotated tag、push或远端修改。保留原开发分支和既有未提交交付成果。

Requested Release Branch: `rdp-sm-microlive-candidate`（本地检查不存在；未创建）。

Requested Release Tag: `rdp-sm-microlive-candidate-v0.1.0`（本地检查不存在；未创建）。

Release Commit: **NOT_CREATED**。Release Branch/Commit/Tag verified GitHub URL: **N/A**。Origin配置识别不等同合法push权限或远端发布成功，本轮不作该声明。

Next Action：最小单市场live-entry/authorization任务，和只读核清已观察的3条历史MATCHED风险；完成后重跑Readiness。不要开始M4、真实订单或冻结SOP修改。

## 14. Change Manifest

仅新增：`docs/development/RDP_SM_MICROLIVE_READINESS.md`。

最终SHA256比较确认：174个入口既有文件全部不变，changed0、deleted0，仅新增本报告。git branch/HEAD不变、staged diff为空、git diff --check通过、14个必需章节齐全；shared-core quiet diff通过。无production/test/config/SOP/历史报告修改或删除，无未知文件清理。

Actual official account observations仅输出脱敏身份与必要aggregate；原始账户响应/订单ID/交易ID、private key、API key/secret/passphrase、签名材料未写入项目文件或Git。Offline日志/Journal仅在外部临时内存盘创建和清理，没有新增项目数据库/log/research数据。

本轮 **SHARED CORE CHANGES: NO**。GitHub发布未执行。

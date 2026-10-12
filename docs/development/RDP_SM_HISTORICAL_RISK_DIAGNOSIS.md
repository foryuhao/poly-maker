# RDP-SM Historical Risk Diagnosis

诊断日期：2026-10-12。项目：`/home/ubuntu/poly-maker`。基线：当前 RDP-SM CLI Logging 工作区。角色：Diagnostic Engineer。

## 诊断结论

```text
ROOT_CAUSE: CONFIRMED
ACTUAL_BLOCKING_RECORD:
  /home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d.jsonl:1-3
  Session ID: 78f615c16be44892874bb33b30e44fd5
  Market ID: 0xd1ead55ae7813a89ee9a8e531874253593d8fbb8107ff2a4c450c0b8645587cc
  Actual predicate: locked account SM journal st_size != 0
CAPITAL_DIFFERENCE: CONFIRMED — 10.15 USDC retained historical pending-cost reserve
DECISION: MINIMAL_FIX — proposal only; production code unchanged
```

根因是 `RiskJournal` 把“账户级 SM Journal 非空”一律作为历史风险阻断，没有检查历史 Session 是否真的提交订单、存在成交或留下未解除的 SM 承诺。实际文件包含一个因 `BOOK_SIDE_MISSING` 停止、零提交、零成交、最终 `SAFE_TO_CONTINUE` 的 Session；它仍使后续启动失败。

该 Session 另有 **10.15 USDC 外部历史交易成本预留**。零 SM 提交不代表账户没有历史交易风险。修复建议必须保留这项预留及现有账户风险验证，不宣称历史交易已经确认、订单已终结或资金可释放。

## 1. Blocker 生成位置与调用路径

唯一错误码生成位置：`src/polymaker/rdp_sm_execution.py:117`，组件为 `RiskJournal.__init__`。

```python
super().__init__(directory, day=f"rdp_sm_{funder.lower()}")
fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
if os.fstat(self._fh.fileno()).st_size:
    raise RuntimeError("HISTORICAL_SM_RISK_REQUIRES_MANUAL_REVIEW")
```

调用路径：

```text
cli.rdp_sm_run                              cli.py:62
  -> run_current_market                    rdp_sm_entry.py:198
  -> SMTradingLoop.run_production           rdp_sm_loop.py:258
  -> SMTradingLoop._run
     -> run_preflight / M1 READY
     -> SMWindowExecutor.open              rdp_sm_loop.py:327
        -> run_preflight / M1 再检查
        -> reads.connect_account / _execution_identity
        -> RiskJournal(...)                rdp_sm_execution.py:504
           -> 账户级同名 JSONL / exclusive flock
           -> st_size != 0 / RuntimeError  rdp_sm_execution.py:116-117
     -> except: result.stop_reason=str(exc) rdp_sm_loop.py:342-343
  -> CLI 人类日志 / RunResult JSON / exit 2
```

`Journal.__init__` 用 append 模式打开文件；`RiskJournal` 获得账户文件独占锁后读取文件长度。这里没有 `json.loads`，没有历史 Session 风险分析，没有读取 SQLite、FL Journal、历史 trade status、Order IDs 或资金承诺。锁冲突产生 `BlockingIOError`，与此次错误码不同。

文件名绑定 Funder，未绑定 Session/Market UUID。因此换市场或新 Session ID 仍命中同一文件。当前目录 `journal` 是指向 `/home/ubuntu/poly-maker-data/journal` 的符号链接，`config/config.toml:50` 配置 `journal_dir = "journal"`。两个持久化 Journal 根目录中只发现这一个 `rdp_sm_*.jsonl`；`livecfg/journal` 中没有另一个 SM ledger。

严格地说，程序没有把三条记录分别“判断为风险”：**整个非空文件**就是拒绝对象。第一条 `sm_session` 已足够满足拒绝条件，另外两条继续使文件非空。

## 2. 实际历史 Session 与逐条证据

只读读取该文件的全部三行，全部 JSON 解析成功，全部属于同一 Session：

- Session ID：`78f615c16be44892874bb33b30e44fd5`。
- Market ID：`0xd1ead55ae7813a89ee9a8e531874253593d8fbb8107ff2a4c450c0b8645587cc`，来自第 3 行 `sm_run_result`；第 1、2 行没有直接存储 condition ID。
- Slug：`btc-updown-5m-1791789000`。
- Session 创建事件：2026-10-12 07:13:52 UTC，15:13:52 Asia/Shanghai。
- 文件大小：2244 bytes。
- SHA-256：`7caf4e9530487523043485e50b92cd3491cbc4a13555051c4dbbc8b976956948`。

| 来源行 / Kind | Session ID | Market ID | Order IDs | Submitted Orders | Confirmed Fills | Pending/Unknown Orders | Unresolved Financial Commitment | Actual Rejection Condition |
|---|---|---|---|---:|---:|---|---|---|
| 1 / `sm_session` | `78f615c16be44892874bb33b30e44fd5` | 上述 CID，由行 3 关联；本行仅存 slug | 无；observed order IDs `[]` | 0，按完整 Session 证据 | 0 | 无 SM pending/unknown；observed trade IDs `[]`，pagination failure `null` | SM 新承诺 0；外部历史成本预留 10.15 USDC | 文件非空，不检查本行内容 |
| 2 / `sm_close_barrier` | `78f615c16be44892874bb33b30e44fd5` | 上述 CID，由行 3 关联 | 无；`proofs={}` | 0，按完整 Session 证据 | `confirmed_fills=[]` | 无 SM pending/unknown | SM 新承诺 0；本行不解除外部历史预留 | 文件非空，不检查 `SAFE_TO_CONTINUE` |
| 3 / `sm_run_result` | `78f615c16be44892874bb33b30e44fd5` | `0xd1ead55ae7813a89ee9a8e531874253593d8fbb8107ff2a4c450c0b8645587cc` | 无；`windows=[]` | 0，按完整 Session 证据 | `confirmed_fills=0` | 无 SM pending/unknown | SM 新承诺 0；外部预留保持 | 文件非空，不检查 stop reason / closure |

完整 Session 的交叉证据：

- Kind 计数：`sm_session=1`、`sm_close_barrier=1`、`sm_run_result=1`。
- **没有任何** `sm_placement_intent`、ACK、placement result、UNKNOWN、order、trade、window result 或取消事件。
- `sm_run_result.total_windows=0`、`active_cycles=0`、`windows=[]`。
- `confirmed_fills=0`、YES/NO=`0/0`、Delta=`0`。
- 原始停止原因：`BOOK_SIDE_MISSING`；`execution_status=BLOCKED`。
- 实际关闭事件与最终结果均为 `SAFE_TO_CONTINUE`，关闭 `proofs={}`、`confirmed_fills=[]`。
- preflight observed order/trade IDs 均为空，`preflight_account_incomplete=null`。

订单 POST 的生产路径先同步持久化 `sm_placement_intent`（`rdp_sm_execution.py:1184`），再进入实际下单调用。此文件既没有 intent，又有完整安全关闭与零窗口结果，支持这个 Session 没有进入下单流程。不能仅依据 `execution_status=BLOCKED` 或没有 ACK 判断零风险；这里使用了完整事件集和关闭证据。

`rdp_sm_loop.py:356` 对“异常停止但关闭验证安全”仍设置 `execution_status=BLOCKED`。因此这个标签表示本次任务未正常完成，并不等价于留下未解决金融暴露。当前 Journal 检查连这个标签都没有读取。

### 后续两次相同 Blocker 与更早多次 Session

用户提供的两次运行现象与该路径一致：M1 可以 READY，之后同一账户文件非空导致 executor 打开失败；`self.executor` 尚未赋值，`finally` 不追加 `sm_run_result`（`rdp_sm_loop.py:359`）。所以反复失败不会增加此文件记录，也不会到达下单路径。

**后续两次启动的 Session ID、Market ID 和完整 stdout/stderr：NOT_VERIFIED。** 现有 SM ledger 没有保存这些 executor 打开前失败的 Session，也没有找到相应持久化 CLI 运行记录。不能将历史 Session ID 冒充后续两次运行 ID。更早“多个无订单 SM Session”的逐次明细同样没有足够现有记录，未据此扩展结论。

## 3. 与三条历史 MATCHED 的关系

三条交易在行 1 的 `historical_matched` 中被保存为外部历史证据：

| Trade ID | 原始状态 | 原始 policy decision | Retained Cost | Redeem / Settlement |
|---|---|---|---:|---|
| `cba74eeb-a251-4ac2-abfb-f9679f7b3521` | `MATCHED` | `HISTORICAL_MATCHED_NONBLOCKING` | 3.250 USDC | `NOT_VERIFIED` |
| `38287f2c-d6a8-4b1a-8d4d-a3d5d5495a00` | `MATCHED` | `HISTORICAL_MATCHED_NONBLOCKING` | 3.50 USDC | `NOT_VERIFIED` |
| `461cd979-8859-4b5f-9336-90fff7fc3564` | `MATCHED` | `HISTORICAL_MATCHED_NONBLOCKING` | 3.400 USDC | `NOT_VERIFIED` |

这些交易与本次**资金预留**直接有关，与本次**Journal 错误码判断**没有直接关系。删除 MATCHED 字段而保留其余 JSON，甚至保留一个空白行，仍满足 `st_size != 0`。不应因 `MATCHED` 标签本身阻断，也不应将其升级成 confirmed fills。

`AccountRiskPolicy.inspect`（`rdp_sm_entry.py:85-195`）是独立风险组件：按完整 trade 分页读取未确认交易，验证账户 BUY 归属、不同市场、唯一历史 market、官方 closed/resolved/not accepting orders、可靠时间、token 映射、没有相关当前/已观察订单或正持仓，最后累计 `price * quantity` 预留。身份不明、当前市场 pending、未解决市场或相关承诺出现时仍拒绝。当前持久化行记载这三条已经被此 policy 接纳为 nonblocking，并留下全部成本。

原有 `RDP_SM_MICROLIVE_READINESS_V2.md` 第 5–6 节记载当时 GET-only 检查：230 trade rows、224 CONFIRMED、3 FAILED、3 MATCHED，历史 market 已 resolved，但相关旧订单读取不可用。本轮把这些视为既有报告证据，未重新请求交易所或宣称旧订单已终结。

这三个 Trade ID 的原始 Market IDs、Order IDs、完整成交 leg 不在现有 SM 文件的 `historical_matched` payload 中；两份本地 SQLite 的 `fills` 表未找到它们，抽查的既有 FL `journal/live.jsonl`、`logs/favorite-first-live.jsonl`、`logs/filltest.jsonl` 也未匹配到这些 ID。因此其原始订单终态、最终确认、Redeem 及原始经济 leg 独立复算：**NOT_VERIFIED**。这些缺口不改变非空文件阻断的已确认根因，也不允许释放 10.15 USDC。

## 4. Capital Reconciliation

此处比较了两种不同口径，现有证据没有表明官方余额下降了 10.15 USDC：

```text
starting_official_balance_usdc          = 36.064169
historical_pending_cost_reserved_usd    = 10.150000
starting_spendable_usdc                 = 25.914169

36.064169 - (3.250 + 3.50 + 3.400)       = 25.914169
```

以上金额在同一条实际 `sm_session` 事件中同时存在。`rdp_sm.py:382-409` 计算：

```text
reserved = official open BUY notional + historical_reserved
available_balance_usd = max(0, official balance - reserved)
basic_spendable_budget_usd = min(30, available balance, allowance - reserved)
```

`rdp_sm_logging.py` 的 Available Capital 使用 `available_balance_usd`。它是减去预留后的可用预算。行 1 的 10.15 USDC 恰与上述三条成本相等；当前 Session 没有新增 SM placement。M2 的 snapshot 也保留这项 reserve（`rdp_sm_execution.py:731-740`）。

旁证：`logs/settlement/20261012T070737952750Z/sweep.json` 保存 07:07:37–07:07:45 UTC 的 starting/ending collateral，均为 `36.064169`；positions before/after 与 actions 均为空，recovered=`0.000000`，status=`NOTHING_TO_DO`。这条旁证不是当前实时余额证明。

**CAPITAL_DIFFERENCE 已确认是上述预算计算中的 10.15 USDC 保守预留。** 它可能与已经体现在官方余额中的历史花费重叠；现有证据不足以确认是否重复预留，不能认定这 10.15 是新冻结资金、两次 SM 扣款、已损失资金或可释放余额。本轮未重新读取账户；两次后来运行的原始官方余额和截至本轮的最新余额均为 NOT_VERIFIED。

## 5. Decision 与最小修复方案（未实施）

**DECISION：MINIMAL_FIX。** 修复范围仅是账户级 `RiskJournal` 对完整、零交易、已安全关闭历史 Session 的拒绝。仍保持账户 ledger、独占锁、同步 durable intent 和既有 M1/M2/账户风险验证。

建议在已获得同一文件锁后，用一个严格且范围受限的历史分类检查替换“一律非空拒绝”。首版只接纳本次证据形态：

1. 完整逐行解析、严格识别 schema、Session 归属和记录顺序；不能有截断、未知 kind、重复/冲突 Session 元数据或未结束 Session。校验文件内账户身份与当前 Funder 一致，并核对相关历史 Market 身份。
2. 每个历史 Session 都有 `sm_session -> sm_close_barrier -> sm_run_result` 完整闭合证据；本次这种零交易形态可用极窄 kind 白名单实现。全部关闭屏障为 `SAFE_TO_CONTINUE`，result 与关闭记录一致，窗口、订单、成交和 SM 暴露均为空/零。
3. 任意 placement intent（即使 `post_started=false`）、ACK、UNKNOWN、order/trade、非空 proofs/windows/fills、preflight observed order/trade、pagination failure 或不完整关闭均继续 Fail-Closed/manual review。首版不实现已有真实交易 Session 的重启恢复，不以 terminal 标签、open orders=0 或单个安全结果自动放行它们。
4. 保留原 Journal 所有字节，审查通过后仍 append 到同一账户 ledger；不删除、truncate、改名绕过或按新 Session/Market 切换风险账本。现有 `sequence=0` 也需从已校验记录数初始化，避免跨 Session journal reference 重复；核对历史中实际使用的引用约定。
5. **外部历史 reserve 必须延续。** 不能把“零 SM 新承诺”当成“账户无预留”。启动仍运行新鲜 M1 和 M2 风险读取，并对 ledger 中保留的三个 Trade ID/成本逐项核对，确保 10.15 USDC 不因新 policy 实例或某次 trades 快照遗漏而消失，成本也不能因多 Session 重复存储而重复累计。当前 payload 缺原始 market/order/leg：若新鲜 evidence 缺失、矛盾或不能与旧保留证据对应，继续 Fail-Closed；最小补丁不增加资金释放逻辑。

该方案只建立“这个已结束 Session 没有新增 SM 订单风险”的窄例外。外部历史 pending 保留 10.15 USDC，相关当前持仓/订单、未确认经济事实、分页问题仍受原有风险规则保护。不把 `execution_status=BLOCKED` 当作金融风险证据，也不把 `MATCHED` 标签本身当作拒绝依据。

后续实现应覆盖必要反例：本次真实零订单文件可分类通过；仅有 session/缺结尾、未知或截断记录、任意 intent/UNKNOWN/成交/观察承诺、blocked barrier、账户冲突继续拒绝；锁仍阻止双 writer；追加后全部旧字节保持；历史 trades 缺失/变化不能释放 10.15，跨 Session 不能重复预留。这些是未来补丁验收要求，本轮未新增生产测试或实施修复。

## 6. 本轮验证与范围

执行了一次无交易、无网络的离线因果复现：AST 提取当前 `Journal` 和 `RiskJournal` 两个原始类，在临时目录用 dummy Funder 执行构造器，不导入 app、SDK 或配置，不读取凭据，不构造订单客户端。真实历史文件只读，所有构造器操作均在临时副本上。

| 临时输入 | 实际结果 |
|---|---|
| 空文件 | OPENED |
| 原始 2244-byte 零订单 Session 副本 | `RuntimeError:HISTORICAL_SM_RISK_REQUIRES_MANUAL_REVIEW` |
| 仅原始第一行 `sm_session` | 相同错误 |
| 仅一个换行字符 | 相同错误 |
| 已由第一个 writer 锁定的空文件 | `BlockingIOError` |

每个临时输入的字节在构造器调用前后相同。真实 SM Journal 诊断前后 SHA-256 一致。`tests/test_rdp_sm_m2_execution.py:609` 的既有测试也明确要求一个已 open、未提交的 executor 关闭后仍拒绝 Journal 重开，表明原行为是粗粒度保守 restart refusal；本轮没有运行交易 CLI，也没有靠真实失败启动复现。

本轮只新增此报告及 `/tmp` 中的诊断指纹文件。`src/`、`config/`、`livecfg` 普通配置文件已做字节指纹核对；生产代码和既有工作区改动均保留。没有修改 SOP，没有删除/清空历史 Journal，没有订单提交、撤单、Merge/Redeem、资金释放、Git commit 或 GitHub push。修复尚未实施，当前生产启动仍会被同一非空 Journal 阻断。

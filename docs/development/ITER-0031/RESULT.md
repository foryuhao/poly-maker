# ITER-0031 — RDP-SIZE-001

任务：Configurable BaseSize + Favorite TargetDelta。日期：2026-10-15。
分支：`rdp-size-001`。开发基线：`68deb7a1c2d277a94e915fd6a60e97df9804b2d7`。
最终状态：**BLOCKED**（实现完成、独立Review通过；ACCEPTANCE-09因13项既有失败未满足）。未运行 MicroLive、真实下单、撤单、账户检查或 RPC，未读取工作区凭证，未修改 `config/`、`livecfg/`、`.env` 或历史数据。

## Architect 与授权边界

先阅读实际源码、RDP_PHASE0_FROZEN、当前基线、M2/M3、Capital20/Merge、MicroLive Readiness v2、最近 RDP-RISK-002 RESULT 及现有单市场测试。
本仓及适用父目录没有 AGENTS.md；`docs/development/ITERATION_INDEX.md` 不存在。已有数字迭代最高为 ITER-0030，本轮使用下一编号 ITER-0031，关联 RDP-SIZE-001；未重建旧 Agent 框架或虚构缺失规范。

实际 HEAD 与原任务描述有差异：NORMAL 截止 T120，状态选择先对绝对确认 Delta 作 ROUND_HALF_UP 整股取整，再比较 5，因此 4.5 即 REBALANCE。用户随后明确授权：**“保持当前 Timing/状态机，授权仅适配执行器中与 Sizing 绑定的校验”**。本轮保持这两项现状，不改成 T180 或原始绝对 Delta 阈值。

独立 Architect 先确认最小位置；Worker 完成实现；独立 Reviewer 检查并复测。新增纯计算辅助位于 `rdp_sm_sizing.py`，唯一生产报价入口仍是 `make_quotes`，没有第二套 Sizing 引擎或 Inventory Ledger。

当前实现核对：

| 入口/行为 | 实际实现及本轮处理 |
|---|---|
| Base Clip | 原来 make_quotes 中固定5；接入现有 RDPSMConfig |
| NORMAL | 原确认库存 Skew + 原 .01 向上取整；仅接入配置与目标 |
| REBALANCE | 原不足侧 BUY + maker_buy_size；理想缺口改为目标缺口 |
| Delta/状态 | confirmed YES−NO；保留当前 ROUND_HALF_UP 状态函数 |
| 最小量/精度 | 复用官方 meta.min_order_size、既有5股保护、1 USDC及 .01 网格 |
| Capital Gate | 原 committed/basic_spendable_budget/capital revision/POST guard，不变 |
| Reconciliation | 原 SMWindowExecutor/Gateway/Tracker/FillTruth/close barrier，不变 |
| 报告 | 原 RunResult、Journal、HumanRunLog，附加最小 Sizing 证据 |
| 配置/实盘入口 | 原 config.toml 的 [rdp_sm]、原 uv run polymaker rdp-sm-run |

执行器改动限于：启动 Sizing 配置校验、最大数量校验、最终刷新及晚窗口的共享 Sizing 数量绑定、Sizing 日志和新旧报告 schema 兼容。原来晚窗口强制中性量的断言在 Delta+7 时拒绝 NO6/NO8；本轮仍保留数量绑定，改为与同一 make_quotes 的当前合法配置结果逐腿核对，未移除校验。原身份/BUY/post-only/资金/未知订单/撤单确认/窗口生命周期没有放松。

## 配置

均在现有 `[rdp_sm]` 表，数量为 shares，Decimal 解析：

| 参数 | 默认 | 校验 |
|---|---:|---|
| base_size | 7 | 有限正数、.01 网格；开 Session 前满足官方最小量及既有最低量 |
| target_delta | 0 | 仅0或1 |
| favorite_threshold | 0.70 | 严格 0.5 < value < 1 |
| max_order_size | 15 | 有限正数、.01 网格、不小于 base_size |

旧 `matched_remaining_tolerance_shares` 保留。其他策略的 `base_size_usdc` 是 USDC 金额，不能拿来充当本策略的 shares。无第二配置来源；不修改用户配置值。

[CONFIG_A.toml](CONFIG_A.toml) 与 [CONFIG_B.toml](CONFIG_B.toml) 是供人工合入配置副本的 Sizing 表，不是完整账户配置。A=7/0/.70/15，B=7/1/.70/15。旧 BaseSize=5 可显式配置。

## NORMAL 前后对比

原公式 Y=5+max(−d,0)、N=5+max(d,0)，提交量向上取整 .01。
新中性公式只把5替换为 base_size。TargetDelta=0 不读取 Favorite，不改变原 Skew、名义金额跳过规则和数量精度。

TargetDelta=1 时，先计算原 Skew，再仅减少非 Favorite 腿：YES Favorite 减 NO、NO Favorite 减 YES。遵守最小合法量与最大量。遇最大量截断后，若仍能通过减小过量腿更接近目标，则选最近合法 .01 数量；不增加任何腿来追目标。

| ActualDelta | 模式/方向 | 原基础5 | 新基础7 |
|---:|---|---|---|
| 0 | Target0 | 5/5 | 7/7 |
| +2 | Target0 | 5/7 | 7/9 |
| −3 | Target0 | 8/5 | 10/7 |
| 0 | Target1，YES | 无该功能 | 7/6 |
| 0 | Target1，NO | 无该功能 | 6/7 |
| 0 | Target1，NONE | 无该功能 | 7/7 |
| +2 | Target1，YES | 无该功能 | 7/8 |

表中均为 YES/NO。新增最大量是显式硬约束：旧中性订单若超过 max_order_size，会被截断或因最小合法量大于上限而跳过；这不是 Favorite 引起的算法变化。历史 parity 在最大量未约束的合法域成立。旧无上限回归 fixture 显式设置 base5/max1000，保留全部原断言；新增默认7/默认15有独立生产入口及集成测试。

## REBALANCE 与 Fallback

价格保持 BestBid。不足侧不变。理想量为 `abs(d) − sign(d) × SignedTargetDelta`，然后复用现有 maker_buy_size，再限制 max_order_size。目标0在最大量未约束时与原公式相同。

| ActualDelta | SignedTarget | 下单 |
|---:|---:|---|
| +7 | +1 | NO6 |
| +7 | 0 | NO7 |
| +7 | −1 | NO8 |
| −7 | +1 | YES8 |
| −7 | −1 | YES6 |
| +5 | +1 | 理想NO4，官方最小5时 NO5，最终0 |

最低合法量沿用 `max(既有5股保护, 官方动态最小量, 1 USDC / 实际报价价格)` 并向上取整 .01，没有新增永久硬编码5。NORMAL 不增大原腿绕过名义金额不足；目标缩减受最低合法量限制。REBALANCE 最低合法量超过 max 时跳过；合法最小量≤max 时用最接近目标的合法量，必要时分后续原窗口修复。不增新窗口，不 SELL/Taker，不为1股违反安全条件。

计划7/6不代表真实Delta+1。库存继续仅来自 executor.confirmed()；部分成交6计划/3实际后实际Delta+4，按原状态返回 NORMAL。T120 后则按原 Timing 等待，不人为保持 REBALANCE。

## Favorite

使用当前原官方 HTTP 双边 snapshot，不迁移 Observer/研究框架。复用 RDP-RISK 已验证的 Decimal midpoint 思路：p=YES bid/ask midpoint，交叉核验 NO midpoint 与1−p，误差≤.02，双方时间差≤1秒。没有新的预测模型或数据源。

要求同一市场和token身份、双边完整且uncrossed、受支持的官方动态Tick且价位在网格、概率0～1内、时间不在未来且未超过既有配置的行情有效期。原执行器仍先核验官方身份/tick/时间和所有交易安全条件。缺失/过期/不一致时本次 Target=0，不缓存上次方向；基础交易数据不安全仍原Fail-Closed。

仅 p > threshold 时YES；仅 1−p > threshold 时NO；其余NONE。默认 .70 的 YES .70/ .30 均NONE。TargetDelta=0 时 Favorite 完全禁用。提交前原最终刷新使用该时刻盘口重新Sizing，不因方向变化另开交易或取消已提交合法订单。

## 日志、归因与经济统计

`sm_strategy_window`、`sm_quote_refreshed` 和最终 RunResult.windows[].sizing 保存配置、Favorite/有效性、SignedTarget、真实ActualDeltaBefore、计划/实际提交量、真实确认成交、ActualDeltaAfter、约束原因；REBALANCE另存理想/实际修复量、侧、官方minimum、Fallback及修复后真实Delta。实际提交量来自 post_started 的真实 placement，确认量来自权威confirmed fills，after来自原 WindowResult 的实际库存快照。跳过决策的Sizing附加于既有 sm_quote_skipped 事件；原 skipped_quotes 列表 schema 保留。

最终 `sizing_report` 保存 FinalTarget/ActualDelta、Intentional/Unintentional份额及PnL、PairPnL、TradingPnLBeforeRebate、MakerFilledShares/FillCount、SAFE_EXIT_STATUS、WinningOutcome/PredictionCorrect。MakerFillCount沿用真实确认经济成交记录数；不按计划量或MATCHED计成交。

Intentional证据仅在有效非零目标实际改变Sizing、所有计划腿均全部确认、真实窗口前后Delta吻合且安全关闭时建立，最多1股，保存 window/order/trade/实际量。仅方向相同、单侧/Partial Fill不会新建证据。后续反向确认净成交优先消耗证据份额；同向额外成交不会增大Intentional：原1股目标随后额外YES1，最终2股拆为Intentional1/Unintentional1；后续NO.5则证据降为.5。Merge成对减少两侧不改变Delta，沿用原实现。

本Session路径没有正式结算结果摄取，也没有实际返佣归属证明。因此所有正式PnL、实际返佣、FinalWinningOutcome及FavoritePredictionCorrect明确 UNKNOWN，不引入新接口、不用理论返佣填实绩、不把原 theoretical_pair_pnl 当作已实现PairPnL、不把Favorite胜率解释为盈利概率。已有确认成交、成本和锁定配对理论统计保留。

RiskJournal仅显式接受旧schema及附加 sizing_report 的新schema，新增零成交报告字段精确校验；原资金保留、关闭证明与未知订单条件不变。篡改份额、成交计数、SAFE状态或未知报告字段仍拒绝释放风险。

## 修改文件

生产代码：

- src/polymaker/config.py
- src/polymaker/rdp_sm.py（只附带已验证的market/minimum元数据）
- src/polymaker/rdp_sm_sizing.py（新增纯辅助与报告投影）
- src/polymaker/rdp_sm_loop.py
- src/polymaker/rdp_sm_execution.py（仅授权的Sizing校验及报告兼容）
- src/polymaker/rdp_sm_logging.py

测试：新增 tests/test_rdp_sm_sizing.py；既有 test_rdp_sm_m2_execution.py、test_rdp_sm_entry.py、test_rdp_sm_m3_loop.py、test_rdp_sm_minimum_notional.py、test_rdp_sm_state_rounding.py 显式传入历史基础5及原无约束上限，保留原测试/断言。

文档与凭据隔离测试收据均在本 ITER-0031 目录：RESULT.md、REVIEW.md、CONFIG_A.toml、CONFIG_B.toml，以及 evidence/baseline-tests.md、cli-help.txt、frozen-invariants.json、scope-check.json、failure-parity.json、full-tests.txt、lint.txt、sizing-tests.txt。没有更改 Gateway、WS、HTTP接口、Merge/Redeem、确认账本、正式结算模块或生产配置。

## 测试与 Reviewer

新增Sizing测试 **129 passed / 0 failed**；独立Reviewer相关回归 **290 passed / 0 failed**，并亲自复测最新129个Sizing测试。Worker修复后相关历史/最小量/Sizing回归238 passed。新测试覆盖配置A/B/旧base5与非法配置、严格Favorite边界及动态tick、缺失/过期/错market/token/异步/不一致行情、非零库存Skew、双符号Max最近解、REBALANCE各目标和minimum fallback、单侧及Partial Fill、真实T200修复、正式生产入口Base7、Capital Gate、伪造factory与超Max拒绝、Intentional建立/消耗/不放大、报告篡改历史拒绝。

完整最终全仓：**1500 passed / 13 failed / 2 skipped，111.08秒**，收据：`evidence/full-tests.txt`。与开发前失败node逐项相同，新增失败0；核对见 `evidence/failure-parity.json`。开发前基线见 `evidence/baseline-tests.md`：1371 passed / 13 failed / 2 skipped。失败包括10个既有CLI JSON stdout污染、1个凭证隔离保护触发、2个历史Shadow冻结断言。没有删除、跳过或修复这些无关测试。

所有pytest通过已有 tests/rdp_sm_m0_offline.py 隔离：0 network/DNS attempts，workspace credentials denied；实时测试默认2 skipped，没有启动实盘。更改文件Ruff、compileall、git diff --check通过；原uv入口--help经uv run --offline --no-sync验证。Gateway、Merge、WS/HTTP解析器、entry/recovery、冻结SOP及Timing/状态函数逐字节或AST未变证据见 frozen-invariants.json。原控制台收据只规范化行尾空白。

独立Review详见 [REVIEW.md](REVIEW.md)：**PASS_IMPLEMENTATION_WITH_BASELINE_TEST_BLOCKER**。未修订相关策略冻结文档；实现Review通过不代表全仓验收通过。

## MicroLive 操作说明

本轮未执行；验收阻塞解决并获得人工明确实盘授权前不启动。

人工可将本目录 CONFIG_A/B 的 `[rdp_sm]` 表合入独立的现有配置副本，不覆盖账户/路径/其他原配置。检查默认base7、target0或1、threshold.70、max15及官方动态最小量；需要历史对照时base5。官方minimum高于base时启动拒绝，不偷偷调大。

已有入口仍为 `uv run polymaker rdp-sm-run --config-dir <人工准备的配置目录>`，每次只发现并处理当前一个BTC5m市场，结束退出。CLI没有新增参数；可用既有 `--json-only` 或 `--shadow`，本轮没有调用交易命令。人工授权后的首次验证建议A、B各单市场，核对实际Sizing、确认成交、单侧敞口、close barrier和UNKNOWN经济字段；不得把未结算PnL当盈利。当前T120截止NORMAL、T300停止新BUY均保持。

COMMIT：由包含本报告的Git提交记录确定，最终终端另列精确hash。


## 最终验收

- ACCEPTANCE-01～08：PASS。默认7/0/.70/15，可配置旧base5；Favorite/Skew/修复/合法量与归因已实现并测试。
- ACCEPTANCE-09：BLOCKED。新增129项全通过，原有13项失败逐项复现，全仓没有达到全部通过；未删除或跳过这些测试。
- ACCEPTANCE-10：PASS，按用户追加授权适配Sizing绑定校验及报告兼容，核心Timing/Pricing/状态/资金/执行/外部接口不变。
- Reviewer：PASS_IMPLEMENTATION_WITH_BASELINE_TEST_BLOCKER。
- STATUS：BLOCKED。
- MICRO_LIVE_EXECUTED：NO。
- MICRO_LIVE_READY：NO，等待全仓验收处置与人工授权。

未自行修复无关CLI/历史审计，未启动交易。完成后停止。

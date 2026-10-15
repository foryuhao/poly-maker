# ITER-0034 — Favorite Clock Precision Fix

日期：2026-10-15。关联：RDP-SIZE-001。
基线：`2dd77dabd002fbd71fb34634f7f13ff6653039d4`，分支 `rdp-size-001`。
任务：用户报告最近两个市场 TargetDelta 仍未生效，核对实际决策、提交与确认成交并修复原因。
遵循用户最近要求：未调用 Architect/Reviewer。未执行真实交易或 MicroLive。

## 实际两个市场

固定分析用户请求到达时最新的两个已完成 Session，时区 Asia/Shanghai：

| 市场开始 | Slug | Session | 实际配置 | 最终报价Favorite有效 | 最终确认Delta |
|---|---|---|---:|---:|---:|
| 2026-10-15 15:45 | btc-updown-5m-1792050300 | c61688a846204e5b9a8f5717ee48f85c | TargetDelta=1 | 1/13 | +7 |
| 2026-10-15 15:50 | btc-updown-5m-1792050600 | a704dbf74aac40b78a77a8d578c378b2 | TargetDelta=1 | 0/15 | 0 |

两场初次策略决策分别13/13、15/15为 INVALID；最终刷新报价分别12/13、15/15为 INVALID。
INVALID 回退 SignedTarget=0，因此大部分实际数量沿用中性Sizing。
第一场w5最终刷新曾得到 NO Favorite=0.865、SignedTarget=-1、NO修复8股，已ACK，
该订单的确认成交为0股；不能把已提交8股当成已达到目标库存。
第二场w12 YES midpoint=0.885、ActualDelta=-7，实际仍买YES7；目标有效时理想修复应为YES8。

证据：[recent-two-markets.json](evidence/recent-two-markets.json)。仅本地只读，
包含两个源文件SHA-256及读取前后字节相同检查。未请求真实账户、行情或正式结算。
旧日志没有被更改或补写，PnL和结算结果不作推断。

## 根因与复现

原Favorite要求 `0 <= estimated_now - book_timestamp <= max_age`。
官方 `/time` 在这两个流程中表现为整秒时钟，盘口 `/book` 带毫秒。
执行器已有最多2秒的时间差保护；Favorite另外使用零未来时间差要求。
整秒截断，以及官方时间响应从生成到收到的耗时，使最新盘口可略晚于该估计时钟，
于是执行器接受的可用盘口被Favorite判为 INVALID。

真实日志没有记录每次Favorite调用的完整时钟输入或失败细项，不能精确还原每次失败的偏移量。
但该差异已由完整生产流程的离线 HTTP 复现证明：

- 整秒官方时间与 `.65` 秒盘口：修复前 YES/NO Favorite 两个用例都错误下7/7。
- 时间响应跨秒后才到达：仅考虑1秒截断还不够，两个用例再次复现错误7/7。

红测试原始记录：[repro-before.txt](evidence/repro-before.txt)、
[repro-rtt-before.txt](evidence/repro-rtt-before.txt)。两个进程均0 network/DNS attempts。

## 最小修复

只修正 Sizing 的时间有效性解释，不修改交易Timing或等待节奏：

1. 复用既有官方时间读取，在该调用前后记录单调时钟耗时 `d`；不增加任何HTTP请求或等待。
2. 给既有 `make_quotes` / `resolve_favorite_target` 传入官方时间及该次读取耗时。
   整秒观察的精度区间为 `[t, t+1)`；考虑接收耗时后，其当前上界为
   `estimated_now + 1 + d`。带小数的官方时间不加1秒，仅计其读取耗时。
3. 上界严格排除，超过可解释时钟区间的未来时间戳仍 INVALID；没有官方时钟证据和耗时输入时，
   原纯函数的零未来容差规则保持。非法时钟、负数/NaN/Infinity耗时拒绝。
4. `max_age` 沿用现有配置及原比较规则，未扩大过期上限；身份、双边完整性、Tick合法性、
   双边时间差≤1秒和 midpoint 合计误差≤0.02均保持。
5. 增加 `favorite_data_reason`、时钟精度/读取耗时/不确定区间/最大盘口超前量到既有Sizing证据。
   HumanRunLog 的原Sizing行追加 Reason，不建立新报告框架。

该区间只解释当前已收到的官方盘口时间精度，不读取未来市场数据或推断方向。
原执行器的时间新鲜度检查和市场截止检查均保留，仍先于提交生效。

未改变：Base7/Target0默认、当前运行Target1、阈值严格>0.70、库存Skew、6/7/8修复公式、
最小数量、精度、最大量、Capital Gate、post-only、Partial Fill、UNKNOWN、撤单、
Reconciliation、Session独立性、Merge、状态机、NORMAL T120/REBALANCE T300、3秒窗口和Pricing。
TargetDelta=0在进入时钟/盘口Favorite校验前直接返回DISABLED，数量算法保持原行为。

## 离线验证

Sizing专项最终 **165 passed**，其中本轮增加36项，未删除已有测试。
新增覆盖：整秒+毫秒、正常读取延迟、跨秒响应、YES/NO NORMAL 7/6及6/7、晚窗口修复6/7/8、
精度区间严格边界、精确时钟未来数据拒绝、过期拒绝、非法时钟/耗时及Target0旧Skew parity。
相关纯函数与生产入口运行真实 M1/M3/M2/SDK/Gateway/Tracker，外部HTTP和时间模拟。
原有缺盘口、过期、未来数据、双边不一致和动态Tick测试保留且通过。

Sizing原始结果：[sizing-tests.txt](evidence/sizing-tests.txt)。
全仓最终 **1580 passed、13 failed、2 skipped，112.98秒**；13个失败的完整测试ID集合
与 ITER-0033 完全相同，无新增失败。仍为1项凭证隔离冲突、10项既有CLI日志污染JSON、
2项旧源码冻结审计。未跳过、删除或修改这些失败测试。
Ruff、编译、Git空白检查通过；移除Sizing时钟元数据后的M2/M3及make_quotes AST与基线一致，
其余Sizing函数、Gateway、状态Tracker、配置、Merge与当前账户检查源码保持不变。
原RDP-SIZE-001全测试验收仍为BLOCKED，修复实现完成。
全仓最终结果：[full-tests.txt](evidence/full-tests.txt)，验收及校验收据见
[verification.json](evidence/verification.json)。专项与全仓不累加。

记录中的盘口另做离线整秒精度模型重放：第一场12/13、第二场14/15快照通过，
各有1条因双边概率不一致继续拒绝。模型时钟取日志记录时刻向下取整，
**不是当时实际调用时钟的还原，也不是成交反事实或盈利预测**；证据明确标注该限制。

## 文件与操作

- `src/polymaker/rdp_sm_sizing.py`：时钟精度与读取耗时区间、具体有效性原因。
- `src/polymaker/rdp_sm_loop.py`：给唯一Sizing路径传入已有官方时钟元数据。
- `src/polymaker/rdp_sm_execution.py`：仅记录既有时间读取耗时，传给Sizing；核心Gate不变。
- `src/polymaker/rdp_sm_logging.py`：原Sizing日志追加Reason。
- `tests/test_rdp_sm_sizing.py`：36项新增覆盖与既有修复用例精度扩展。
- 本报告及 evidence 六份记录。

用户授权的两处 target_delta=1 配置保留，不纳入本次代码提交；没有修改生产环境配置。
未读取 `.env`/`.aws`、未启动交易/Shadow/MicroLive、未查询真实账户、未执行Merge/RPC。
下一次由操作者启动的进程才能加载新代码；没有声称修复已经完成新一轮实盘验证。

ARCHITECT/REVIEWER：NOT_USED（用户要求）。MICRO_LIVE_EXECUTED：NO。
COMMIT：由包含本报告的提交确定，最终回复给出hash。

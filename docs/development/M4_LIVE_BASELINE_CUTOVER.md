# M4 live and baseline cutover

## 启动超时跨过 T15 后继续接入本场 — 2026-11-13

用户要求修复：账户身份TLS超时、时钟超时使准备越过T15后，旧代码一直等待下一
完整场，损失了剩余入场窗口。实盘新场现在在发现时和最后时钟复核时均以T240
为最晚准备边界；T15只保留为最早交易时间。官方秒级时钟的不确定性和请求RTT
使用整数微秒计算，保守上界达到T240则不新开本场；准备途中跨场时丢弃旧准备，
重新发现当前场。已有账本仍按原恢复规则查清所属订单，T240后不重启买入。

晚接入的新场创建全新行情/信号状态，只能从恢复后的有效行情重新积累信号，
不重放或补造离线期间信号。价格、5/10 USDC预算、最小量、UNKNOWN、网络和
停机门槛均不改变。基线的新场完整观察逻辑不变，且基线仍按用户要求停用。
启动时保存 `startup-admissions/<slug>.json` 的首次及最近准备记录，包含
`NEW_MARKET_BEFORE_T240_V1`、准备时刻、保守场内时间、晚启动/观察缺口及失败次数。
该记录独立于交易账本，不清空原事实；`startup-status.json` 的READY同样带运行时
标识，可在用户重启后确认新版本实际加载。本次代码修改不自动重启实盘。

## 已准入 FAK 直接执行与金额精度 — 2026-11-13

用户明确要求修复行情积压导致已准入信号不 POST。信号生成仍使用原行情质量检查；
持久执行任务不再因本地行情积压超过20ms、盘口暂时过期/为空或本地Best ask高于
原上限而拒绝发送。交易所按原锚定价＋1 tick的固定签名限价撮合；无匹配按既有
零成交规则退避重试。仍核对官方tick、价格网格、签名、现金/含费预算/每场风险、
UNKNOWN、网络保护、人工/余额停机及T240原生发送边界，不提高价格上限。

本轮真实 FAK 回执拒绝了0.92×5.4=4.968 USDC：立即BUY的USDC金额最多2位小数。
用户选择保留0.1份步长、接受少买。因此每次初单/补单在剩余含费预算内向下寻找
同时满足0.1份步长及本金分精度的最大数量，不独立舍入两条签名金额改变有效价格。
例：0.92限价改为5份，计划含费4.62576 USDC。最小量仍为至少5份或本金严格大于1。
若固定限价与剩余预算下无合法数量，则记DUST；某些0.001细价位（如0.971）在
5 USDC预算内可能没有满足两种精度的数量，不能为提高覆盖率擅自改变价格或份数步长。
锚定价的参考份数不受POST金额精度过滤，防止用非下单价格提前剔除本可执行的信号。

只对本次精确金额精度错误、空订单身份/状态、无正金额/成交引用且本地签名本金确实
不符分精度的回执认定零成交（平台可能同时给success=true）。冲突或其他不完整回执
继续UNKNOWN。旧账本保留原数量/原回执；新进程可恢复旧精度拒单，T240后只释放占款，
不重新买入。离线恢复仅操作证据副本。本次不自动重启实盘，基线已按用户要求停用。

以上新规则覆盖下文较早描述的“已准入任务发单积压20ms/实时盘口门槛”。

## 共用价格下限调整 — 2026-11-13

实盘与基线继续共用 `config/m4.toml`，设置 `minimum_price="0.50"`，按原信号
锚定价准入：0.50（含）以上可进入执行，0.49 即使加 1 tick 达到 0.50 也不准入。
最高买价继续为锚定价＋1 tick，实际价格改善不受这个信号筛选下限限制。
每信号含费资金目标、每场预算、最小下单量、UNKNOWN 恢复及 T240 限制不变。

不增加实盘专用下限。两个进程均在启动时读取共用配置；新市场把生效下限保存在
原有 settings.minimum_price 字段，同场重启和历史订单核对仍使用该场保存的
下限与指纹。保留历史 0.20 / 0.60 合同的读取能力。此次配置修改不启动实盘，
不重启基线，不清除停机标记；已运行的基线保持启动时加载的 0.20，下次启动
才读取共用的 0.50。

## 实盘 Taker 资金目标任务 — 2026-11-12（当前执行合同）

业务目标：准入信号出现后，在原信号锚定价格附近尽快买入，累计本金及费用
尽量接近 5 USDC。不是达到首次计算的固定份数就结束。

本次只修改实盘专用模块。运行中的基线及其配置、信号文件、共享执行器均不改动，
不重启基线，不清除人工/余额停机标记，不启动实盘。配置仍为 `mode="maker"`；
未来选择 `mode="taker"` 时，新市场使用 `m4-taker-budget-v1`。历史市场按保存的
settings 恢复原合同，避免对已发送订单悄悄改变预算含义。此标识仅用于实盘恢复，
基线身份和新/旧两条 PnL 曲线完全不变。

- 准入前验证原信号；准入后立即持久化并入队，落盘期间原短窗口到期不再丢任务。
  原锚定价和准入 tick 冻结，最高买价为锚定价＋1 tick，严格低于1；
  tick 更新后，只要原限价仍在新的合法价格网格上就继续，不提高最高价。
- 使用 BUY FAK，每个签名订单只 POST 一次。证明零成交后使用新签名重试，
  连续零成交按0.1秒起、最多2秒退避；有成交的可用余款尽快补单。
  ACK不等于成交；超时、404、空记录、缺失明细和证据冲突都不等于零成交。
  官方明确的 FAK 无匹配错误，无论 HTTP 400 还是批量成功响应中的失败项，
  都按零成交处理；错误响应若同时含成交数量、金额或其他冲突证据，仍为UNKNOWN。
  只有已证明零匹配的任务，才可在盘口恢复、价格改善或可成交卖量增加时提前唤醒，
  最短间隔仍为0.1秒，原生发送前照常重验全部门槛。盘口没有改善则保留原退避；
  HTTP 425/429、网络保护及UNKNOWN不会因行情更新而绕过。
  HTTP 425/429 只有在回包没有订单ID、成交量/金额、成交引用或其他接受冲突时，
  才按明确零成交重试。非2xx回包的原始正文及HTTP状态码在分类前持久化，
  避免重启或滞后的零成交查询抹掉正成交证据；恢复时按原状态码重放分类，
  不能把500/408正文中的拒单文字升级成明确零成交。
- 预算分为已确认含费支出、已匹配但待最终确认的占款、正在提交/未查清订单的
  最坏占款。每次按 `5－已确认支出－待确认占款` 和限价实际费率重新定量，
  数量向下取0.1份。初始参考份数只供诊断，不限制累计最终买入份数。
- 完整所属订单证据证明 FAK 已结束，且关联成交明细与匹配总量一致时，
  MATCHED/MINED/RETRYING 记为 SETTLING，按公式及费用容差的稳定上界保留占款，
  可以买入已证明未被占用的剩余资金。待确认匹配不能当零成交，也不记入确认量。
  真正 UNKNOWN 或证据冲突阻止该任务所有新增买单；先查清再补。
- 查询使用独立、有界调度（当前市场最多2个查询任务），不占用 POST 调度；
  不同信号最多2个提交任务并行，同一信号持独立锁串行处理；共享账本仍以事务
  预留每场风险，不为并行任务各给一份10 USDC预算。首单优先于补单，
  所属订单轮转查询以避免饥饿；每个当前POST的应答先落账再查询该单。
  旧 SETTLING 订单的重复查询不重置已排队补单的重试时间，避免结算延迟期间
  高频查询不断推迟零成交后的重试。任务从未知恢复为可补单，或缺失于提交队列时，
  才重新安排提交；重复证据仍更新查询退避，且不会绕过任何发单门槛。
  余额刷新不等待成交查询。原生 POST 前以账本锁再次检查
  UNKNOWN、5 USDC 计划预算和10 USDC每场最坏风险，防止签名后状态变化。
  SDK签名期间旧订单可能确认、失败或重复返回SETTLING，重算任务占款。
  写入新PREPARED订单时，必须在同一事务内重新取得该单所需占款，并以替换后的
  每场总占款校验10 USDC上限；空间不足则不落准备单，下一次按可用资金重新定量。
  这样旧单查询既不会反复阻挡合法补单，也不会留下重启时占款不一致的PREPARED。
  停机和T240清理会等待所有已启动提交任务排空，不取消原生SDK调用。
- SQLite FULL同步提交意图之后，真正调用原生HTTP之前，再检查原限价、盘口、
  资金、最小量、UNKNOWN、停机、时钟和T240发送余量。该检查只豁免本次已落盘
  但尚未发送的精确订单的UNKNOWN，不豁免此前订单的不确定性。若本地拒绝发送，
  写入 `LOCAL_NATIVE_POST_BLOCKED` 证据并转为UNSENT；不能把它计为交易所拒单
  或网络失败。`started/posts` 保留已落盘意图历史，可用该证明区分实际未发送。
  若来不及落盘未发送证明而退出，恢复时仍保守按UNKNOWN处理。
- 查询仅针对本执行器已发送的精确订单及关联 trade ID。订单端点不可用时，
  可使用此前持久化、包含数量/金额/精确成交ID的成功 FAK matched 应答，
  再逐笔核实完整成交明细；证据不足仍保持 UNKNOWN。
  此路径也覆盖HTTP 200返回null或空对象：空结果本身不证明成交或零成交，
  必须同时具备上述完整回执及所属成交明细；非空但身份/经济含义冲突的结果
  不得使用空结果恢复路径绕过检查。
  HTTP 200 的同一所属订单若仍为滞后的 LIVE/DELAYED，也可采用上述最终回执，
  但身份、持有人、限价和原数量必须先一致，查询里的匹配量/成交ID只能是回执的
  子集。终态冲突、额外匹配、金额不符或成交明细缺失均继续阻断补单。
  不完整 POST 回执里的正成交金额/数量也必须保留：缺数量、缺成交ID或回执缺订单ID，
  均不能让后续零成交查询抹掉已有证据。回执与单次签名POST绑定；外来/冲突订单ID
  阻断恢复。回执声明最终 matched 时，成交本金与查询明细须在既定容差内一致。
  即使回执只有 matched 状态、成交ID或交易哈希，也不能被滞后的零匹配结果覆盖。
  实盘恢复统一读取 `tradeIDs/tradeIds/trade_ids` 和三种现有交易哈希字段；别名
  内容冲突、格式错误或单次POST返回多条结果时，保留UNKNOWN及占款，不丢弃证据。
  拒单分类同样检查全部交易哈希字段，拒单文字不能覆盖正匹配线索。完整的别名
  回执可走现有所属成交查询恢复路径；成交最终FAILED仍可依据完整证据释放占款。
  若明确零成交回执已经落盘，但在更新订单状态之前退出，恢复时重放与在线路径
  相同的严格拒单/FAK killed 分类，再释放该次占款。已有正匹配量或关联成交ID时，
  不允许零成交回执覆盖这些证据；T240后的恢复只记账，不重新开启补单。
- T240及官方时间不确定性/发送耗时边界禁止新增 POST；T240至T300继续只读查询。
  后续 Taker 市场按账本轮转补查已结束市场的未决所属订单，包括上轮进程退出
  来不及写 complete 的账本。历史查询客户端禁止 POST，不复活过期买入任务。
  整个实盘进程停止时不运行后台查询；下次显式启动后恢复补查。
- 最小 POST：**数量≥5 shares，或者签名本金严格>1 USDC**。不向上加量凑门槛。
  剩余资金不足以生成合法订单时记 DUST，保留实际支出、剩余资金及原因。
- 沿用用户已接受的边界：计划含费≤5；FAK价优导致增量份数及手续费时，
  实际支出仍可能超过5。发生后立即停止补单；每场及现金准入仍按最坏费用占款。
  本次不改变该已确认例外，也不以 rebate 抵扣预算。

数值与时效：本金/成交量使用百万分之一单位，手续费公式以十万分之一单位舍入。
原始十进制字符串参与成交计算；同 trade ID 的估算费用更新为确认费用时，
使用与解析层一致的公式误差范围。身份、价格上限、签名金额和预算硬条件不放宽。
源时间500ms、未来时间2ms、订单簿200ms、处理200ms、发单积压20ms等门槛不变；
实盘实例用整数微秒计算边界，避免 Unix 浮点相减把恰好2ms误判为超时。
该修正覆盖行情准入、原轮特征、频率候选的生成/保留和发单检查，并有原信号
行为一致性测试；只绑定实盘实例，不修改基线使用的共享信号类。

实现入口：`m4_live_budget.py`（账本）、`m4_live_budget_market.py`（调度）、
`m4_live_budget_reconcile.py`（所属订单恢复）、`m4_live_numeric.py`（数值/时间单位）。
离线测试覆盖资金目标、零成交重试、部分匹配占款、UNKNOWN、重启和确认乱序、
手续费更新、最小订单、最终POST截止、历史只读恢复、所有权限制及行情边界。
网络故障回归使用实际 SDK 签名和 MockTransport，覆盖 ReadTimeout、WriteTimeout、
连接中断、HTTP 500/408、订单404、成交索引暂缺、真实 NetworkHealth 暂停及恢复。
追加验证使用禁止联网的独立测试子进程，在10个持久化/传输节点直接退出，
不执行Python清理，再分别在T240前、T240和T300恢复。另以12组固定种子、共720步
模拟两路信号的超时、查单缺失、滞后状态、部分成交、确认/失败和每组三次重启；
逐步核对每签名仅一次POST、账本可恢复、实际负债有占款覆盖和T240后无新买入。
网络恢复本身不释放 UNKNOWN 占款；必须取得所属订单及完整成交证据才允许补余款。
原生同步 GET 阻塞时另一信号仍可通过独立调度和线程池提交。此为离线故障注入，
不表示已验证真实接口延迟、限价内流动性，或保证T240前订单必达交易所。

## 实盘 Taker 固定份数任务 — 2026-11-12（历史执行合同）

用户授权把一个准入信号保存为一个持续执行任务，并允许仅跟踪本执行器所属
订单的成交。实盘仍不启动；`config/m4-live/execution.toml` 当前保持
`mode="maker"`。以后选择 `mode="taker"` 时，新建市场采用持续任务实现。
基线、公共信号、共享 Maker 执行器及其账本没有修改，运行中的基线不重启。

- 接收信号时固定原锚定价、tick、最高价（锚定价＋1 tick，严格低于1）、
  目标数量和场次。初始目标仍按限价对应的市场实际费率、0.1份步长及
  5 USDC 计划含费预算向下定量；原信号消失不撤销已准入任务。
- 每次尝试都是新的非 post-only FAK，保存唯一签名订单ID，发送前以
  SQLite FULL 同步提交 UNKNOWN。**同一签名订单只发送一次**。
  重启遇到已发送订单先查询，不把超时、404、空响应或记录缺失当成零成交。
- 明确零成交的 FAK killed、确定性拒单，或所属订单及关联成交已证明最终
  零成交时，重新准备并退避重试。ACK只表示提交应答，不能据此认定成交。
- 只查询本任务已发送的精确订单ID；成交查询必须携带该订单明确关联的
  精确 trade ID、market和token。禁止账户全量订单/成交扫描和仓位查询。
  校验订单归属、方向、限价、数量、关联记录完整性及成交费用。
- MATCHED/MINED/RETRYING 成交保留原占款，等 CONFIRMED/FAILED 最终结果。
  同一 trade ID 去重；成交经济字段、终态或已观察成交量出现矛盾时保持
  UNKNOWN。复用 CA 的逐档、互补 token 和手续费解析，不引入 CA 库存或退出逻辑。
- 订单终态与成交明细完整一致后，将确认成交和费用原子记账，再按
  `原目标－累计确认份数` 补剩余，并向下取到0.1份。已经确认的本金及费用
  会扣减本任务剩余计划预算，不为每次重试重新给5 USDC。
- 用户最新确认的最小下单条件是 **数量≥5 shares，或者签名下单金额>1 USDC**。
  是“或”，金额门槛是严格大于。份数不足5且金额恰好1不能发送。
  每次准备及原生POST前均校验；不允许为了凑最低量向上加仓。
  无法补足的余量记录 DUST 或 BUDGET_EXHAUSTED，保留数量和原因。
- 保留既有价格、方向名额、每场10 USDC最坏风险、现金、盘口、时钟、日志
  和停机保护。原先已接受的“价格改善增量份数可能令实际含费超过5”例外
  仍有效；确认后以真实费用记账，达到目标或预算上限后不再补单。
  方向名额准入后，暂时资金不足的任务保留等待；每次只发当前现金和场次
  剩余风险能够覆盖的数量，不因暂时不足而重置目标或增加预算。
- T240前最后一道原生发送检查仍生效，T240起不再发新单。FAK未成交部分
  由平台立即失效，无需另发撤单。T240至T300只处理已发送订单的查询；
  T300或人工停机时未查清的订单与风险保留，并明确标记待核对，不宣称零仓位
  或已结算。没有跨场自动补旧信号，也没有实盘PnL或全账户核对。

代码入口沿用 `m4_live_taker.py`，新逻辑隔离在 `m4_live_task.py`。
实盘内部执行合同标识 `m4-taker-task-v1` 用于防止把新账本按历史单次合同恢复；
旧市场仍使用保存的 `m4-taker-single-fak-v1` 行为，不重写旧事实。
基线模型标识、两条曲线和统计起点均不变。

备份、差异与离线验证记录：
`research_outputs/m4_live/reviews/20261112T200239_continuous_task/`。
以下单次FAK章节为历史记录；持续任务的重试和所属订单查询权限以上述合同为准。

## 实盘 Maker / 单次 FAK 切换 — 2026-11-12（历史合同）

用户授权实盘侧改造，基线服务持续运行，实盘保持停止。实盘新增
`config/m4-live/execution.toml`，`mode="maker"` 为默认值，可改为 `"taker"`。
配置在实盘进程启动时读取；已创建市场恢复其已保存的模式与设置，换模式
不会清除方向名额、占款或人工/余额停机标记。基线不加载此配置。

Taker 使用与现有 Maker 相同的信号库，执行合同如下：

- 原信号锚定价加接收时的 1 tick 为最高买价，必须低于 1；tick 变化须重新
  通过保护检查，不能提高已经固定的价格上限。
- 目标数量以最高价对应的实际手续费公式计算，向下取到 0.1 份，并满足
  至少 5 份及场次最低量。每信号计划本金加费用最多 5 USDC。
  用户已明确接受价格改善后增量买入使实际含费扣款超过 5 USDC 的情况。
  每场最坏风险占款仍最多 10 USDC，不使用计划金额替代风险占款。
- 同一信号只允许一次真实 POST，类型为非 post-only 的 FAK，接受部分成交。
  尚未 POST 的意图可以在原 RF/EX 失效后继续准备，但只能在 T240 前提交，
  且继续检查价格、盘口质量、时钟、资金、日志和停机状态。
- 实际 POST 后，无论 ACK、明确拒单、部分成交回包、异常或超时，均不再
  补单。ACK 仅表示提交应答；不采信为真实成交，不查询真实订单/成交/仓位。
  ACK 与 UNKNOWN 保留全额风险，明确拒单仅释放该笔风险；已 POST 方向
  名额永久保留于该场。崩溃重启后不重复 POST。
- FAK 的未成交部分由交易所立即失效，无后续 GTC 挂单或主动撤单流程。
  单次 FAK、价格上限和资金限制意味着不能承诺每个信号足额成交。

复用原 CA 的 SDK 签名、原生 POST 防护与回包分类；纯费用函数提取至
`execution/taker_fees.py`，CA 原导入接口保留。CA 的库存、成交核对、补买
与退出生命周期没有接入 M4。实盘专用适配器和账本位于 `m4_live_*` 模块。
共享执行器仅新增首单时间预算判断的可覆盖方法，Maker 默认行为保持不变。
下文异常重试、Post-only 拒单降价和 `5/p` 数量规则继续描述 Maker 路径。

用户于同日确认改为按限价精确估费：
`planned_fee = quantity × rate × [limit_price × (1-limit_price)]^exponent`。
费率与指数读取对应市场，Crypto 常见 rate=0.07、exponent=1。
费用按 0.00001 USDC 向上取整后，计划本金加费用不得超过 5 USDC；不计 rebate。
例如 0.80 每份计划含费 0.81120；0.99 买 5 份计划含费 4.95347。
0.88 不再是准入截点；仍按场次最小量、价格低于 1 和 T240 等规则校验。

实际 BUY 按已签名本金金额撮合，改善价格可能增加份数，不构成固定份数上限。
例如 0.99 × 5 签名本金 4.95，若实际在 0.50 花完本金，会买到 9.9 份，
按公式费用 0.17325、合计 5.12325。逐档撮合及取整也可能偏离限价合计估算。
`planned_cost_usdc` 是计划成本，不是实际扣款硬上限，不据此推断成交或 PnL。

为保留每场 10 USDC 及现金保护，`commitment_usdc` 仍使用独立的保守最坏
风险 `本金 + 本金 × 2 × rate + 0.000001`。该金额只用于市场/现金准入，
不再用于把单笔目标数量压到 5 USDC 以下。第二个信号若使两笔最坏占款超过
10，记录 `M4_SESSION_BUDGET` 并跳过，即使两笔计划成本合计不超过 10。
ACK/UNKNOWN、崩溃恢复保留该风险。账本与心跳分别展示计划费用、计划成本、
最坏占款和该政策标识，避免把计划成本误读为实际风险。
旧保守单笔定量的 Taker 设置指纹不能冒充此政策；不重置历史账本。

备份及离线验证记录：
`research_outputs/m4_live/reviews/20261112T182243_maker_taker_development/`。
费用定量更新及边界验证：
`research_outputs/m4_live/reviews/20261112T185436_fee_planning/`。
本次没有启动实盘、发送真实订单或重启运行中的基线。

## 后续多信号架构记录 2026-11-11

用户已确认“公共信号库与执行核心复用，实盘和基线各自独立进程、分别选择信号集合”的方向。
例如实盘接M4，基线接M4和M6；每个信号只提出独立入场操作，策略不管理退出或库存。
详细职责、状态隔离、资金协调及待定合同见[多信号实盘与基线共用执行架构](../architecture/MULTI_SIGNAL_LIVE_BASELINE_ARCHITECTURE_20261111.md)。
该方向尚未实施，本次仅记录，不改变现行M4规则、配置、服务或停机状态。

## M4 日志与小时报告容量维护 — 2026-11-10

11:49 检查时，近期完整五分钟基线日志约 1.4–1.7 MiB，每小时约 19 MiB。
重复诊断仍按 token、类型、原因、接受状态和 generation 每秒采样一次。
1.53 MiB 样本压缩后约 159 KiB，压缩比约 9.84。

小时巡检调用 `ops/m4_log_maintenance.py`。已结束、官方已结算且关闭超过
一小时的基线原始日志无损压缩至 `research_outputs/m4_virtual/journal-archive`。
压缩完成后对解压内容校验 SHA-256，再替换原文件。活动、未结束、未结算、
校验失败或发生变更的源文件持续保留。全部 SQLite 账本继续保留。

小时报告的重复 `snapshot.json`、`market-pnl.json`、逐场 CSV 和运行日志
保留 24 小时，最新报告及生成失败记录受到保护。历史 PNG、汇总、验证记录
和巡检主记录保留。维护参数位于小时巡检 `control.json` 的 `log_maintenance`。
原始日志合计超过 128 MiB、单场超过 16 MiB、压缩归档超过 2 GiB 或磁盘
剩余不足 5 GiB 时，小时巡检报告容量告警。归档仍会随运行时间增长。

## 基线每小时巡检与双口径图 — 2026-11-10

用户授权在本对话每小时巡检 M4 基线并展示双口径 PnL 图。
`polymaker-m4-pnl-watch.timer` 于北京时间每小时整点后 20 秒触发，首轮
为 2026-11-10 12:00:20。生成 PNG、汇总和服务检查结果后，通过本地
Codex queue 投递到当前对话 `01a19f0f-b745-7111-9a8d-7305d5bda514`。
统计范围读取 `statistics-scope.json`，当前从北京时间 2026-11-11 01:00 场起累计，两条曲线均从 0 开始。
报告与开关位于 `research_outputs/m4_pnl_hourly_watch`。停止监控时禁用
该 timer 并将 `control.json` 的 `enabled` 原子改为 false。
旧五分钟实盘巡检继续停用，实盘当前停止。

## 新版本统计起点与日志清理 — 2026-11-10

用户指定从北京时间 **2026-11-10 11:00 开场**（epoch `1794279600`，含本场）
开始统计新版本数据。新旧成交口径均使用该起点之后的同批 POST，累计 PnL
从 0 开始；10:55 部署场及此前记录不纳入新版本统计。权威范围文件为
`research_outputs/m4_virtual/statistics-scope.json`，当前双曲线快照脚本读取该文件。

仅清理当前基线目录中 11:00 之前、已结束且已结算场次的原始 `.jsonl`
日志，共删除 181 份、318,300,505 字节（303.6 MiB）；历史账本和结算文件
保留备查并从新统计中排除。11:00 起的日志和
账本保留，基线继续运行，实盘、巡检及停机标记保持原状。
清理清单与验证证据：`research_outputs/analysis/m4_baseline_cleanup_1100_latest.txt`。

## 基线触价成交模型与同批 POST 对照 — 2026-11-10

蓝色新曲线按用户于2026-11-11确认的规则计算，沿用现有基线标识，统计起点以 `statistics-scope.json` 为准。
橙色旧曲线继续按同批POST立即全量模拟成交，图中保持两条实线。

- 新建场次将观察截止、1tick偏移和模拟撤单参数写入 `baseline_fill_model`。
  历史场次的原始参数与事实保留，兼容读取。
- 模拟POST开始时固定锚定价、当时tick、报价和数量。观察区间为
  `(POST,T240]`，T240使用实盘共用的官方时钟锚点。
- 同token有效Best ask低于或等于锚定价减POST时1tick，即按原POST价和数量
  全量模拟成交。同一帧触价后回升也保留证据，后续tick变化不移动门槛。
- T240无有效触价证据记 `NOT_TOUCHED`、收益0，并原子保存 `virtual_cancel`
  模拟撤单事实。断流、过期、延迟仍保留诊断。截止后行情不再产生模拟成交。
- T240停新单与重试，先排空已收到的截止前输入，再关闭行情并执行原有GC。
  重启恢复保留已确认成交，缺失过程按无证据未成交处理。
- 模拟成交与模拟撤单都保留本场已POST方向名额及总占款账本。
  真实实盘继续停机，模拟观察器仅在基线启用。

官方结算输出的 `modeled_pnl_usdc` 由已保存的 `virtual_fill` 计算，
`instant_fill_comparison` 按同批全部 `virtual_post` 计算立即成交对照。
新输出保存 `fill_evidence_policy=VALID_TOUCH_REQUIRED_NO_EVIDENCE_NO_FILL_V1`。

小时报表从11:00起统一重算。历史终态 `UNKNOWN` 无有效触价证据时计为未成交、收益0，
在旧口径按原 POST 价和量全量成交。历史事实及结算文件保留原值，
报表按原版本验证已保存结果，再应用当前口径。观察缺口独立统计，
已完整结束且官方已结算的场次正常连线。仍待观察、整场不完整、缺失和
未官方结算的场次保留灰底及断线。
心跳 `baseline_execution` 显示已 POST、待观察、模拟成交、未成交以及缺口数量。
只保存 POST、触价/终态等少量事件，不恢复全量行情日志。

177 项 M4 离线测试及静态检查通过。最终版本于北京时间 2026-11-10
10:54:25 在旧场 T240 后载入，10:55–11:00 场首次启用新模型；已核对
运行进程的源文件指纹、场次模型标记与心跳。10:50 场沿用旧口径，其
已 POST 方向名额和 4.98 USDC 占款在恢复前后保持一致。实盘和巡检
保持停止，余额保护标记内容未变。证据索引：
`research_outputs/analysis/m4_touch_fill_update_latest.txt`。

## T240 内存回收与旧日志清理 — 2026-11-09

实盘与基线共用的执行器在场次时间下界达到 T240 后关闭公共行情订阅。
参考 M234 信号测试，先保存关闭/失效事件，等签名、POST、账户与 tick
读取任务自然结束，再确认日志落盘、释放信号和盘口缓存，复用
`m2_memory.recycle_idle_memory()` 执行 GC 及可用时的 `malloc_trim(0)`。
每场只尝试一次；跨越 T300 的请求仍先处理并保存结果，再在换场前回收。
回收前后 RSS、swap、耗时及成功/失败状态写入日志和心跳。

信号计数、失败原因、已使用的信号根、订单身份、方向名额与全部已 POST
占款保留。未 POST 的剩余任务沿用 T240 中止规则；不取消在途 SDK 工作，
不撤单，不查成交或仓位。关闭行情后现金保护与停机检查继续执行；日志
落盘失败时不清理证据，GC 不支持或失败不会重置风险。下一场建立全新
行情与信号对象。此变更不调整价格、弱轮耗损门槛或其他交易条件。

按用户授权，20:02 已删除 71 份旧基线 `.jsonl`，合计 18,301,427,839
字节；保留 19:55 起修复后的日志、全部模拟账本及实盘文件。删除证据：
`research_outputs/analysis/m4_baseline_log_cleanup_latest.txt`。
内存变更与部署证据：`research_outputs/analysis/m4_t240_memory_latest.txt`。
仅基线获准运行；实盘、定时巡检和现金保护停机状态保持不变。

## 基线日志与结算恢复修复 — 2026-11-09

16:49–19:47 磁盘写满造成基线反复退出，检查时累计自动重启 1139 次。
旧基线日志约 18GB，全部保留。重复 `EVENT_DECISION` 诊断改为按
token、事件类型、拒绝原因、接受状态和 generation 每秒最多记录一次；
心跳 `signal_counts` 仍累计每个事件，轮次、候选、准入及执行事实不采样。
因此诊断日志行数不能再直接当作行情事件总数。

结算扫描跳过 SQLite 打开失败遗留的无表空文件，保留原文件且不将其
标记为已完成场；有未知表或身份错误的数据库仍拒绝加载。正常的过期
账本继续使用原身份和全部占款恢复，结算状态在成功扫描后更新，清除
陈旧错误提示。没有补造缺失场次或实际成交，没有查询实际账户成交。

137 项 M4 离线测试通过后于北京时间 19:54:30 恢复基线。价格下限
0.20、弱轮最低耗损 5% 和所有交易规则均不变；实盘和定时巡检保持停止。
修复及完整性证据：`research_outputs/analysis/m4_baseline_health_latest.txt`。

## 最新调整：卖盘弱轮最低耗损 5% — 2026-11-09

用户明确将弱轮耗损门槛从 20% 降到 **5%（含边界）**。实盘与基线
共享该信号参数；价格范围仍为 `0.20 <= p < 1`，其他条件和风险限制不变。
基线在旧规则场次完整结束后切换到新场，不改写旧场参数或重置占款，
已有 0.20 价格范围的基线日志继续保留，按场次头中的信号参数区分新旧样本。
实盘与定时巡检继续停止，现金保护停机标记继续有效。
部署证据索引：`research_outputs/analysis/m4_weak_loss05_update_latest.txt`。

## 当前价格与运行授权 — 2026-11-09

用户明确授权实盘和基线共用价格范围 **`0.20 <= p < 1`**，并启动基线；
实盘与巡检继续停止。卖盘弱轮最低耗损已按最新授权调整为 5%，恢复率、
每笔 5 USDC、每场 10 USDC、方向名额、重试和 T240 限制均不变。
0.20 对应最多 25.0 份，0.21 对应 23.8 份，均按 `5/p` 向下取整。

价格范围同步用于信号准入、最终 POST 防护、账本报价/签名授权、明确
Post-only 拒单后的下调重挂以及 UNKNOWN 占款恢复。旧场若存有 0.60 下限，
继续按该场设置校验，不因新配置降低旧场门槛或重置风险。设置指纹区分
新旧价格配置；0.60 仅作为显式旧配置/场次设置兼容，不是新默认值。

用户同时要求从 0.20 起重新记录基线。停下旧基线进程后，将旧基线日志、
模拟账本、模拟结算和 latest 输出整体归档移出 `research_outputs/m4_virtual/`，
新进程在空运行目录等待完整新场；不再加载旧基线结算队列。实盘账本、
挂单及余额保护标记保留。该操作只启动基线，不启用实盘、巡检或基线的
开机自启。部署证据索引：`research_outputs/analysis/m4_price20_baseline_latest.txt`。

## 当前策略补充：卖盘弱轮最低耗损 5% — 2026-11-09

用户在三场亏损复盘后明确授权补充规则并更新代码。当次更新保持实盘、
基线和巡检停止；后续基线启动授权及 0.20 价格范围见上节。
初版耗损门槛为 20%，本次授权将它降低到 5%。
策略条件调整不触发历史 BUG 修复后的实盘自动恢复授权。
下文启动、试运行和巡检记录均为历史记录，不表示当前服务正在运行。

当前 M4 买入信号须同时满足：

1. 原有 500ms 合格轮次、成交冲击、100ms 保持和行情质量检查通过。
2. 同方向买盘有一轮稳定支持，最终恢复率至少 50%，有效期 8 秒。
3. 卖盘先有一轮稳定参考，恢复率至少 50%，参考有效期 15 秒。
4. 随后的合格卖盘弱轮必须满足
   **`loss / initial = (轮初数量 − 轮内最低数量) / 轮初数量 >= 0.05`**，
   恰好 5% 通过。减少量可能包含撤单，并不等于全部由主动成交消耗。
   此门槛只作用于弱轮，不额外限制买盘支持轮或卖盘参考轮。
5. 弱轮仍须满足恢复率 `<= min(0.60, 参考恢复率 × 0.80)`；归一化
   `hit/initial` 和 `loss/initial` 相对参考轮的比率均在 `[0.1,10]`。
   未满足新门槛的弱轮消耗旧参考，不跳过它向后寻找另一个弱轮。
6. 一轮合格弱轮形成有效期 5 秒的 DF；后续稳定卖盘恢复至少 50% 时撤销
   旧 DF。DF 与买盘支持必须在同 token/generation 内重叠，按可见买盘
   支持锚点报价；候选截止时间取两个组件截止时间的较早值。

实现参数为 `m4_frequency_signal.PARAMETERS['weak_min_loss_fraction'] = '.05'`。
不足门槛的可比弱轮记录 `WEAK_ROUND_REJECTED`，原因为
`ASK_LOSS_FRACTION_BELOW_MINIMUM`，同时保留实际比例和门槛。
新场次头及离线 `--check` 输出 `signal_parameters`，该参数自动进入
`settings_hash`。旧场按原指纹/源代码复核，不倒套新门槛；旧账本身份与
新指纹不匹配时拒绝恢复，不改写历史、不清除风险或方向名额。

其他执行规则继续有效：价格 `0.20 <= p < 1`；数量为 `5/p` 向下取到
0.1 份并满足场次合法最小量及至少 5 份；每笔风险最多 5 USDC，每场含
异常重试总占款最多 10；每方向最多一个已 POST 独立机会，未 POST 中止
不占名额，已 POST 拒单仍占名额。异常返回/超时合计只允许一次原价额外
POST 并保留两笔可能成交风险；只有明确 Post-only 拒单允许严格低于原价
和上次拒单价的合法 Best bid 重挂，且只释放明确拒单那笔风险。
T15 起允许新单，T240 停新单和重试；GTC 单保留，信号失效不自动撤单，
不增加成交核对、账户互斥、Merge 或 T300 撤单。

现金保护仍读取 `config/m4-live/balance-guard.toml`，当前开启、10 USDC；
配置无效回退开启/20。实际可用余额观测严格低于有效阈值时锁定停机；
读取失败不按零处理。人工/余额停机标记及 `requires_confirmation=true`
持续有效，代码更新不解除；保留全部挂单与账本。

真实轮次回归样例：02:00 的 23.95% 和 02:20 的 20.23% 仍可形成 DF；
02:25 场 0.98 Up 对应的 1.18% 弱轮被过滤。这是信号规则验证，不是
修改后的反事实 PnL 证明，也不表示 5% 已获整体样本的盈利验证。

## Original cutover record

Implemented 2026-11-08/09. Both services are installed, **disabled and inactive**.
No live orders, account fills or positions were queried during this work.
The user must explicitly authorize a later start. The stopped chat monitor remains retired.

## Five completed work items

1. **Shared execution contract.** `polymaker.m4_run` runs an M4-only frequency-v2
   detector. Live and baseline use the same `M4Market` admission, signing, final
   native POST guard, retry and durable ledger code. Baseline substitutes only
   account inputs and the final POST effect; its receipts are explicitly modeled.
2. **Downward repricing.** Explicit Post-only rejection permits a new Best bid
   only when it is below both the original signal price and the last rejected
   price. No lower bid means no POST. This is checked by both the quote selector
   and durable quote authorization. The same original M1 fallback bug was fixed.
3. **Isolated persistence and restart.** M4 uses strategy-version, mode, account,
   market and settings identities. Foreign databases, symlinked roots and identity
   changes fail closed. DF roots identify independent opportunities; support
   evidence is not globally unique. Restart retains sent direction claims and
   every uncertain attempt's gross reservation. No historical M1 imports exist.
4. **Independent cash protection.** The live process checks cash during its
   account refreshes. The default is enabled, strictly below 20 USDC. Invalid
   configuration falls back to enabled/20 and records the error. Unavailable
   cash is not zero. A persistent latch ends the process and blocks subsequent
   starts, including after configuration changes. Baseline ignores cash controls.
5. **M1 retirement.** M1 live/baseline and its watch timer/service are masked.
   Legacy CLI entrypoints reject startup. 5,983 old runtime/configuration/analysis
   files, totaling 1,920,130,250 bytes, were moved outside the project and verified.
   DB/WAL/SHM bytes were preserved without reading account fills or positions.

## Confirmed trading contract

| Item | Value |
|---|---|
| Signal | `m4-frequency-v2`; current weak Ask loss floor is 5% as specified above |
| Both modes' quote band | Current `0.20 <= price < 1`, legal venue tick; old markets keep stored settings |
| Quantity | `floor(5 / price, 0.1 shares)`, at least venue minimum and 5 shares |
| Each POST | Principal at most 5 USDC; never round upward |
| Market gross cap | 10 USDC including every UNKNOWN and its extra attempt |
| Direction quota | One independent POSTed opportunity per direction |
| Unsent skip/abort | Does not consume direction quota |
| Explicit rejection | Keeps direction quota; releases only its own attempt risk |
| Abnormal response/timeout | At most one extra POST at original signal price; requires remaining funds |
| Post-only rejection | Strictly lower current Best bid; recompute and recheck size/budget/band |
| Entry window | T15 through before T240, including all retries |
| Market end | No cancellation, Merge, account reconciliation or inferred live fills |

ACK denotes acknowledged submission only. Baseline settlement uses public market
resolution and explicitly modeled full-fill receipts; it never establishes actual
account fills or PnL and never releases the execution ledger's gross commitments.

## Files and controls

| Purpose | Path |
|---|---|
| Shared frozen trading settings | `config/m4.toml` |
| Independent live transport settings | `config/m4-live/config.toml` |
| Cash protection | `config/m4-live/balance-guard.toml` |
| Live data | `research_outputs/m4_live/` |
| Baseline data | `research_outputs/m4_virtual/` |
| Persistent cash stop | `runtime/trading-control/balance-stop.json` |
| User live stop | `runtime/trading-control/m4-live-stop.json` |
| User baseline stop | `runtime/trading-control/m4-baseline-stop.json` |
| Retired M1 entrypoint control | `runtime/trading-control/m1-retired.json` |

The original balance stop ID `e39d42deab904eb4a40f17f72b076cd2` was copied intact.
Its recorded balance is historical, not a new cash observation. Neither changing
configuration nor fixing code releases any stop marker. The legacy M1 balance
marker path is also checked defensively if it ever reappears.

The M4 data directories are empty at handover. Future newly started sessions wait
for a fresh market boundary. Resuming an existing M4 market requires its stored
identity to match and uses that market's persisted settings, preserving all
reservations. Configuration changes apply to newly created market sessions.

Offline inspection only:

```sh
.venv/bin/python -B -m polymaker.m4_run --mode live --check
.venv/bin/python -B -m polymaker.m4_run --mode baseline --check
```

Both checks currently report `inhibited: true`, with no network requests.
Do not remove control files or start/enable either service without explicit user
confirmation. Cash below the enabled threshold will immediately relatch a resumed
live service. Baseline has its own manual stop and is independent of cash protection.

## Archive and scope

Archive metadata: `/home/ubuntu/polymaker-retired/m1-20261109T0015/`

On 2026-11-09 (Asia/Shanghai), the user explicitly authorized deletion of the
5,983 retired M1 files. All original hashes and symlink targets were reverified,
then the archived `runtime/` payload was deleted. The original manifest and
retired service definitions remain; the archived runtime data is no longer
available. Deletion evidence is in
`research_outputs/analysis/m4_unrelated_cleanup_20261108T1734Z/result.json`.

`manifest.json` records each original path, size and hash (or symlink target).
Three additional root-owned retention-preview files were discovered while
removing old directories; they were included and verified. The archive utility
now fails on unreadable subtrees instead of silently omitting them.

M1 compatibility source still imported by the separate M1234/M234 research tasks
is retained. It is not an M4 strategy or state input. The shared feed retains some
historical `m1_*` names for its event-validation port; M4 installs `M4LiveSignals`
there and never constructs an M1 detector. Shared queue arithmetic was extracted
to neutral helpers. Dashboard code, caches and services, other research datasets,
and the shared operating-system journal are outside this cutover and untouched.

## Validation evidence

`research_outputs/analysis/m4_implementation_20261108/` contains pre-change backups,
test output, source hashes and the retirement result. The related regression run
passed 263 tests; the final focused cutover/CLI/archive run passed 40 tests.
Tests use synthetic books, isolated SQLite databases, disposable signing keys and
mock HTTP transports, including the real SDK/native final POST checks. Network
sockets are blocked by the M4 acceptance suite. Both systemd units passed static
verification. No service was started or restarted.

## Second code review — 2026-11-09

The live and baseline services remain **inactive/disabled**. Both offline
`--check` commands report `inhibited: true` and zero network requests. No stops
were cleared, no trading or monitoring service was started, and no account fills,
positions, or actual PnL were read or inferred. M4 runtime directories remain empty.

The review reproduced and repaired these boundaries using isolated synthetic
ledgers and injected crashes; the trading contract above is unchanged.

| Finding | Repair and acceptance evidence |
|---|---|
| A reserved/repriced order could pass after cash or allowance dropped below its commitment. | Recheck fresh capital against gross risk at admission and immediately before native POST. A lower quote with exactly enough capital still passes. |
| A crash after explicit Post-only rejection, before retry authorization, lost the repricing task. | Store rejection proof atomically with the outcome; recover only that original opportunity and only with explicit proof. Stop controls and T240 still apply. |
| An authorized extra attempt could be lost if its unsent preparation became ABORTED before a restart. | Recover from the durable authorization for the same POST count. A two-restart test retains 9.94 USDC of UNKNOWN risk and permits only two total abnormal-response POST attempts. |
| Logging failure after a durable ACK could incorrectly replace ACK with UNKNOWN. | Separate errors after recording a response from transport failures. Keep the durable response and inhibit further submission on follow-up failure. |
| Restart used current settings instead of the existing market's settings; partial session initialization was also possible. | Validate strategy/mode/account/market first, load and validate stored settings, and write session ID/settings/market in one transaction. New sessions use configured settings. |
| A baseline market interrupted before T300 could remain incomplete forever. | On a future authorized baseline run, use official public time to finish expired local observation windows. Exclude the running ledger; preserve sent ACK/UNKNOWN risk and never submit an old order. |
| Baseline model result and completion marker could split across a crash; export could also be missed. | Commit result/marker atomically, recover an existing result idempotently despite a new observation timestamp, reject conflicting economics, and regenerate missing exports from the stored result. |
| A wrong-mode database's journal mode could be changed before identity rejection. | Inspect identity through a read-only connection before writable open, WAL changes or schema migration. M1 and mismatched M4 state fail closed. |
| Journal callback failure could clear candidates during iteration and raise RuntimeError. | Iterate a candidate snapshot and stop selection immediately on recording failure. No candidate survives the failure. |
| A WS tick-size change remained unverified for the rest of the market. | Revalidate changed ticks through the shared public reader. Stale REST ticks cannot overwrite newer WS constraints; submissions stay blocked until verified. |

`tests/m4/test_m4_review.py` (originally `tests/test_m4_review.py`) adds 24 acceptance cases. The final related regression
run passed **111 tests**, covering M4 cutover, retirement/isolation, original M4
frequency signals, shared M234 signal compatibility, and public-feed realities.
The new SDK tests check native signed amounts at 0.60, 0.67, 0.71 and 0.98 with
mock HTTP transport; they assert the 5-USDC ceiling and 0.1-share downward rounding.
The changed Python files pass Ruff.

An expanded run also exercised the separate CA maker sizing suite: it recorded
131 passes and one failure in the unrelated `.05-1` CA entry test (no entry was
returned). That exact case passed alone on rerun. No CA code was changed, and
the expanded run is not reported as an all-pass result.

Evidence is in `research_outputs/analysis/m4_code_review_20261109/`: pre-change
source copies/hashes, failing reproductions, final regression output, the CA
rerun, audit JSON and a before/after patch. This is offline verification; exchange
connectivity and actual live order acceptance were deliberately not exercised.

## Repair recheck and M4-only acceptance — 2026-11-09

The follow-up review found no new reproducible production defect in the previous
repairs. No production Python or trading configuration was changed in this
recheck. Both M4 services remain inactive/disabled; existing stop-control bytes
are unchanged and both M4 runtime directories remain empty.

The acceptance entrypoint is now:

```sh
bash ops/test_m4.sh -q
```

It explicitly collects **only `tests/m4/`**, using its own pytest configuration,
conftest and synthetic helpers. It excludes the repository-level conftest,
blocks real network connections, rejects outside test paths before collection,
and checks for imports of other strategy test modules. All simulated databases,
journals, cash controls and crash-worker files use pytest temporary directories.
Shared production feed/SDK code is still exercised because M4 uses that code.

| Test file | Scope |
|---|---|
| `tests/m4/test_m4_cutover.py` | M4 trading contract, SDK/native boundary, cash stop, mode/data isolation |
| `tests/m4/test_m4_review.py` | Regression of the ten repaired boundary cases |
| `tests/m4/test_m4_recovery.py` | Concurrent repeated scheduling, restart/risk invariants and hard-process-crash transactions |
| `tests/m4/test_m4_signal.py` | Current M4-only frequency detector, signal expiry and invalidation |
| `tests/m4/test_m4_retirement.py` | M4 migration controls and opaque legacy-data isolation in a synthetic project |

The historical mixed M2/M3/M4 research file was renamed to
`tests/test_m234_frequency_research.py` and is excluded from this entrypoint.
M1/M234/OBI test fixtures are no longer imported by M4 acceptance. CA/BA/OBI and
other research suites are not counted in M4 results. The M1 references remaining
inside the M4 isolation tests check rejection/retirement of foreign state, not
M1 trading behavior. The previous broader 111-test result is historical and is
not the current M4-only acceptance count.

Final result: **91 M4-only cases passed**, including 24 new recovery cases. Ruff
passed for `tests/m4/`. A deliberate attempt to include `test_m234_signal.py` was
rejected with pytest exit code 4 before collection.

The new cases cover both live-adapter and baseline modes with network effects
replaced by synthetic responses:

- Twenty concurrent calls for one opportunity, followed by three reloads with
  process-local order knowledge discarded: ACK produces one POST; repeated
  abnormal responses produce two distinct POSTs, one retry, and 9.94 USDC retained.
- An opposite-direction ACK plus the original UNKNOWN occupies 9.94 USDC;
  repeated restarts cannot authorize an extra POST.
- UNKNOWN → original-price retry → explicit Post-only rejection → lower Best
  bid: the first UNKNOWN remains reserved, only the rejected attempt is released,
  and another abnormal response cannot reset the one-retry counter.
- Stop/T240 after recovery prevents the authorized-but-unsent retry; the original
  possible POST retains its 4.97-USDC risk. Releasing an unsent reservation does
  not release a possible POST's risk.
- Forked workers call `os._exit` between SQLite archive/update/event/commit
  boundaries. Recovery yields either the original 4.97-USDC UNKNOWN or the
  atomic 9.94-USDC pair; there is no partially committed retry-counter/risk state.
  The durable POST-intent test also rejects a second start for the same intent.

Evidence and pre-change backups:
`research_outputs/analysis/m4_recheck_20261109/`. The tests validate local
durability and scheduling; they do not verify exchange connectivity or actual
order acceptance. No real orders, account fills or positions were queried.

## Baseline observation trial — 2026-11-09

Only the M4 baseline was started, at 00:47:51 Asia/Shanghai. It waited for the
next fresh market, ran three complete five-minute markets, and was stopped at
01:05:03 with its manual stop latch restored. Neither service was enabled.
Live remained inactive throughout; the live manual and cash-stop files retained
their original hashes, and `research_outputs/m4_live/` remained empty.

| Local market time | Epoch | Admitted signals | Simulated POST / ACK | Uncertain retries | Retained model commitment |
|---|---|---|---|---|---|
| 00:50–00:55 | 1794156600 | 0 | 0 / 0 | 0 | 0 USDC |
| 00:55–01:00 | 1794156900 | 1 | 1 / 1 | 0 | 4.992 USDC |
| 01:00–01:05 | 1794157200 | 0 | 0 / 0 | 0 | 0 USDC |

The one admitted opportunity was YES at 0.78, sized down to 6.4 shares. Its
simulated POST occurred at T235.374, approximately 78 ms after signal formation.
There was one signed ID and no repeat POST. Every completed market followed
PREPARING → OPEN → DRAINING → COMPLETE; no POST occurred after T240. New-market
ledgers began at zero while the prior market retained its 4.992-USDC commitment.
Both services ended inactive/disabled, with no automatic baseline restarts.

Public-feed guards did reject unavailable, stale or out-of-order inputs; the
signal observer did not fail. No abnormal response, timeout or Post-only
rejection occurred during this trial. Retry behavior therefore remains covered
by the isolated offline fault cases, not by naturally observed runtime retries.
The baseline endpoint returns modeled acknowledgements locally; these are not
exchange acknowledgements or evidence of account fills.

The trial exposed a reporting defect: `sm_run_result` used live settlement
labels even for VIRTUAL sessions, although the ledger labels were correct.
After stopping the baseline, the result record was changed to use the same
mode-specific policy/status as the ledger. Trading, sizing, retry and risk logic
were untouched, and historical journals were preserved. A regression first
failed for baseline mode, then passed after repair. Both modes also assert that
repeated completion neither repeats the POST/result nor releases commitment.

Verification after repair: **93 M4-only tests passed**; Ruff passed for the
changed Python files. Neither service was restarted after the repair.
Evidence, source backups, reproduction, per-market audit and service states:
`research_outputs/analysis/m4_baseline_trial_20261108T164751Z/`.

## Authorized live start and same-chat watch — 2026-11-09

The user explicitly requested a 10-USDC cash threshold and live startup, then
five-minute inspection and prompt repair of reproducible bugs in this chat.
Cash protection remains enabled; invalid configuration still falls back to the
code default of enabled/20 USDC. The fresh pre-start cash read was 19.764957 USDC.
The manual and old cash-stop latches were archived under the startup evidence
directory and released under this authorization. The baseline stop was preserved.

`polymaker-m4-live.service` started at 01:15:16 Asia/Shanghai, after T15, so it
waited for the fresh 01:20 market. Its first observed instruction received ACK,
with a 4.95-USDC commitment and no uncertain retry. ACK is not a fill claim.
Trading rules and the M4 market-settings fingerprint were unchanged.

`polymaker-m4-watch.timer` is enabled at `*:00/5:20 Asia/Shanghai`. Its separate
M4-only observer reads submission ledgers/journals and strictly GET-only cash,
enforces existing stop latches, and queues reviews to the same thread:
`01a19f0f-b745-7111-9a8d-7305d5bda514`. It never starts trading, queries account
fills/positions, creates credentials, cancels orders, or rewrites trading ledgers.
Unreviewed markets are retained; pending reviews coalesce and the review cursor
is updated atomically under `agent-state.lock`. Initial queue/resume succeeded.
Reports and delivery state are in `research_outputs/m4_live_watch/`.

The inspection/repair instructions are in `ops/m4_watch_prompt.txt`; updates
take effect on the next timer run. To stop monitoring, stop/disable
`polymaker-m4-watch.timer` and stop its oneshot service; this does not start or
stop the baseline. M1 monitoring remains retired.

On 2026-11-09 (Asia/Shanghai), the user authorized automatic resumption of M4
live after a bug repair: "如果发现BUG，修完以后就自动启动实盘，继续交易。"
If live was paused for that bug, the inspection agent resumes the necessary
service after targeted offline tests and checks of affected duplicate-POST,
retry-budget and restart-reservation behavior pass. Before starting, it rechecks
the pause reason, cash protection, stop markers and any later user stop request.
It retains all ledgers, direction claims and reservations, prefers a market
boundary, verifies the resumed service, and reports here. Bug-maintenance pauses
are recorded separately from manual/cash stops. This authorization never releases
a manual or cash-protection latch or resumes the baseline. The timer itself
continues to inspect and stop when necessary; repair/resume decisions remain
with the inspection agent. Evidence:
`research_outputs/analysis/m4_bug_resume_authorization_20261108T1745Z/`.

Verification: **107 M4-only tests passed**, including 14 monitor cases covering
threshold equality, low cash, sticky stops after config changes, unavailable
cash, invalid-config fallback, forbidden network routes, UNKNOWN commitments,
and same-thread/coalesced delivery. Ruff passed for the added Python files.
Startup evidence: `research_outputs/analysis/m4_live_start_20261108T171455Z/`.
Monitor evidence: `research_outputs/analysis/m4_watch_setup_20261108T172055Z/`.


## 2026-11-14: signal-clock ten-second Best ask fallback

User authorized ANCHOR_10S_THEN_BEST_ASK_V1. The first ten seconds from
signal_start_mono use original anchor + its original tick. At >=10 seconds,
each new FAK for remaining cash captures a fresh, same-token, verified-grid
Best ask as its exact signed cap, without a relative-anchor cap. No count of
POSTs triggers this transition. No unconditional market-order fill guarantee.

Signal origin (boot identity, monotonic time and official market elapsed time)
is saved in the immutable root plan. Quote evidence is saved separately and
referenced by each attempt; recovery validates capture-time freshness and
never resets the timer or reprices a started order. Signing/fsync crossing
the ten-second boundary leaves the old anchor preparation locally UNSENT.
A new quote requires a fresh book; transport does not reimpose backlog or
ask-equality gates on an already signed, capped FAK. UNKNOWN is reconciled
first. Known pending matches consume budget before a proven residual is bought.

The 5-USDC planned all-in budget, 10-USDC market reserve, .1-share step,
USDC cents precision, minimum quantity/notional, direction slots, stops and
T240 remain enforced. An unexecutable residual may wait for a legal reprice
until T240; it is never rounded upward. Historical settings serialization
and fingerprints remain unchanged, so existing markets keep their saved
fixed pricing policy. New settings take effect for new markets after the
user restarts live. No automatic service restart or test order is authorized.
Baseline remains retired.

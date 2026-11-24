# 盘口信号 SOP 工具箱

## 新增 M24 哪边在补

[M24 独立 SOP](M24.md)（`m24-two-sided-net-replenishment-v1`，SPEC_ONLY）比较同一窗口内原 Bid 和 Ask 真实受击后的净补给。买方需有正净补给，并同时在补给股数与每股受击的补给比例上领先。与 M12 同家族，M21 的零 BUY、M17 的分桶保量、M23 的预加托均不进入其定义。已登记文档，测试优先顺位未指定。

## 2026-11-18 信号测试优先顺序

新增开发按 [M8 弱后复强交接](HANDOFF_M8_RESPONSE_REPAIR.md)、[M5 两轮稳接独立新版](V_M5_RELATIVE_RECOVERY_DIAGNOSTIC.md) 的顺序逐项处理。M23、M21 转入已有观察的结果复核；M12 已过开发频次，独立基线等待用户另行启动。M7、M9 停止当前结构的独立调频，M18 暂缓。所有 23 个信号的下一步、测试顺位和收尾条件见[逐项工作清单](SIGNAL_TEST_PRIORITY_20261118.md)。以下 11 月 17 日的批次、状态和调试结果保留供追溯。

已按用户2026-11-12最新指令停止每小时汇报及本线程自动小时唤醒：本任务control.enabled=false，小时timer已禁用并停止。已有信号与SOP保留，后续按用户指令继续。测试仍由用户模块负责。

## 当前交易语意

**信号有效期间在固定锚价成交。** 信号形成、证据交接、有效等待、失效与原截止先独立定义；Maker/Taker分别作为执行方式列出。成交方式不替代形成证据，不改变原锚，不延长证据寿命。本单以Maker成交也不自动成为一次新的承接确认。

- [固定锚价信号／执行合同](FIXED_ANCHOR_CONTRACT.md)：统一状态、时钟、待执行意向、两种执行接口及历史执行参考。
- [M1–M23 原语与顺序总表](STRATEGY_PRIMITIVE_MAP.md)覆盖前 23 个编号。M24 的原始输入及家族查重见本页上方独立入口。
- [确认／失效冲突审查](FIXED_ANCHOR_REVIEW.md)：逐策略截止与交接核对，区分信号矛盾、数量无解和执行不可准入。
- [文档一致性回执](../../research_outputs/signal_discovery/fixed_anchor_review.json)：参数、源快照、JSON和引用检查；不代表信号测试或实现支持。

2026-11-17已按用户授权落实[设计建议](M7_M23_M4_METHOD_DESIGN_REVIEW_20261117.md)：当前入口与确认／失效检查见[增频设计落实及模块交接](M7_M23_FREQUENCY_DISPOSITION_20261117.md)。当日最初新增M8/M18/M21/M23四份独立V及M11时序诊断，后续新增M12有效期重叠版及M23首SELL750ms仅诊断版；本次再新增M21端点共同确认、M7暂停释放观察、M9局部EX有效期重叠三份独立SOP。M13/M15/M16/M17/M22停止跟进，不再保留重构或输入审查候补。原M/V/D/P、共用合同及历史结果保留，catalog只更新文档身份和研究安排，不启动测试或切换运行版本。

23份主SOP均采用`mN-fixed-anchor-entry-v1`，保留`basis_version`和旧完整文档快照。原形成数字、信号数量守卫、期限及原语定义保持；新的执行分层不冒称旧Maker运行版本完整行为不变。8份旧显式备选作为冻结历史Maker规格保留，catalog登记definition_file，未自动升级为新Taker版本。

正常未交叉的同token盘口下，原Bid P仍有量意味着Ask高于P，因此这些主版保留的原Bid存在/支撑条件与同价Taker买入没有共同准入状态。文件已将此列为执行限制；不会为取得Taker成交而忽略原档丢失或底线失效。信号有效与某执行方式当前可用分别记录。

交易数、胜率、PnL和执行性能留给后续用户模块。本轮不计算、不重新排序收益、不把未测试的组合称为已提高结算概率。

## M 系列定义索引与 2026-11-17 研究状态

2026-11-17按用户最新授权落实增频设计：**七个分支保留跟进入口，五项仅诊断，五项停止跟进。** M4正式实盘沿用用户此前确认；M6按历史停止记录已于2026-11-17 10:02停止基线，继续结果收尾。本轮未查询运行状态，未切换服务或测试。

本表和catalog是文档研究安排，不是运行开关。原M1–M23、已有V/D/P、共用合同及历史结果不改；M4、M6、M16原SOP字节保持。较早文档中M15/M17重构候补、M16先审输入及旧交接批次已被本轮研究安排取代，不能据旧条目恢复跟进。停止不表示已证明所有参数无效；新规格也未证明实际增频。

研究优先级：

- **P0**：M4实盘与M6已停基线收尾；不把研究分支直接注入运行版本。
- **P1／第1批**：M12有效期重叠版、M23首SELL750ms仅诊断版。两者先前分支均保留为对照。
- **P1／第2批**：M21端点共同确认版（双侧100ms、首轮Bid10%/5%仅诊断）。
- **P1／第3批**：M7暂停释放观察版、M9局部EX有效期重叠版；M9仍按H/L资格交接。
- **P2／后备新版**：M8响应转强版、M18恢复达标版。M18仍有已知输入瓶颈，不因新文件存在而重复调参。
- **P3／原对照或候补**：M1、M2、M3、M5，本轮不改变它们的安排。
- **D／仅诊断**：M10、M11、M14、M19、M20，不增加母信号等待、筛选、退出或订单。
- **A／停止跟进**：M13、M15、M16、M17、M22；保留原定义和历史，无自动重构或输入审查任务。

| 策略 | 当前状态／研究定位 | 下一步与当前入口 | 研究优先级 |
|---|---|---|---|
| [M1 承接后的卖压余波](M1.md) | 保留基础对照 | 汇总已有研究与实盘结论，作为判断M9新增复验价值的对照；暂不优先增加版本。 | P3／对照 |
| [M2 买入进攻后的受控回流](M2.md) | 保留家族主策略 | 母定义不变；使用[M10回流递减诊断](D_M2_RETURN_DECAY.md)描述变化，不将诊断重新变成硬门槛。 | P3／母策略与对照 |
| [M3 反击后守价](M3.md) | 保留候补 | 检查后续BA相对CA增加的证据，以及串行确认消耗的有效时间，再决定是否需要新分支。 | P3／候补 |
| [M4 守强攻弱](M4.md) | **正式实盘，用户确认** | 保持当前信号版本稳定，优先核对真实执行、成交与结算；研究分支独立管理，不直接进入实盘版本。 | **P0／现行实盘** |
| [M5 强压稳接](M5.md) | 保留候补 | 与M1区分持续受压承接和衰竭后余波；先整理已有测试结论，再决定新增研究投入。 | P3／候补 |
| [M6 撤供留托](M6.md) | **基线已停；2026-11-17 10:02历史停止确认** | 保留冻结版本和账本，核对停止前快照与最终截止范围，分开记录新触价模拟和旧全额假设；不自动恢复基线。 | **P0／已停基线收尾** |
| [M7 歇后再攻](M7.md) | 改用暂停释放观察新版 | [M7_PAUSE_RELEASE](V_M7_PAUSE_RELEASE.md)：暂停单桶35%仅诊断，暂停后解除冻结AP1占用；新AP2完整观察、并行支撑及原截止保持。旧并行版保留。 | P1／第3批 |
| [M8 失守后修复](M8.md) | 原版保留，改为响应转强新版 | [M8_RESPONSE_REPAIR](V_M8_RESPONSE_REPAIR.md)：删除损失比门槛，真实新受击、旧滞回稳定和60%恢复保留；不声称同压改善。 | P2／后备新版 |
| [M9 余波复验](M9.md) | 改用局部EX有效期重叠新版 | [M9_EX_VALIDITY_OVERLAP](V_M9_EX_VALIDITY_OVERLAP.md)：EX确认即维护3秒局部证据，单桶30%仅诊断；原H/L交接、50%/95%及复验期限不变。旧交接版保留。 | P1／第3批 |
| [M10 回流递减](M10.md) | 退出独立优化，降为M2诊断 | 只通过[M2_RETURN_DECAY](D_M2_RETURN_DECAY.md)描述回流变化，不独立触发或取消交易。 | D／仅诊断 |
| [M11 多档续托](M11.md) | 退出强制附加筛选，改为时序诊断 | [M11_REAR_RETENTION_TIMELINE](D_M11_REAR_RETENTION_TIMELINE.md)：母确认后可得的续托仅描述，不回填、不等待、不新增母退出。 | D／仅诊断 |
| [M12 买卖恢复差](M12.md) | 改用有效期重叠新版 | [M12_VALIDITY_OVERLAP](V_M12_VALIDITY_OVERLAP.md)：在去损失比版上将1.5s配对限制降为诊断；保留各自3s、冲击可比及连续数量守卫，完整边界仍有效才确认。原版及SHOCK_COMPARABLE保留。 | P1／第1批 |
| [M13 撤供后再承接](M13.md) | 保持归档，停止跟进 | 保留历史；不重启数值优化或另起重复复验策略。 | A／不排队 |
| [M14 弱供连续受击](M14.md) | 退出独立优化，降为弱供诊断 | 使用[REPEATED_WEAK_ASK](D_REPEATED_WEAK_ASK.md)保留Ask双轮弱恢复描述，不继续优化其与Bid支撑相遇的独立组合。 | D／仅诊断 |
| [M15 撤供后的买入接替](M15.md) | 停止跟进，取消重构候补 | 交接与新供给保护未闭合；保留原文，不继续调参或新建分支。 | A／不排队 |
| [M16 承接提速](M16.md) | 本轮频次未达标；停止本任务主动跟进 | 来源可证明性阻断没有SOP增频解法；不继续调参或排输入审查。原SOP和历史保持；未进入独立验收或24小时测试。 | A／不排队 |
| [M17 卖压打不薄](M17.md) | 本轮频次未达标；停止跟进，取消重构候补 | 去桶会改变持续受压命题且与QR家族重叠；不自动启用SB_QR。保留历史；未进入独立验收或24小时测试。 | A／不排队 |
| [M18 加压不增损](M18.md) | 原版保留，交付恢复达标新版 | [M18_STABLE_RECOVERY](V_M18_STABLE_RECOVERY.md)：删除r2≥r1，两轮严格稳定、加压及双损失比较保留。已有R1/R2仍卡前段输入，未核实全轮收尾，不承诺新版解决零信号。 | P2／后备新版 |
| [M19 轻攻更薄](M19.md) | 退出独立优化，降为Ask响应诊断 | 使用[ASK_RESPONSE_CHANGE](D_ASK_RESPONSE_CHANGE.md)复用已有两轮证据，缺特征不延迟或取消母信号。 | D／仅诊断 |
| [M20 买托前聚](M20.md) | 退出独立形态优化，降为Bid分布诊断 | 使用[BID_DEPTH_PROFILE](D_BID_DEPTH_PROFILE.md)记录固定锚档及后档分布，不增加母策略准入门槛。 | D／仅诊断 |
| [M21 卖来供退](M21.md) | 改用端点共同确认新版 | [M21_ENDPOINT_COFLOW](V_M21_ENDPOINT_COFLOW.md)：双侧末100ms、首轮Bid10%/5%仅诊断；真实受卖、端点恢复、净减、来源/零BUY/F/U保持，后续100ms不再隐藏否决。旧版保留。 | P1／第2批 |
| [M22 近薄远厚](M22.md) | 保持归档，停止跟进 | 方向语意不足，不继续放宽形态；保留历史定义。 | A／不排队 |
| [M23 加托扛卖](M23.md) | 改用首SELL750ms仅诊断新版 | [M23_FIRST_SELL_WHILE_VALID](V_M23_FIRST_SELL_WHILE_VALID.md)：加托持续有效时等待首SELL，750ms不退出；保留100ms种子、首笔身份、500ms验证、Fpre→F及原种子3s。两份旧分支保留。 | P1／第1批 |

当前安排合计：2项P0、5项P1、2项P2、4项P3、5项诊断、5项停止跟进，共23个M编号。M7–M23中七个跟进分支、五项诊断、五项停止；不为归档编号另设增频目标。具体版本、与旧版差异及确认／失效核对见[本轮落实说明](M7_M23_FREQUENCY_DISPOSITION_20261117.md)。

**第1批专用交接已更新至v3：**[M12 / M23最新SOP信号测试模块交接说明](HANDOFF_BATCH1_M12_M23_SIGNAL_TEST.md)。固定入口为`M12_VALIDITY_OVERLAP`与`M23_FIRST_SELL_WHILE_VALID`，列明版本/hash、旧版差异、确认与失效等号、原截止及执行限制。M12的1.5s配对、M23的750ms首SELL均只诊断，不得在缓存/计时器/退出路径残留旧筛选；原3s不延长，短剩余寿命与执行受限单列。旧M9/M12交接适用保留的SHOCK_COMPARABLE；旧M21/M23交接及本说明v2的M23仍有750ms限制，不能代替本批。旧SOP保持，未启动测试或切换运行版本。

**第2批专用交接已更新至v2：**[M21最新SOP信号测试模块交接说明](HANDOFF_BATCH2_M21_SIGNAL_TEST.md)。唯一入口`M21_ENDPOINT_COFLOW`；核对诊断与首轮绝对材料性分离、端点50%、后续轮不再按100ms退出、原始零BUY至完整发布、双侧末量及连续F/U。旧NO_PREQUIET、单轮及主版SOP保留，本交接v1和旧M21/M23交接不能替代新版。

**第3批专用交接已更新至v2：**[M7 / M9最新SOP信号测试模块交接说明](HANDOFF_BATCH3_M7_M9_SIGNAL_TEST.md)。入口`M7_PAUSE_RELEASE`与`M9_EX_VALIDITY_OVERLAP`；核对单桶仅诊断、Kp后唯一新watch、AP1到期不取消再攻观察，以及EX从确认起连续维护、无补建/续期、q同输入50%优先和q后95%。M9首SELL提前交接未实施；旧并行/交接SOP及旧专用交接保留。

上述批次表示后续信号调试与核对的优先顺序，不是自动启动命令，也不要求前批先完成收益验收或24小时测试才能接收后批。用户明确优先取得足够频次以便短期评价：不承担必要机制证明的附加时间限制先降为诊断，有足够样本后再由用户模块评估筛选；必要来源、顺序和数量守卫保留。本次落实六项授权，新增M21/M7/M9三份SOP并更新第2/3批交接v2；第1批M12/M23及其他已有SOP不改；未启动测试、未实现适配器、未操作运行进程，批次顺序不变。

### 2026-11-17：M16 / M17此前频次阶段收尾（历史）

以下保留当时反馈与处理理由。其“先审输入”“重构候补”安排已由上方最新停止跟进决定取代，历史数字和原结果不变。

两者均完成原版及三轮调参回放，四版各为0/24有信号场，未达到本轮主口径10/24场门槛，均未进入独立验收或24小时测试。每个策略的四版使用各自同一批固定24场，不能合成96场独立样本，也不将两个策略的批次视为同一批。M16隔离4场、M17隔离1场，均保留在各自24场分母中；两批均有局部UNKNOWN。结论限于本次有限搜索未达标，不能推断所有参数均不可能达标或未知区间不存在机会。没有成交样本，不评价收益优劣。

- **M16先审输入**：合格参考从原版19次增加至最多49次，最终配对审查仍为0；主要阻断包括源顺序不可重建、BBO/深度不一致、质量过期及盘口不可用。暂停继续调整速度比例、参考门槛或等待时间，先区分可依据原始证据修复的重建问题与数据本身无法证明的部分；不删质量保护、不放宽原SOP。来源：[收尾报告](../../research_outputs/m1223_sim/M16/frequency-stage-closeout-20261116T235747/report.md)、[链条诊断](../../research_outputs/m1223_sim/M16/frequency-stage-closeout-20261116T235747/bottleneck-review.json)。
- **M17停止当前结构调参**：四版最终形成复核root分别为91、109、97、94个，可证明记录中均无三个250ms子窗都有直接SELL的案例；笔数、卖量和集中度也有重叠失败，另有root在质量或逐笔数量复核处提前终止。不能把全部失败归因于三桶，或推断只改桶数即可恢复这些root。重构仅作候补，先审独立交易语意与已有信号的重叠，不新增版本或启动后续测试。来源：[收尾报告](../../research_outputs/m1223_sim/M17/frequency-stage-closeout-20261117T062119/report.md)、[阻断诊断](../../research_outputs/m1223_sim/M17/frequency-stage-closeout-20261117T062119/bottleneck-review.json)。

上述报告及既有结果只读保留；以下为此前交接记录，当前入口和批次以本页上表及本轮落实说明为准。

此前第1批文档交接见[M9 / M12新版信号测试模块交接说明](HANDOFF_M9_M12_SIGNAL_TEST.md)：集中列出SOP入口与冻结身份、相对目录主版和实际改动基线的差异、确认／失效边界、执行可行性及最小反馈字段。仅供模块后续接收，不表示已适配或启动测试；M4、M6、M16的SOP保持不变。

此前第2批文档交接见[M7并行确认新版信号测试模块交接说明](HANDOFF_M7_PARALLEL_CONFIRM_SIGNAL_TEST.md)：明确AP1后新承接与暂停后全新再攻的两条证据链、原首笔起点门槛、私有AP占用、联合发布时点及先完成组件的原截止。仅整理文档，不表示已适配或启动测试；M7及M4、M6、M16的SOP均保持不变。

此前第3批文档交接见[M21 / M23新版信号测试模块交接说明](HANDOFF_M21_M23_SIGNAL_TEST.md)：列出单轮共同确认与缓冲留存的入口、冻结身份及新旧差异，重点核对零成交证据范围、窗内末量来源、M21本轮F/U、M23首SELL底线交接及各自原截止。仅整理文档，不表示已适配或启动测试；M21/M23及M4、M6、M16的SOP均保持不变。

## 已知调试结果与日期

更新／核对日期：**2026-11-17（Asia/Shanghai）**。日期优先采用来源明确记载的北京时间；只有目录日期时标注“收尾目录”，只有文档状态时标注“文档／核对日”，均不冒充实际测试起止时间。不使用文件mtime推断测试日期。

本节摘录已存在的报告和开发记录，不重跑行情、不重算PnL。原版、外部数值修订、当前V分支及执行模型分别标识；旧版结果不能替代新版验证。“未核实”表示本次所读材料不足，不等于从未测试、零信号或零收益。部分早期原始结果目录已不在当前工作区，本节链接仍可读的开发记录，不能据此声称原始结果可重新回放。

### M1–M11：已知历史记录

| 信号 | 日期与阶段 | 已知结果及范围 | 来源与当前版本边界 |
|---|---|---|---|
| M1 | 2026-11-02：cal-16观察与恢复记录；11-03：实盘启用记录 | 11-02当时已记录13场，10场完整，完整场中9信号覆盖5场；全额成交假设净额−15.3174 USDC，排队模型0笔明确成交、4笔不确定。同日评估目标延长至总计300场，并显式变更10ms乱序容忍口径；本次未核实300场最终汇总。11-03记录首笔真实订单ACK，但ACK不证明成交。 | [观察记录](../../docs/development/M1_ISOLATED_OBSERVATION.md)、[实盘开发记录](../../docs/development/M1_LIVE_DEVELOPMENT_NOTES.md)。不能把300场目标写成同口径完成300场，也不把假设收益写成实盘收益。 |
| M2 | 2026-11-05：12场公共观察；11-08：58市场执行回放 | 12场中2笔假设成交、假设结算收益−9.9998 USDC。11-08的58市场记录68信号、43发单；main/cancel-half确认模拟成交13单、未决28单，已知毛PnL−44.9242；main/trades-only成交4单、未决33单，已知毛PnL−17.7334。 | [12场记录](../../docs/development/M2_CANDIDATE_STUDY.md)、[58市场执行对照](../../docs/development/M234_MAIN_EXECUTION.md)。后者含42个完整窗和16个未完成预热窗，两排队情景不能相加，未决剩余收益不是零。 |
| M3 | 2026-11-05：12场公共观察；11-08：58市场执行回放 | 12场中6笔假设成交、假设收益−6.3746 USDC。58市场记录89信号、41发单；main/cancel-half确认模拟成交9单、未决25单，已知毛PnL−5.9317；main/trades-only无确认成交、未决30单，不能据此评价为零收益策略。 | [12场记录](../../docs/development/M2_CANDIDATE_STUDY.md)、[58市场执行对照](../../docs/development/M234_MAIN_EXECUTION.md)。范围、版本及未决限制同上。 |
| M4 | 2026-11-05：原始frequency-v2校准；11-08：执行回放；11-14：实盘代码快照 | 180个完整市场的历史频次估计中，旧基线0候选，所选宽档估计89个root、覆盖61场；不是成交验证。58市场执行回放记录55信号、27发单，main/cancel-half成交5单／未决21单／已知毛PnL−5.3663，main/trades-only成交2单／未决22单／−4.5011。11-14有实盘代码快照，用户此前已确认正式实盘。 | [原始频次校准](../../docs/development/M4_FREQUENCY_STUDY_20261105.md)、[执行对照](../../docs/development/M234_MAIN_EXECUTION.md)、[实盘快照](../../docs/development/M4_LIVE_RELEASE_20261114.md)。11-09后弱Ask损失规则已有修订，旧统计不重贴为当前实盘结果；本次未汇总真实成交收益。 |
| M5 | 2026-11-12：v7前向采集启动；11-13：来源批次开发记录 | 已纳入M5–M10 v7批次。11-13开发记录引用刚结束的288场录制，其中283个完整窗、5个不完整窗；本次未取得M5单策略的最终频次／成交汇总，不写成独立验收通过。 | [M5–M11历史开发记录](../../docs/development/M5_M11_SIGNAL_TESTS.md)。批次采集完成不等于M5经济有效。 |
| M6 | 2026-11-17 09:31：基线结果快照；10:02：停止确认 | 同批78个POST，新触价模型68笔已结算模拟成交、毛PnL−43.266 USDC；旧“指令价立即全额成交”假设78笔、毛PnL+315.753。快照含864个完整已结算场，另有98缺失、2不完整、1未结算场标记，不能称连续无缺口。10:02已有用户要求停止及服务退出的历史确认。 | [快照摘要](../../research_outputs/analysis/m6_pnl_20261117T0931/summary.json)、[口径核对](../../research_outputs/analysis/m6_pnl_20261117T0931/validation.json)、[停止确认](../../research_outputs/analysis/m6_stop_20261117T100257/stop-confirmation.json)。均为虚拟执行；09:31快照不是10:02停机最终账。新口径要求有效Best ask触及锚价减1 tick，不能混同普通Maker真实成交。 |
| M7 | 2026-11-12：旧数值校准收尾；11-16：并行确认新版文档 | 旧收尾记录校准6个信号，其他完整25场／288场验收未完成，不能自行写成6/24或全天达标。其后并行确认新版SOP及交接已完成；本次未核实该新版测试结果。 | [旧开发记录](../../docs/development/M5_M11_SIGNAL_TESTS.md)、[新版交接](HANDOFF_M7_PARALLEL_CONFIRM_SIGNAL_TEST.md)。旧v7结果不替代`M7_PARALLEL_CONFIRM`。 |
| M8 | 2026-11-12：v7批次启动；11-13开发记录 | 曾纳入M5–M10 v7采集，后续密度优化队列已排除M7/M8；本次未核实M8单策略的终态信号、成交和收益汇总，不填零、不标通过。 | [历史范围及版本记录](../../docs/development/M5_M11_SIGNAL_TESTS.md)。保留候补定位，不由采集启动推断策略验收完成。 |
| M9 | 2026-11-14：旧v8开发选型；11-15：前向观察收尾；11-16：EX交接新版文档 | 开发100场所选配置39个候选，未达40个目标。旧v8前向处理288场，其中284完整、4未在T15前暖机；全记录37信号、27发单／假设成交、17笔正数模拟成交、10未决；按M4活动隔离61场后的主统计仍为PARTIAL。 | [旧v8收尾与开发记录](../../docs/development/M5_M11_SIGNAL_TESTS.md)、[新版交接](HANDOFF_M9_M12_SIGNAL_TEST.md)。运行计划完成不等于经济验收通过；本次未核实`M9_EX_HANDOFF`测试结果。 |
| M10 | 2026-11-14：旧v8开发选型；11-15：前向观察收尾；11-16：诊断化 | 开发100场所选配置30个候选，未达40个目标。与M9同批旧v8前向全记录19信号、13发单／假设成交、11笔正数模拟成交、2未决；主统计仍为PARTIAL，不能因正常退出称经济有效。 | [旧v8记录](../../docs/development/M5_M11_SIGNAL_TESTS.md)、[当前M2诊断](D_M2_RETURN_DECAY.md)。旧独立策略结果不归入诊断特征或M2收益。 |
| M11 | 2026-11-17核对日；历史附加筛选记录 | 旧文档明确M11专项暂缓；其角色为M4/M9/旧M10的附加筛选。本次未核实可归属于当前完整留存定义的独立效果汇总，不填零或宣称通过。 | [历史测试范围](../../docs/development/M5_M11_SIGNAL_TESTS.md)、[M11定义](M11.md)。M10现为诊断，不能继续作为新母信号。 |

上述早期“假设成交”“正数MODELLED_FILL”“真实订单ACK”是不同证据。执行情景独立，未决可能与部分已成交订单重叠，不能把成交与未决简单加成互斥订单数，也不将已知部分PnL当最终总收益。

### M12–M23：阶段结果与缺失项

| 信号 | 日期与阶段 | 已知结果及结论 | 来源／当前版本边界 |
|---|---|---|---|
| M12 | 2026-11-16 00:08收尾报告；此前开发及两批独立验收 | 旧数值原版／R1／R2／R3有信号场依次3/24、6/24、9/24、10/24；独立验收为5/24、9/24，均未达10/24，未进入本轮24小时测试。所有列示阶段正数模拟成交均0，存在执行未决，假设收益见下表。 | [正式收尾](../../research_outputs/m1223_sim/M12/frequency-stage-closeout-20261116T000822/report.md)、[带报告时间的摘要](../../research_outputs/m1223_sim/M12/frequency-stage-closeout-20261116T000822/report.json)。不代表11-16去损失比版已测试，也不代表11-17新增`M12_VALIDITY_OVERLAP`的结果；旧数字不改贴新版。 |
| M13 | 2026-11-16（收尾目录日期） | 原版及三轮调参均0/24、0信号、0发单，无成交样本；本轮未达门槛，未进入独立验收或24小时测试。 | [收尾报告](../../research_outputs/m1223_sim/M13/frequency-stage-closeout-20261116T051714/report.md)。归档继续有效，不从有限搜索推断所有参数不可行。 |
| M14 | 2026-11-16（收尾目录日期） | 原版／R1／R2／R3有信号场0/24、0/24、1/24、0/24；R2仅1信号，四版均0发单、无成交样本。未进入独立验收或24小时测试。 | [收尾报告](../../research_outputs/m1223_sim/M14/frequency-stage-closeout-20261116T103336/report.md)。这是旧独立策略结果，当前仅保留弱供诊断。 |
| M15 | 2026-11-16收尾，16:31最终核对 | 原版／R1为0/24，R2／R3各1/24、各1信号；四版均0发单、无成交样本。未进入独立验收或24小时测试。 | [收尾报告](../../research_outputs/m1223_sim/M15/frequency-stage-closeout-20261116T162839/report.md)、[最终核对](../../research_outputs/m1223_sim/M15/frequency-stage-closeout-20261116T162839/final-delivery-validation.json)。停止原版调参，重构未形成新版。 |
| M16 | 2026-11-16收尾目录；11-17 00:00最终核对 | 原版及三轮调参均0/24、0信号、0发单；合格参考19→最多49次，最终配对审查仍0。未进入独立验收或24小时测试；暂停调参，先审输入可证明性。 | [收尾报告](../../research_outputs/m1223_sim/M16/frequency-stage-closeout-20261116T235747/report.md)、[最终核对](../../research_outputs/m1223_sim/M16/frequency-stage-closeout-20261116T235747/final-delivery-validation.json)。详见上文阶段说明，原SOP不变。 |
| M17 | 2026-11-17收尾，06:31最终核对 | 原版及三轮调参均0/24、0信号、0发单；最终复核91／109／97／94个root中，均无三桶都有直接SELL的案例，其他失败项重叠。未进入独立验收或24小时测试；停止当前结构调参，重构候补。 | [收尾报告](../../research_outputs/m1223_sim/M17/frequency-stage-closeout-20261117T062119/report.md)、[阻断与日期](../../research_outputs/m1223_sim/M17/frequency-stage-closeout-20261117T062119/bottleneck-review.json)。不把质量未知视为没有机会。 |
| M18 | 2026-11-17 10:03／10:29，R1／R2阶段摘要 | 两轮各0/24、0信号、0发单／模拟成交，均标`COMPLETED_NOT_PASSED_FIXED24`；参考确认25／46次，尚无最终配对审查。两轮为同批开发数据，24场均有局部UNKNOWN。本次未核实原版最终汇总、R3或全轮收尾，不写成“三轮全部结束”或独立验收结论。 | [R1摘要](../../research_outputs/m1223_sim/M18/frequency-revision-1/frequency-review-summary.json)、[R2摘要](../../research_outputs/m1223_sim/M18/frequency-revision-2/frequency-review-summary.json)。摘要中的independent review是同批频次核对，不是新的独立验收样本。 |
| M19 | 2026-11-16文档处理；11-17结果核对日 | 已降为Ask响应诊断；本次未核实旧独立策略或当前诊断的正式信号测试结果。设计处置不是测试失败数据。 | [设计更新](M17_M23_DESIGN_UPDATE.md)、[诊断SOP](D_ASK_RESPONSE_CHANGE.md)。不补零，不占独立测试队列。 |
| M20 | 2026-11-16文档处理；11-17结果核对日 | 已降为Bid深度分布诊断；本次未核实旧独立策略或诊断特征的正式测试结果。 | [设计更新](M17_M23_DESIGN_UPDATE.md)、[诊断SOP](D_BID_DEPTH_PROFILE.md)。不把文档交付称为信号验证。 |
| M21 | 2026-11-16新版SOP及交接文档日期；11-17核对日 | 单轮共同确认版已交付；本次未核实原版定量收尾或`M21_SINGLE_ROUND`测试结果。仍为显式选择的待验证分支。 | [新版SOP](V_M21_SINGLE_ROUND.md)、[交接说明](HANDOFF_M21_M23_SIGNAL_TEST.md)。删除第二轮的逻辑假说尚不是频次增量。 |
| M22 | 2026-11-16文档归档；11-17结果核对日 | 因当前设计方向含义不足而归档；本次未核实正式测试结果，不把逻辑归档记成实测0信号或亏损。 | [设计更新](M17_M23_DESIGN_UPDATE.md)、[历史SOP](M22.md)。停止跟进。 |
| M23 | 2026-11-16新版SOP及交接文档日期；11-17核对日 | 缓冲留存版已交付；本次未核实原版定量收尾或`M23_BUFFER_RETENTION`测试结果。 | [新版SOP](V_M23_BUFFER_RETENTION.md)、[交接说明](HANDOFF_M21_M23_SIGNAL_TEST.md)。允许零损失不等于已证明更多信号或更高结算概率。 |

M12旧数值版的关键经济记录按[原报告](../../research_outputs/m1223_sim/M12/frequency-stage-closeout-20261116T000822/report.md)保留，单位USDC，均为同SUBMIT群体的**全额成交假设**，不是已确认模拟或真实成交收益：

| 阶段（收尾报告日期2026-11-16） | 有信号场/24 | 信号 | 发单／假设成交 | 正数模拟成交 | 执行未决 | 假设毛PnL |
|---|---:|---:|---:|---:|---:|---:|
| 开发修订3 | 10/24 | 14 | 8／8 | 0 | 5 | −1.3637 |
| 独立验收1 | 5/24 | 9 | 6／6 | 0 | 6 | −1.3884 |
| 独立验收2 | 9/24 | 15 | 7／7 | 0 | 5 | +24.9188 |

独立验收2去最高盈利1场后假设毛PnL为+0.5085，去最高盈利3场后为−4.4281；这里按**盈利市场**剔除，不改写成“去3笔”。官方结算已完成也不消除执行未决。开发四版共享同一批24场，两批独立验收是另外两批互不重叠的24场，不能把开发版本重复计算为独立样本或拼成连续24小时。

M13–M17每个策略的四版同样各复用该策略自己的24场，不是每个策略96场独立样本。隔离和局部UNKNOWN按原报告保留在分母或另列；无成交样本的PnL不填零。M18阶段摘要也保留其原同批口径，不提前外推最终结果。

本节只增加历史结果与日期，除依据已读快照纠正M6阶段和补注M18阶段反馈外，不重新排名研究队列，不修改SOP、原始结果、catalog、测试配置或任何运行进程。

## 主版定义与历史来源版本

下表收录 M1–M23 的来源版本和 M24 的初版定义。M10/M13/M14/M15/M19/M20/M22数值和历史规则保留，另列研究状态；M7/M12/M16/M17/M18/M21/M23原文件字节不变。M11旧M10母策略引用也只属历史，不把诊断特征当新母信号。

| 策略 | 名称 | 继承的信号来源版本 |
|---|---|---|
| [M1](M1.md) | 承接后的卖压余波 | `m1-hold-v2/cal-16` |
| [M2](M2.md) | 买入进攻后的受控回流；新增回流变化诊断入口 | `m2-candidates-v1/all_candidates`，母定义不变 |
| [M3](M3.md) | 反击后守价 | `m3-ca-then-ba-v1` |
| [M4](M4.md) | 守强攻弱 | `m4-frequency-v2` |
| [M5](M5.md) | 强压稳接 | `m5-overlap-recovery-v1` |
| [M6](M6.md) | 撤供留托 | `m6-withdrawal-supported-v1.1` |
| [M7](M7.md) | 歇后再攻，原版保留；后续仅并行确认分支 | `m7-short-pause-single-refill-v1` |
| [M8](M8.md) | 失守后修复 | `m8-depth-repair-v1` |
| [M9](M9.md) | 余波复验 | `m9-single-support-weak-retest-v1` |
| [M10](M10.md) | 回流递减，已降为M2诊断 | `m10-two-return-decay-v1`（历史） |
| [M11](M11.md) | 多档续托 | `m11-full-retention-v1.1` |
| [M12](M12.md) | 买卖恢复差，后续有效期重叠新版；去损失比版保留 | `m12-response-asymmetry-v1.2`（原版保留） |
| [M13](M13.md) | 撤供后再承接，已归档停止跟进 | `m13-withdrawal-retest-v1.2`（历史） |
| [M14](M14.md) | 弱供连续受击，已降为家族诊断 | `m14-repeated-weak-supply-v1.2`（历史） |
| [M15](M15.md) | 撤供后的买入接替，原版停止优化 | `m15-withdrawal-buy-handoff-v1.2`（历史；重构未定义） |
| [M16](M16.md) | 承接提速，本轮频次未达标；暂停调参，先审输入 | `m16-faster-absorption-v1.2`（原版保留） |
| [M17](M17.md) | 卖压打不薄，本轮频次未达标；停止当前结构调参，重构候补 | `m17-retained-depth-under-selling-v1.1`（原版保留） |
| [M18](M18.md) | 加压不增损 | `m18-escalating-hit-contained-loss-v1.1` |
| [M19](M19.md) | 轻攻更薄，已降为响应变化诊断 | `m19-lighter-hit-larger-loss-v1`（历史） |
| [M20](M20.md) | 买托前聚，已降为三档分布诊断 | `m20-front-concentrated-bid-support-v1`（历史） |
| [M21](M21.md) | 卖来供退，原版保留；后续单轮分支 | `m21-supply-retreat-under-selling-v1.1` |
| [M22](M22.md) | 近薄远厚，已归档 | `m22-ask-depth-backloading-v1`（历史） |
| [M23](M23.md) | 加托扛卖，原版保留；后续留存分支 | `m23-preloaded-bid-buffer-test-v1` |
| [M24](M24.md) | 哪边在补 | `m24-two-sided-net-replenishment-v1`（独立初版） |

## 独立分支 SOP 存档与当前入口

截至 2026-11-18，目录包含 24 份 M 定义、15 份原语、17 份独立 V 定义、5 份诊断，另有 8 份冻结历史备选。前 23 项测试顺位见[逐项工作清单](SIGNAL_TEST_PRIORITY_20261118.md)。M12_SHOCK_COMPARABLE、M21_SINGLE_ROUND、M21_COFLOW_NO_PREQUIET、M7_PARALLEL_CONFIRM、M9_EX_HANDOFF、M23_BUFFER_RETENTION及M23_BUFFER_NO_LOSS_COVER为保留旧版，M17_SB_QR停止跟进，M4_DF_AP仅作既有研究存档。

M17的[V_M17_SB_QR](V_M17_SB_QR.md)作为既有定义保留，仍继承三桶直接SELL及逐笔复核要求，不能直接视为本轮低频问题的解决方案，不自动成为重构入口或进入测试队列。2026-11-17最新决定取消其重构候补，不再跟进。

| 独立文件 | 别名与原语顺序 | 关键确认／失效边界 |
|---|---|---|
| [M17_SB_QR](V_M17_SB_QR.md) | 短退补回后扛卖：`SB_BID → 新SELL → 新QR验证` | SB750ms内首SELL开启历史交接；全新750ms验证通过才确认。原SB绝对底线一直保留，截止为SB确认+3s。 |
| [M4_DF_AP](V_M4_DF_AP.md) | 供弱后续攻：`[Bid支撑 ∥ DF_ASK] → 全新AP_BUY` | 新AP须实际打到弱供原档。原支撑、DF和AP共同有效；新稳定Ask轮清掉旧DF后不能换ID续上。 |
| [M9_EX_HANDOFF](V_M9_EX_HANDOFF.md) | 余波接棒复验：`S ∥ EX → 新R首次H/L合格 → hist(EX) → 完整R` | 基于M9 v8；交接前旧EX仍可否决，合法交接后撤去EX当前态50%否决，保留95%滚动SELL、新R、原档及质量守卫。同输入先失效后交接。 |
| [M12_SHOCK_COMPARABLE](V_M12_SHOCK_COMPARABLE.md) | 保留对照：同击异损、买强卖弱 | 基于M12第三轮参数；保留u比0.10–10，删除d比及其推导Bid下限，仍要求确认间隔≤1.5s。本文件保持，不作为当前第1批入口。 |
| [M12_VALIDITY_OVERLAP](V_M12_VALIDITY_OVERLAP.md) | 当前入口：承接仍在、弱供未复，`live(S_BID) ∥ live(W_ASK) + u可比` | 在去损失比版上将1.5s降为诊断；两侧各自3s内连续有效且完整发布时仍重叠才确认，不按配对重计。Bid=FB有效、Ask=FA失效，短剩余寿命与执行受限分开。 |
| [M7_PARALLEL_CONFIRM](V_M7_PARALLEL_CONFIRM.md) | 歇后攻托同证：`AP1→新S`与`AP1→pause→新AP2`联合 | 沿v7数值。AP2必须从暂停确认后开始，可早于S完成；S/AP2均当前有效才确认，截止取两组件与AP1历史原期限最小值。 |
| [M21_SINGLE_ROUND](V_M21_SINGLE_ROUND.md) | 一轮卖来供退：`单轮S_BID ∥ 同窗Ask无BUY净减` | 删除第二轮及跨轮比较；500ms共同确认、c+3s，形成零BUY至完整发布，守本轮F/U。 |
| [M23_BUFFER_RETENTION](V_M23_BUFFER_RETENTION.md) | 加托后受卖留存：`预加托→首SELL→完整受卖留存` | 删除最低L与末段上限；保留H≥L、80%→20%合法交接、窗内末量与种子3s。 |
| [M8_RESPONSE_REPAIR](V_M8_RESPONSE_REPAIR.md) | 弱后复强：`历史弱W→首个合格新R` | 删d比；真实H/L、旧held与60%恢复保留，cR<cW+5s，新支撑8s。 |
| [M5_RELATIVE_RECOVERY_DIAGNOSTIC](V_M5_RELATIVE_RECOVERY_DIAGNOSTIC.md) | 强压中两轮各自稳接，第二轮可相对较弱 | 仅将两轮恢复率之比降为诊断，保留 AP 形成窗与第二轮受击窗的正时长重叠和原截止。 |
| [M18_STABLE_RECOVERY](V_M18_STABLE_RECOVERY.md) | 加压不增损、恢复达标 | 删r2≥r1，两轮各自严格稳定及原H/Q0/L/d比较保留，新F2和c2+3s不变。 |
| [M21_COFLOW_NO_PREQUIET](V_M21_COFLOW_NO_PREQUIET.md) | 同窗卖来供退、历史波动仅诊断 | 删前250ms平稳，当前初量/链、新Ask净减、形成零BUY及c+3s保留。 |
| [M23_BUFFER_NO_LOSS_COVER](V_M23_BUFFER_NO_LOSS_COVER.md) | 保留对照：加托后受卖留存、不限定H≥L | H仍材料性，L>H允许；Fpre→F、窗内末量、首SELL750ms及种子3s保留。本文件不改，非当前第1批入口。 |
| [M23_FIRST_SELL_WHILE_VALID](V_M23_FIRST_SELL_WHILE_VALID.md) | 当前入口：托在再验，首卖750ms仅诊断 | 在NO_LOSS_COVER上只删750ms硬限制；首SELL前持续Fpre，合法后切F，完整500ms及实际发布必须<cS+3s。严格750ms双时钟标签仅诊断，不重开原期限。 |
| [M21_ENDPOINT_COFLOW](V_M21_ENDPOINT_COFLOW.md) | `同轮受卖补回 ∥ 原Ask零BUY净减 → F/U有效时原P成交` | 双侧末100ms与Bid10%/5%仅诊断；保留绝对材料性、端点50%、Ask净减、来源/零BUY/F/U及c+3s；后续100ms不隐藏否决 |
| [M7_PAUSE_RELEASE](V_M7_PAUSE_RELEASE.md) | `AP1→总量pause→全新AP2`与`AP1→新S`联合 | 暂停单桶35%仅诊断；Kp后仅解除冻结AP1占用，唯一新watch完整750ms，S/AP2/root原截止保持 |
| [M9_EX_VALIDITY_OVERLAP](V_M9_EX_VALIDITY_OVERLAP.md) | `EX确认起持续维护3s ∥ 当前S → 新R的H/L资格交接 → 完整复验` | EX单桶30%仅诊断；允许原生750ms后配对，不补建或续期；q同输入50%优先、q后95%不变，不提前到首SELL交接 |

新增诊断[M11_REAR_RETENTION_TIMELINE](D_M11_REAR_RETENTION_TIMELINE.md)取代M11独立筛选研究，原M11文件保留。诊断不改母确认或执行，所有后续字段带真实可得时刻。

首个JSON固定参数、来源hash与快照，catalog按显式版本登记。新增M9/M12/M7/M21/M23、诊断特征及转为历史定义的M14/M15/M19/M20/M22采用引用式登记：读取`parameters_file`的首个JSON块，以`parameters_sha256`校验其按UTF-8、sort_keys、紧凑分隔符序列化后的hash；全文另有`sha256`。不把catalog未内联参数误作空参数或默认主版，也不代表现有模块已支持读取新分支。M主版形成数值及15原语不变；M10/M13/M14/M15/M19/M20/M22另有当前研究状态。研究优先检查机制增量、首选事件和guard冲突；不按假设交易表现排序。

此前M17/M4两分支的v1.1仅补语意与边界，保留数值和状态规则。SB量界可能已经覆盖QR的70%／90%条件，按max合并计算，不重复计证据；M4的新AP与HA不保证已完成新弱Ask轮。旧DF可能与当前QR_ASK“买来供不薄”并存，不能把旧弱供未失效称为新弱供验证。详见这两份SOP第8节。

2026-11-16按用户指令新增M9/M12两份增频逻辑分支：以[已审查基线快照](../signal_discovery/spec_archive/M9_M12_FREQUENCY_BASIS_20261116.json)为准，分别改EX交接后的失效归属和跨侧损失比条件。数值沿既有测试版本，不混用目录主版更严格参数。两份SOP第7节逐项核对同输入优先级、等号、旧截止、发布前guard和选根影响；[文档检查回执](../../research_outputs/signal_discovery/m9_m12_frequency_sop_review_20261116.json)只验证交付定义和引用。未做测试，未证明频次增量；主版与外部实现均保留。

2026-11-17后续按用户授权新增[M12有效期重叠SOP](V_M12_VALIDITY_OVERLAP.md)，以保留的SHOCK_COMPARABLE及其全文hash为直接基线。语意由近时冲击比较调整为已受击验证的承接/弱供同时有效；1.5s仅诊断，当前原数量路径、u可比与各自3s期限保留。第7节核对2.2s配对、3s到期等号、实际完整发布延迟、破界后恢复及0.1s剩余寿命等情况；这些是定义检查，不是测试结果。第一批交接已同步，其他策略与已有SOP未改。

同日随后新增[M23首SELL有效期版](V_M23_FIRST_SELL_WHILE_VALID.md)，直接基线NO_LOSS_COVER完整保留。750ms只作received/available双时钟历史对照；保留先加托后受卖、种子100ms及零SELL、500ms验证、合法80%→20%底线交接和原种子3s。明确0.75s不提前退出、2.5s首SELL无法赶上完整确认、最终到期等号优先，以及短寿命信号与执行余量分开。第一批交接v3已同步，未运行测试，旧结果不改贴本版。

同日新增M7并行确认SOP，并完成M10诊断化、M13归档；[基线与旧文档快照](../signal_discovery/spec_archive/M7_M10_M13_DISPOSITION_BASIS_20261116.json)冻结来源。M7第7节核对AP2先完成时的到期、同输入失效优先、pause历史和冻结首选证据；[文档回执](../../research_outputs/signal_discovery/m7_m10_m13_document_review_20261116.json)仅验证定义、引用与状态一致性，不代表测试通过。

同日按M12–M15低频反馈完成 [设计取舍](M12_M15_DESIGN_REVIEW.md)：M12继续已有新版、M13维持归档、M14降为 [双轮弱供诊断](D_REPEATED_WEAK_ASK.md)、M15原版停止调参。M14诊断复用明确来源profile的Ask链，可记录无Bid的组件事实，但无独立锚、准入或退出权；不把M14当M12的严格子集。M15候补的并行确认、LW历史交接与供给接管尚有未决定义，不列为可测试SOP。原版快照与摘要来源见 [本轮基线](../signal_discovery/spec_archive/M12_M15_DISPOSITION_BASIS_20261116.json)，一致性见 [文档回执](../../research_outputs/signal_discovery/m12_m15_design_review_20261116.json)。

2026-11-16按用户排除M16后完成 [M17–M23设计更新](M17_M23_DESIGN_UPDATE.md)：新增两份V与两份D，M22归档；M16及M17/M18/M21/M23原版保持。两份V各自第7节核对确认/失效、同输入优先级及旧时钟；来源见 [基线快照](../signal_discovery/spec_archive/M17_M23_DISPOSITION_BASIS_20261116.json)，[文档回执](../../research_outputs/signal_discovery/m17_m23_sop_review_20261116.json)只证明定义与引用一致，不代表测试。

## 原语与研究家族

15份独立原语见[原语索引](PRIMITIVES.md)，按[语意分类与冲突审查](PRIMITIVE_SEMANTIC_REVIEW.md)的6类、13个研究入口组织。本轮全部原语文件保持不变；旧单轮支撑、COMMON严格S、P_RF两轮和BA不能互换，策略内的速度／幅度／多档构成条件仍明确保留。

[SR](P_SR.md)、[QR](P_QR.md)、[XR](P_XR.md)、[LW](P_LW.md)、[RD](P_RD.md)、[XW](P_XW.md)、[QO](P_QO.md)、[SB](P_SB.md)、[AP](P_AP.md)、[AA](P_AA.md)、[RF](P_RF.md)、[EX](P_EX.md)、[DF](P_DF.md)、[CA](P_CA.md)、[BA](P_BA.md)。

研究按家族组织：M1/M5/M9卖压承接；M2/M7买攻后回流；M4/M12承接与弱供；M6/M21撤供与支撑；M8响应修复、M18耐压、M23预加托保量保留不同分支。M10/M11/M14/M19/M20仅诊断；M13/M15/M16/M17/M22停止跟进，不继续输入审查或重构候补。原家族ID和定义保留供溯源，不将家族成员自动AND/OR或合并账本。[历史家族审查](FAMILY_CONSOLIDATION.md)与旧排期按历史阅读，最新研究身份以本页、[落实说明](M7_M23_FREQUENCY_DISPOSITION_20261117.md)及catalog的research_dispositions为准。

## 共用定义与历史来源

[COMMON](COMMON.md)及[LEGACY_COMMON](LEGACY_COMMON.md)字节不变。新SOP的calculation_contract只继承它们的计算、时钟、质量和信号guard；旧post-only、FIFO、400ms、预算等放入单独执行参考，不能直接当作两种执行方式的共同确认事实。

[输入能力](INPUT_CAPABILITIES.md)、[交接时钟](CAUSAL_HANDOFF_REVIEW.md)、[零记录证据](ABSENCE_EVIDENCE_REVIEW.md)、[深度批次语意](DEPTH_BATCH_SEMANTICS.md)继续用于定义核对。缺字段标UNKNOWN/UNAVAILABLE，只有summary或旧stable布尔值不能冒充完整路径。以上文档若含旧执行讨论，以本轮分层合同为当前口径。

[研究协议v2](RESEARCH_PROTOCOL.md)明确当前研究、独立分支、执行分层和资源边界。[Maker可行性](MAKER_FEASIBILITY.md)、[Maker准入](MAKER_ADMISSION_REVIEW.md)、[成交选择](MAKER_SELECTION_REVIEW.md)保留为此前Maker阶段和后续评价参考，不在本轮执行其测试要求。

[此前目录与完整交付记录](../signal_discovery/spec_archive/README-63f4088689fca588.md)保留各阶段来源、参数版本和历史研究顺序。[暂停阶段报告](../signal_discovery/STAGE_REPORT_20261112_PAUSED.md)是当时状态记录；当前按用户单次指令继续整理，每小时汇报仍关闭。小时状态以`research_outputs/signal_discovery/control.json`、`latest.json`及自己的timer为准；队列请求状态与实际送达分开。

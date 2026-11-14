# 盘口信号 SOP 工具箱

已按用户2026-11-12最新指令停止每小时汇报及本线程自动小时唤醒：本任务control.enabled=false，小时timer已禁用并停止。已有信号与SOP保留，后续按用户指令继续。测试仍由用户模块负责。

## 当前交易语意

**信号有效期间在固定锚价成交。** 信号形成、证据交接、有效等待、失效与原截止先独立定义；Maker/Taker分别作为执行方式列出。成交方式不替代形成证据，不改变原锚，不延长证据寿命。本单以Maker成交也不自动成为一次新的承接确认。

- [固定锚价信号／执行合同](FIXED_ANCHOR_CONTRACT.md)：统一状态、时钟、待执行意向、两种执行接口及历史执行参考。
- [M1–M23原语与顺序总表](STRATEGY_PRIMITIVE_MAP.md)：全部23个主版、统一成交语意和家族归并。
- [确认／失效冲突审查](FIXED_ANCHOR_REVIEW.md)：逐策略截止与交接核对，区分信号矛盾、数量无解和执行不可准入。
- [文档一致性回执](../../research_outputs/signal_discovery/fixed_anchor_review.json)：参数、源快照、JSON和引用检查；不代表信号测试或实现支持。

23份主SOP均采用`mN-fixed-anchor-entry-v1`，保留`basis_version`和旧完整文档快照。原形成数字、信号数量守卫、期限及原语定义保持；新的执行分层不冒称旧Maker运行版本完整行为不变。8份旧显式备选作为冻结历史Maker规格保留，catalog登记definition_file，未自动升级为新Taker版本。

正常未交叉的同token盘口下，原Bid P仍有量意味着Ask高于P，因此这些主版保留的原Bid存在/支撑条件与同价Taker买入没有共同准入状态。文件已将此列为执行限制；不会为取得Taker成交而忽略原档丢失或底线失效。信号有效与某执行方式当前可用分别记录。

交易数、胜率、PnL和执行性能留给后续用户模块。本轮不计算、不重新排序收益、不把未测试的组合称为已提高结算概率。

## 独立策略 SOP

| 策略 | 名称 | 继承的信号来源版本 |
|---|---|---|
| [M1](M1.md) | 承接后的卖压余波 | `m1-hold-v2/cal-16` |
| [M2](M2.md) | 买入进攻后的受控回流 | `m2-candidates-v1/all_candidates` |
| [M3](M3.md) | 反击后守价 | `m3-ca-then-ba-v1` |
| [M4](M4.md) | 守强攻弱 | `m4-frequency-v2` |
| [M5](M5.md) | 强压稳接 | `m5-overlap-recovery-v1` |
| [M6](M6.md) | 撤供留托 | `m6-withdrawal-supported-v1.1` |
| [M7](M7.md) | 歇后再攻 | `m7-short-pause-single-refill-v1` |
| [M8](M8.md) | 失守后修复 | `m8-depth-repair-v1` |
| [M9](M9.md) | 余波复验 | `m9-single-support-weak-retest-v1` |
| [M10](M10.md) | 回流递减 | `m10-two-return-decay-v1` |
| [M11](M11.md) | 多档续托 | `m11-full-retention-v1.1` |
| [M12](M12.md) | 买卖恢复差 | `m12-response-asymmetry-v1.2` |
| [M13](M13.md) | 撤供后再承接 | `m13-withdrawal-retest-v1.2` |
| [M14](M14.md) | 弱供连续受击 | `m14-repeated-weak-supply-v1.2` |
| [M15](M15.md) | 撤供后的买入接替 | `m15-withdrawal-buy-handoff-v1.2` |
| [M16](M16.md) | 承接提速 | `m16-faster-absorption-v1.2` |
| [M17](M17.md) | 卖压打不薄 | `m17-retained-depth-under-selling-v1.1` |
| [M18](M18.md) | 加压不增损 | `m18-escalating-hit-contained-loss-v1.1` |
| [M19](M19.md) | 轻攻更薄 | `m19-lighter-hit-larger-loss-v1` |
| [M20](M20.md) | 买托前聚 | `m20-front-concentrated-bid-support-v1` |
| [M21](M21.md) | 卖来供退 | `m21-supply-retreat-under-selling-v1.1` |
| [M22](M22.md) | 近薄远厚 | `m22-ask-depth-backloading-v1` |
| [M23](M23.md) | 加托扛卖 | `m23-preloaded-bid-buffer-test-v1` |

## 当前新增分支 SOP

以下两份为未测试假说，归入已有家族；不新增M编号，不宣称已提高结算概率。当前目录为23份主策略、15份原语、2份当前独立分支；另有8份冻结历史备选。

| 独立文件 | 别名与原语顺序 | 关键确认／失效边界 |
|---|---|---|
| [M17_SB_QR](V_M17_SB_QR.md) | 短退补回后扛卖：`SB_BID → 新SELL → 新QR验证` | SB750ms内首SELL开启历史交接；全新750ms验证通过才确认。原SB绝对底线一直保留，截止为SB确认+3s。 |
| [M4_DF_AP](V_M4_DF_AP.md) | 供弱后续攻：`[Bid支撑 ∥ DF_ASK] → 全新AP_BUY` | 新AP须实际打到弱供原档。原支撑、DF和AP共同有效；新稳定Ask轮清掉旧DF后不能换ID续上。 |

首个JSON固定参数、来源hash与快照，catalog按显式版本登记。当前新增分支不改变M1–M23主定义与15原语。研究优先检查机制增量、首选事件和guard冲突；不按假设交易表现排序。

本次两分支修订为v1.1，仅补语意与边界，保留数值和状态规则。SB量界可能已经覆盖QR的70%／90%条件，按max合并计算，不重复计证据；M4的新AP与HA不保证已完成新弱Ask轮。旧DF可能与当前QR_ASK“买来供不薄”并存，不能把旧弱供未失效称为新弱供验证。详见各SOP第8节；本轮无新增编号或分支。

## 原语与研究家族

15份独立原语见[原语索引](PRIMITIVES.md)，按[语意分类与冲突审查](PRIMITIVE_SEMANTIC_REVIEW.md)的6类、13个研究入口组织。本轮全部原语文件保持不变；旧单轮支撑、COMMON严格S、P_RF两轮和BA不能互换，策略内的速度／幅度／多档构成条件仍明确保留。

[SR](P_SR.md)、[QR](P_QR.md)、[XR](P_XR.md)、[LW](P_LW.md)、[RD](P_RD.md)、[XW](P_XW.md)、[QO](P_QO.md)、[SB](P_SB.md)、[AP](P_AP.md)、[AA](P_AA.md)、[RF](P_RF.md)、[EX](P_EX.md)、[DF](P_DF.md)、[CA](P_CA.md)、[BA](P_BA.md)。

研究归并沿用12个方向家族和M11公共过滤器：M1/M5/M9卖压承接；M2/M7/M10买攻后回流；M4/M12/M14/M19承接与弱供；M6/M13/M15/M21撤供与留托。M3/M8/M16/M17/M18/M20/M22/M23保留不同观察维度。没有创建联合运行策略或共享成交账本。[历史家族审查](FAMILY_CONSOLIDATION.md)保留形成差异，Maker执行语句按其历史范围阅读。

## 共用定义与历史来源

[COMMON](COMMON.md)及[LEGACY_COMMON](LEGACY_COMMON.md)字节不变。新SOP的calculation_contract只继承它们的计算、时钟、质量和信号guard；旧post-only、FIFO、400ms、预算等放入单独执行参考，不能直接当作两种执行方式的共同确认事实。

[输入能力](INPUT_CAPABILITIES.md)、[交接时钟](CAUSAL_HANDOFF_REVIEW.md)、[零记录证据](ABSENCE_EVIDENCE_REVIEW.md)、[深度批次语意](DEPTH_BATCH_SEMANTICS.md)继续用于定义核对。缺字段标UNKNOWN/UNAVAILABLE，只有summary或旧stable布尔值不能冒充完整路径。以上文档若含旧执行讨论，以本轮分层合同为当前口径。

[研究协议v2](RESEARCH_PROTOCOL.md)明确当前研究、独立分支、执行分层和资源边界。[Maker可行性](MAKER_FEASIBILITY.md)、[Maker准入](MAKER_ADMISSION_REVIEW.md)、[成交选择](MAKER_SELECTION_REVIEW.md)保留为此前Maker阶段和后续评价参考，不在本轮执行其测试要求。

[此前目录与完整交付记录](../signal_discovery/spec_archive/README-63f4088689fca588.md)保留各阶段来源、参数版本和历史研究顺序。[暂停阶段报告](../signal_discovery/STAGE_REPORT_20261112_PAUSED.md)是当时状态记录；当前研究已恢复。小时状态以`research_outputs/signal_discovery/control.json`、`latest.json`及自己的timer为准；队列请求状态与实际送达分开。

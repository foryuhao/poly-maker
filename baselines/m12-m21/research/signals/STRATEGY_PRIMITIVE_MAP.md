# M1–M23 原语、时序与固定锚价成交语意

版本 `strategy-primitive-map-v2.11`。信号语意为**信号有效期间在固定锚价成交**，Maker/Taker 单列。2026-11-18 登记 M5 两轮各自稳接分支与 M8 交接。新的开发优先级、已有观察和暂缓项见[逐项工作清单](SIGNAL_TEST_PRIORITY_20261118.md)。前一日的各批分支及其变更见[11 月 17 日交接说明](M7_M23_FREQUENCY_DISPOSITION_20261117.md)。

```json
{
  "document_type": "strategy_primitive_semantic_map",
  "version": "strategy-primitive-map-v2.11",
  "status": "DOCUMENT_LOGIC_REVIEW_ONLY",
  "main_strategies": 23,
  "primitive_ids": 15,
  "historical_strategy_variants": 8,
  "current_independent_variant_sops": 17,
  "diagnostic_feature_sops": 5,
  "diagnostic_only_strategy_ids": [
    "M10",
    "M11",
    "M14",
    "M19",
    "M20"
  ],
  "archived_no_followup_strategy_ids": [
    "M13",
    "M15",
    "M16",
    "M17",
    "M22"
  ],
  "variant_only_followup": {
    "M5": "M5_RELATIVE_RECOVERY_DIAGNOSTIC",
    "M8": "M8_RESPONSE_REPAIR",
    "M12": "M12_VALIDITY_OVERLAP",
    "M21": "M21_ENDPOINT_COFLOW",
    "M23": "M23_FIRST_SELL_WHILE_VALID"
  },
  "stopped_design_redesign_idea_only_strategy_ids": [],
  "directional_research_families": 12,
  "overlays": 1,
  "signal_contract": "fixed-anchor-signal-v1",
  "execution_contract": "fixed-anchor-execution-v1",
  "formation_numeric_parameters_changed": false,
  "signal_guard_numeric_parameters_changed": false,
  "unchanged_scope": "ALL_ORIGINAL_MAIN_SOPS_AND_EXISTING_V_D_P_COMMON_CONTRACTS",
  "parameter_change_scope": "MAIN_SOPS_ONLY_NEW_VARIANTS_EXPLICIT",
  "new_variant_logic_changes": [
    "M9_EX_HANDOFF",
    "M12_SHOCK_COMPARABLE",
    "M7_PARALLEL_CONFIRM",
    "M21_SINGLE_ROUND",
    "M23_BUFFER_RETENTION",
    "M8_RESPONSE_REPAIR",
    "M18_STABLE_RECOVERY",
    "M21_COFLOW_NO_PREQUIET",
    "M23_BUFFER_NO_LOSS_COVER",
    "M12_VALIDITY_OVERLAP",
    "M23_FIRST_SELL_WHILE_VALID",
    "M21_ENDPOINT_COFLOW",
    "M7_PAUSE_RELEASE",
    "M9_EX_VALIDITY_OVERLAP",
    "M5_RELATIVE_RECOVERY_DIAGNOSTIC"
  ],
  "tests_run": false,
  "current_research_status_authority": "SIGNAL_TEST_PRIORITY_20261118.md"
}
```

## 1. 读法与版本

`A→B`表示由A之后的新输入形成B；`A∥B`表示可并行形成但组合时需同时可用。`hist(A)`仅作历史，`live(A)`按其原版本仍当前有效；历史交接只在SOP指定的事件发生，不能救活先前已失效的root。

RF2是旧两轮滞回恢复；S_L/R_L为各旧策略单轮构件；S_C/R_C为COMMON严格500ms轮；W_C为弱轮。它们不能直接互换，单轮S不冒称P_RF两轮或BA。M8旧DF_BID不是P_DF强转弱主版，M21主版不直接调用XW，M23主版没有必需的XR。AA与SB未在这23个主版本中新增调用。

执行写成`E(P,G,d; mode)`：P为固定原Bid，G为上文当前守卫，d为原信号截止，mode显式选择Maker/Taker。具体执行窗口和准入单列；不能因选择Taker就删除原Bid存在或floor。正常未交叉盘口下，同价Taker与原Bid正量守卫没有交集，详见[分层合同](FIXED_ANCHOR_CONTRACT.md)。

## 2. 23个M编号的历史定义索引（当前入口另列）

下表保留各原SOP的形成语意。原M11仍是历史过滤器定义，当前改为[M11时序诊断](D_M11_REAR_RETENTION_TIMELINE.md)；M16/M17及其旧分支停止跟进但不改文件。旧文中M15候补、M17/M18主版优先等研究安排不再作为当前入口。11月17日累计新增的九份V在第7节列出，M21/M7/M9本次各有独立新版；M12先前去损失比版、M23先前NO_LOSS_COVER保留为对照。

| 策略 | 原语与顺序 | 统一成交语意 |
|---|---|---|
| [M1 承接后的卖压余波](M1.md) | `hist(AP_SELL@M1) → live(EX_SELL@M1)`，与 `live(RF2@M1)` 重叠；AP/EX/RF分别对应原语的旧版本语意，消费端仍按本策略。 | 多轮受卖承接证据仍可用，先前卖压已经衰竭；在这一状态有效期间，于原RF锚价成交。 |
| [M2 买入进攻后的受控回流](M2.md) | `hist(AP_BUY@M2) → 新R_L1 → 新R_L2 = RF2@M2`。这是买攻后新增承接，不是 `EX_BUY → RF`。 | 买方曾进攻，随后全新两轮卖出回流已被承接；在新承接有效期间，于其固定锚价成交。主版没有BUY衰竭门槛。 |
| [M3 反击后守价](M3.md) | `live(CA@M3) → 全新live(BA@M3)`；所用CA、BA即P_CA/P_BA选定的M3版本。 | 先有CA反击，再有CA之后同原Bid的新BA守价；在两事实及原买一身份仍有效期间，于该锚价成交。 |
| [M4 守强攻弱](M4.md) | `live(S_L,BID@M4) ∥ live(DF_ASK@frequency-v2)`。DF对应P_DF主版；Bid是单轮旧稳定支撑，不是P_RF两轮或COMMON严格S。 | Bid真实受卖后稳定补回，Ask对可比冲击的恢复比过去减弱；在两侧事实重叠有效期间，于支撑锚价成交。 |
| [M5 强压稳接](M5.md) | `live(AP_SELL@shared) ∥ live(RF2@shared)`，另要求R2恢复不弱于0.8*r1。 | 持续卖压与连续两轮恢复重叠，第二轮恢复没有明显减弱；在当前进攻和承接都有效期间，于原RF锚价成交。 |
| [M6 撤供留托](M6.md) | `live(RF2@shared) ∥ live(LW_ASK@LEGACY_COMMON)`。RF两轮；LW为旧固定Ask三档残余减量，非无标记替换成P_LW新独立生命周期。 | 原Bid两轮承接与Ask固定多档残余退量同时有效；在这一组合有效期间，于原RF锚价成交。 |
| [M7 歇后再攻](M7.md) | `AP_BUY1 → [pause@M7 ∥ 新S_L,BID] → 全新live(AP_BUY2)`。pause和单轮支撑是局部构件，不分别冒称EX和P_RF两轮。 | 买攻之后有暂停与新承接，再有真正晚开始的新买攻；在新买攻和支撑有效期间，于固定承接锚价成交。 |
| [M8 失守后修复](M8.md) | `hist(W_L,BID@M8,r≤0.30) → 新可比S_L,BID@M8(r≥0.60)`。原文旧名DF_BID指单弱轮，既不是P_DF强参考后衰退，也不是RD。 | 原Bid先经历弱恢复，再由新可比受卖轮修复；在新修复仍有效期间，于同一原锚价成交。 |
| [M9 余波复验](M9.md) | `[live(S_L,BID@M9) ∥ live(EX_SELL)] → 首个新SELL资格 → 新S_L,BID`；最终的局部单轮RF不是P_RF两轮。 | 单轮支撑与卖压衰竭提供种子，之后新弱卖压已完成稳定复验；在新证据和余波量界有效期间，于原锚价成交。 |
| [M10 回流递减（历史；现仅M2诊断）](M10.md) | 历史：`hist(AP_BUY) → 新稳定R_L1 → 新稳定R_L2`，另加H/u递减与恢复比例。 | 旧信号不再独立优化；当前H2/H1、u2/u1、r2/r1仅描述母M2已有两轮，不设置阈值、当前守卫或订单。 |
| [M11 多档续托](M11.md) | `母候选(M4/M9/M10) → 两个后方Bid净增加 → 完整留存`。这是策略覆盖层，不是第16个方向原语，不是BA或RF。 | 母信号成立之后，原锚下两个Bid档新增量经过完整留存；过滤和母信号均有效期间，在母固定锚价成交。M11自身不产生方向。 |
| [M12 买卖恢复差](M12.md) | `live(S_C,BID) ∥ live(W_C,ASK)`，W要求r≤0.30、d≥0.05；跨侧u/d比各在[0.25,4]。S/W是严格轮局部构件，不直接调用P_BA/P_AA/P_DF。 | 相近时刻的可比受击中，Bid严格恢复而Ask恢复不足；在买托底线与弱供上界仍有效期间，于固定Bid锚价成交。 |
| [M13 撤供后再承接（归档）](M13.md) | 历史：`[live(S_C,BID) ∥ live(LW_ASK@COMMON)] → 首个新SELL复验R_C → 新S_C`。 | 旧底线与新减损可比可形成空交集，多阶段时限进一步压缩机会；停止跟进当前设计和调参分支，保留旧定义，不自动重构。 |
| [M14 弱供连续受击（历史；现仅诊断）](M14.md) | 历史：`W_C,ASK1 → 新W_C,ASK2`与近期`live(S_C,BID)`配对；两弱轮不是DF。 | 旧完整链退出优化；当前仅描述明确来源profile的Ask双弱轮，无Bid也可记录组件，不产生锚、交易信号或母候选筛选。 |
| [M15 撤供后的买入接替（原版停止优化）](M15.md) | 历史：`[live(S_C,BID) ∥ live(LW_ASK@COMMON)] → 首个新BUY弱Ask轮W_C`；单轮不是完整AP。 | 原版旧托、LW残余、新弱供守卫保留供追溯；并行与LW历史交接仅候补，锚选择/交接点/新供给guard未闭合，不是测试入口。 |
| [M16 承接提速](M16.md) | `hist(S_C,BID1) → 新S_C,BID2 ＋ 恢复耗时改善`；严格支撑轮是局部构件，新增信息是时间比较，不是另一个RF事实。 | 同Bid在相当或更强受击下，第二轮稳定恢复更快且不浅；在新支撑和后续速度维护仍有效期间，于固定锚价成交。 |
| [M17 卖压打不薄](M17.md) | `QR_BID@M17`：持续成交与绝对队列保量；对应P_QR v1.1的Bid信息，但主M17保持自身发布/待复核生命周期，不以原语引擎无标记替换。 | 同Bid在750ms分段真实SELL下仍保持规定初量比例；在绝对保量底线、来源复核与截止有效期间，于固定锚价成交。 |
| [M18 加压不增损](M18.md) | `hist(S_C,BID1) → 新S_C,BID2 ＋ 加压不增损`；不是AP_SELL或RF2的简单别名。 | 同Bid第二轮承受更大卖量，绝对及相对减损未扩大且恢复不差；在新支撑及来源维护有效期间，于固定锚价成交。 |
| [M19 轻攻更薄（历史；仅诊断）](M19.md) | `hist(Ask受击A1) → 新弱A2 ＋ 轻攻增损`，与近期 `live(S_C,BID)` 配对。A1可强可弱，没有必需的DF强参考。 | 同Ask第二轮较少BUY却对应更大减损及不改善的弱恢复，另有近期Bid支撑；在两侧守卫有效期间，于固定Bid锚价成交。 |
| [M20 买托前聚（历史；仅诊断）](M20.md) | `真实SELL@原Bid → profile@M20`：锚档净增、两个后方档适度净减、固定三档总量不减。它是局部结构构件，不等于BA、QR、XR或RF。 | 真实SELL之后，固定Bid三档出现前增后减且总量不减；在该数量结构及成交后复核有效期间，于原前档锚价成交。 |
| [M21 卖来供退](M21.md) | `[SELL轮1:S_C,BID1 ＋ Ask无BUY净减] → 新的同类SELL轮2`。这是跨侧退量语意的局部两轮规格，不能直接写成主版RF2+XW_ASK。 | 两轮SELL都获得Bid恢复，同一Ask在无该档BUY记录时重复净减；在第二轮新Bid底线和Ask上界有效期间，于固定Bid锚价成交。 |
| [M22 近薄远厚（归档）](M22.md) | `已有live(S_C,BID) → 新BUY@原Ask → profile@M22`：Ask近档净减、后两档净增、总量近似保留。不是LW、DF或AA。 | 已有Bid支撑后，新BUY对应固定Ask三档近减远增且总量近似保留；在旧托与该结构有效期间，于固定Bid锚价成交。 |
| [M23 加托扛卖](M23.md) | `buffer@M23预加托留存 → 首个新SELL → 缓冲受击后仍部分保留`。主版没有要求先AP_BUY或XR_BID；增量本身不是BA/RF。 | Bid预先加托并留存，之后首轮新SELL与数量回落验证部分缓冲仍在；在剩余缓冲底线及原期限有效期间，于固定锚价成交。 |

## 3. 历史家族关系与冲突（不代表当前跟进队列）

| 研究家族 | 成员 | 保留的区别 |
|---|---|---|
| 卖压承接 | M1/M5/M9 | 衰竭余波、当前强压、衰竭后新复验 |
| 买攻后回流 | M2/M7；M10仅诊断 | 新承接、暂停再攻；回流量比不再独立生成信号 |
| 承接与弱供 | M4/M12；M14/M19仅诊断 | 相对转弱、跨侧恢复差；双轮弱供与响应变化仅描述 |
| 撤供与留托 | M6/M21；M13归档、M15原版停止 | 当前LW、单轮共同响应新版；M21双轮原版保留，M15仅候补 |
| 其他独立维度 | M3/M8/M16/M17/M18/M23；M20诊断、M22归档 | 反击再守、修复、速度、保量、耐压、预增缓冲；不合并M16或改变其测试版本 |
| 公共过滤器 | M11 | 仅原M4/M9/M10母分支，沿母锚及截止 |

没有完整等价的策略对被删除，没有创建OR/AND联合订单政策。Maker/Taker解耦不消除原语顺序和状态差别；M16/M18仍分别比较时间和损失幅度。逐策略确认/失效、数量无解、阶段交接及执行准入冲突详见[本轮审查](FIXED_ANCHOR_REVIEW.md)。

## 4. 历史备选

8份旧显式备选保留原参数、版本及完整文本，作为冻结历史Maker规格；各M文件和catalog链接其definition_file。没有静默升级为新的Taker备选。

| 所属 | 冻结版本 |
|---|---|
| M4 | [`m4-relative-exit-v1`](../signal_discovery/spec_archive/M4-2ef8edac68aa16bf.md) |
| M6 | [`m6-fixed-bid-retention-v1`](../signal_discovery/spec_archive/M6-c5193289588df166.md) |
| M8 | [`m8-rd-level-rebuild-v1`](../signal_discovery/spec_archive/M8-940e32e73ce6cb13.md) |
| M12 | [`m12-rd-ask-supported-v1`](../signal_discovery/spec_archive/M12-d05fc088599c34c8.md) |
| M17 | [`m17-qr-sr-ask-v1`](../signal_discovery/spec_archive/M17-d83b111b214477e8.md) |
| M17 | [`m17-qo-then-retained-sell-v1`](../signal_discovery/spec_archive/M17-d83b111b214477e8.md) |
| M21 | [`m21-xw-prior-support-v1`](../signal_discovery/spec_archive/M21-9c07799794925706.md) |
| M23 | [`m23-xr-seeded-sell-test-v1`](../signal_discovery/spec_archive/M23-c4ef287dbbcc2df0.md) |

## 5. 此前七份独立分支（定义保留）

| 分支 | 原语顺序与固定锚语意 | 与主版的区别 |
|---|---|---|
| [M17_SB_QR：短退补回后扛卖](V_M17_SB_QR.md) | `live(SB_BID) → 首新SELL交接hist(SB) → 专属新QR验证 → 原P成交` | 新增低打单短退补回前史；绝对SB量界与QR量界取交集，截止从SB确认计3s。SB原750ms寿命不要求覆盖整段新QR，首SELL交接前仍必须有效。 |
| [M4_DF_AP：供弱后续攻](V_M4_DF_AP.md) | `[live(S_L,BID) ∥ live(DF_ASK)] → 新AP_BUY@direct → 三项当前有效时原P成交` | 新AP必须晚于种子真实可得时间，并有材料性BUY打到原弱Ask；冻结组件不替换，任一结束即终止。 |
| [M9_EX_HANDOFF：余波接棒复验](V_M9_EX_HANDOFF.md) | `[live(S_L,BID@v8) ∥ live(EX_SELL@M9-v8)] → 新R首次H/L资格交接hist(EX) → 完整新R → 原P有效成交` | 基于外部v8；q前旧EX退出优先，q后只取消旧EX当前市场阶段的否决；95%滚动SELL、新R、P及历史真实性继续守卫，不把局部20%单轮称为P_RF。 |
| [M12_SHOCK_COMPARABLE：同击异损、买强卖弱](V_M12_SHOCK_COMPARABLE.md) | `live(S_C,BID@revision3) ∥ live(W_C,ASK@revision3) + 仅u可比 → 原P有效成交` | 基于第三轮参数；保留0.10–10冲击比，删除损失比及其隐含Bid下限，材料性和数量guard不变。不需要历史强Ask参考，不等同M4。 |
| [M7_PARALLEL_CONFIRM：歇后攻托同证](V_M7_PARALLEL_CONFIRM.md) | `AP1→新S`与`AP1→pause→全新AP2`分别形成，`live(S)∥live(AP2)→原P成交` | 沿v7局部参数；AP2只须从暂停确认后开始，不必晚于S确认。先完成者持续当前有效，截止取S/AP2/root原期限最小值，不换ID续期。 |
| [M21_SINGLE_ROUND：一轮卖来供退](V_M21_SINGLE_ROUND.md) | `同一SELL轮S_BID ∥ Ask无BUY净减 → 原P有效成交` | 单轮500ms，删第二轮及其比较/计时；零BUY至实际发布，c+3s守本轮F/U。 |
| [M23_BUFFER_RETENTION：加托后受卖留存](V_M23_BUFFER_RETENTION.md) | `预加托→首SELL合法交接→500ms真实受卖留存→原P成交` | 删L最低值与末段上限，保留H≥L、窗内末量、80%→20%及种子3s。 |

以上七份均保留原身份；11月17日先新增四份V、随后新增M12_VALIDITY_OVERLAP和M23_FIRST_SELL_WHILE_VALID，本次再新增M21/M7/M9三份，共有16份V及8份历史备选，诊断为5份。M17_SB_QR不跟进，M4_DF_AP不进入本轮队列；M12_SHOCK_COMPARABLE、M21_SINGLE_ROUND、M21_COFLOW_NO_PREQUIET、M7_PARALLEL_CONFIRM、M9_EX_HANDOFF、M23_BUFFER_RETENTION与M23_BUFFER_NO_LOSS_COVER作为旧版保留。文件存在不等于当前启用，不能按多个版本重复计独立信息。

此前M17/M4分支的v1.1均为语意补充：SB量界与QR量界取max即可，不额外累加确认；M4的live(DF)只表示原退出规则未触发，新AP不等于新弱Ask轮已完成。当前QR_ASK可与旧DF重叠，表示买攻遇到保量供给；这是方向解释的反例，不是该分支自动新增的否决条件。

M9/M12新分支是显式逻辑变化，数值沿[冻结外部基线](../signal_discovery/spec_archive/M9_M12_FREQUENCY_BASIS_20261116.json)，不宣称与目录主版参数相同。M9改变EX交接后的当前退出归属，M12删除结果损失可比；两者均保留原家族，不新增原语或独立M编号。完整确认/失效边界见各分支第7节；本次没有测试。

M7并行分支沿 [v7参数与原文快照](../signal_discovery/spec_archive/M7_M10_M13_DISPOSITION_BASIS_20261116.json)，只移除支撑完成在AP2起点之前的要求；暂停起点gate、首选证据、反证和原截止保留。私有AP的750ms当前生命不同于P_AP主版滚动重检，不能混用。M10诊断绑定确切M2候选/轮次，不把旧M10样本改名为M2，也不携带旧1.25倍退出；M13归档不授权停止运行进程。

M21_SINGLE_ROUND去掉第二轮，只用一轮Bid/Ask共同响应及其新F/U；M23_BUFFER_RETENTION删除最低可见损失和末段上限，保留H≥L、合法交接及种子3s。两者均明确版本，旧主版不改。M19/M20诊断见 [Ask响应变化](D_ASK_RESPONSE_CHANGE.md) 与 [Bid三档分布](D_BID_DEPTH_PROFILE.md)。M16保持不变，M17/M18也未修改共用来源或参数；最新决定见 [设计更新](M17_M23_DESIGN_UPDATE.md)。

## 6. 交付范围

23个M文件保留`mN-fixed-anchor-entry-v1`历史形成版本，首个JSON记录basis_version及计算合同。M10/M13/M14/M15/M19/M20/M22另列研究状态，形成数值、历史guard、原锚和TTL不变；M7/M12/M16/M17/M18/M21/M23原文件字节不变。诊断、归档或停止设计优化不修改运行状态，当前入口以本页及README为准。M12–M15取舍与未决设计点见 [设计审查](M12_M15_DESIGN_REVIEW.md)。

[上一轮Maker语意映射快照](../signal_discovery/spec_archive/STRATEGY_PRIMITIVE_MAP-5ea9964d09ee0d66.md)保留当时23主版和8备选的详解，不能将其中Maker专属等待条款反套成新信号确认条件。

## 7. 2026-11-17新增入口与当前归并

| 新入口 | 原语／顺序 | 确认与失效变化 |
|---|---|---|
| [M12_VALIDITY_OVERLAP](V_M12_VALIDITY_OVERLAP.md) | `live(S_C,BID) ∥ live(W_C,ASK) + u可比 → 原P有效成交`，允许任一侧先确认 | 独立1.5s配对限制仅诊断；原3s、连续FB/FA守卫、完整frame及一次性选根保留，实际c<较早expiry；不从组合重计，不再声称同期冲击比较 |
| [M8_RESPONSE_REPAIR](V_M8_RESPONSE_REPAIR.md) | `hist(弱W)→首个合格新R→新承接` | 删除跨轮d比；新R真实受击、旧滞回稳定、r≥0.60、5s形成及新8s不变，不宣称同压改善 |
| [M18_STABLE_RECOVERY](V_M18_STABLE_RECOVERY.md) | `hist(R1)→新R2 + 加压且损失不增` | 删除r2≥r1；两轮各自严格稳定、H与初量可比、双损失比较及新F2不变；输入证明仍可能阻断 |
| [M21_COFLOW_NO_PREQUIET](V_M21_COFLOW_NO_PREQUIET.md)（保留对照） | `单轮受卖承接∥同窗无BUY净减` | 删除前250ms平稳门槛；当前初量/连续链、真实新增退量、零BUY及F/U保留 |
| [M23_BUFFER_NO_LOSS_COVER](V_M23_BUFFER_NO_LOSS_COVER.md)（保留对照） | `预加托→首SELL→真实受卖与缓冲留存` | 已删除H≥L；仍有首SELL750ms，文件不改，非当前入口 |
| [M23_FIRST_SELL_WHILE_VALID](V_M23_FIRST_SELL_WHILE_VALID.md) | `预加托留存→Fpre仍有效时首SELL→500ms受卖留存→原P成交` | 750ms仅诊断，不提前清root；首笔身份、Fpre→F、材料H、末量来源及c<cS+3s保留，无新最小余量门槛 |
| [M21_ENDPOINT_COFLOW](V_M21_ENDPOINT_COFLOW.md) | `同轮受卖补回 ∥ 原Ask零BUY净减 → F/U有效时原P成交` | 双侧末100ms与Bid10%/5%仅诊断；保留绝对材料性、端点50%、Ask净减、来源/零BUY/F/U及c+3s；后续100ms不隐藏否决 |
| [M7_PAUSE_RELEASE](V_M7_PAUSE_RELEASE.md) | `AP1→总量pause→全新AP2`与`AP1→新S`联合 | 暂停单桶35%仅诊断；Kp后仅解除冻结AP1占用，唯一新watch完整750ms，S/AP2/root原截止保持 |
| [M9_EX_VALIDITY_OVERLAP](V_M9_EX_VALIDITY_OVERLAP.md) | `EX确认起持续维护3s ∥ 当前S → 新R的H/L资格交接 → 完整复验` | EX单桶30%仅诊断；允许原生750ms后配对，不补建或续期；q同输入50%优先、q后95%不变，不提前到首SELL交接 |

[M11时序诊断](D_M11_REAR_RETENTION_TIMELINE.md)记录母确认之后的后档越线/留存，禁止回填未来信息，不延迟母信号或增加退出。与M20共享分布研究入口，基线与观测时点分别保存；旧M10诊断不再作为母root。

以上为 11 月 17 日的交接记录。家族合并保留各版本的触发器及独立账本。

## 8. 2026-11-18 新增 M5 分支

[M5 两轮各自稳接](V_M5_RELATIVE_RECOVERY_DIAGNOSTIC.md) 复用 `live(AP_SELL@shared) ∥ live(RF_BID2@shared)`。AP 形成与第二轮受击须有正时长交集，两轮各自稳定且恢复至少一半。r2/r1 描述第二轮相对强弱；旧 80% 只作标签。这与 M1 的衰竭后承接、M8 的先弱后强各有不同的前史。M7/M9 的低频结果和 M18 的配对阻断已纳入[当前工作清单](SIGNAL_TEST_PRIORITY_20261118.md)。

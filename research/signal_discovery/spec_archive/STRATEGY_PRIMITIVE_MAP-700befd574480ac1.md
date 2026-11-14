# M1–M23 原语、时序与固定锚价成交语意

版本 `strategy-primitive-map-v2.1`。主SOP统一为**信号有效期间在固定锚价成交**，Maker/Taker单列为执行方式；原语和形成顺序沿来源版本。这里只整理逻辑，不评价交易数、胜率或PnL。

```json
{
  "document_type": "strategy_primitive_semantic_map",
  "version": "strategy-primitive-map-v2.1",
  "status": "DOCUMENT_LOGIC_REVIEW_ONLY",
  "main_strategies": 23,
  "primitive_ids": 15,
  "historical_strategy_variants": 8,
  "current_independent_variant_sops": 2,
  "directional_research_families": 12,
  "overlays": 1,
  "signal_contract": "fixed-anchor-signal-v1",
  "execution_contract": "fixed-anchor-execution-v1",
  "formation_numeric_parameters_changed": false,
  "signal_guard_numeric_parameters_changed": false,
  "tests_run": false
}
```

## 1. 读法与版本

`A→B`表示由A之后的新输入形成B；`A∥B`表示可并行形成但组合时需同时可用。`hist(A)`仅作历史，`live(A)`按其原版本仍当前有效；历史交接只在SOP指定的事件发生，不能救活先前已失效的root。

RF2是旧两轮滞回恢复；S_L/R_L为各旧策略单轮构件；S_C/R_C为COMMON严格500ms轮；W_C为弱轮。它们不能直接互换，单轮S不冒称P_RF两轮或BA。M8旧DF_BID不是P_DF强转弱主版，M21主版不直接调用XW，M23主版没有必需的XR。AA与SB未在这23个主版本中新增调用。

执行写成`E(P,G,d; mode)`：P为固定原Bid，G为上文当前守卫，d为原信号截止，mode显式选择Maker/Taker。具体执行窗口和准入单列；不能因选择Taker就删除原Bid存在或floor。正常未交叉盘口下，同价Taker与原Bid正量守卫没有交集，详见[分层合同](FIXED_ANCHOR_CONTRACT.md)。

## 2. 23个主版本

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
| [M10 回流递减](M10.md) | `hist(AP_BUY) → 新稳定R_L1 → 新稳定R_L2`；两轮承接骨架上加H2≤0.90H1、u2≤0.90u1、r2≥0.80r1。 | 买攻之后两轮新卖出回流的H/u递减且恢复未明显恶化；在后续压力和承接维护条件仍有效期间，于固定锚价成交。 |
| [M11 多档续托](M11.md) | `母候选(M4/M9/M10) → 两个后方Bid净增加 → 完整留存`。这是策略覆盖层，不是第16个方向原语，不是BA或RF。 | 母信号成立之后，原锚下两个Bid档新增量经过完整留存；过滤和母信号均有效期间，在母固定锚价成交。M11自身不产生方向。 |
| [M12 买卖恢复差](M12.md) | `live(S_C,BID) ∥ live(W_C,ASK)`，W要求r≤0.30、d≥0.05；跨侧u/d比各在[0.25,4]。S/W是严格轮局部构件，不直接调用P_BA/P_AA/P_DF。 | 相近时刻的可比受击中，Bid严格恢复而Ask恢复不足；在买托底线与弱供上界仍有效期间，于固定Bid锚价成交。 |
| [M13 撤供后再承接](M13.md) | `[live(S_C,BID) ∥ live(LW_ASK@COMMON)] → 首个新SELL复验R_C → 新S_C`；LW随后只作形成历史，另有策略残余维护状态。 | Ask退量与旧Bid支撑重叠后，新SELL在旧底线未破时完成复验；在新支撑和延续退量守卫有效期间，于原锚价成交。 |
| [M14 弱供连续受击](M14.md) | `W_C,ASK1 → 新W_C,ASK2`，与近期 `live(S_C,BID)` 配对；两弱轮不等于“强转弱”的DF。 | 同一Ask两次新受击都弱恢复，并配近期严格Bid支撑；在第二弱供上界和买托底线有效期间，于固定Bid锚价成交。 |
| [M15 撤供后的买入接替](M15.md) | `[live(S_C,BID) ∥ live(LW_ASK@COMMON)] → 首个新BUY弱Ask轮W_C`。单个500ms真实BUY轮不是完整AP_BUY。 | 退量与旧托就绪后，新BUY打向剩余Ask且供给仍弱；在原Bid支撑、LW残余与新弱供守卫有效期间，于固定Bid锚价成交。 |
| [M16 承接提速](M16.md) | `hist(S_C,BID1) → 新S_C,BID2 ＋ 恢复耗时改善`；严格支撑轮是局部构件，新增信息是时间比较，不是另一个RF事实。 | 同Bid在相当或更强受击下，第二轮稳定恢复更快且不浅；在新支撑和后续速度维护仍有效期间，于固定锚价成交。 |
| [M17 卖压打不薄](M17.md) | `QR_BID@M17`：持续成交与绝对队列保量；对应P_QR v1.1的Bid信息，但主M17保持自身发布/待复核生命周期，不以原语引擎无标记替换。 | 同Bid在750ms分段真实SELL下仍保持规定初量比例；在绝对保量底线、来源复核与截止有效期间，于固定锚价成交。 |
| [M18 加压不增损](M18.md) | `hist(S_C,BID1) → 新S_C,BID2 ＋ 加压不增损`；不是AP_SELL或RF2的简单别名。 | 同Bid第二轮承受更大卖量，绝对及相对减损未扩大且恢复不差；在新支撑及来源维护有效期间，于固定锚价成交。 |
| [M19 轻攻更薄](M19.md) | `hist(Ask受击A1) → 新弱A2 ＋ 轻攻增损`，与近期 `live(S_C,BID)` 配对。A1可强可弱，没有必需的DF强参考。 | 同Ask第二轮较少BUY却对应更大减损及不改善的弱恢复，另有近期Bid支撑；在两侧守卫有效期间，于固定Bid锚价成交。 |
| [M20 买托前聚](M20.md) | `真实SELL@原Bid → profile@M20`：锚档净增、两个后方档适度净减、固定三档总量不减。它是局部结构构件，不等于BA、QR、XR或RF。 | 真实SELL之后，固定Bid三档出现前增后减且总量不减；在该数量结构及成交后复核有效期间，于原前档锚价成交。 |
| [M21 卖来供退](M21.md) | `[SELL轮1:S_C,BID1 ＋ Ask无BUY净减] → 新的同类SELL轮2`。这是跨侧退量语意的局部两轮规格，不能直接写成主版RF2+XW_ASK。 | 两轮SELL都获得Bid恢复，同一Ask在无该档BUY记录时重复净减；在第二轮新Bid底线和Ask上界有效期间，于固定Bid锚价成交。 |
| [M22 近薄远厚](M22.md) | `已有live(S_C,BID) → 新BUY@原Ask → profile@M22`：Ask近档净减、后两档净增、总量近似保留。不是LW、DF或AA。 | 已有Bid支撑后，新BUY对应固定Ask三档近减远增且总量近似保留；在旧托与该结构有效期间，于固定Bid锚价成交。 |
| [M23 加托扛卖](M23.md) | `buffer@M23预加托留存 → 首个新SELL → 缓冲受击后仍部分保留`。主版没有要求先AP_BUY或XR_BID；增量本身不是BA/RF。 | Bid预先加托并留存，之后首轮新SELL与数量回落验证部分缓冲仍在；在剩余缓冲底线及原期限有效期间，于固定锚价成交。 |

## 3. 合并与冲突

| 研究家族 | 成员 | 保留的区别 |
|---|---|---|
| 卖压承接 | M1/M5/M9 | 衰竭余波、当前强压、衰竭后新复验 |
| 买攻后回流 | M2/M7/M10 | 新承接、暂停再攻、回流递减 |
| 承接与弱供 | M4/M12/M14/M19 | 相对转弱、跨侧恢复差、连续弱、轻攻增损 |
| 撤供与留托 | M6/M13/M15/M21 | 当前LW、新SELL复验、新BUY验证、两次SELL时单Ask退量 |
| 其他独立维度 | M3/M8/M16/M17/M18/M20/M22/M23 | 反击再守、修复、速度、保量、耐压、前聚、后移、预增缓冲 |
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

## 5. 当前独立分支

| 分支 | 原语顺序与固定锚语意 | 与主版的区别 |
|---|---|---|
| [M17_SB_QR：短退补回后扛卖](V_M17_SB_QR.md) | `live(SB_BID) → 首新SELL交接hist(SB) → 专属新QR验证 → 原P成交` | 新增低打单短退补回前史；绝对SB量界与QR量界取交集，截止从SB确认计3s。SB原750ms寿命不要求覆盖整段新QR，首SELL交接前仍必须有效。 |
| [M4_DF_AP：供弱后续攻](V_M4_DF_AP.md) | `[live(S_L,BID) ∥ live(DF_ASK)] → 新AP_BUY@direct → 三项当前有效时原P成交` | 新AP必须晚于种子真实可得时间，并有材料性BUY打到原弱Ask；冻结组件不替换，任一结束即终止。 |

两者均SPEC_ONLY，Maker/Taker接口单列，未改变主策略/原语，未证明独立信息或结算优势。15原语中SB在此新增分支被调用；主23版仍没有新增SB调用。历史8备选加当前2独立分支共10个显式备选，分别保留执行合同与来源，不混用。

## 6. 交付范围

23主版均为`mN-fixed-anchor-entry-v1`，首个JSON记录basis_version及计算合同。形成数值、信号guard、原锚和TTL不变；执行分离后的新文档不代表旧运行版本完整行为相同。当前入口以本页及各M新SOP为准。

[上一轮Maker语意映射快照](../signal_discovery/spec_archive/STRATEGY_PRIMITIVE_MAP-5ea9964d09ee0d66.md)保留当时23主版和8备选的详解，不能将其中Maker专属等待条款反套成新信号确认条件。

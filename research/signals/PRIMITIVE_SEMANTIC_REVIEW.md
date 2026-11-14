# 15个盘口原语的语意分类、重叠与生命周期审查

本页是一次文档与逻辑审查。结论按各文件首个JSON的明确主版本成立；没有进行行情测试、统计重叠计算或执行实现。本轮把15个原语整理为6个盘口语意类别、13个研究入口：SR/SB合为一个带两个模式的入口，XR/XW合为另一个；15份独立SOP及各自版本仍保留。13不是统计独立信息数量。

本次没有找到可直接互相替换、删除其中一份定义的完全等价原语。能合并的是上述两组的研究入口，以及严格同条件下的重复数量检查；历史证据、失效规则、期限和输入身份不能随之删除。

```json
{
  "document_type": "primitive_semantic_and_lifecycle_review",
  "version": "primitive-semantic-review-v1",
  "status": "DOCUMENT_LOGIC_REVIEW_ONLY",
  "reviewed_at": "2026-11-12T09:27:04.225170+00:00",
  "semantic_classes": [
    {
      "id": "ACTIVE_FLOW_PHASE",
      "name": "主动成交：进攻、接替与衰竭",
      "members": [
        "AP",
        "CA",
        "EX"
      ],
      "question": "谁在主动打单，进攻是否持续、接替或减弱"
    },
    {
      "id": "PASSIVE_HIT_RESPONSE",
      "name": "受击后的回补与耐耗",
      "members": [
        "BA",
        "AA",
        "RF",
        "QR"
      ],
      "question": "原档真正受击以后，补过多少、恢复多少、能否持续留量"
    },
    {
      "id": "DEFENSE_DETERIORATION",
      "name": "防守能力与恢复量衰退",
      "members": [
        "DF",
        "RD"
      ],
      "question": "新冲击下恢复变弱，还是旧恢复量在无新打单阶段回吐"
    },
    {
      "id": "MULTILEVEL_DEPTH_RETREAT",
      "name": "多档深度收缩",
      "members": [
        "LW"
      ],
      "question": "固定近端档组的净减量，有多少不能由已知成交覆盖"
    },
    {
      "id": "CROSS_SIDE_QUANTITY_RESPONSE",
      "name": "进攻后的对侧挂量响应",
      "members": [
        "XR",
        "XW"
      ],
      "question": "一侧攻击之后，另一侧原档是增量还是减量"
    },
    {
      "id": "LOCAL_DEPTH_PATH",
      "name": "单档短时数量路径",
      "members": [
        "SR",
        "SB",
        "QO"
      ],
      "question": "原档挂量短暂偏离后返回，还是在小幅带宽内反复回摆"
    }
  ],
  "merged_research_entries": [
    {
      "id": "SHORT_LIVED_DEPTH_EXCURSION_AND_RETURN",
      "name": "短暂偏离后返回",
      "members": [
        "SR",
        "SB"
      ],
      "modes": {
        "SR": "先增后退",
        "SB": "先退后补"
      },
      "decision": "MERGE_RESEARCH_ENTRY_KEEP_VERSIONED_BRANCHES",
      "preserve": [
        "正量约束及减量上限",
        "返回带宽方向",
        "当前上界或下界",
        "各root及截止"
      ]
    },
    {
      "id": "CROSS_SIDE_ATTACK_THEN_TARGET_DEPTH_RESPONSE",
      "name": "进攻后对侧挂量响应",
      "members": [
        "XR",
        "XW"
      ],
      "modes": {
        "XR": "跟挂增量",
        "XW": "退挂减量"
      },
      "decision": "MERGE_RESEARCH_ENTRY_KEEP_VERSIONED_BRANCHES",
      "preserve": [
        "目标数量方向及正量约束",
        "XR的3秒与XW的750ms寿命",
        "首达事件和发布量界",
        "各root及复核预算"
      ]
    }
  ],
  "primitive_count": 15,
  "semantic_class_count": 6,
  "research_entry_count": 13,
  "primitive_definitions_deleted": false,
  "full_behavioral_equivalence_established": false,
  "behavioral_parameters_changed": false,
  "runtime_changed": false,
  "tests_executed": false,
  "actual_overlap_measured": false,
  "conditional_pairing_constraints": [
    {
      "id": "C01",
      "relation": "AP_PARENT_AND_OWN_EX",
      "verdict": "NO_CURRENT_OVERLAP",
      "condition": "同一AP身份及其衍生EX；EX确认不早于AP当前截止"
    },
    {
      "id": "C02",
      "relation": "EX_SELL_WITH_AP_BUY_OR_EX_BUY_WITH_AP_SELL",
      "verdict": "REVERSE_AP_ENDS_EX",
      "condition": "同token同Pressure来源；主动接替须把EX转历史，不维持两个当前态"
    },
    {
      "id": "C03",
      "relation": "TTL750_FACT_THEN_FRESH_QR",
      "verdict": "NO_CURRENT_OVERLAP",
      "condition": "QR首笔严格晚于前项确认，原750ms期限不延长"
    },
    {
      "id": "C04",
      "relation": "TTL750_FACT_THEN_FRESH_500MS_ROUND",
      "verdict": "INSUFFICIENT_COMMON_400MS_FIRST_SEND_BUDGET",
      "condition": "新轮首笔严格晚于前项确认；不等于不能观察到组合"
    },
    {
      "id": "C05",
      "relation": "QO_CURRENT_WITH_TARGET_CONSUMING_FILL",
      "verdict": "ZERO_HIT_STATE_ENDS",
      "condition": "同token同P同份额和成交视图；竞态成交仍保留"
    },
    {
      "id": "C06",
      "relation": "XR_WITH_SR_SAME_ADD_OR_XW_WITH_SB_SAME_DROP",
      "verdict": "RETURN_ENDS_OTHER_BRANCH",
      "condition": "同P、同B及同增减量事件；完整路径限定，非全局互斥"
    },
    {
      "id": "C07",
      "relation": "XR_WITH_XW_SAME_BASELINE",
      "verdict": "DISJOINT_CURRENT_QUANTITY_BOUNDS",
      "condition": "同P、同B>0，独立增减幅均至少20%；不同基线另查"
    },
    {
      "id": "C08",
      "relation": "RD_WITH_OWN_COMMON_SUPPORT",
      "verdict": "OLD_SUPPORT_ALREADY_INVALID",
      "condition": "同一Qmin/L且L>0；不适用于无数量floor的旧BA/RF"
    },
    {
      "id": "C09",
      "relation": "DF_STABLE_WEAK_ROUND",
      "verdict": "ORDERED_STATE_TRANSITION_NOT_SELF_CANCELLATION",
      "condition": "先退出旧DF、再用旧参考生成新DF、最后设置新参考"
    },
    {
      "id": "C10",
      "relation": "QUOTE_OR_DEPTH_FACTS_WITH_TRUE_HIT_FACTS",
      "verdict": "ALIGN_TARGET_ZERO_HIT_INTERVALS_AND_SCOPE",
      "condition": "不能把零目标打单区间同时用作新BA/AA/RF/QR受击证据"
    }
  ],
  "primitive_manifest": [
    {
      "id": "SR",
      "version": "sr-short-replenishment-reversal-v1",
      "file": "P_SR.md",
      "sha256": "1c928b6b9c70dcaf2f7c327a59de7402386c7fb48254df2bc4d00fb85058694d"
    },
    {
      "id": "QR",
      "version": "qr-consumed-queue-retention-v1.1",
      "file": "P_QR.md",
      "sha256": "cb28cf522e8452607c302029985ce6f053175cf88a7fac77d92c2e1fcf7d4030"
    },
    {
      "id": "XR",
      "version": "xr-cross-side-following-liquidity-v1",
      "file": "P_XR.md",
      "sha256": "7c8c3d9d1f2373cfb3e19ccf7396a96cc456103b04e7b4a41655e542da379c8d"
    },
    {
      "id": "LW",
      "version": "lw-fixed-side-depth-withdrawal-v1",
      "file": "P_LW.md",
      "sha256": "ffb843d59696a9b5bd75115be8bf678ec5e1b06440ed4f721b150d4c6f6e64f6"
    },
    {
      "id": "RD",
      "version": "rd-recovery-decay-without-new-hit-v1",
      "file": "P_RD.md",
      "sha256": "2716b65c4bd72db1537ab6ad64c9c788bcac8b7b4fa664e3a60657d7d2bcc115"
    },
    {
      "id": "XW",
      "version": "xw-cross-side-following-withdrawal-v1",
      "file": "P_XW.md",
      "sha256": "17313b83f4c57b18495db37fbaceb454941c7ff2e904a586dc513b138e0cedd1"
    },
    {
      "id": "QO",
      "version": "qo-bounded-depth-oscillation-v1",
      "file": "P_QO.md",
      "sha256": "c89100c98d8caa1396459fcc8321bc30292b0c289f2e8ec24f2d04ae1df0022a"
    },
    {
      "id": "SB",
      "version": "sb-short-depth-dip-replenishment-v1",
      "file": "P_SB.md",
      "sha256": "08f7745d68fcce6cc62c49afe84fe21c64b302637f6ddbc1a7c70b708ef6a360"
    },
    {
      "id": "AP",
      "version": "ap-legacy-pressure-750ms-v1",
      "file": "P_AP.md",
      "sha256": "772cdc2a001d6fc4ec1bf836cdc258384715401e6e7c0a7c839aba1069f4bd93"
    },
    {
      "id": "AA",
      "version": "aa-toolbox-net-recovery-750ms-v1",
      "file": "P_AA.md",
      "sha256": "a3b848b8d06261f117061f0242a7813146edc090d21b9ecb44c9f6642ce435e9"
    },
    {
      "id": "RF",
      "version": "rf-shared-bid-500ms-v1",
      "file": "P_RF.md",
      "sha256": "f5465ed79ff4a67b554d0948c8b8b03f74ae5f20fb96b41bc997c9bdc88fd704"
    },
    {
      "id": "EX",
      "version": "ex-legacy-post-ap-750ms-v1",
      "file": "P_EX.md",
      "sha256": "a8a532edb61526cd12963f038983233547617fa65ba296a1d3cce0d488096b9d"
    },
    {
      "id": "DF",
      "version": "df-m4-frequency-ask-v2-sop-v1",
      "file": "P_DF.md",
      "sha256": "3756beed29c23b8b310dd57237150c41353a193e4f91431f0068b511687a469a"
    },
    {
      "id": "CA",
      "version": "ca-m3-sell-then-buy-v1",
      "file": "P_CA.md",
      "sha256": "c5e11529dc8fdf8fef1481ec1fe05c0282166618ca03c24d6c30f945d908da53"
    },
    {
      "id": "BA",
      "version": "ba-m3-cumulative-refill-v1",
      "file": "P_BA.md",
      "sha256": "aafa927af084bc4a44a93e60a00b0b65bcc32ccd71599eb2dd62f2e0297b9dcf"
    }
  ],
  "predicate_simplifications": [
    {
      "primitive": "RF",
      "rule": "BOTH_STABLE_R_GE_0_5_IMPLIES_MEDIAN_GE_0_5",
      "scope": "CURRENT_TWO_ROUND_MAIN_ONLY_NOT_PROTOTYPE_MEDIAN_0_7"
    },
    {
      "primitive": "AP",
      "rule": "TWO_OCCUPIED_DISJOINT_BUCKETS_IMPLIES_AT_LEAST_TWO_RECORDS",
      "scope": "DEDUPLICATED_ATOMIC_RECORD_HAS_EXACTLY_ONE_RECEIVED_BUCKET"
    },
    {
      "primitive": "QR",
      "rule": "THREE_OCCUPIED_DISJOINT_BUCKETS_IMPLIES_AT_LEAST_THREE_RECORDS",
      "scope": "DEDUPLICATED_ATOMIC_RECORD_HAS_EXACTLY_ONE_RECEIVED_BUCKET"
    },
    {
      "primitive": "RD",
      "rule": "H_GE_L_AND_L_GE_M_IMPLIES_H_GE_M",
      "scope": "SAME_ROUND_AND_UNITS"
    },
    {
      "primitive": "DF",
      "rule": "R_LE_0_6_AND_R_LE_0_8_RREF_EQUIVALENT_TO_MIN_BOUND",
      "scope": "SAME_FROZEN_REFERENCE"
    },
    {
      "primitive": "LW",
      "rule": "ZERO_H_AND_LOSS_GE_0_3_T_IMPLIES_OTHER_FORMATION_RATIOS",
      "scope": "VERIFIED_H_EQ_ZERO_ONLY_NOT_MISSING_TRADES"
    }
  ]
}
```

## 1. 按盘口语意分类

| 类别 | 原语与别名 | 核心区别 |
|---|---|---|
| 主动成交：进攻、接替与衰竭 | [AP持续进攻](P_AP.md)、[CA反击](P_CA.md)、[EX进攻衰竭](P_EX.md) | AP看分布持续；CA看卖后买的顺序接替；EX看相对旧AP的减弱 |
| 受击后的回补与耐耗 | [BA守价](P_BA.md)、[AA卖方吸收](P_AA.md)、[RF反复补给](P_RF.md)、[QR耗而不薄](P_QR.md) | BA累计正补量；AA单轮净恢复；RF多轮稳定；QR成交下持续保量 |
| 防守能力与恢复量衰退 | [DF防守衰退](P_DF.md)、[RD回补回吐](P_RD.md) | DF比较新真实冲击；RD看先恢复、后无新增打单时回吐 |
| 多档深度收缩 | [LW疑似退量](P_LW.md) | 固定三档的总损失及已知成交扣量，区别单档恢复或回摆 |
| 进攻后的对侧挂量响应 | [XR攻后跟挂](P_XR.md)、[XW攻后退挂](P_XW.md) | 攻击发生在另一侧，目标档形成期未被消耗；分别增量和减量 |
| 单档短时数量路径 | [SR短补回落](P_SR.md)、[SB短退回补](P_SB.md)、[QO挂量回摆](P_QO.md) | SR/SB是明显偏离后返回；QO是小带宽内固定节点双反转 |

方向必须连同侧别解释：AP_BUY是主动买，QR_ASK却是卖方耐受买单；XR_BID是买攻后Bid跟挂，XR_ASK是卖攻后Ask跟挂。不能把同一缩写所有侧实例统一加成看多，也不能把“确认”直接等同“允许Maker买入”。

## 2. 合并决定

| 对照 | 决定 | 必须保留的差别 |
|---|---|---|
| SR / SB | 统一为“短暂偏离后返回”，保留先增后退/先退后补两模式 | 返回带宽、当前上/下界；SB减量还受原档正量和D<B限制 |
| XR / XW | 统一为“进攻后对侧挂量响应”，保留增量/减量模式 | XR寿命3秒、XW仅750ms；不能只改符号再沿用同一个deadline |
| AP / EX | 同属进攻阶段，保留两个身份及明确历史交接 | EX要求已确认AP参照和新的750ms窗口；不是AP=false |
| AP / CA | 保留 | CA可由单笔后续BUY达到10份，AP要求跨桶分布；AP可以没有先前SELL |
| BA / AA | 保留 | 即使作方向对称，累计正补量与净恢复仍不同；保持、触及档和期限也不同 |
| AA / RF | 保留 | 早期单轮AA成功不保证轮末稳定；主RF是两轮500ms、旧原型RF才是三轮750ms |
| BA / RF / QR | 保留，归同类对照 | 回补过、多轮恢复过、当前仍保量不是同一个谓词 |
| DF / RD | 保留 | 新冲击下恢复转弱与无新打单回吐是不同阶段 |
| LW / SR / RD / XW | 保留，标共享减量事件 | 固定三档与单档、基线及先前受击/攻击历史均不同 |
| QO / SR / SB | QO独立；SR/SB按上项合并入口 | QO全程±5%与SR/SB至少20%材料变化，不能把它当多个小SR/SB累计 |

“共用公式或输入字段”不足以合并原语。比如可共用H、Q0、Qmin和恢复率的定义，但必须继续绑定窗口、成交视图、锚规则、保持方式与发布时刻；本任务不实现公共计算器或统一检测器。

## 2.1 可以直接合并解释的条件冗余

这些是在既定前提下的逻辑蕴含，属于同一条件的重复表达，不是新增预测证据。本轮保留来源参数字段及校验，避免改变旧版本接口；解释或审查时不重复加分。

| 原语 | 可简化的条件 | 严格适用范围 |
|---|---|---|
| RF | 两轮均稳定已含r1≥0.50、r2≥0.50，必有median(r1,r2)≥0.50 | 仅当前两轮主版；原型三轮中位≥0.70仍有额外约束 |
| AP | 至少两个不重叠时间桶有同向量，已蕴含至少2条同向记录 | 成交去重完成，每条原子记录按received只归一个桶；不适用于原型至少3条 |
| QR | 三桶各有目标打单量，已蕴含至少3条目标记录 | 同样要求单条记录只归一桶；最大单笔≤80%仍有独立筛选作用 |
| RD | L≥m且H≥L，已蕴含H≥m | 同轮、同份额单位；不反套旧RF，因为旧RF不要求H≥L |
| DF | r≤0.60且r≤0.80r_ref，可统一为r≤min(0.60,0.80r_ref) | 相同冻结参考；不能把两个上限叫作两次独立弱化 |
| LW | 已证明H=0时，U=L；L/T0≥0.30已蕴含U/T0≥0.20及U/L≥0.50 | 仅真实满足零直接成交合同的分支；H>0或未知时三条件仍须完整计算 |

AP/QR的记录数冗余还可以作为数据一致性检查：若分桶条件已通过却记录数不够，应先查重复、记录跨桶或身份丢失，不能把它当另一份市场信息。

## 3. 15个原语逐项核对确认与失效

以下c均是实际确认可得时刻；t0是该版规定的首笔/首批接收时刻。原期限都是上限，质量或业务失败可以更早结束。表中没有将数据完整性条件省略成默认通过。

| 原语 | 形成、发布 | 当前期限及主要维护 | 审查结论 |
|---|---|---|---|
| AP | 完整750ms，后50ms内发布；份额≥65%、量及分布达标 | c+750ms；每250ms检查份额60%、半数最低量、末桶同向流；反向AP结束 | 确认阈值高于维护阈值；与自身EX不能当前重叠 |
| CA | SELL≥5后后续BUY≥10，必须c<t0+750ms | t0+3秒；原Bid仍为best_bid、质量合格 | 总寿命从首笔起；没有原语层100ms最低形成时长 |
| EX | AP确认后再完整750ms，三桶各≤Vref/6、总≤Vref/4，后50ms内发布 | c+750ms；同向滚动量≥Vref/2或反向AP即结束 | 原AP是历史参照；反向AP接替会结束当前EX |
| BA | 卖量达5后累计减量达5，再累计正补量达5，c<t0+750ms | t0+3秒；原Bid及质量；无持续净恢复floor | 确认“曾补过”不等于当前补量仍在 |
| AA | 750ms内跨50%后连续100ms≥40%；允许c=t0+750ms，无额外50ms补发AA | c+750ms；冻结低谷的恢复率不得<20%，原Ask触及档仍在 | 确认时40%可以成立；不等于轮末稳定的50% |
| RF | 主版两轮500ms滞回稳定；首轮起至确认≤15秒 | c+8秒；后续合格失败轮、原档/质量失效结束；无独立量floor | 不是末100ms连续≥50%，也不是需等8秒确认 |
| QR | 750ms受击分布；全程70%、末100ms90%、首次发布90% | c+3秒；70%floor、逐笔100ms复核；待复核不可用于新行动 | 交接70%与发布点90%有意不同，不冲突 |
| DF | M4频次版单参考+下一合格弱轮；r≤min(60%,0.8r_ref)，d≥5% | c+5秒；未来合格稳定轮r≥50%先清旧DF | 必须按旧/新ID顺序处理，不能让生成轮立即自我撤销 |
| RD | 500ms严格恢复种子；其后750ms内首达20%并保持100ms，发布仍≤20% | c+750ms；维持0<Q≤旧40%恢复线 | 同轮COMMON支撑已在RD确认前失效；不可追认复活 |
| LW | 三项净减量阈值100ms保持，首次发布仍满足，c<t0+750ms | c+750ms；残余至少初量10%及损失25%，H始终累计 | 形成比维护严格；50ms发布预算不能越过750ms硬截止 |
| XR | 攻击资格后新净增量，100ms保80%；发布点80%，整体耗时>100且<650ms | c+3秒；e起50%底线，新目标成交100ms复核 | e后新打单允许但须复核；形成期零打单不能延续错阶段 |
| XW | 对称净减量100ms保80%；发布点80%，耗时>100且<650ms | c+750ms；e起50%减量上界及正量，新目标成交100ms复核 | 数量对称不等于寿命对称 |
| SR | 20ms增量留存、200ms内首次返回，再保持50ms；耗时70至不足300ms | c+750ms；e起Q≤B+0.5Δ，形成下界解除 | 发布不另加Q≤B+0.20Δ的返回门槛；新成交不计入旧形成H |
| SB | 20ms减量留存、200ms内首次补回，再保持50ms；耗时70至不足300ms | c+750ms；e起Q≥B−0.5Δ，形成上界解除 | 与SR同结构，但不能漏掉正量/减量上限 |
| QO | 三个250ms节点、双反转、总变差≥20%B，750–800ms确认 | c+750ms；±5%带宽及零目标消耗持续 | 确认后无需继续反转；首笔目标消耗结束，不能直接作成交时零打单守卫 |

本次未发现主版确认条件必然导致自身不可成立的新矛盾。上述是按阶段与版本进行的纸面检查，不是检测器实现通过测试的证明。尤其不能把“发布点检查”“整个交接路径检查”“确认后维护”写成同一个阈值。

## 4. 明确的时间与顺序冲突

### 4.1 同一AP与其EX没有当前交集

固定同一AP身份，dA=cA+750ms。EX从cA开始全新750ms，cEX≥cA+750ms=dA。期限到等号已失效，所以“AP当前有效 + 它自己的EX当前有效”无解。

正确表述是AP历史 → EX当前。AP因变弱或自然到期不自动取消EX观察；但反向AP有明确取消观察的规则，不能混淆。

### 4.2 EX与反向AP不能作为两个当前确认

已有Pressure在AP_BUY确认时结束EX_SELL及其形成观察，反向同理。因此“卖方衰竭后的主动接替”应表达为EX_SELL历史 → AP_BUY当前，而不是EX_SELL当前 + AP_BUY当前。CA不是AP：小规模反击可能尚未形成AP，不能把这条互斥直接扩大为EX与CA必然互斥。

同一Pressure实例的AP_BUY与AP_SELL也采用反向确认结束旧态的政策，不能从两个相邻确认记录拼成两个当前事实。换来源或另立保留政策必须另记版本。

### 4.3 短寿命原语之后再等新确认

对任何确认后TTL=750ms的原语A，若新QR首笔q>cA，则cQR≥q+750ms>cA+750ms=dA。AA、AP、EX、LW、SR、SB、RD、XW、QO均受这个通用时间限制；有些还会更早因新打单失效。

若只等新的500ms轮，cR>cA+500ms，A剩余不足250ms，也达不到COMMON首次发送至少400ms的要求。这是Maker准入不可行，不等于观察记录完全不能重叠。可以用事先已形成的另一组件，或明确历史交接并另定完整政策；不能自动给A续期。

### 4.4 QO不能一边零受击、一边解释同档Maker受击成交

QO_BID要求原P零SELL。让原P Maker买单成交的真实消耗流一旦进入同一输入口径，就结束QO当前态。订单取消在途仍可能成交，这些成交照常记账；不能为维持解释删除它们。

QO可作历史前置状态，随后由新SELL和QR重新验证。现有M17回摆后扛卖已冻结一种具体交接；不能把它简写成成交时QO依然当前成立。

## 5. 相同端点可能掩盖形成路径冲突

### 5.1 XR与SR，XW与SB：同一变化不能同时保持与回归

固定同P、同B和同一次净加量Δ>0。XR当前底线是B+0.5Δ，而SR首次返回需Q≤B+0.2Δ；到达返回时XR已经结束或形成失败。后来Q回到共同的50%边界不能复活XR。

减量对称：同B、同次D>0时，XW当前上界B−0.5D，SB首达补回要求Q≥B−0.2D。SB补回先终止XW。仅把最后的两个50%量界相交，会漏掉先前20%返回事件造成的失效。

这是指定共同基线与事件的结论，不是两种缩写在全市场永远互斥。

### 5.2 XR与XW：同基线当前量界不相交，不同基线可以共存

同B>0时，XR的F≥1.10B，XW的U≤0.90B，所以当前交集为空。不同历史则不能直接套用：

旧XR的B=100、Δ=20、F=110；后来量已到140，以新B=140开启另一XW，D=28，减到112仍未跌破旧F。XW保持上界117.6、当前U=126；若其余零打单、顺序、来源及旧TTL均合法，两者可以在112共存。这个例子仅证明数量路径有解，不证明共同触发频次或独立信息。

### 5.3 SR与SB同家族，但不是全局互斥

若SB复用SR原峰值回落，按[P_SB](P_SB.md)的推导，SB补回达标必先超过原SR上界。不同后续基线则可以共存：旧SR B=100、Δ=20、U=110，后续SB由104降80再补至100，SB当前F=92；当前量100可落在[92,110]，原期限和其他证据仍须成立。

### 5.4 QO在先时，20%新变化会先结束它

QO当前带为[0.95B,1.05B]。从带内b开始，同档新XR/SR至少20%增量达到≥1.14B，超过1.05B；新XW/SB至少20%减量达到≤0.84B，低于0.95B。因此这个明确顺序下，旧QO在材料性门槛到达时就已失效。不同档位、反向顺序或不同历史不能据此作全局排除。

## 6. 看似“承接”或“弱供”，实际上证据不同

### 6.1 BA、AA、RF、QR不能互换

BA卖量已达5后，原量100降70，再补到75，累计减量和正补量均达标，BA可确认；净恢复却只有5/30，不能当作50%净恢复。

QR可以有真实大量打单但可见数量始终100：在身份、三个桶及复核合格时QR成立，却没有合格低谷供AA/RF确认。反过来，500ms小冲击两轮可给RF足够证据，但远未达到QR的H≥0.5Q0及三桶分布。单轮AA提前确认，也可能在750ms封口时恢复不足50%，不能用AA条数代替稳定轮数。

旧BA和主RF没有持续数量floor。因而它们当前ID尚有效，只表示对应历史证据仍可引用，不能自动解释成此刻仍守住某个数量水平。与当前QR/新COMMON S一起使用时，必须明确谁提供持续守卫。

### 6.2 RD结束的是同轮严格支撑，不是所有叫“承接”的原语

同一COMMON种子R的m=Qmin、L>0，S的floor=m+0.4L；RD必须先到Q≤m+0.2L。于是S必已失效，不能等RD确认才撤旧订单。

这不能推广成“RD与旧RF/BA互斥”。旧RF可能已由两轮确认，随后没有新合格失败轮；无新打单的回吐不自动触发它的数量退出，因为该退出不存在。两标签并存表达历史多轮补给与后续回吐，不能作为两项正向加分。

即使AA与RD共用同一低谷/损失基线，AA当前只在r<20%时退出，RD形成要求r≤20%，两者形成交集也可能恰在20%边界；RD发布后放宽至40%，可与仍存活AA落在20%–40%区间。若此前已经低于20%使AA结束，后来回到边界也不能复活。不同窗口与来源仍须单独成立。

### 6.3 DF的弱化与恢复退出必须带事实顺序

参考恢复0.80、下一轮稳定恢复0.55时，0.55≤min(0.60,0.64)，可生成新DF；同时0.55满足清除旧DF的稳定≥0.50条件。

正确顺序是先清旧DF，再用旧参考判断新DF，最后按本轮建立下一参考。不能把本轮又作为新DF之后的“未来恢复轮”，立刻把新DF清掉。阈值交集是既有状态机选择，不是本次新增规则。若希望改成相对恢复退出，M4已有独立备选，不改主版。

DF来源FixedRounds与research Rounds在封口数量/接收时间过滤上也不同；“都500ms、都r≥50%”不足以共享一个未注明版本的轮次。

### 6.4 LW与单档模式不能按名字合并

原三档100/100/100，仅前档降到80时，XW可在其他条件合格时形成，但三档只损失6.67%，不是LW的30%。反过来前档100不变、后两档各55，LW可确认，而前档QR、旧支撑或QO仍可能有效。

SR从增厚前算返回，LW可从峰值建立另一个基线；RD从真实受击低谷算恢复回吐，XW从跨侧攻击前算净减。它们可以共享一个减量事件，却保留不同历史。记录共同事件引用，不增加独立票数。

### 6.5 零目标成交窗口与真实受击窗口

XR/XW形成期目标零成交，QO从前500ms到当前结束始终零目标消耗；SR/SB则允许低预算而不是零。不能把这些窗口的零记录，同时当成该目标档新BA、AA、RF或QR受击证明。

CA与XR_BID/XW_BID也不总能叠加：若CA的原Bid SELL落入跨侧原语前500ms零SELL区间，该路径失败；若SELL更早或档位不同，需要按各版本具体检查，不一概禁止。

## 7. 统一解释，不统一掉版本差异

每个组合至少保留如下身份：token/generation、主动方向或挂单侧、原价档/档组、root及原始事件引用、基线/低谷、source/received/available、深度视图、自有量处理、直接或镜像成交口径、形成/交接/当前阶段、确认与原截止。

同一明确配对、同数量和输入口径下，多个下界可写成max(F1,F2)，多个上界可写成min(U1,U2)，截止取min(d1,d2)。但前提是两历史都真实成立且从未失效；数量区间非空只是必要条件。复核待办、质量和迟到反证仍分别保留。仅方法名、价档或数值相同不证明可以删除一个历史证据。

[既有组合审查](PRIMITIVE_COMPOSITION_REVIEW.md)中的“QR先、XR后”是可合并重复数量检查的例子：XR底线更高，QR原截止却更早；删除QR整个事实会丢掉先前受卖证据和较早期限。

“历史已确认”“当前未失效”“可用于首次行动”“订单是否已生效/成交”是四个不同问题。QR/XR/XW的PENDING_RECHECK可保留历史事实，但阻止新行动；BA/RF无持续floor也不应伪装成强守价状态。不存在一个所有原语通用的布尔valid就足以表达完整Maker语意。

AA/CA/BA要求触及档身份的口径，不能覆盖到允许原档离开最优位的RF/DF或新原语；AP/EX按整个token方向流量，不能默认为单价档攻击。兼容条件不明时保留UNKNOWN，不能用“未见另一个信号”当不存在该状态。

## 8. 本次实际修改与交付边界

- 新增本审查，给15个原语登记6个语意类别与13个研究入口；SR/SB、XR/XW使用统一入口并保留模式。
- 更新PRIMITIVES与catalog分类，README提供统一入口；原有15个ID继续可引用。
- 修正P_QO、P_XW中两处过期交接说明：M17回摆后扛卖、M21旧托先行已经有完整显式备选SOP。更正引用不等于批准执行；两个原语的参数、版本和生命周期未改，旧文本按hash保留。
- 其他13份原语及23份M的正文、全部原语参数、M主版参数和8个策略备选保持原样；没有合并订单/收益账本。
- 不新增原语或策略编号，不实现检测器/适配器，不运行信号测试、回测或模拟。长期任务与小时定时器保持关闭。

本页覆盖全部15份主定义的形成与退出审查，并对最可能混淆的配对给出条件证明；不声称穷尽任意历史、价档和窗口组合，也不声称测出了实际重叠率或结算优势。后续若要让两个分支共用可执行状态机，应另立明确版本并由用户模块验证，不能直接把本研究分类当运行迁移。

# M7–M23 增频设计落实与模块入口

2026-11-17，Asia/Shanghai。用户已授权落实[M4思路设计建议](M7_M23_M4_METHOD_DESIGN_REVIEW_20261117.md)，并停止没有明确可交付增频改法的独立策略。本文件落实的是SOP与研究安排；没有更改检测器、测试配置、服务、订单或运行进程。

同日此前修订v1.1：按用户授权新增M12有效期重叠版，取代去损失比版作为第1批文档入口；原SOP与去损失比版保留，其他策略和批次顺序不变。1.5s只诊断，各自3s、冲击可比及连续数量守卫不变，详细交接见[第1批说明v3](HANDOFF_BATCH1_M12_M23_SIGNAL_TEST.md)。

同日此前修订v1.2：按用户增频优先要求，新增M23_FIRST_SELL_WHILE_VALID，在第1批位置取代NO_LOSS_COVER。首SELL750ms降为诊断；首笔身份、100ms种子、500ms验证、连续Fpre/F及原种子3s保留，不新增降频过滤。原M23及旧分支均保留；M12定义和其他策略安排不变。

当前修订v1.3：落实用户六项授权，独立新增M21_ENDPOINT_COFLOW、M7_PAUSE_RELEASE、M9_EX_VALIDITY_OVERLAP，分别更新第2批和第3批入口。M21双侧100ms及首轮10%/5%、M7暂停单桶35%、M9 EX单桶30%仅诊断；M7解除暂停后冻结AP1占用，M9从EX确认即维护局部3秒。M9仍在新R的H/L资格q交接，未提前至首SELL。全部旧SOP与第1批定义保持。

```json
{
  "document_type": "signal_research_disposition",
  "version": "m7-m23-frequency-disposition-v1.3",
  "date": "2026-11-17",
  "status": "USER_AUTHORIZED_DOCUMENTATION_IMPLEMENTED",
  "scope": "SOPS_INDEX_AND_CATALOG_ONLY",
  "new_variant_ids": [
    "M8_RESPONSE_REPAIR",
    "M18_STABLE_RECOVERY",
    "M21_COFLOW_NO_PREQUIET",
    "M23_BUFFER_NO_LOSS_COVER",
    "M12_VALIDITY_OVERLAP",
    "M23_FIRST_SELL_WHILE_VALID",
    "M21_ENDPOINT_COFLOW",
    "M7_PAUSE_RELEASE",
    "M9_EX_VALIDITY_OVERLAP"
  ],
  "retained_variant_ids": [],
  "latest_amendment": "BATCH2_M21_AND_BATCH3_M7_M9_SIX_CHANGES_NO_EARLY_M9_HANDOFF",
  "preserved_previous_variant_ids": [
    "M12_SHOCK_COMPARABLE",
    "M23_BUFFER_NO_LOSS_COVER",
    "M21_COFLOW_NO_PREQUIET",
    "M7_PARALLEL_CONFIRM",
    "M9_EX_HANDOFF"
  ],
  "new_diagnostic_feature": "M11_REAR_RETENTION_TIMELINE",
  "diagnostic_only_strategy_ids": [
    "M10",
    "M11",
    "M14",
    "M19",
    "M20"
  ],
  "stopped_followup_strategy_ids": [
    "M13",
    "M15",
    "M16",
    "M17",
    "M22"
  ],
  "original_sops_and_existing_variants_preserved": true,
  "tests_or_simulations_started": false,
  "runtime_changed": false,
  "hourly_reports_enabled": false,
  "current_followup_variant_ids": [
    "M12_VALIDITY_OVERLAP",
    "M23_FIRST_SELL_WHILE_VALID",
    "M21_ENDPOINT_COFLOW",
    "M7_PAUSE_RELEASE",
    "M9_EX_VALIDITY_OVERLAP",
    "M8_RESPONSE_REPAIR",
    "M18_STABLE_RECOVERY"
  ]
}
```

## 1. 明确的取舍

保留七个独立分支文档入口：M7/M8/M9/M12/M18/M21/M23。11月17日最初新增四份，随后M12/M23各新增一份，本次M21/M7/M9再新增三份，累计当日九份V；当前七个入口均为当日明确规格，旧版作为对照。M11仅母确认后的时序诊断，M10/M14/M19/M20继续诊断；M13/M15/M16/M17/M22不列后备、输入审查或重构候补。

“停止跟进”依据是当前已知设计与输入下没有本轮可闭合、值得独立推进的增频方案，不是证明所有市场和参数永远无效。特别是M16的数据可证明性不能靠删质量门槛解决；M17去桶后变为更宽的QR命题，需重定义且与保量家族重叠，本轮不另开重构。保留其原SOP、原语、旧分支和历史结果，不停止可能由其他任务管理的进程。

M18只有一项清楚的定义简化，所以交付后备分支；但既有摘要尚未进入最终配对，移除恢复比较不解决已知输入阻断。它不授权继续多轮调参或在相同输入阻断上重复投入。后续模块若反馈仍卡输入、没有新增可证明配对，应收尾停止本分支；本任务不运行该核查，也不把已有原版结果称为新版结果。

## 2. 当前入口及顺序

顺序按本轮可解释的增频路径安排，不按收益排名。它是文档研究交接顺序，不能触发调度器或自动切换已有测试。

| 批次 | 策略 | 当前唯一跟进入口 | 与保留旧版的差异 |
|---|---|---|---|
| 1 | M12 | [M12_VALIDITY_OVERLAP](V_M12_VALIDITY_OVERLAP.md) | 去损失比版保留；独立1.5s配对上限降为诊断，各自3s内连续有效重叠且u可比才确认 |
| 1 | M23 | [M23_FIRST_SELL_WHILE_VALID](V_M23_FIRST_SELL_WHILE_VALID.md) | 首SELL750ms仅诊断；Fpre持续有效时取首笔，完整500ms仍须在原种子3s内确认，旧分支保留 |
| 2 | M21 | [M21_ENDPOINT_COFLOW](V_M21_ENDPOINT_COFLOW.md) | 双侧末100ms及首轮Bid10%/5%仅诊断；端点恢复、绝对材料性、净减/零BUY/F/U及原3s保持 |
| 3 | M7 | [M7_PAUSE_RELEASE](V_M7_PAUSE_RELEASE.md) | 暂停单桶35%仅诊断；Kp后仅解除冻结AP1占用，新AP2与S共同有效、原截止保持 |
| 3 | M9 | [M9_EX_VALIDITY_OVERLAP](V_M9_EX_VALIDITY_OVERLAP.md) | EX确认即连续维护3s，单桶30%仅诊断；q仍H/L资格，50%/95%与复验保持 |
| 后备 | M8 | [M8_RESPONSE_REPAIR](V_M8_RESPONSE_REPAIR.md) | 删除dR/dW相近要求；命题改为新响应由弱转强，不宣称同压改善 |
| 后备 | M18 | [M18_STABLE_RECOVERY](V_M18_STABLE_RECOVERY.md) | 删除r2≥r1，各轮严格稳定、加压和双损失比较保留；明确输入瓶颈 |

新的[M11诊断](D_M11_REAR_RETENTION_TIMELINE.md)没有独立交易语意、准入或退出，不延迟母确认。母确认后才可得的越线/留存不能回填为入场特征；诊断观察到母结束即截断，不占单独的信号频次目标。

旧[M9/M12交接](HANDOFF_M9_M12_SIGNAL_TEST.md)和[M7交接](HANDOFF_M7_PARALLEL_CONFIRM_SIGNAL_TEST.md)仍绑定原先明确版本，数值与边界不变，旧批次顺序以本文件为准。其中旧M12仍有1.5s限制，当前重叠版须读[第1批交接v3](HANDOFF_BATCH1_M12_M23_SIGNAL_TEST.md)。旧[M21/M23交接](HANDOFF_M21_M23_SIGNAL_TEST.md)仅适用于先前单轮/缓冲版，不能直接作为本次新规格的适配验收；旧文件与旧SOP完整保留。第2/3批最新须读[M21交接v2](HANDOFF_BATCH2_M21_SIGNAL_TEST.md)与[M7/M9交接v2](HANDOFF_BATCH3_M7_M9_SIGNAL_TEST.md)，旧交接及两批v1不覆盖新规则。M23的NO_LOSS_COVER虽删H≥L，仍有首SELL750ms上限；当前入口以本表和第1批v3为准。

## 3. 新规格的重点交接检查

| 对象 | 确认重点 | 失效与截止重点 |
|---|---|---|
| M12_VALIDITY_OVERLAP | gap/1500ms仅诊断；两侧原有效期重叠、u可比、完整原始frame后首次确认，允许任一侧先形成 | 连续FB/FA、各自c_i+3s及一次性选根不变；不在1.5s清组件，不重新给3s；c=较早expiry先失效，短寿命与执行受限分开 |
| M8_RESPONSE_REPAIR | 只删除跨轮损失比；仍有合格弱W、首个合格新R、旧滞回held及rR≥0.60；未知轮不能跳过 | W作为历史，原档不能消失；cR<cW+5s，确认后cR+8s；后续合格失败或r<0.60结束；没有新增COMMON floor |
| M18_STABLE_RECOVERY | 两轮各自末100ms恢复≥0.50，H2/H1仍[2,4]、初量可比、L2≤L1且d2≤d1；r差只描述 | 不把旧r2≥r1藏在配对或发布路径；R1不加floor；e2→c2守F2，c2<c1+2s，最终c2+3s；输入不可证仍结束 |
| M21_ENDPOINT_COFLOW | 首轮H≥5/L≥0.5、端点r≥0.50、Ask净减达标；双侧100ms及10%/5%仅诊断 | 后续合格轮不因100ms不足退出，端点失败仍结束；来源/零BUY/连续F/U、c+3s与等号规则保留 |
| M7_PAUSE_RELEASE | 总量pause≤50%；Kp后唯一全新AP2 watch忽略冻结AP1占用，完整AP与S确认 | 无重复watch/重放旧成交；AP1自然到期不取消新watch，S/AP2/root原截止与反证保留 |
| M9_EX_VALIDITY_OVERLAP | EX总量≤49%、30%桶仅诊断，cEX即建连续维护代理；S/局部EX当前交集 | 无原生750ms配对硬限，无补建/重置3s；q同输入50%优先，q后95%，原历史反证及R完整复验保留 |
| M23_FIRST_SELL_WHILE_VALID | 750ms只诊断；仍首笔SELL、真实H、完整500ms及窗内成交后末量；L=0/L>H继承允许 | 不在0.75s清root，不跳首笔；合法Fpre→F、e→c守卫、原cS+3s及到期等号保留；无隐藏1700ms余量 |
| M11诊断 | 只有母确认时可得的基线，后续字段带真实as-of；缺字段不拦母 | 无独立订单/退出，无母期限延长；母结束截断，原M10诊断不作母信号 |

放宽接受条件不保证全样本频次单调上升，首选根、占用和一次性消费仍会改变路径。未知仍未知；没有以放宽来源、新鲜度、次序或原档守卫增加信号。Maker/Taker及固定锚价的真实执行限制按[共同合同](FIXED_ANCHOR_CONTRACT.md)独立保留。

## 4. 停止、归并与历史

| 策略 | 本次落实后的处理 |
|---|---|
| M10 | 仅M2回流诊断，不恢复独立策略 |
| M11 | 仅续托时序诊断，退出强制附加筛选；与M20共享分布研究入口，基线与时间不同 |
| M13 | 维持归档；删复验后归M6家族，不保编号另起策略 |
| M14 | 仅重复弱供诊断，归M4/M12家族 |
| M15 | 停止跟进，取消重构候补；LW接替关键保护未闭合，不自动新建V |
| M16 | 停止本任务主动跟进及调参，输入审查也不再列主动队列；原SOP和无样本结论保留 |
| M17 | 停止跟进，取消一般QR重构候补；旧SB_QR分支不自动启用 |
| M19 | 仅Ask响应变化诊断，归M4/M12家族 |
| M20 | 仅Bid分布诊断，不新增形态信号 |
| M22 | 维持归档；方向语意未补足，不继续调整形态范围 |

其余独立分支按前表保留。家族归并不合并root、条件或账本，也不将相同历史样本重复计数。原M1–M23、所有旧V/D/P及共用合同不改；M4正式实盘与M6已停基线的历史状态不因本轮文档改变。

## 5. 版本与检查范围

[本轮基线](../signal_discovery/spec_archive/M7_M23_FREQUENCY_IMPLEMENTATION_BASIS_20261117.json)保存最初四份V及诊断改动来源的hash及参数；后续M12重叠版和M23首SELL版各自在首JSON固定直接基线SHOCK_COMPARABLE、NO_LOSS_COVER及相应上游清单，不修改已有快照。本次三份SOP也各自固定直接旧分支的全文hash及上游basis；旧来源不修改。新文件与catalog以明确版本绑定，读取各文件首JSON，不继承隐式主版默认值；没有假定当前测试模块已支持新分支。

本轮只核对文档JSON、引用、版本/hash、删除项在确认与维护中的一致性，以及原文件未变。没有启动信号测试、回测、模拟、采集、适配器或定时器；没有计算收益或更新任何历史结果。每小时汇报继续关闭。下列身份表是本轮交付入口，数据结果须绑定它们，不能拿旧回放填作新规格验收。

| 入口 | 明确版本 | SOP SHA256 |
|---|---|---|
| [M12_VALIDITY_OVERLAP](V_M12_VALIDITY_OVERLAP.md) | `m12-live-support-weak-ask-overlap-v1` | `99c100e5d03e51f648a62f63090f5b45cbc38e31864086bac2c76effccf7b1e0` |
| [M8_RESPONSE_REPAIR](V_M8_RESPONSE_REPAIR.md) | `m8-weak-to-strong-response-v1` | `b026facd9c187b4c3e673541e2826dcabfe7707a0907fd6c8c3598ab4261e308` |
| [M18_STABLE_RECOVERY](V_M18_STABLE_RECOVERY.md) | `m18-escalating-hit-stable-recovery-v1` | `4e22ca19251956367571f65b26a23d3faf7c7f187d9fb0de7d2e407e3a5c3897` |
| [M21_ENDPOINT_COFLOW](V_M21_ENDPOINT_COFLOW.md) | `m21-endpoint-recovery-withdrawal-v1` | `a7e36318bb3d0098c0a7c6a0425925c51d7dbdd9ba21eba7c79f54cd76b7ee0c` |
| [M23_FIRST_SELL_WHILE_VALID](V_M23_FIRST_SELL_WHILE_VALID.md) | `m23-buffer-first-sell-while-valid-v1` | `498e3d1af5e710e6c79e82a7836282ac61cb7e320f67de081d22cc70b80f1b09` |
| [M11_REAR_RETENTION_TIMELINE](D_M11_REAR_RETENTION_TIMELINE.md) | `m11-rear-retention-timeline-diagnostics-v1` | `5fceb5314e9ddc7c83136befb8b5b77c9cbf663518a951b3582049f07ed4c811` |
| [M7_PAUSE_RELEASE](V_M7_PAUSE_RELEASE.md) | `m7-total-pause-released-reattack-v1` | `21fffffb96001acac2264bd3b0cd6f042b87456448780ce61bf4ba645d5b2acc` |
| [M9_EX_VALIDITY_OVERLAP](V_M9_EX_VALIDITY_OVERLAP.md) | `m9-continuous-ex-overlap-handoff-v1` | `0af9c4415088218669a2f6bdf15827f66203d3123dde5703ba174c1f6c149122` |

# M8 增频分支：弱后复强，不限定损伤相近

2026-11-17，`m8-weak-to-strong-response-v1`，SPEC_ONLY。**同一原Bid先弱恢复，之后首个合格新受卖轮恢复转强；在新承接有效期间于固定锚价成交。** 保留[M8原版](M8.md)。本版删除新旧损失比例相近门槛，明确不再声称同等损伤或同等压力下的能力改善。

```json
{
  "document_type": "signal_sop_variant",
  "strategy_id": "M8",
  "variant_id": "M8_RESPONSE_REPAIR",
  "version": "m8-weak-to-strong-response-v1",
  "status": "SPEC_ONLY",
  "selection": "EXPLICIT_ONLY",
  "revision_date": "2026-11-17",
  "research_family_id": "F_REPAIR",
  "baseline_version": "m8-fixed-anchor-entry-v1",
  "baseline_definition_file": "M8.md",
  "baseline_definition_sha256": "91fd75a1c27081e6a21bf22de26cdbba1d6f84037e5099a0a203704c1b1a40c9",
  "main_sop_preserved": "M8.md",
  "calculation_contract": "LEGACY_COMMON.md",
  "signal_contract": "fixed-anchor-signal-v1",
  "execution": "fixed-anchor-execution-v1",
  "execution_modes": [
    "MAKER",
    "TAKER"
  ],
  "entry_semantics": "FILL_AT_FIXED_ANCHOR_WHILE_SIGNAL_VALID",
  "weak_rounds": 1,
  "weak_recovery_max": "0.30",
  "repair_rounds": 1,
  "repair_recovery_min": "0.60",
  "round_window_ms": 500,
  "round_min_hit_shares": "0.5",
  "round_min_depth_fraction": "0.0001",
  "repair_hold_ms": 100,
  "repair_hold_start_recovery": "0.50",
  "repair_hold_timer_reset_below": "0.40",
  "repair_held_revoke_below": "0.20",
  "repair_formation_ms": 5000,
  "depth_loss_ratio": null,
  "hit_ratio_required": false,
  "cross_round_ratios_role": "DIAGNOSTIC_ONLY_NO_GATE_NO_EXIT",
  "repair_round_selection": "FIRST_QUALIFIED_NEW_ROUND_SKIP_ONLY_PROVEN_UNQUALIFIED",
  "new_round_gate": "INPUT_KEY_AFTER_WEAK_CONFIRMATION_NO_EVENT_REUSE",
  "weak_fact_role_after_confirmation": "HISTORY",
  "post_repair_ttl_ms": 8000,
  "followup_qualified_round_recovery_min": "0.60",
  "common_strict_recovery_floor_added": false,
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE",
  "tests_run": false,
  "basis_file": "../signal_discovery/spec_archive/M7_M23_FREQUENCY_IMPLEMENTATION_BASIS_20261117.json",
  "basis_sha256": "510f0315d67092a6e42d624def47f5df7aedb7a8fa04706fbe097068a231a762"
}
```

## 1. 身份、输入与机制

本版只修改原M8的dR/dW接受区间及其传播路径；u比原本已是诊断，不新增冲击可比。基线身份见首JSON及[本轮基线](../signal_discovery/spec_archive/M7_M23_FREQUENCY_IMPLEMENTATION_BASIS_20261117.json)。使用LEGACY_COMMON的固定档、去重成交、滞回稳定、因果可得和质量合同；沿其已核验互补映射政策，保留来源身份，不能双计或冒称全为直接成交。

原价P仅识别队列，不以价格推进确认方向。按`K=(available,input_seq,subindex)`推进，received右开窗统计；原token/generation/tick及健康连续数量链固定。缺来源、覆盖、前置量或顺序为UNKNOWN，不能视为未达门槛而跳过。若适配器采用不同输入政策，须另标版本，不能悄悄用新版质量口径替换旧源。

准确命题是“新的受击响应由弱转强”，不是原Bid消失后重建，也不保证恢复到弱轮之前的绝对量。第二轮可能更轻；dR/dW、uR/uW和初量比负责描述该差异，不承担筛选。

## 2. 弱轮与新修复轮

每轮首个匹配P的SELL冻结前置量Q0>0，观察500ms，记录H、Qmin、Qend、`L=Q0−Qmin`、`m=max(0.5,0.0001Q0)`。H≥m且L≥m才合格；`u=H/Q0,d=L/Q0,r=clip((Qend−Qmin)/L,0,1)`。无真实H/L不能靠净加量通过。

空闲时采用首个可引用的合格弱轮W：rW≤0.30，冻结W、P及cW/KW，进入观察，形成截止cW+5s。旧“DF_BID”只是这份单弱响应，不是P_DF的强参考后减弱，也不是RD。

KW之后按旧输入键gate形成同P新轮R，不复用W内成交，不挪动原窗口。沿原版跳过可证明的未合格轮；**第一合格R**必须通过修复，不能跳过合格失败轮再选成功轮。未知不是可跳过的未合格。原P不能消失、重建或换成另一最优档。

R按旧滞回保持：r≥0.50开始计时，连续100ms不低于0.40取得held；r<0.40清正在进行的计时，r<0.20撤销held，新低清两者。封口须held且rR≥0.60。它不是COMMON末100ms始终≥0.60，也不添加COMMON的40%固定数量floor。

**删除`0.5≤dR/dW≤2`。** 不以d比或u比限制R，不保留从该比推导出的任何资格下限、上限或提前结束；各轮自身材料性m不变。跨轮比值在完整确认时描述，分母不可证则字段null，必要形成事实本身未知仍结束。

## 3. 确认及历史交接

完整R封口及时可得、held且rR≥0.60，当前质量/原档仍有效，且实际发布cR严格<cW+5s，才确认。沿原来源最多50ms封口延迟；同输入失效、到期先于发布，不能只看发布末态抹去已记录失败。

W只是历史：原弱事实自然TTL到期、或当前回补到较高恢复使旧弱态正向结束，都不否定形成前史；历史真实性反证及原档丢失仍结束。等待R时不另加W的恢复上界或floor。R确认为新支撑，`deadline=cR+8s`；不得从W自然到期、收到小SELL或执行回执重计期限。

## 4. 当前维护与重新开始

维持原P正量、generation/tick、覆盖与质量至deadline；后续同P合格轮若不稳定或最终r<0.60则结束。后续可证明未合格轮依旧不是新反证；原档/质量仍即时检查。不存在dR/dW后补检或额外损失比退出，新好轮不替换旧引用、不续期。

每token仅一组本策略形成/有效观察。结束后只能由新的、可得键晚于结束且无已用成交重叠的合格弱轮建立新root；不把结束本root的同一轮回收再用，不滑窗救回已失败路径。形成期5s与确认后8s分别处理；到期等号先结束。

## 5. 确认与失效复核

| 边界 | 本版结论 |
|---|---|
| W初量100、L=20、r=0.20；新R初量84、L=1、r=0.80 | dR/dW约0.060，旧版拒绝；真实H/L、旧保持、来源及截止均合格时新版允许。R可能是更轻挑战，不声称压力可比 |
| R没有可见损失，或H不足m | 仍不合格，不能以去d比允许零损失恢复率 |
| 第一合格R恢复失败，后来R更好 | 原root已结束，不重选 |
| cR恰好等于cW+5s | 形成过期，不能借新8s发布 |
| 修复使旧弱态结束 | 允许历史留存；若实际原档已消失则仍结束 |
| 后续合格R恢复0.55 | 低于本版0.60维护门槛，结束；不因初次恢复很好而续存 |

这些是定义算例，不是测试。删除一个比值只扩大局部接受路径，首选根和占用变化使总体数量未必单调增加。

## 6. Maker/Taker与输出

按[固定锚价合同](FIXED_ANCHOR_CONTRACT.md)，Maker在合法P被动等待，Taker仅在信号有效且P有可取得的对手量时准入。正常未交叉同token盘口下，原Bid正量与同P主动买入无共同准入状态，不能忽略原档、换价或续期。执行余量、资金、时段、提交次数另选；在途成交及实际价格全部保留，自有成交不新增承接证据。

输出版本/hash、W/R身份及窗口、P、Q0/H/L/u/d/r/held、仅诊断跨轮比值、cW/cR/deadline、来源质量与结束原因。原版/旧备选/本版分开，不改历史结果。本轮不实现适配器、不启动测试或回放、不操作运行进程，不宣称已增频或提高结算概率。

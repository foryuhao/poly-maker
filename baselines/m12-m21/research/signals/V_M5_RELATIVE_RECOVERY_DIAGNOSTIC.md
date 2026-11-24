# M5 持续卖压下两轮稳接

2026-11-18。持续卖压覆盖第二次受卖，两次受击的买档都恢复至各自损失的一半并取得稳定证明。在原卖压和承接仍有效时，于原承接档 P 成交。相比[主版](M5.md)，唯一变动是把“第二轮恢复率至少为第一轮的 80%”降为诊断。因而新版允许第二轮相对较弱，取消“两轮强度相近”的解释。

```json
{
  "document_type": "signal_sop_variant",
  "strategy_id": "M5",
  "variant_id": "M5_RELATIVE_RECOVERY_DIAGNOSTIC",
  "version": "m5-relative-recovery-diagnostic-v1",
  "status": "SPEC_ONLY",
  "selection": "EXPLICIT_ONLY",
  "research_family_id": "F_SELL_ABSORPTION",
  "baseline_version": "m5-fixed-anchor-entry-v1",
  "baseline_definition_file": "M5.md",
  "baseline_definition_sha256": "9b53aa21f1136e65929cdbb40ca7b622d26ad6979d6bb0330a4bfe9b87d8222b",
  "calculation_contract": "LEGACY_COMMON.md",
  "signal_contract": "fixed-anchor-signal-v1",
  "execution": "fixed-anchor-execution-v1",
  "entry_semantics": "FILL_AT_FIXED_ANCHOR_WHILE_SIGNAL_VALID",
  "execution_modes": ["MAKER", "TAKER"],
  "rf_rounds": 2,
  "round_window_ms": 500,
  "stable_round_min_recovery": "0.50",
  "ap_second_round_overlap_required": true,
  "second_recovery_fraction_min": null,
  "diagnostic_second_recovery_fraction_reference": "0.80",
  "relative_recovery_role": "DIAGNOSTIC_ONLY_NO_GATE_NO_EXIT",
  "hit_comparability_required": false,
  "depth_comparability_required": false,
  "candidate_ttl_upper_ms": 750,
  "execution_owner": "USER_SIGNAL_TEST_MODULE",
  "adapter": null
}
```

## 形成与确认

1. 对同一市场、token、generation 和原 Bid P 按[既有计算合同](LEGACY_COMMON.md)识别 RF 的两个不同受卖轮。每轮 500ms，冻结受击前量 Q0；真实受卖量 H 和可见损失 L 都须 ≥ max(0.5, 0.0001 × Q0)。末恢复率 r = clip((Qend − Qmin) / L, 0, 1)。两轮各自封口须 r ≥ 0.50 且取得原滞回保持。r 达 0.50 起计，连续 100ms 不低于 0.40 取得保持，跌破 0.20 撤销已取得的保持，新低清空计时与保持。R1 开始至 R2 确认 ≤15s；中间合格失败轮、去重和质量沿原 RF。
2. 独立识别同 token 的 AP_SELL，沿既有 750ms 方向量、份额、分桶及集中度规则。AP 的**形成窗**与 R2 的**受击窗**须有正时长交集，即 `min(endAP,endR2) > max(startAP,startR2)`。这条要求支撑“第二轮仍在持续卖压中”的解释。
3. 两份证据均已可得且 AP、RF 仍有效的首个合法时点确认。根为 `rf.id|ap.id`，锚取 RF 的 P。成交来源、可得顺序、窗口封口、选根与去重沿主版。记录 r1、r2、r2/r1 和旧门槛是否通过；u2/u1、d2/d1 沿用诊断身份。任何比值均不得用于推迟发布、拒绝或撤回。构成 RF/AP 所需的原始证据未知仍不得发布。

## 有效期、失效与执行

截止取 AP、RF 原截止的较早值，AP 自确认起 750ms，RF 自确认起 8s。组合发布只获得两者剩余寿命。原 AP 滚动检查失败、反向 AP、RF 后续合格轮失败、原档消失或重建、质量失效或到达截止即结束。同一可得边界先应用失效和到期，再判断发布及执行准入。冻结的 r2/r1 不产生后续失效，新正向事实不延长原根。

| 核对边界 | 预期判定 |
|---|---|
| r1=0.90、r2=0.65，两轮稳定，其他条件全满足 | 新版确认；旧版要求 r2 ≥0.72。标记为“仅新版接受”。 |
| r1=0.80、r2=0.64，两轮稳定，其他条件全满足 | 两版均可确认；等于旧门槛算通过。 |
| r2=0.49，或 r2≥0.50 但未取得保持 | 两轮稳接尚未成立。 |
| AP 与 R2 两窗仅端点相接，或发布时 AP 恰到期 | 前者缺形成窗重叠，后者缺当前卖压。均不确认。 |

**Maker**　在信号仍有效且能合法被动挂价时，于 P 等待；失效后撤未成交余量。**Taker**　限信号有效期间在 P 有可取得对手量时准入；正常未交叉同 token 盘口中，原 Bid P 仍有量时同价主动买入受限。两者都按[固定锚价合同](FIXED_ANCHOR_CONTRACT.md)记录真实成交价、失效与撤单在途成交。执行结果不反向改变信号。

## 交给测试模块的判定问题

在同一批固定 24 场、同一输入与执行口径下，分别报告两版有信号的市场集合、独有市场、共有市场及去重信号。按根身份核对新增确认是否确由旧 80% 条件单独挡住，并区分因占用或选根改变而出现的路径差异。主版已有较高频次，首先看新增覆盖和确认时的剩余有效期，再看较弱第二轮在固定锚价有效期内是否有可确认成交；没有新增覆盖或独立解释时，保留原版。此处仅给出待检验假说。

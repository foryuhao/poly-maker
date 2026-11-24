# M17–M23 审查后的SOP更新（M16保持不变）

版本 `m17-m23-design-update-v1`，2026-11-16。用户明确M16正在信号测试，本次不修改M16文件、catalog条目、参数、研究状态或相关实现。[此前逻辑审查](../signal_discovery/M16_M23_LOGIC_REVIEW_20261116.md)中对M16的降级建议**不执行**，其余按下表落实。外部测试状态来自用户说明，本任务没有查询或操作测试进程。

```json
{
  "document_type": "signal_design_disposition",
  "version": "m17-m23-design-update-v1",
  "status": "DOCUMENTED_USER_APPROVED_RESEARCH_DISPOSITION",
  "scope": "DOCUMENTATION_ONLY_NO_RUNTIME_CHANGE",
  "basis_file": "../signal_discovery/spec_archive/M17_M23_DISPOSITION_BASIS_20261116.json",
  "basis_sha256": "c248f4745b7aa57fc10f4c1c2ea7ceb0be405383ca7bfcd2221dd36d2796c89f",
  "M16_excluded_by_user": true,
  "byte_unchanged_main_sops": ["M16", "M17", "M18", "M21", "M23"],
  "research_metadata_only_main_sops": ["M19", "M20", "M22"],
  "new_variant_sops": ["M21_SINGLE_ROUND", "M23_BUFFER_RETENTION"],
  "new_diagnostic_features": ["ASK_RESPONSE_CHANGE", "BID_DEPTH_PROFILE"],
  "common_or_primitive_rules_changed": false,
  "M17_window_end_recheck_contract_changed": false,
  "M18_recovery_comparison_changed": false,
  "tests_run": false,
  "runtime_changed": false,
  "hourly_reports_enabled": false
}
```

## 1. 当前处理与交付

| 对象 | 当前处理 | 入口 |
|---|---|---|
| M16 | 用户明确保持不变，不执行降级或参数修改 | [原M16](M16.md)，catalog条目原样保留 |
| M17 | 保留核心主版；不叠更多前史，不改复核合同 | [M17](M17.md) |
| M18 | 保留主版；r2≥r1的可选简化尚未采用 | [M18](M18.md) |
| M19 | 退出独立优化，保留已有两Ask轮响应描述 | [D_ASK_RESPONSE_CHANGE](D_ASK_RESPONSE_CHANGE.md) |
| M20 | 退出独立形态策略优化，保留原锚及后档分布描述 | [D_BID_DEPTH_PROFILE](D_BID_DEPTH_PROFILE.md) |
| M21 | 原版保留，后续入口为单轮共同确认新版 | [V_M21_SINGLE_ROUND](V_M21_SINGLE_ROUND.md) |
| M22 | 归档停止跟进当前设计，不自动重构 | [历史M22](M22.md) |
| M23 | 原版保留，后续入口为受卖后缓冲留存新版 | [V_M23_BUFFER_RETENTION](V_M23_BUFFER_RETENTION.md) |

新增两份V与两份D，不新增M编号或原语。目录仍存23份M、15份原语；当前独立分支增至7份、诊断至4份，8份冻结历史备选保留。M19/M20/M22历史正文与数值未改；旧priority不代表当前主动研究顺序。

## 2. M21：单轮共同确认

保留原版每轮的真实SELL材料性、Bid末100ms稳定恢复、Ask前250ms平稳、形成期零直接BUY、Ask末100ms净减及双侧来源证明。只用这一轮形成候选，使用其新F/U，从实际确认c计3s。

删除第二轮以及所有依赖第二轮的比较、二次前置平稳和2s形成计时。没有旧S要等，也没有R2完成后再切floor的阶段。重复响应最多作为后续可得的诊断，不能回填第一次确认、延迟它或使旧候选续期。

确认与失效有非空交集：Bid封口至少比F高0.10L；Ask封口比U低0.50D。e到c路径仍必须完整守界；发布批次后行BUY先作为形成反证处理。确认后的真正新BUY允许，但晚到、received≤c且归属形成阶段的BUY仍在发现时结束。

它不是将P_XW改成500ms或把M6改成单轮RF；两者的观测和期限不同。新信号频次尚未测试，不将“少一轮”直接宣称为实际增频。

## 3. M23：挑战用真实H证明，数量不必进入损伤窄带

删除L≥0.50Δ及末100msQ≤B+0.50Δ；保留H≥max(5,0.50Δ)、H≥L、F=B+0.20Δ、完整500ms验证和成交后明确数量来源。允许L=0，但缺量不能当成零损失。末100ms的下限由全程F覆盖，不重复算信息。

H≥L明确保留为保守量级筛选，不证明逐笔归因：它不会拒绝H充分、L很小的留存路径，仍排除可见损失已大于全部观察SELL的部分路径。原80%种子底线只有在合法首笔新SELL处才切20%；之前已破不能救回。种子3s截止不从验证完成或回执重计。

数量例B=100、Δ=50、Q0R=150、H=30、最低和末量145，已知L=5且F=110，在来源和时序都合格时新版允许。若最低110，则L=40>H仍失败。均为定义边界例，不是测试结果。

M17与M23可共同研究受卖后的数量留存，但M17的70%Q0不是M23的B+20%Δ，形成前史、成交分布与恢复要求不同；本次不改两者家族ID或合并触发器、账本。

## 4. 诊断与归档

M19诊断只读取来源已选好的参考/当前Ask轮，H/u/L/d比与恢复差无阈值；没有参考就null，不能让M12为补诊断等第二轮。M20诊断冻结指定母root初始触发前的原P与两个后档，确认点描述净变化；没有后档就null，不增加100ms留存、形态退出或发单。

两者无独立信号寿命、准入和退出权。诊断缺字段不影响母信号，真正否定母证据的质量问题仍按母合同。多字段共享事件不重复算独立方向信息，不把旧M19/M20成交移入母策略。

M22保持历史结构记录，停止其独立设计与调参，不把Ask近薄远厚当成LW或与Bid前聚取并集。

## 5. 保持与延期的规则

- M16文件及完整catalog条目原样保留；本次也不修改其共用COMMON、原语或输入合同。
- M17窗末复核限制值得另行研究，但当前100ms/窗内末量合同不改；不能将窗外当前量倒填为窗内留存。
- M18保留r2≥r1；“可以研究简化”不自动变成新版，本次没有新M18参数。
- M21/M23原版与所有既有V分支不改，消费者须显式选择新版本；本任务不声称现有模块已支持读取。
- 固定锚价、真实执行限制与在途回执边界保持；原Bid正量与正常未交叉盘口同价Taker买入无共同准入状态，不删除守卫制造成交。

完整边界核对见两份新V的第7节及两份D的生命周期部分。一致性回执为[文档检查](../../research_outputs/signal_discovery/m17_m23_sop_review_20261116.json)。仅检查JSON、hash、引用与定义关系；不运行信号测试、回测、模拟或适配器，不修改运行配置、定时器或进程，每小时汇报保持关闭。

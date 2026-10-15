# Independent Review — ITER-0032

结论：**PASS_IMPLEMENTATION_WITH_BASELINE_TEST_BLOCKER**。

按用户要求使用现有独立 Reviewer Agent；Reviewer 未编辑工作区，未接触真实凭证、
账户或交易，所有复测使用既有 offline guard。最终独立组合回归：
**233 tests passed，0 failed，0 network/DNS attempts**，12.61秒。

独立审查中发现并修复：

1. archived fallback 未执行原严格 Session header/balance 检查。
   提取并复用 `_check_session_header`，18项坏schema、数值、风险证据反例通过。
2. 恢复查询新发现的 pending 可能绕过原 AccountRiskPolicy，后续空页会丢证据。
   将原始成交送入既有 policy.pending 并刷新 `_account`；marker回放也必须重做相同检查。
   错误时间、外国maker、错误角色、Gamma Token/时间及空页/再次中断均覆盖。
3. 无intent尾组可包含孤立执行事件，window/close嵌套UNKNOWN也可能被忽略。
   新的 bound-events 检查绑定此前 durable ACK/Placement，复用原 WindowResult schema；
   嵌套记录须完全匹配已落盘 Placement。无ACK库存/资金非零和未知POST不能被忽略。

Reviewer 确认：

- 缺少最终 `sm_run_result` 可触发只读核验，不能直接宣布安全。
- 历史订单只获得临时 GET 权限，不进入 known_ids，不获得撤单权限。
- 原文件只追加独立 marker，不篡改旧日志，不伪造完成、安全退出、成交或结算。
- 新Session之前完成官方终态/成交/关联/资金风险检查；历史pending在漏页和重启后保留。
- 已完成Merge的摘要只从原结果投射，仍经过原receipt/token/cash/cost validator；
  不完整或冲突Merge拒绝，恢复不会执行/重复Merge。
- Timing、Pricing、Sizing、状态、交易窗口和原M2下单/资金/关闭机制未改变。
- 前述独立复现的blocker均已关闭，未发现剩余P1/P2。

全仓原有13项失败不属于该恢复修复的新增回归；保留原始失败记录。
因原任务仍要求全仓全部通过，整体 STATUS=BLOCKED；不把实现Review通过写成实盘已获准。

# 提交期间撤单回执的关闭核验（2026-10-17）

## 授权范围

只处理 12:10–12:15 场 Session `093f3786d7f54e2695f4538c605cc417` 暴露的
`order <ID> is invalid. Already canceled.` POST 回执。用户确认第一轮最低 1 USDC
引起的超额补仓符合设计，补仓数量、NORMAL/REBALANCE 状态阈值不改。
用户要求第 4 项日志先不动，因此不增加日志事件、字段或修改现有日志措辞，不改写历史 JSONL。

## 原因

w6 在 12:11:00.447 POST，00.707 有效反转触发 DELETE，00.973 DELETE 返回找不到订单，
01.139 POST 才返回 `success=true`、空 orderID/status/金额及明确的 Already canceled 业务错误。
错误文本中的 ID 与预先计算的签名订单 ID 一致。旧解析归 UNKNOWN，关闭流程不断要求读取
无法取得的订单详情，34 次失败后以 INCOMPLETE / BLOCKED 结束，保留 3.90 USDC 风险承诺。

## 修改

- 保留原始回执，在关闭核验时识别完整句式、精确签名 ID、已开始 POST、空接受/成交字段。
  DELETE 的 `already canceled or matched` 不适用此分支。缺字段、错误 ID、多个回复或成交信息均不匹配。
- 限定 OBI 模式与本 Session 唯一签名 ID。候选仍是 UNKNOWN，直到新的完整开放订单与市场成交分页查询成功。
- 以身份绑定的明确 POST 业务错误为拒绝证据，空列表只检查是否存在矛盾，不能独自解除未知风险。
- 已观察订单事件、开放订单、终态、成交、失败成交或隔离记录时，转回原订单与 FillTruth 核验。
  真实成交成功核实后变为 VERIFIED 并进入确认库存，不标成拒单。
- 无矛盾且读完全部分页时，使用既有 REJECTED 结果，不伪造 ACK 或不存在的终态订单。
  该签名 ID 不再要求完整订单详情；其原始回执及分页证据保留在 Session 内存中。
- 保留客户端取消权限及成交观察身份、tracker ownership；下一次账户快照或关闭时重新核验该拒单。
  迟到 WS 事实或新的 REST 矛盾会撤销拒单证明，恢复 UNKNOWN 与完整资金承诺，阻止新单。
- 读取失败、分页不完整、重用签名 ID、响应改变、确认期间 WS 事件等仍 BLOCKED。
  不因超时、零开放订单或余额未变直接放行；不重新发送原签名订单。
- 有效反转立即撤单、原始 2 秒期限、REBALANCE 3 秒与补仓策略、网络规则和恢复标记规则不变。

既有 `sm_placement_result` 保留收到时的原始回执与 UNKNOWN；既有 `sm_window_result`
在核验成功后呈现 REJECTED。日志结构、措辞与记录调用均不调整。

## 历史只读核对

[核对结果](RDP_CANCELED_SUBMISSION_RECHECK_20261017.json) 记录真实官方 GET 的路径与核对结果，
请求钩子禁止所有非 GET 请求，不运行 Session 入口或恢复撤单流程。

- 历史原始回执匹配新规则，签名 ID 在该 Session 中只提交一次。
- 官方直接订单详情仍触发 GatewayReadError / _Issue。
- 当前全部开放订单为 0；该市场交易记录为 0；按签名 ID 过滤订单也返回空列表；分页完整。
- 历史 JSONL 与账户恢复标记的前后 SHA-256 比较一致；保留原 UNKNOWN/INCOMPLETE 历史结果和 UNRESOLVED 标记。
- 未启动或重启交易，未手工解除账户恢复标记；标记恢复按已批准边界另行处理。

这些是当前官方读取与旧回执的一致性核对，不是回溯证明每个历史时刻的交易所内部处理顺序。

## 验证

离线真实 SDK/Gateway/Journal/Tracker 覆盖：双向有效反转且 DELETE 先于 POST 回执、
严格回执/身份匹配、核验后下一轮可入场、完整分页失败保留风险并可重试、部分/全部真实成交、
迟到 WS/REST 成交、下一快照核验失败、签名重用以及已核验后回执变化。

验证结果：相关执行/故障/最低金额/批次回归 136 passed（41.16 秒）；最终新增专项
25 passed（4.42 秒）。全量回归 `PYTHONPATH=src:tests .venv/bin/pytest -q --disable-warnings --maxfail=5`
1822 passed、2 skipped（161.54 秒）；全量运行收集后补充的回执变化测试在最终专项中通过。
涉及的 Python 文件通过 Ruff，`git diff --check` 通过。

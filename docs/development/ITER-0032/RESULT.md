# ITER-0032 — Interrupted Session Startup Recovery

关联任务：RDP-SIZE-001 后续实盘启动故障。日期：2026-10-15。
分支：`rdp-size-001`。开发基线：`f9cbbfebee00da30d72fa19d30c73bcb949642c4`。
代码修复及独立 Review 完成。整体验收状态：**BLOCKED**，原因是保留的全仓13项既有失败；不是继续因缺少 Session 收尾日志而一律拒绝恢复。未执行 MicroLive。

## 故障与授权

用户报告相邻两次启动均在 M1 SESSION_READY 后退出，原因是
`HISTORICAL_SM_RISK_REQUIRES_MANUAL_REVIEW`，新 Session 均没有订单。
用户说明上一场被 Ctrl+C 打断，要求不能仅因缺失最终报告永久阻断后续市场。

实际旧 Session `4b13c107f0474103b86b07b5422e6f96`，市场
`btc-updown-5m-1792040700`，已有两笔 durable ACK，最后没有 `sm_run_result`。
旧实现要求完整 Session 分组，EOF 时未闭组直接失败；不是 TargetDelta=1 的配置校验失败。
旧 YES 订单曾观察到 MATCHED，不能把缺失最终日志推断成零成交、安全退出或已结算。

项目及父目录没有适用 AGENTS.md，`docs/development/ITERATION_INDEX.md` 不存在；
沿用已有数字迭代、Architect/Worker/独立 Reviewer 和既有离线隔离测试机制。
已读当前实际实现、冻结边界、历史风险/资金启动、运行恢复 SOP 和 ITER-0031 结果。
历史 SOP 与当前代码冲突处仍以当前 T120/300、ROUND_HALF_UP 状态判断为准。

## 最小实现

只在新 Session 开启前增加历史尾组恢复核验；不恢复旧交易会话或其订单所有权。

1. 原严格历史解析器继续校验 JSON、顺序、账户、市场过期、intent/ACK/result 和既有 Merge 证据。
   仅完整 JSON 行但缺最终 `sm_run_result` 的尾组可进入候选核验。ACK 后冗余 result 未落盘也可核验。
2. 利用现有 Gateway/SDK 官方只读查询：全账户分页订单、已知旧订单详情、旧市场完整成交。
   订单必须已经达到既有终态条件；身份、价格、数量、成交关联和此前已观察成交均须一致。
   没有 ACK 的头部或明确拒单尾组使用既有 Gamma 接口核验旧市场，兼容 closed=true/false。
3. 恢复查询中新发现的 MATCHED/MINED/RETRYING 原始成交进入既有 AccountRiskPolicy.pending，
   重新执行原 `_account` 的市场、时间、角色、归属、Token、成本、承诺检查。
   后续空页和进程重启仍保留原始证据，再次经过同一策略检查。
   CLI 沿用既有“官方启动余额、旧承诺由操作者管理”口径；library 保持保守预留口径。
4. 所有执行事件和 window/close 中嵌套 Placement 必须绑定此前 durable 记录。
   不能通过无 ACK 的孤立成交、撤单、未知 POST、非零库存或资金证据绕过检查。
5. 通过核验后只追加 `sm_session_interrupted_reconciled`，包含原始官方证据和此前日志的 SHA-256。
   旧 Session 仍为 INCOMPLETE/BLOCKED；不伪造 `sm_run_result`、安全退出、CONFIRMED 或 PnL。
   再次读取时验证摘要和证明；新 Session 使用新 ID 并重新走原 M1/M2。
6. 已完成 Merge 的数量仅从原 CONFIRMED 结果投射，继续由原 `validate_merge_history` 核验
   intent/submission、receipt、Token、现金及成本。未知/不完整 Merge 仍阻断；恢复不会执行 Merge。

旧订单 ID 只临时允许 GET，不进入 known_ids、不授予 DELETE、不取消旧单、不重发旧订单。
未改 Gateway、WS、外部接口、Sizing、Pricing、Timing、状态机、M2窗口、资金 Gate、Merge执行或结算。
生产代码差异仅为 `rdp_sm_execution.py` 的历史读取/启动接线及新增 `rdp_sm_restart.py`。

不能自动放行：未过期市场、损坏/截断的 JSON 行、未知 POST 无 ACK、LIVE 订单、分页缺失、
身份/成交冲突、不完整 Merge、无法验证的账户风险。这些是缺少风险证明，不是缺最终报告本身。

## 验证

新增 `tests/test_rdp_sm_restart.py` **84项**：

- 无成交及 CONFIRMED/MATCHED/MINED/RETRYING、ACK后缺result、仅header、全拒单恢复。
- 旧字节保持、归档回放、篡改拒绝、归档后再次中断、账户页遗漏成交仍保留风险。
- 当前/未来市场、无ACK/UNKNOWN、损坏JSON/重复key、账户冲突、18项坏header拒绝。
- LIVE/订单查不到、身份/价格/数量/关联冲突、丢失已有成交、分页不完整拒绝。
- 恢复独有 pending 的时间/归属/角色/Gamma Token/时间冲突拒绝。
- 已完成 Merge 不重复执行；缺result和现金冲突拒绝。
- 孤立执行事件及嵌套UNKNOWN/ACK、无ACK非零库存/资金拒绝。

独立 Reviewer 最终实际运行：**233 passed，0 failed，0 network/DNS attempts**；
具体结论见 [REVIEW.md](REVIEW.md)。Worker全仓最终结果见原始
[full-tests.txt](evidence/full-tests.txt)，以及 [verification.json](evidence/verification.json)。
完整命令使用现有隔离 runner：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/ -q --tb=short
```

全仓最终 **1584 passed，13 failed，2 skipped，117.15秒**；新84项全部通过。
13项失败集合与 ITER-0031 完全相同：
1项 `.env` 保护冲突、10项既有 CLI 日志污染 JSON、2项旧源码冻结审计。
未删除/跳过失败测试，未放开网络或凭证读取，未扩大范围修改无关 CLI/历史研究审计。
Ruff、编译和 `git diff --check` 的最终结果亦记录于 verification.json。

实际历史只读核对：新解析器能识别同一旧 Session 的2个已知订单，原字节未改。
见 [actual-tail-check.json](evidence/actual-tail-check.json) 与
[actual-tail-tests.txt](evidence/actual-tail-tests.txt)。这只是解析验证；
没有查询真实账户、写入真实恢复 marker 或证明真实订单当前终态。

## 配置与人工验证

原 RDP-SIZE-001 配置默认仍为 base_size=7、target_delta=0、favorite_threshold=.70、max_order_size=15。
用户此前已授权将 `config/config.toml`、`livecfg/config.toml` 的 target_delta 改成1；
保留这两处工作区修改，本修复提交不包含它们，也未再次修改生产配置。

人工后续使用原入口 `uv run polymaker rdp-sm-run --config-dir livecfg`。
命令会真实交易，仅由操作者按原授权流程执行；本轮未运行。
下一次人工启动时先核验旧订单，证明通过后追加独立归档并开启新 Session；
若官方证据不足，会返回具体核验失败，不承诺无条件启动。
不得删除/清空历史日志来绕过检查。新 Session 沿用原 Timing 和 Favorite Sizing，
没有报价窗口时报告 Target=0 不代表配置被改回0。

COMMIT：由包含本报告的提交确定；最终输出列出精确hash。
MICRO_LIVE_EXECUTED：NO。MICRO_LIVE_READY：NO（全仓验收仍有既有阻塞，真实核验待人工启动）。

## 修改文件

- `src/polymaker/rdp_sm_execution.py`：复用严格header校验，历史尾组解析和启动恢复接线，GET权限范围。
- `src/polymaker/rdp_sm_restart.py`：只读核验、证据绑定、原风险策略复核及独立归档。
- `tests/test_rdp_sm_restart.py`：84项离线回归。
- 本目录 `RESULT.md`、`REVIEW.md` 及 `evidence/` 中四份原始结果/核对证据。

两处用户授权的 target_delta=1 配置保留为原有工作区修改，不包含在上述提交清单。

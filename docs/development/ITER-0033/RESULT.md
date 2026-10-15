# ITER-0033 — Remove Cross-Session Dependencies

日期：2026-10-15。关联：RDP-SIZE-001 及 ITER-0032 启动恢复方案。
分支：`rdp-size-001`。基线：`a1613f3cf5e5c1b108b6ed5b8919356ac6d5b614`。

人工新要求：**“每个session是独立单元，不依赖于其他session，检查关联性，把这个功能去掉！不需要Architect和Reviewer参与！”**

本轮仅由 Worker 实现、检查和测试，未调用 Architect 或 Reviewer。
该要求覆盖旧历史风险/恢复 SOP 中的跨 Session 启动约束。
功能移除已完成；原 RDP-SIZE-001 的“全仓全部测试通过”验收因13项既有失败仍为 BLOCKED。
没有真实账户查询、下单、撤单、Merge、RPC 或 MicroLive；未读取真实凭证。

## 关联检查与移除

| 原关联 | 当前处理 |
|---|---|
| 钱包级 Journal 必须完整收尾 | 删除历史解析与准入规则；不读取此前日志 |
| 启动逐笔查询旧订单终态/成交 | 删除 `rdp_sm_restart.py` 及启动调用 |
| 恢复 marker、前缀摘要、跨轮身份与成交证据回放 | 删除，不再生成 marker |
| 将旧 Session 的 UNKNOWN/库存/订单/待确认成本带到新 Session | 删除导入和历史资金预留 |
| 账户历史成交要求旧市场时间、Token、角色和正式结算证明 | 删除；仅查询当前市场成交，无历史 Gamma 查询 |
| 钱包级共享日志及文件锁 | 每 Session 独立文件，不以其他 Session 的文件或锁决定启动 |
| Shadow 固定读取钱包级日志 | 改为读取当前 Session 对应文件，保留已有 session-id 筛选 |

新文件名：`rdp_sm_<funder>_<session_id>.jsonl`，独占创建、逐条 flush/fsync。
同一 Session ID 的已有文件不可被覆盖/续写；不同 Session 可分别创建日志。
记录中的 session_id 必须属于本文件，序号从0开始，不从其他日志继承。
旧钱包级和既有 Session 文件保留原样，无删除、修改、迁移或补写。

报告中的 `historical_matched` 和两个 `historical_pending_cost_*` 字段仅为兼容消费者保留为空/0；
它们不再持有或参与历史判断、资金扣减。没有新增配置开关或隐藏恢复模式。

## 保留的当前安全检查

新 Session 仍执行 M1 的当前市场/行情、钱包、余额、allowance、账户订单和仓位检查。
当前市场未知归属的 pending 成交仍 Fail-Closed；自己的 pending 仍由原 M2 确认及关闭屏障处理。
同一 Session 内的风险观察在暂时漏页后继续保留；不同 Session 不传递此内存状态。
官方账户当前仍有订单或风险时，原有实时账户条件仍有效，与旧日志是否完整无关。

保持：Capital Gate、BUY-only/MAKER-only/Post-only、单侧成交敞口、UNKNOWN 本轮处理、
3秒窗口、撤单确认、Reconciliation、库存一致性、Merge执行与结算、Sizing/Favorite参数。
不修改 NORMAL T120 / REBALANCE T300、ROUND_HALF_UP 状态阈值或 Pricing。

## 测试迁移与验证

旧测试中“历史日志/证据必须阻止下一 Session”的断言已按新人工规则改为：
相同的缺收尾、损坏、未知订单、账户冲突、资金、Merge、Sizing报告等历史 fixture，
不会被新 Session 打开、导入或用来拒绝启动。
旧测试没有被标成 skip/xfail；本轮被移除恢复模块的84项旧专项由44项独立性专项替代。

`tests/test_rdp_sm_restart.py` 当前44项包括：

- 16类旧日志 × TargetDelta 0/1：旧文件一旦被打开就让测试失败；新 Session 正常完成。
- 旧订单404/429/500/timeout接口从未被调用；当前报价正常执行。
- 不同 Session 文件互不依赖，同 ID 不覆盖，跨 Session 写记录被拒绝。
- 路径穿越/非法ID拒绝。
- 真实生产流程上一轮UNKNOWN保持BLOCKED；下一次调用在当前账户检查通过后独立执行，
  不沿用旧 attempts、known_ids、确认库存或旧日志。

另有保留的 M1/M2/M3、资金、Partial Fill、Tick、Sizing、Merge、Shadow 路径回归。
测试只模拟外部 HTTP/SDK/RPC 边界，不连接真实服务；使用既有
`tests/rdp_sm_m0_offline.py`，清除继承环境、临时工作目录、拒绝网络/DNS及真实凭证访问。

全仓命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/ -q --tb=short
```

最终全仓 **1544 passed、13 failed、2 skipped，116.69秒**；
失败集合与 ITER-0032 完全一致，无新增失败。
相关专项 **277 passed、0 failed、0 skipped/deselected，19.47秒**。
两个最终进程均为 **0 network/DNS attempts，workspace credentials denied**。
Ruff、编译、Git空白检查及源码关联检查通过。
失败集合对照、源码关联检查及 lint/compile 收据见
[verification.json](evidence/verification.json)。原始结果：
[full-tests.txt](evidence/full-tests.txt)、[targeted-tests.txt](evidence/targeted-tests.txt)。
专项和全仓有重叠，不把通过数累加。

## 变更清单与交付

生产：

- `src/polymaker/rdp_sm_execution.py`：移除历史Journal准入、历史读取权限、旧承诺扣减；Session独立日志。
- `src/polymaker/rdp_sm_entry.py`：移除历史市场查证，仅保留当前市场pending与原启动资金口径。
- `src/polymaker/rdp_sm.py`：删除历史风险预留运算，保留报告兼容键为0。
- 删除 `src/polymaker/rdp_sm_restart.py`。
- `src/polymaker/risk_shadow_launch.py`：Shadow绑定当前Session文件。

测试：上述独立性专项，historical_risk/expiry、capital_restart、starting_balance、history_lookup、
entry、M2、Merge、Sizing、latest_quotes、inventory_wait、minimum_notional、postonly_response、
tick_recovery及Shadow路径断言按新的独立性规则更新。精确文件清单记录于本次 Git Commit。

文档：本报告、证据及当前基线说明追加人工新边界。
`config/config.toml` 与 `livecfg/config.toml` 中用户已授权的 target_delta=1 保持不动，
不纳入本提交；本轮没有修改生产配置。

原入口 `uv run polymaker rdp-sm-run --config-dir livecfg` 保持。
下次由操作者启动时直接按独立 Session 流程运行，不需修复或删除旧日志。
本轮未运行该交易命令，未自动启动下一市场。

ARCHITECT：NOT_USED（用户明确要求）。REVIEWER：NOT_USED（用户明确要求）。
MICRO_LIVE_EXECUTED：NO。COMMIT：包含本报告的提交，最终回复给出精确hash。

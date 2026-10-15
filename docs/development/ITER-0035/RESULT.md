# ITER-0035 — Session Capital Limit 25 USDC

日期：2026-10-15。关联：RDP-SIZE-001后续人工参数调整。
开发基线：`7cf92e73c397924e23de2d7b2c96e396092b3b03`，分支 `rdp-size-001`。
用户指令：先将每个Session资本限制从20调至25。

## 最小修改

生产代码仅修改 `src/polymaker/rdp_sm.py` 的共享常量：
`TEST_CAPITAL_LIMIT_USDC = 20.0` → `25.0`。

复用既有引用链，未新增配置来源或资金门槛实现：

- M1 `PreflightResult`与基本预算的25 USDC上限。
- M2 `CAPITAL_LIMIT`及提交前确认库存成本、现有承诺和新增订单承诺的合计上限。
- Session记录的 `test_capital_limit_usdc`。
- 启动可用预算标签与最大资金利用率的25 USDC分母。

官方余额、allowance和实际可用资金检查继续约束预算。
新启动进程读取25，未重启正在运行的交易进程。

没有修改BaseSize、TargetDelta、FavoriteThreshold、订单数量上限15股、最低BUY金额1 USDC、
Timing、Pricing、状态阈值、执行逻辑、Merge或Session独立性。
之前 `MINIMUM_ORDER_EXCEEDS_MAXIMUM` 的异常分类缺口本轮未处理；
提高资本限制不会消除最低金额与最大股数的冲突。
既有 `config/config.toml`、`livecfg/config.toml` 的TargetDelta=1工作区改动未修改或纳入提交。

## 离线验证

修改前：相同八个相关测试文件 **255 passed，25.77秒**。
修改后：**257 passed，26.82秒**。复用既有M0隔离执行器，
**0 network/DNS attempts，workspace credentials denied**。

覆盖：M1预算、Type2账户、官方余额不足、当前资金约束、资本耗尽与Merge后释放、
Session资金报告、行情及读取恢复、撤单和Reconciliation。
既有单单20 USDC允许测试扩展为20允许、25允许、25.005拒绝三个边界用例；
该fixture明确使用历史max_order_size=1000以隔离资本门槛，不改变生产数量上限15。
其余修改仅同步原20 USDC预期、资金耗尽前允许的五组报价和相应25对Merge结果。

运行：

```bash
.venv/bin/python tests/rdp_sm_m0_offline.py \
  tests/test_rdp_sm_m1.py tests/test_rdp_sm_m2_execution.py \
  tests/test_rdp_sm_entry.py tests/test_rdp_sm_starting_balance.py \
  tests/test_rdp_sm_type2_account.py tests/test_rdp_sm_historical_risk.py \
  tests/test_rdp_sm_tick_recovery.py tests/test_rdp_sm_recovery.py -q
```

原始结果：[baseline-tests.txt](evidence/baseline-tests.txt)、[tests.txt](evidence/tests.txt)。
Ruff及Git空白检查通过。没有执行全仓测试，不声称既有全仓13项失败已解决。
按用户现有指令未调用Architect/Reviewer；未执行真实交易或MicroLive。

STATUS: CODE_READY

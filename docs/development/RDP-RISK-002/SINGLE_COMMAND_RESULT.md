# 单命令 Risk Shadow 集成结果

2026-10-14。根据本轮人工指令，将 `rdp-sm-run --shadow` 接入现有独立 Observer。
默认仍关闭；不修改 NORMAL/REBALANCE、执行 Gateway、库存账本、RiskJournal、Merge 或停止规则。
保留前一阶段 RESULT.md 与真实纯行情观察作为历史记录。

## 使用

```bash
uv run polymaker rdp-sm-run --shadow
# 沿用原有配置目录：
uv run polymaker rdp-sm-run --config-dir livecfg --shadow
```

只需该命令，无需手动选择另一个市场、Session ID 或 Journal。
这是原有真实交易入口加只读 Shadow，不是 paper/dry-run。
未加 `--shadow` 时保持原调用路径。

市场发现后，将准确的 slug、condition、YES/NO token、真实 Session ID 和当前配置的
账户 Journal 路径交给有界启动工作队列。后台线程只负责创建独立子进程和日志文件。
子进程只收到公共身份/路径，环境只保留 PATH/LANG，不继承私钥、API Secret 等。
Popen 使用 start_new_session/close_fds，交易关闭不会等待 Observer 市场观察结束。

Observer 二次验证官方元数据与父进程身份一致，并等待 Journal 创建；按20ms周期只读过滤本次
Session，早期未观察槽位 MISSED，不伪造完整覆盖。假如实际交易在T300之前退出，Observer
仍观察同一市场到T300。没有向真实引擎返回指令，没有修改原 Fail-Closed 语义。

启动提示包含 market、session、PID 和日志路径：
`research_outputs/rdp_risk_shadow/launch_logs/<session_id>.log`。
数据仍在 `research_outputs/rdp_risk_shadow/<market_slug>/<observer_uuid>/`，
每条 session_id 与真实运行一致。启动失败记录 SHADOW_ERROR，不影响原交易执行。

## 同一决策的库存关联

避免上一窗口刚确认库存、下一 REBALANCE 已开始，但1秒行情槽位还未刷新导致遗漏：
只从已读到且源时间不晚于实际决策的 sm_window_result 复制权威库存（已经包含验证 Merge），
并检查与实际决策 Delta 相同。没有从成交自行加减库存。
行情与波动率输入仍明确标注此前至多1秒的研究槽位；未知或陈旧数据不生成有效候选。
该次关联写成独立 record_type=DECISION 风险记录，risk_snapshot_id 对应 decision_id；
原市场/风险槽位保持原样，后来的确认成交不会反向修改任何历史记录。
动态 Tick 若在该决策前已经收到则使用更新值；只看到决策后的 Tick 时返回 UNKNOWN。

每秒风险在尚未得到可靠权威库存时仍为 UNKNOWN，不能据此声称完整持仓路径覆盖。
启动提示仅表示子进程启动，实际数据质量须检查子进程日志与 market_summary。

## 最小改动范围

既有源文件仅改：

- src/polymaker/cli.py：增加 --shadow，关闭时保持原调用。
- src/polymaker/rdp_sm_entry.py：发现市场后可选启动独立 Observer，并隔离启动错误。

Shadow 研究模块改动：risk_shadow_launch.py（新增）、risk_shadow.py、risk_shadow_model.py。
测试：test_risk_shadow_cli.py（新增13项参数化测试）及既有本轮 test_risk_shadow.py 的保护范围。
文档：README、字段字典、本报告及 evidence/single-command-*。
原交易循环、执行、风险 Journal、Merge 和所有批准基线的既有测试逐字节未改。
入口剥离新增可选启动分支后与批准基线 AST 完全一致。

## 验证

- Shadow 与单命令测试：**57 passed, 0 failed**。
- 全部相关回归：**1016 passed, 0 failed**。
- 实际 CLI help 显示 --shadow。
- Ruff（包含改动的 CLI/入口）、compileall、git diff --check 通过。
- 所有测试使用现有 offline runner：**0 network/DNS attempts；workspace credentials denied**。

生产路径模拟分别运行关闭、开启、启动异常三种情况。实际 SDK/Gateway/执行循环依旧经过
MockTransport；三组均保持 NORMAL→REBALANCE→NORMAL、同样5笔报价/数量、同样撤单集合、
同样60/63/66秒提交节奏及3秒 Fill Window、同样验证 Merge 数量和最终关闭屏障。
另测子进程隔离、不传凭证、自动身份一致、等待 Journal、Session 去混淆、决策风险关联、
动态 Tick 的过去/未来时间边界以及旧风险记录不被回填。

未启动真实 rdp-sm-run、未提交真实订单。本次组合入口实盘验证尚未执行；上一阶段的
一个真实市场 OBSERVATION_ONLY 数据不能算作该入口的实盘交易验证。

复现：

```bash
.venv/bin/python tests/rdp_sm_m0_offline.py tests/test_risk_shadow_cli.py tests/test_risk_shadow.py -q
```

完整回归输出见 evidence/single-command-regression.txt；Shadow 输出见
 evidence/single-command-tests.txt。HEAD 仍为人工批准 a728c4d；未提交或改动历史基线。

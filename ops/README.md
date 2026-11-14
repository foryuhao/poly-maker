# M1 余额停机保护

`polymaker-m1-watch.timer` 每五分钟巡检时，使用现有账户身份读取一次 CLOB 现金余额。
仅访问现有凭据派生和 collateral 余额两个 GET 接口，不读取成交、仓位或 PnL，不发试单。
余额保护配置位于 `config/m1-live/balance-guard.toml`，默认开启、阈值 **20 USDC**：

```toml
enabled = true
threshold_usdc = "20"
```

每轮巡检重新加载，修改后无需重启实盘或基线服务。缺少文件或字段使用默认值；
阈值必须是有限正数，开关必须是 TOML 布尔值。未知字段、非法值或无法读取的配置会
回退到默认开启、20 USDC，并在巡检结果中报告 `INVALID_USING_DEFAULTS`。

保护开启且余额严格小于配置阈值时，先原子写入
`research_outputs/m1_live_watch/balance-stop.json`，再停止 `polymaker-m1-live.service`，
并唤醒原聊天报告余额及停机结果、等待用户确认。恰好等于阈值不触发；读取失败只报告不可用，
不会当成 0。原挂单、全部风险记录和基线服务保留。

`enabled = false` 只关闭新的低余额停机判定，仍读取和报告现金余额。
停机标记持续生效，关闭保护、调整阈值或余额恢复都不会解除它，也不会自动恢复交易。
该文件仅由实盘巡检读取，不改变基线模拟资金、场次配置或策略规则。
安装 `ops/polymaker-m1-live-balance-stop.conf` 到
`~/.config/systemd/user/polymaker-m1-live.service.d/40-balance-stop.conf`，
并执行 `systemctl --user daemon-reload`，使后续服务启动也受该标记约束；无需重启运行中的实盘。
只有用户明确确认恢复后，才可归档并解除标记、恢复服务。普通巡检或 BUG 修复不得解除。
最新余额和停止请求结果在 `balance-guard.json` 和巡检 `latest.json` 的 `balance_guard` 中。

# M1 日志定时清理

`poly-maker-log-retention.timer` 每 5 分钟检查一次，开机后补跑漏掉的检查。
沿用 `ca_log_retention.py` 入口名称，当前策略只清理最后写入时间严格早于当前时间减 24 小时的
**已结束 M1 原始日志**；正常调度下最多延迟约 5 分钟。

| 目录 | 清理规则 |
| --- | --- |
| `journal`、`livecfg/journal` | 头部明确标注原生 `m1_live_v1`，对应账本会话和市场身份一致、已标记完成，且没有 UNKNOWN、RESERVED、PREPARED 或保留的不确定提交风险的过期流水 |
| `research_outputs/m1_virtual/sessions/<market>-<run>` | 虚拟账本确认场次已结束、目录仅有过期 `signals.jsonl` 的会话 |

M1 300 场研究（整个 `research_outputs/m1_isolated`）、所有 SQLite 账本、恢复 JSON、
虚拟指令、看板数据、配置和备份均保留；其他研究目录不在清理范围内。
历史会话缺少可验证账本或原生 M1 头部时保留。CA/BA 流水不属于本任务的删除范围。
原生 M1 的 `state_path` 恢复标记按 M1 格式验证，不按旧 CA 会话格式报错；
旧格式恢复标记明确引用的日志继续保留。该任务不修改交易逻辑，也不重启交易或看板进程。

复用 `log_retention.py` 的文件身份、打开文件、恢复引用和删除审计保护。
删除前再次核对文件身份、修改时间、恢复引用及账本状态，并检查所有进程的打开文件。
不跟随符号链接，不清理硬链接文件；恢复状态无法解析时在删除前退出。
只以只读方式查询本地会话元数据和提交风险状态，不连接平台、不查询成交或仓位。
脚本默认只预览，带 `--apply` 才删除。每轮记录删除意图、实际完成记录和汇总；审计自身仅保留最近 288 轮。

## 安装与查看

安装器按自身位置识别当前项目路径（本机为 `/home/ubuntu/polymaker`），
将脚本复制至 root 所有的 `/usr/local/lib/poly-maker`，渲染并验证 systemd 单元，
立即清理一次并启用定时任务：

```bash
sudo bash ops/install_log_retention.sh
systemctl list-timers poly-maker-log-retention.timer
sudo systemctl status poly-maker-log-retention.service
sudo journalctl -u poly-maker-log-retention.service -n 10 --no-pager
sudo cat /home/ubuntu/polymaker/logs/retention/latest.json
```

服务使用系统 Python 标准库，不依赖交易虚拟环境。以 root 运行仅用于检查全部进程的打开文件；
systemd 将写权限限制在上述原始日志和清理审计目录，禁用网络访问。
使用 nice 19、I/O idle、10% CPU 配额与 128 MiB 内存限制。

手动预览或执行：

```bash
sudo /usr/bin/python3 -I -B ops/ca_log_retention.py --data-root /home/ubuntu/polymaker
sudo /usr/bin/python3 -I -B ops/ca_log_retention.py --data-root /home/ubuntu/polymaker --apply
```

停止定时任务（正在运行的本轮不受影响）：

```bash
sudo systemctl disable --now poly-maker-log-retention.timer
```

测试：`python -m pytest -q tests/test_log_retention.py`。
独立看板和五分钟归档仍由 `bash ops/install_dashboard.sh` 管理，详见
[CLI Dashboard v1](../docs/development/CLI_DASHBOARD_V1.md)。

## M1 虚拟后台验收

`polymaker-m1-virtual.service` 是公共行情专用服务模板，安装时使用用户服务名称
`polymaker-m1.service`。当前 `uv run polymaker m1-run` 只输出虚拟指令，真实下单入口已关闭。
不读取钱包密钥或账户状态，不签名、不 POST、不撤单、不 Merge；HTTP 层只允许官方公共 GET。
已有实盘账本和账户未完成标记保留。

```bash
cp ops/polymaker-m1-virtual.service ~/.config/systemd/user/polymaker-m1.service
systemctl --user daemon-reload
systemctl --user start polymaker-m1.service
journalctl --user -u polymaker-m1.service -n 15 --no-pager
```

服务异常退出后 5 秒自动重启；网络异常在进程内重建公共客户端并退避重试，公共 WS 复用原有重连机制。
`--exercise-failover` 会将读超时、HTTP 400 和公共 WS 断开各注入一次，状态持久化，重启后不重复。
演练日志有 CONTROLLED 标记，与自然发生的故障区分；不连接任何账户接口。

虚拟指令记录 YES／NO、RF 原档位价格、合法股数、预算与机会 ID。窗口 T15–T240，验证价格
0.01–0.19，单笔最多 1 USDC、单场最多 20 笔。虚拟成功代表指令已写入持久账本，
模拟 PnL 假设按指令价足额成交并持有至官方胜负确认；没有 Maker 排队或真实成交证明。

输出：

- `research_outputs/m1_virtual/state.sqlite3`：完整指令、信号、故障、官方结算证据与恢复元数据。
- `research_outputs/m1_virtual/latest.json`：最新状态、连续计数及最近 48 场的报告；累计数按完整账本计算。
- `research_outputs/m1_virtual/report.md`：逐场信号、虚拟指令和结算时间线；待结算显示待确认。
- `research_outputs/m1_virtual/sessions/`：有界异步写入的信号与诊断日志；拒绝帧诊断限频，原始拒绝数量保留在计数器中。

首次晚加入、处理缺口、故障与修复部署不得冒充完整正常场次。连续至少 12 场完整报告且经过用户
确认，才结束本阶段；服务不会自行切回实盘。停止、重启和代码变动保留异常历史并重新累计。
完整历史仍在 SQLite；报告整理在独立、低优先级的只读进程进行，避免 Python 线程共享执行资源而
拖慢行情消费。时间线使用按场次和时间的索引。信号输入阶段暂停自动循环内存回收，在订阅前及
T240 关闭公共连接后集中回收；引用计数仍正常释放对象，退出路径恢复原回收开关。

离线验证：`PYTHONPATH=tests:src .venv/bin/python -m pytest -q tests/test_m1_virtual.py`。
Dashboard 改造按用户要求延后，不作为本阶段的运行依据。

## M4 Taker 首笔成交试运行

人工启动：`bash /home/ubuntu/polymaker/ops/m4_first_taker_market.sh`。
默认连续运行完整市场，一次只接收一个信号。首个实际成交信号继续按剩余资金补到
计划含费接近 5 USDC；保持锚定价加 1 tick、每场 10 USDC 风险上限、最小下单量和
T240 限制。余款不足最小量或到 T240 就停止补单，完成本场收尾后退出，不开下一场。
ACK 不算成交；明确零成交可按原退避规则重试。场末仍有 UNKNOWN／待确认成交、运行
异常或保护停机时，停止连续开场并保留所有订单记录。脚本不解除任何停机标记。

每次输出目录为 `research_outputs/m4_live/manual-runs/first-taker-*`，内含 `run.log`
和原子保存的 `campaign.json`。中断后恢复同一次试运行使用
`bash /home/ubuntu/polymaker/ops/m4_first_taker_market.sh --resume /绝对路径/原运行目录`；
已完成首笔成交的试运行不会再次下单，仍未查清的场末订单也不会自动跨场重买。
恢复只使用本执行器所属 Taker 订单；历史未跟踪成交的 Maker ACK 保持原样。

`--once` 保留单场模式；`--check` 只做离线配置检查，不启动交易。
此入口不控制基线服务，也不恢复每小时巡检。

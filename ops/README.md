# 交易日志自动清理

独立 CLI 看板和每五分钟市场归档使用另外的用户级服务，安装命令为
`bash ops/install_dashboard.sh`。它不会修改交易服务或本页的清理服务。
运行和核算说明见 [CLI Dashboard v1](../docs/development/CLI_DASHBOARD_V1.md)。

`poly-maker-log-retention.timer` 每 5 分钟运行一次，开机后补跑漏掉的检查。每次计算当前时间减去 24 小时的界限，仅删除最后写入时间严格早于界限的日志。正常调度下，过期日志在下次检查时清理，最多约延迟 5 分钟。

清理范围固定为项目 `/home/ubuntu/poly-maker` 内的交易日志，不扫描其他研究数据。数据目录均为项目内的真实目录，不再链接到并列目录：

| 目录 | 清理规则 |
| --- | --- |
| `journal`、`livecfg/journal` | 过期的 `rdp_sm_*.jsonl`，跳过市场恢复标记和账户状态引用的会话 |
| `logs`、`livecfg/logs` | 已关闭的已知应用日志文件；`runs` 中整组过期的日志与报告 |
| `logs/settlement`、`livecfg/logs/settlement` | 仅清理 `finished_at` 已填且无不确定交易结果的过期结算日志与报告 |
| `research_outputs/periodic_sync` | 有 `summary.json`、所有文件均过期且目录只含已知日志文件的已结束会话 |

所有恢复 JSON、账户锁、`pending.json`、未完成或结果不确定的结算记录、恢复标记引用的流水均保留，不因超过 24 小时而删除。市场标记在正常退出后也可能继续存在，因此这类恢复流水可能长期保留。恢复状态损坏或暂时无法解析时，本轮在删除前退出，等待下次检查；不推断恢复记录已无用途。

清理器跳过打开中的日志，删除前重新核对文件身份、修改时间与恢复引用。会话目录里有近期文件、未知文件、子目录或符号链接时不清理整组。目录描述符与 `O_NOFOLLOW` 限制文件操作范围；硬链接文件保留。

**整个 `research_outputs/rdp_risk_shadow` 目录永久排除**。清理程序不进入该目录；systemd 服务还将该目录设为只读。此前数据目录已被用户删除，本次只重建目录，不恢复历史文件。其他研究产物、配置、数据库和项目内的 `backups/` 均不属于清理范围。

## 安装与查看

```bash
sudo bash ops/install_log_retention.sh
systemctl list-timers poly-maker-log-retention.timer
sudo systemctl status poly-maker-log-retention.service
sudo journalctl -u poly-maker-log-retention.service -n 10 --no-pager
sudo cat /home/ubuntu/poly-maker/logs/retention/latest.json
```

安装器将代码和服务配置复制到 root 所有的 `/usr/local/lib/poly-maker` 与 `/etc/systemd/system`，验证配置后立即清理一次并启用定时任务。交易进程和策略配置无需重启。服务使用系统 Python 标准库，不依赖交易虚拟环境。以 root 运行是为了检查所有进程的打开文件；systemd 限制其写权限为列出的日志目录和审计目录。

手动只预览（默认不删除）：

```bash
sudo /usr/bin/python3 -I -B /usr/local/lib/poly-maker/log_retention.py
```

审计保存在项目内的 `/home/ubuntu/poly-maker/logs/retention`：`latest.json` 是最近一次结果；`run-*.jsonl` 记录每个文件的删除意图、完成记录及本轮汇总，最多保留 288 轮，避免审计自身无限增长。旧 `/var/lib/poly-maker-log-retention` 不再写入。systemd 日志记录每轮摘要；错误会使本次服务显示失败。

停止定时清理：

```bash
sudo systemctl disable --now poly-maker-log-retention.timer
```

若清理服务当时正在运行，停止定时器不会中断该轮。更新脚本后重新运行安装器即可部署。

验证：`python -m pytest -q tests/test_log_retention.py`。

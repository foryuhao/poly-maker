# 项目内数据目录与账户只读核对

2026-10-21按用户要求，历史数据暂不恢复，修复目录并把以后数据全部保存在项目内；本次没有启动交易。

## 存储布局

以下均为 `/home/ubuntu/poly-maker` 内的真实目录，没有指向并列目录的符号链接：

| 目录 | 用途 |
|---|---|
| `journal/` | 默认运行交易流水、会话及市场恢复状态 |
| `logs/` | 默认运行日志、结算日志；`logs/retention/`保存清理审计 |
| `livecfg/journal/`、`livecfg/logs/` | livecfg运行流水及日志 |
| `research_outputs/` | 周期快照及研究输出 |
| `research_data/`、`research_audits/` | 研究输入及只读核对证据 |
| `backups/` | 后续本地备份 |

两份配置原本使用项目内相对路径，保留原配置及Pilot-x100交易源码。请从项目根目录运行命令。`.gitignore`已排除运行数据和`backups/`。

原7个指向`/home/ubuntu/poly-maker-data`的失效链接已替换为真实目录；没有重新创建并列的数据或备份目录。已有SQLite、凭据及项目内历史复盘未修改，没有补造已删除的市场恢复标记。

清理器默认data root和已部署systemd服务均已改为项目根目录，清理审计改存`logs/retention/`；24小时保留和恢复证据保护规则保持。安装后服务执行成功，候选/删除文件均为0；timer继续每5分钟执行。服务仅处理已知日志范围，不扫描项目源码、研究审计或备份。旧`/var/lib/poly-maker-log-retention`不再写入。

本次变更前的运维配置备份保存在`backups/data_layout_20261021_225710/`。

## 账户核对

证据目录：`research_audits/account_check_20261021_225813/`。

- 使用现有GET-only账户读取器，认证只派生现有凭证；没有创建API key。POST/DELETE交易路径被该客户端禁用。
- 2026-10-21约22:58，前后两次完整分页的官方未结订单结果均为0。CLOB抵押币余额为29.836614 USDC。
- v2仓位与显式`sizeThreshold=0`的旧版仓位接口均返回相同15个token，包含小残仓；补查`includeArchived=true`没有发现额外仓位。
- 23:00在Polygon区块95926295，用只读`eth_call`核对15个持有token及其15个对侧token，以及各市场兑付分子/分母。15个仓位实际合计15.954592份，全部为已结算失败方向，兑付分子均为0，对侧链上余额均为0。官方市场结算结果一致，未发现未结算风险仓位。
- 这些失败方向token仍存在于钱包，不等于钱包没有任何token；`redeemable=true`也不代表这些token能兑付现金，实际兑付权益为0。本次未赎回或处理这些token。
- 补查最近24小时406笔成交，全部为CONFIRMED，未发现非终态成交；随后再次完整读取未结订单，仍为0。

`account_snapshot.json`保存完整读取及分页证据；`position_verification.json`保存链上余额、兑付和官方市场结果；`pending_trade_check.json`保存补充的最近24小时成交终态和最终挂单核对。余额和挂单均为读取时点的结果。

## 核对边界

完成目录真实路径、配置解析、清理服务实际运行、备份忽略规则及源码仍与Pilot-x100一致的检查。本次没有运行交易入口、提交/撤销订单、合并/赎回或更新本地持仓账本，没有用旧SQLite数据替代官方账户事实。没有运行单元测试。

# M23 → M12 顺序基准测试

用户于2026-11-17确认先M23、后M12，各连续24小时。每次预先固定288个连续五分钟市场，
不按信号数、成交或PnL挑选市场，不沿用历史频率筛选阶段的准入门槛。

**最新人工指令覆盖原顺序自动接续：M23结束后停止，M12必须等待用户另行启动。**
已将正在运行的M23进程移至独立scope并交给 `polymaker-m23-baseline-guard.service`，
采集PID、固定市场列表和账本均不变。旧自动顺序控制器已终止，不会启动M12。
独立保护每5秒检查空间，剩余≤10GiB时只停止基准测试；保护进程故障退出也会停止其M23 scope。
不操作M4实盘或其巡检，不自动清理历史数据。最新控制以本目录control.json和state.json为准。

选定版本为 `M23_FIRST_SELL_WHILE_VALID` 与 `M12_VALIDITY_OVERLAP`，
均验证选定SOP全文、参数和实现的哈希。历史默认版本不可替代。

## 执行与隔离

- 仅公共行情和官方结果，BUY-only模拟Maker；不加载账户凭证，无真实订单通道。
- 固定原锚价；同token直接SELL穿过POST时冻结的1tick，按剩余数量及未使用打印量部分成交。
- 信号失效、原期限或T240请求模拟撤单；模拟撤单到达前的已知成交保留并分类。
- POST/ACK/撤单延迟分别100/200/200ms；ACK不作为成交。
- 同批POST另记立即全量成交对照；只在官方确认后计结算PnL，未知余量单列。
- 每笔5、每场10 USDC，每方向一次POST；0.1份步长向下取整，保留平台最小量及tick限制。
- 全部合法价格，不继承M6价格带、400ms额外准入或T240以前一直等待触价的旧成交模型。

沿用现有独立公共采集、持久审计账本、连续换场、资源保护及官方结算底座，
复用已有SOP检测器和成交模拟器。M6保持停止，历史目录不变；M4实盘与巡检不在本任务操作范围。
通用共享交易模块未作行为更改；基准0.1份定量仅由本次冻结执行配置启用。

## 入口及运行记录

入口 `ops/m1223_baseline.py`，计划 `config/m1223-baseline-plan.json`。
独立根目录 `research_outputs/m1223_baseline/`：

- `runtime/<hash>/`：从已验证保留底座、当前SOP与基准入口生成的冻结代码。
- `plan.json`、`state.json`、`latest.json`、`control.json`：顺序及当前运行状态。
- `cohorts/`：每阶段启动前依据公共时钟保存的固定市场列表。
- `runs/m23-<epoch>/`、`runs/m12-<epoch>/`：分别独立的数据、SQLite、gzip原始输入和报告。
- `validation.json`、`preflight*.json`、`launch.json`：离线验证、资源准入及服务启动证据。

原顺序服务 `polymaker-m1223-baseline.service` 已停止。当前由用户级
`polymaker-m23-baseline-guard.service` 保护原M23进程，`Restart=no`，用flock排他锁防重。
M23整个固定市场列表观察完、账本正常关闭后即停止；M12保持 `HELD_BY_USER`，不自动接续。
将来启动M12必须重新取得用户指令并检查完整磁盘预算；资源不足会明确停止，保留已完成数据。
异常退出、人工停止或已开始阶段缺失完成记录，均停止并要求复核，不能把同一批重新启动。
不因为没有信号或没有成交而延长或重选测试。

离线验证必须禁用IPv4/IPv6连接并匹配冻结运行哈希；启动前执行只读资源检查。
启动后检查首个完整市场和下一场切换，分别报告信号、POST、ACK、部分成交、撤单及官方结算。

停止本任务可停止 `polymaker-m23-baseline-guard.service`，其退出钩子会向独立M23 scope发送SIGINT，
并把本目录 `control.json` 的 `enabled` 改为false。
不得连带操作M4服务、巡检或账户。计划完成后不自动恢复M6，也不继续第三阶段。

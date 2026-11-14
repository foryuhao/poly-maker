# M1–M4 连续 24 小时模拟

更新：2026-11-08，北京时间。此入口仅使用公共行情和模拟订单。

## 当前公共行情运行

**队列扫描与报告汇总优化后重启（2026-11-08）**：当前运行 [24h-20261108T154229-66fde6](/home/ubuntu/polymaker/research_outputs/m1234_sim/forward/24h-20261108T154229-66fde6)，服务 `polymaker-m1234-sim-24h-20261108t154229-66fde6.service`。继续采用 M1 两个 profile 无策略价格区间过滤、每笔 5 USDC 的配置；两个既有排队情景及 M234 信号规则不变。前一轮 15:23 运行在第二场触发采集队列溢出；失败事实和原始行情保留，没有把两次运行拼成连续 24 小时。修复将每次水位计算的全队列扫描改为维护 FIFO 头部和计数，并由 SQLite 汇总诊断日志，减少报告线程的 Python 解析；队列容量仍为 2048。70 项采集/策略回归与 8 项报告/结算检查通过，保存市场的报告逐字段一致。真实全天验证仍在进行，聊天巡检保持关闭。见[修复证据](/home/ubuntu/polymaker/research_outputs/analysis/m1234_report_queue_fix_20261108/incident.json)。以下保留上一轮及更早历史。

**M1 原始价格范围、统一预算（2026-11-08 15:23:44，北京时间，已失败）**：当时目录 [24h-20261108T152344-5141d4](/home/ubuntu/polymaker/research_outputs/m1234_sim/forward/24h-20261108T152344-5141d4)，服务 `polymaker-m1234-sim-24h-20261108t152344-5141d4.service`，计划从 15:25 起重新观察 288 场。M1 两个 profile 均取消策略价格区间过滤，每笔预算改为与 M234 一致的 5 USDC，数量向下取整到 0.01 份；市场最小数量、价格刻度、maker 条件、信号时效、20 USDC 独立账户上限及排队撮合保持原规则。两个 M1 profile 现在参数相同，仍各自保留独立模拟账本。旧实盘/虚拟 M1 的配置及默认 1 USDC 算法未改。

11:46 的上一轮已为本次参数变更人工停止，终态 `MANUAL_STOP`，没有完成 24 小时。此前带价格限制的 M1 样本已按用户要求废弃，不再混入后续 M1 统计；M234 共用行情和不可拆分的审计文件保留，详见 [M1 数据使用范围](/home/ubuntu/polymaker/research_outputs/m1234_sim/m1-data-policy.json)。本轮输出上限 16 GiB、保留 4 GiB 空间；调整基于上一轮约 13.6 GiB/天的采集速率估算，不能保证未来全天流量相同。对话巡检保持禁用，启动器切换运行时保留此偏好。以下为历史运行记录。

**结算空响应修复后重启（2026-11-08 11:46:59，北京时间，已人工停止）**：当时目录 [24h-20261108T114659-d8ab11](/home/ubuntu/polymaker/research_outputs/m1234_sim/forward/24h-20261108T114659-d8ab11)，服务 `polymaker-m1234-sim-24h-20261108t114659-d8ab11.service`，计划 11:50 起重新观察 288 场。公共结算接口返回 JSON null 或其他非对象时保留原始响应、记录 `PUBLIC_RESOLUTION_RESPONSE_NOT_OBJECT`，保持待结算并继续重试；明确的市场身份冲突仍按原规则失败。原 09:10 起运行因该异常终止，最终为 28 个有效完整窗口、2 个无效市场，失败事实与原始行情全部保留，不能计为全天完成。当前输出上限为 19 GiB，另保留 4 GiB 磁盘余量；策略及撮合规则未变。见[修复证据](/home/ubuntu/polymaker/research_outputs/analysis/m1234_resolution_fix_20261108)。以下为前次运行历史。

**队列修复后重启（2026-11-08 09:07:22，北京时间）**：新运行目录为 [24h-20261108T090722-4f03ad](/home/ubuntu/polymaker/research_outputs/m1234_sim/forward/24h-20261108T090722-4f03ad)，服务为 `polymaker-m1234-sim-24h-20261108t090722-4f03ad.service`。计划从 11 月 8 日 09:10 开始观察 288 场。五分钟巡检已启用；实时状态以 [monitor/latest.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/monitor/latest.json) 为准。以下旧运行记录保留作故障证据。

首个 09:10–09:15 市场完整结束，处理 112,643 帧，最大积压 87 帧，最大处理延迟 260.67 ms，市场终态 VALID；已进入第二场。09:10、09:15 两次定时巡检执行成功。见[现场检查](/home/ubuntu/polymaker/research_outputs/analysis/m1234_queue_fix_20261108/live-check.json)及[修复汇总](/home/ubuntu/polymaker/research_outputs/analysis/m1234_queue_fix_20261108/result.json)。此记录不替代全天运行终态。

**状态更新（2026-11-08 早间检查）：本次运行已失败停止，未完成 24 小时。** 00:52:26 触发 `SIM_CAPTURE_QUEUE_OVERFLOW`，服务于 00:52:29 退出；完成 00:35–00:50 的三个窗口，第 4 个窗口为 INVALID。停止前资源样本的 RSS 峰值为 130.21 MiB。已完成窗口记录 M1/M2/M3/M4 信号分别为 4/3/1/1；跨模拟配置与排队情景的物理订单提交分别为 4/6/0/2，确认成交均为 0，M1 的 4 笔订单尾部未决。故障窗口没有新增信号或订单。整体运行 FAIL，不作为全天运行验收通过。见[只读核对结果](/home/ubuntu/polymaker/research_outputs/analysis/m1234_24h_status_20261108/result.json)。以下保留启动时的计划和检查记录。

2026-11-08 00:30:36 已启动后台服务，首个观察窗口为 00:35，计划观察至 2026-11-09 00:35，共 288 场。运行目录：[24h-20261108T003035](/home/ubuntu/polymaker/research_outputs/m1234_sim/forward/24h-20261108T003035)。服务名为 `polymaker-m1234-sim-24h-20261108t003035.service`，由用户级 systemd 管理，终端断开后继续运行。

已为 `ubuntu` 启用 systemd user linger，确保最后一个登录会话结束后用户服务仍可运行；记录见 `launch/session-persistence.json`。

00:35:30 的启动检查确认公共行情正在进入：首条心跳 9,777 帧、0 次断档、队列积压 14 帧、RSS 峰值 84.05 MiB；M1 双 profile、M2、M3、M4 和两个排队情景均已加载。启动证据见 [startup-check.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/forward/24h-20261108T003035/launch/startup-check.json)。`launch/` 保存本次配置、公共策略定义、源码压缩副本、源码哈希和预检结果。

00:37:07 复查：最新心跳累计 41,778 帧、0 次断档、0 帧积压，RSS 峰值 91.68 MiB。期间一次官方时间查询失败已自动恢复，后续 CLOCK 事件确认重新获取官方时间；服务持续运行。见 [followup-check.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/forward/24h-20261108T003035/launch/followup-check.json)。

查看当前服务与日志：

```bash
systemctl --user status polymaker-m1234-sim-24h-20261108t003035.service
tail -f research_outputs/m1234_sim/forward/24h-20261108T003035/runtime.jsonl
```

## 运行范围

- `config/m1234-sim-24h.toml`：连续 288 个五分钟市场，共 24 小时观察；采集循环的保护时限为 87,300 秒（24 小时 15 分钟），为启动对齐及运行延迟预留余量。停止后的结算查询、落盘和关闭仍需额外时间。触发保护时限时保留实际未完成状态，不算正常完成。
- `config/m1234-sim-contract.json`：M1 两个 profile 均无策略价格区间过滤，使用 5 USDC 的 `budget_quantity`；M2、M3、`m4-frequency-v2` 及两个排队情景不变。每市场/profile/情景仍为独立模拟账户。历史带价格限制、1 USDC 的配置保存在各旧运行的 `launch/strategy-contract.json`。
- 当前单次输出上限 16 GiB；磁盘可用空间低于 4 GiB 或进程峰值 RSS 超过 512 MiB 时停止并保留失败证据。空间和内存每五秒检查一次，停止收尾仍可能产生少量数据。
- 当前预检要求至少 20 GiB 可用空间，覆盖 16 GiB 输出预算及 4 GiB 余量；之前共享数据和失败证据保留。采集量估算仍需随实际流量核对。
- 原 `config/m1234-sim.toml` 保持 12 场/4500 秒；连续模式需显式配置 `continuous = true`。

## 资源及结算

已结束并已结算的市场在换场时保存账本快照、关闭数据库和录制线程。已结束但未结算的市场最多保留 12 个在线实例；超过上限时，将最早实例封存为待结算，其原始输入、订单、资金和未知敞口全部保留。`completion.json` 的 `deferred_settlements` 和 `runtime.jsonl` 的 `SETTLEMENT_DEFERRED` 标明需要后续续结算的市场。活动市场之外最多额外保留 12 个待结算实例。

汇总报告在换场及收尾阶段更新，避免随市场数量增加的全量核查阻塞当前行情处理。`runtime.jsonl` 中的心跳持续记录当前市场和队列状态。资源统计包含打开数据库数、存活录制线程数、待结算市场数和已封存市场数。已封存目录的体积缓存计入总输出，原始行情保留在各市场的 `input.jsonl.gz`。

## 预检与启动

在 `/home/ubuntu/polymaker` 下执行。预检不请求网络，不启动采集：

```bash
.venv/bin/python -m polymaker.m1234_sim check \
  --config config/m1234-sim-24h.toml \
  --contract config/m1234-sim-contract.json
```

预检 PASS 后，使用新的目录启动；`nohup` 允许终端断开后继续运行：

```bash
mkdir -p /home/ubuntu/polymaker/research_outputs/m1234_sim/forward
M1234_RUN_DIR="$(mktemp -d /home/ubuntu/polymaker/research_outputs/m1234_sim/forward/24h-XXXXXXXX)"
nohup .venv/bin/python -u -m polymaker.m1234_sim run \
  --config config/m1234-sim-24h.toml \
  --contract config/m1234-sim-contract.json \
  --output "$M1234_RUN_DIR" >"$M1234_RUN_DIR/console.log" 2>&1 &
printf '%s\n' "$M1234_RUN_DIR"
```

运行期间保持源码版本固定。每个新市场会检查源码身份；研究代码更新后应另建新运行，不向已有录制伪造版本身份。

历史录制仍使用其原冻结源码回放。新的连续模式配置和实现身份会写入新运行的 INIT，不改写旧输入或旧交付包。

### 当前后台服务入口及五分钟巡检

本次已通过 `ops/m1234_sim_start.py` 启动。该入口先预检磁盘、拒绝重复实例，再创建新运行目录并保存源码/配置，最后更新巡检目标。发生故障后的修复重启使用新的运行身份，旧失败记录继续保留。

```bash
.venv/bin/python ops/m1234_sim_start.py \
  --thread 01a1b68a-6e00-73a2-87c0-daec7de28f1e
systemctl --user enable --now polymaker-m1234-sim-watch.timer
```

`polymaker-m1234-sim-watch.timer` 在北京时间每逢 00、05、10…分触发检查，模拟服务的 `OnFailure` 还会在异常退出时立即触发同一个检查器。检查服务退出、心跳过期、积压接近上限、内存和磁盘余量，结果写入 `research_outputs/m1234_sim/monitor/reports/`。按用户要求，`control.json` 中 `notify_every_check=true`：每次检查均调用本地 `codex queue --resume`，在已绑定的当前对话汇报运行状态和 M1–M4 数量，正常且数量未变化也汇报。沿用既有计时器和对话；相同运行、五分钟时间段、状态及事故的通知合并，新的故障不被同时间段内的正常通知抑制。无法唤醒时记录失败供下一次巡检重试。

`ops/m1234_sim_metrics.py` 只读当前运行的 `*/simulation.sqlite3`，按审计序号增量统计，缓存保存在 `monitor/metrics-cache.json`。信号按市场、策略、候选 ID 去重；成交订单必须有正数 `MODELLED_FILL`，同一情景下的同一订单多次部分成交仅算一单。报告保留两个排队情景及 M1 两个执行版本的明细，成交汇报优先展示两个情景各自的数量。活动事务尚未提交的记录不计入；数据库与运行身份必须一致，无效市场排除并注明，读取失败标记 `PARTIAL/UNAVAILABLE`，不得当成确定的零。逐库审计水位随每次报告保留；该运行统计不替代最终验收结论。

只有复核确认模拟服务异常时，才由本任务定位、修复、定向验证后重启。普通巡检通知、统计器或唤醒器故障不构成重启健康模拟服务的理由。

2026-11-08 10:10 核账补充：巡检同时汇报模拟发单数及未决订单数。`ORDER_UNCERTAIN` 或 `UNKNOWN_TAIL` 按订单去重计入 `unresolved_orders`；已确认成交为零不能解释为未决订单已确定没有成交。旧指标缓存自动重建以补回历史未决事实。见[发单及撮合核账](/home/ubuntu/polymaker/research_outputs/analysis/m1234_execution_diagnostic_20261108/result.json)。

正常计划完成或明确 `MANUAL_STOP` 时不会要求重启，并自动结束该轮巡检。重启后依然按新的 288 场计算，不把失败轮次拼为连续 24 小时。服务退出不是修改信号或撮合规则的理由。

```bash
systemctl --user status polymaker-m1234-sim-watch.timer
.venv/bin/python ops/m1234_sim_watch.py --no-wake
```

用户要求停止整轮任务时，先禁用巡检并设置 `monitor/control.json` 的 `enabled=false`，再使用下方停止命令停止 control 中指定的模拟目录：

```bash
systemctl --user disable --now polymaker-m1234-sim-watch.timer
```

### 2026-11-08 队列修复的验证

实现哈希：`348c106ad81fd8519f45f3451c214231d8baa9417620b2cf10419e69ba904f46`。

连续模式将 `pending` 保存为最早两个待处理收取时间，另存完整 `pending_count`。现有准入与归因只使用是否为空、最早时间及弹出当前帧后的最早时间；覆盖统计仍使用真实完整计数。原始行情帧完整保留，旧全量 pending 记录仍可回放。价格精度约束没有变化时复用已有独立副本，订单冲突检查每次照常执行。断线失效处理自身报错时，仍关闭所有 socket 子任务。

故障前 49,799 条原始输入的诊断回放确认：除源码定义哈希外，修复前后完整状态一致，最大积压仍记录为 2,044；未把压缩后的列表长度伪装成实际积压。原故障最大积压记录的序列化体积为 35,378 字节，修复后为 952 字节；同一记录序列化 2,000 次的本机测量为 5.44 秒与 0.052 秒。这仅是该序列化热点的测量，不代表整机吞吐提升倍数。

113 项定向测试通过，包括原生 socket 到协调器的 10,000 帧心跳/深度突发、约束与成交回归、连续资源释放、故障收尾、巡检分类和唤醒合并。五分钟 systemd 定时表达式及服务配置检查通过；当前对话唤醒链路实测返回成功。证据：[修复验证目录](/home/ubuntu/polymaker/research_outputs/analysis/m1234_queue_fix_20261108)。真实 24 小时是否成功以新运行终态及核账为准。

## 查看、停止和核账

运行目录保存 `plan.json`、`runtime.jsonl`、`latest.json`、`report.md` 和各市场子目录。结束后保存 `completion.json`、`resources.json`。`latest.json` 是已有市场的研究报告；运行是否正常结束以 completion 及核账结果为准。

提前停止只匹配指定输出目录的模拟进程，不停止旧策略服务：

```bash
.venv/bin/python ops/m1234_sim_stop.py --run-dir "$M1234_RUN_DIR"
```

手动停止标记 `MANUAL_STOP`/FAIL，保留未完成窗口及已有结果。正常完成后执行：

```bash
.venv/bin/python ops/m1234_sim_verify.py \
  --run-dir "$M1234_RUN_DIR" \
  --output "$M1234_RUN_DIR/verification.json" --full-inputs
```

连续配置按声明的 288 场检查完整性；普通一小时配置仍按 12 场验收。已知失败、缺失终态及人工输入不会因放宽运行时长而获得公共验收资格。

对于 `deferred_settlements` 中的某个市场，用保留的输入续查官方结果，输出到新的模拟目录：

```bash
M1234_MARKET='btc-updown-5m-具体市场时间戳'
.venv/bin/python -m polymaker.m1234_sim settle \
  --input "$M1234_RUN_DIR/$M1234_MARKET/input.jsonl.gz" \
  --output "${M1234_RUN_DIR}-settled-$M1234_MARKET"
```

续结算仍未公布时继续保持待定，不把未知收益写成已实现收益。

## 本次验证

2026-11-08 定向验证完成，源码实现哈希为 `c6c32b549fa5849d6587b07df0c4d556f94a8ec1ba559ba0b6d3f454d8277260`。

- 原生协调器以人工时钟完成 288 个窗口，首尾市场保留完整信号、提交与成交事件，中间市场采用精简行情。完整窗口数为 288；资源样本中同时打开的数据库和录制器最多各 2 个，结束后全部关闭，首尾回放状态哈希一致。
- 另有 65 项配置、待结算上限、手动停止、时限、持久化、资源清理、报告和失败路径测试通过；静态检查通过。待结算积压用例确认超限后继续换场、原始数据保留、未知收益不变成已实现收益。
- 当前磁盘预检 PASS；旧 M1、M2 及 dashboard archive 服务的进程身份保持一致。

以上为软件就绪验证；其后的公共行情运行见本页开头。加速用例不能证明全天自然行情吞吐或网络稳定性。既有真实成交校准仍为 `UNCALIBRATED`。

证据：[验证汇总](/home/ubuntu/polymaker/research_outputs/analysis/m1234_24h_enablement_20261107/result.json)、[288 场记录](/home/ubuntu/polymaker/research_outputs/analysis/m1234_24h_enablement_20261107/accelerated-288.json)。

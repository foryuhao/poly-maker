# PolyMaker CLI Dashboard v1

看板读取独立观察器生成的快照。观察器只以只读方式增量打开已有的交易 journal 和
session 头记录的 CA 文件；不加载 `.env`、`Config`、交易 SDK、交易 SQLite 或账户锁，
不连接交易所、不发送订单、不启动或重启交易。新文件放在 `research_outputs/dashboard/`。

## 启动

在项目根目录执行：

```bash
bash ops/install_dashboard.sh
.venv/bin/polymaker dashboard
```

安装器只启用用户级 `polymaker-dashboard-archive.service`。观察器独立于终端，按秒采集、
按北京时间整五分钟生成运行快照。`q` 或 Ctrl+C 只关闭看板，`a` 切换归档索引。
`h` 按 **4h → 12h → 24h → 4h** 循环切换历史周期，默认 24h；曲线、PnL 分布和归档索引
一起按所选周期更新，当前 session 保持独立展示。周期仅影响本次看板显示，不改变采集或归档。
完整总览建议终端至少 120 列、40 行；小终端使用紧凑总览，宽度足够时同时显示曲线和分布。
窄终端自动纵向排列，`--once` 可以输出完整文本供滚动查看。不会修改已有交易服务。

```bash
.venv/bin/polymaker dashboard --once
.venv/bin/polymaker dashboard --json
.venv/bin/polymaker dashboard --session-id SESSION_ID --history-hours 48
systemctl --user status polymaker-dashboard-archive.service
journalctl --user -u polymaker-dashboard-archive.service -n 20 --no-pager
systemctl --user disable --now polymaker-dashboard-archive.service
```

不使用服务时，在独立终端运行：

```bash
.venv/bin/python -m polymaker.dashboard archive --project-dir .
```

另一个终端使用 `.venv/bin/python -m polymaker.dashboard view` 打开看板。
独立模块入口不导入主交易 CLI。`dashboard-archive --once` 仅进行一轮有上限的增量采集，
不会为补齐全部历史无限运行。输出目录有独立进程锁，重复启动会明确退出。
可以用重复的 `--journal-dir` 指定多个日志根目录；默认跟踪 `journal` 和 `livecfg/journal`。
初次发现范围默认为最近 24 小时，可在观察器启动时用 `--history-hours` 扩大。
看板的同名参数仅过滤已采集的数据，不会主动读取更早的日志。

## 展示与统计

- 当前市场、session、策略阶段、等待/退出原因、日志时间、观察器快照年龄。
  当前市场没有新 session 时展示最近一次并明确标记历史，不把旧日志当成正在运行。
- CA / BA 分方向展示最近 episode、状态、anchor、剩余有效期。
  CA 使用头信息记录的实际路径，其独立 session_id 可以不同。确认计数不是可交易机会数。
- 订单、确认份数、YES / NO、Delta、未配残仓、已配未合并份数。
  撤单 ACK 不等于成交核对完成；不会据此提前清空风险。
- 已确认配对 PnL 的累计 Total / Maker / Taker 曲线，Other 单列。
  分布按 FIFO 配对批次统计，部分成交可能形成多个批次，不当作不同订单或完整交易链。
  Maker、Taker、OTHER 共用六桶，自上而下为 `P>1.0`、`0.5<P≤1.0`、`0<P≤0.5`、
  `−0.5<P≤0`、`−1.0<P≤−0.5`、`P≤−1.0`，单位 USDC/批；零收益归入 `−0.5<P≤0`。
  单元格数字是该归因落入桶中的批次数，三列柱长使用相同尺度。
- 交易日志没有提供完整行情估值，所以 v1 不展示未实现 PnL、实时 WS 延迟或账户净值估计。
  日志年龄只代表日志新鲜度。超过 10 秒的采集快照或当前日志明确标为过期。

成交按 `(trade_id, maker_order_id)` 去重，仅 CONFIRMED 入账。
使用 Decimal，FIFO 采用首次观察成交的日志顺序，迟到的 CONFIRMED 不改变该顺序。
Taker 必须同时具备精确 `principal_usdc` 和 `fee_usdc`，不能拿报价或 POST 中的 nominal
份数代替真实成本。费用按匹配份数分摊，已计入收益，不二次扣除。
Maker / Taker 按完成配对的退出腿归因；CA 新开仓吸收旧残仓、TP、系统退出记 Other。
合并确认只改变库存和合并量，不再次增加配对收益。

未配残仓在结果未知时保留 PENDING。仅当最终 run result 有明确胜方及可核对的残仓结算
分项时计算结算值；不会额外调用交易所或将赎回现金重复计为新收益。
返佣与 gas 单列，未知项不填零。缺失日志、解析错误、CA 丢弃、成交成本缺失或最终
库存不一致会保留质量提示；已确认配对部分仍可查看，完整交易 PnL 保持 PENDING。
风险 writer 的 `flush()` 本身会占用无输出的序号，所以前向序号空洞记录在 manifest，
不直接声称丢失了交易；CA 序号缺口及风险序号倒退明确报错。

## 归档

```text
research_outputs/dashboard/
  observer.sqlite3              # 独立采集游标和派生证据，不是交易 state.db
  observer.lock
  latest.json                   # 原子替换的实时快照
  intervals/YYYYMMDD/HHMM/       # 每整 5 分钟一次，即使当前没有交易
    snapshot.json
    summary.md
    manifest.json
  archives/YYYYMMDD/HHMM_SESSION_ID/
    latest.json                 # 最近版本指针
    v0001/
      summary.md
      snapshot.json
      signals.jsonl
      orders-fills.jsonl
      manifest.json
    v0002/                      # 迟到确认、收尾或结算证据追加修订
```

市场归档在五分钟市场结束、完整记录读取后产生；未完成交易或待结算市场为 PROVISIONAL。
最终结果、核对和结算均完整才可成为 FINAL。旧版本不覆盖，每个文件有 SHA256 和大小，
manifest 同时记录源路径、inode、读取偏移、序号、解析/缺口状态、源码哈希与策略参数。
归档先在临时目录写完再原子发布，磁盘错误会在看板和服务日志显示并重试。

interval 名称是调度区间 `[start,end)`，内容是 `observed_at` 实际已观察的快照，
不是严格重建的截止时刻账本。初次启动补档明确记录观察延迟；停机漏掉的窗口仅记录数量，
不伪造当时状态。市场归档保存该 session 截至观察时间的完整生命周期，两个时间都保留。
每秒快照只精简历史配置；详细来源和配置在市场归档保留，避免重复写入全部历史大对象。

读取每轮预算 8 MiB，每个源预算 1 MiB，允许为读完一行额外读取至多 4 MiB，
并设置 150ms 的读取时间预算；历史与最新源轮转，
未追平时显示 CATCHING_UP。JSONL 半行等待补全，重启使用已提交游标；源替换/截断明确提示。
大体量的 cache/prepared 诊断只提取时间、序号和丢弃计数，不解码其内部 payload；跳过数量
写入来源记录。订单、成交、信号和收尾证据仍完整解析与校验。
服务 CPUQuota=15%、Nice=10、MemoryMax=256M、IOWeight=10；资源限制只作用于观察器。
初次历史补齐需要时间；清理器已删除的原始日志无法恢复。归档目录不属于现有交易日志清理范围，
会持续保留，需要按自己的磁盘保留策略另行管理。

## 验证

```bash
.venv/bin/pytest -q tests/test_dashboard.py
.venv/bin/ruff check src/polymaker/dashboard tests/test_dashboard.py
```

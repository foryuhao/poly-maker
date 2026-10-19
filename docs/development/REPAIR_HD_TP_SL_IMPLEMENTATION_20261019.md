# 已确认参数的HD/TP/SL实现

日期：2026-10-19。用户已确认[参数研究方案](REPAIR_PARAMETER_PROPOSAL_20261019.md)，参数、完整路径SL与会计/行情适配已完成。默认配置与livecfg已切换新版。已完成缓存详情饥饿、明确拒单后的重复撤单、实际仓位已证明后的CONFIRMED更新打断队列、取消提交UNKNOWN等待，以及下单准备时盘口变化导致整场退出的修复。最新状态见[准备报价失效修复](reviews/20261019_repair_quote_race_fix/README.md)，此前记录见[TP队列与取消提交验证](reviews/20261019_repair_queue_followup/README.md)、[明确拒单耗时修复](reviews/20261019_repair_rejection_latency/README.md)、[首场实盘修复](reviews/20261019_repair_first_live/README.md)和[首次上线交接](reviews/20261019_repair_release/README.md)。开发与修复过程没有发送真实订单。

## 参数和规则

`RDPRepairPathConfig`现在提供已确认的默认值：

|字段|值|含义|
|---|---:|---|
|tp_profit_rate|0.002|USDC/(原始share·秒)，当前episode公平价浮盈/Q0严格超过r×从首笔成交起持有时间时进入TP|
|shock_window_sec|2|短窗基准的最小年龄|
|shock_max_age_sec|3|基准最大年龄；稀疏历史不无限延长观察窗|
|shock_min_ticks|2|持仓方向token真实tick的最小逆向幅度|
|shock_z|1.5|逆向变化还需达到事前sigma×sqrt(实际时差)的1.5倍|
|drawdown_per_share|0.06|每原始share行情价格贡献从可靠峰值的累计回吐门槛|
|calibration_status|模型默认PARAMETERS_CONFIRMED；已验收运行配置REPLAY_VALIDATED|工程执行验收与正EV验证分开记录|

保留默认HD、单向HD→TP→SL/HD→SL、SL优先、T180强制SL、T240撤单截止。TP没有新增冷却期，可能成交后很早进入优惠价等待。已接受TP固定价格不因后续sigma缺失而丢失；首次计算新TP价仍需要有效sigma。SL直接ask-tick，状态切换必须撤旧单/核对后重挂，同一SL内按既定规则追价/保留队列。

路径SL：`adverse >= max(2*tick_held, 1.5*sigma_pre*sqrt(h))`或`drawdown >= .06`。短窗h必须落在[2,3]秒；未知sigma不按0代入，已知为0时仍需2 tick。B分支不再无关地依赖当前sigma，不要求总PnL为负；使用Decimal累计价格贡献，防止0.06边界因二进制误差错过。

## 会计和可靠性

- 按最新用户确认，HD/TP/SL 的 session 实际净敞口 `|YES−NO|≤2.5` 股时不再发新修复单；已有修复余单在成交事实核对后撤销。该门槛不替代原有 0.1 股配平口径或 0.01 股 Data API 核对容差，不清零仓位/成本，不据此宣布配平或重新开启 NORMAL；保留剩余仓位等待后续真实成交或截止/Merge。敞口变化后重新评估。新修复单另须满足整单成交后绝对 Delta 严格下降，避免最小 1 USDC 金额要求买 10 股而将 5 股敞口等量反向。首次等待与 Delta 变化记录 `sm_repair_small_exposure`，不重复刷同一条记录。详见后续[小敞口与 SL 耗时复盘](reviews/20261019_repair_small_exposure_sl_latency/README.md)。
- OBI路径的Data API持仓核对容差按用户确认从0.000001调整为0.01股，保留严格小于口径：每侧差异小于0.01允许，达到0.01仍阻断；不据此改写经济账本、成本或真实Delta。11:30场成交账本4.994445与API的4.9944相差0.000045，现在可通过该账户核对。新增正负0.000045、0.009999、0.01、0.010001的8个边界回归；与已有尾仓、REPAIR、Merge和缓存执行测试共80 passed，25.92秒。
- Data API未展示已知小尾仓时，保留之前的窄范围例外：仅在本session订单/成交已安全核对、经济账本有正数量且严格小于现有0.1股门槛、API返回数量为0时接受该省略。账本真实数量、成本及Delta继续保留，快照标记`data_api_omitted_known_dust=true`；其余差异使用上述0.01容差。策略dust门槛、session资金/Delta限制和成交/链上证明规则未修改。
- `PathPoint`分别携带session Delta、episode Delta、持仓方向tick、行情可靠段编号和仓位路径可靠性。session Delta仅用于限额、目标缺口与完成判断；价格贡献使用episode实际Delta/Q0。
- 已知原生时间且可可靠归属的部分成交插入价格事件之间：先用最后可信mark记录库存变化，再按变化后的Delta计后续价格变化。补仓本身的成本/浮盈跳变不进入行情价格贡献。
- 原生时间不能早于该订单实际POST下界；缺失/不可信时间明确标为本地观察代理。新增记录第一观察时间及经济证据可用时间，不把MATCHED超时自动当CONFIRMED。
- 成交时间/经济证据晚到或未解MATCHED使累计路径不可靠时，暂停跨该区间的DD积分，在可靠仓位恢复后建立局部可靠段；独立的价格冲击仍能触发SL。可靠局部段回吐达到0.06是全episode回吐达到门槛的充分证据。
- 开仓前行情不进入episode路径。官方时间锚点发生超过2秒的修正时，旧映射不拼接到新映射；保留已锁存状态并标记路径不完整。此2秒是时钟不连续性处理，不是新增交易信号阈值。
- 首笔成交处用同一可靠行情段中实际存在的as-of BBO建立mark锚点；即使盘口在成交前已静止，也不丢失第一段成交后价格变化。这个锚点来自行情，不来自开仓成本；缺少覆盖或有断档则使用局部可靠段并明确标记。断档恢复不重新使用原始成交锚点。已舍弃的开仓前/旧时钟数据也推进消费游标，避免静止盘口反复触发复核或旧数据重新进入路径。
- 行情重连、短暂不一致的双侧概率、传输中断及历史覆盖丢失均切断相应连续段；恢复时即使价格相同也生成新段。普通健康PONG/静止盘口不凭空制造价格波动。
- 原始成交量后来获得修正时不混用新旧Q0归一化累计量；清楚保留不完整路径与既有退出意图。
- SDK最终提交守卫用同一策略对尚未处理的行情路径作只读投影；短暂恶化后恢复不能让已无风险语义的旧TP计划漏过。投影不写journal、不推进真实策略游标/锁存状态。

## 日志和状态

策略快照schema_version=2，保存模式、成交/价格可靠性、局部峰值与回吐、短窗历史、TP固定价、触发事件及断档证据。旧版缺少可靠段契约的快照保留锁存意图，但不伪装成完整累计路径。

只保存短窗所需的3秒历史和一个边界点；累计峰值另存。内存episode快照每次复核更新，模式/TP接受价/库存可靠性等关键变化立即记journal，未变化的完整检查点最多每秒一次；逐次行情路径与决策证据仍保留。该节流不延迟SL检测或撤单，避免高频完整历史重复写入导致日志膨胀。

## 验证范围

- [真实策略代码回放](reviews/20261019_repair_implementation/replay.py)：将47市场704条研究探针送入实际`RepairPolicy`，与已确认研究的TP/SL触发时点、原因及删失逐条比较，**0个差异**。445条先TP、228条先SL、31条首次退出前删失。
- 回放按生产口径用Decimal BBO恢复mid，避免旧观察器浮点mid串化的约1e-16噪声影响等号边界；没有修改原始研究数据。
- 原81条旧决策输入回放：第1点TP，第11点SL，事前z=1.641883。此为稀疏输入解释性复核，不声称事件级真实首次下单时点。
- 定向测试包括真实签名/SDK/网关/异步日志/撤单/实际仓位关闭屏障，交易所和时钟为注入的虚拟边界；新增覆盖可信/未知/晚到部分成交、session尾差隔离、同代断档、0波动率和2 tick底线、2–3秒基准边界、sigma缺失仍可累计止损、六分钱精确触发和无TP冷却期。
- 704条研究探针只有秒级行情；没有假定任何Maker成交，不构造长挂策略的反事实收益或成交率。[结果](reviews/20261019_repair_implementation/replay_summary.json)明确写出PRICE_POLICY_ONLY及ready_for_live=false。

## 运行状态

`config/config.toml`与`livecfg/config.toml`均已显式选择`repair.policy="hd_tp_sl_v1"`，保留原有NORMAL、钱包和其他设置。两套配置的`uv run polymaker rdp-sm-repair-check`均返回0，`policy_selected=true`、`ready_for_live=true`，引用[最新队列与取消提交修复验证](reviews/20261019_repair_queue_followup/validation.json)。就绪检查本身检查本地声明，不替代该引用中的实际工程证据，也不代表实时网络/账户预检通过。

下一次执行`uv run polymaker rdp-sm-run`将使用新版HD/TP/SL，实际启动仍执行既有交易所/账户预检。未通过REPAIR就绪检查时，生产入口在交易所读取/NORMAL开仓之前阻止启动。本轮没有启动后台进程或发送真实订单。

已补齐模拟交易所边界下的整场执行、跨旧3秒TTL的TP队列保持、部分/迟到成交、SL撤换、截止与Merge、异常退出和重启仅撤单验收。真实交易所的成交表现和往返时延由接下来的实盘测试采集。当前仅支持同episode控制器快照恢复，进程崩溃后同市场重启做候选ID清理并阻止新开仓；没有自动接管交易续跑。此为有人值守测试的退出模式，不声明无人值守自动恢复或正EV。

## 下单准备报价失效修复（最新）

- 10:45场已触发SL，但计划生成到准备之间盘口变化，`QUOTE_WOULD_TAKE`从准备阶段上抛，整场停止并留下NO 5股。
- 现在只将尚未POST的prepare阶段该错误视为候选失效：记录`sm_repair_quote_invalidated`，清理旧计划/缓存候选、立即允许新复核，保留原episode及SL/TP状态。其他硬错误、激活/发送阶段异常保持原阻断流程。
- 旧版两种实际盘口复现均失败；修复后新增6个回归通过，覆盖SL/TP重算后配平及错误边界。扩展执行回归128 passed；本次未另跑全量。源码与验证证据见[修复记录](reviews/20261019_repair_quote_race_fix/README.md)与[验证结果](reviews/20261019_repair_quote_race_fix/validation.json)。两套配置就绪检查通过，配置及交易参数未修改。

## TP确认更新与取消提交修复验证（历史）

- 已有实际仓位及抵押品证明、数量价格与身份完全相同的MATCHED/MINED→CONFIRMED更新，保留活动Maker队列的原经济证明；仍使session关闭revision失效，最终关闭和新单准入必须重新核对。
- 带精确签名ID的Already canceled响应在没有冲突证据时暂停重复DELETE，保留UNKNOWN和全部风险预留，等待现有完整新快照证明；watcher被动监视，迟到活动订单或成交可恢复取消。普通UNKNOWN及真实冲突继续原核对流程。
- 新增13场景；最终专项**54 passed，42.86秒**。最终源码全量**2020 passed、2 skipped，327.79秒**。取消提交模拟生产轮询及请求延迟，最终全量中POST返回至关闭约1.106秒、返回后1次DELETE。
- 两套本地就绪检查通过，配置仅更新验证引用。策略参数、阶段截止、资金/Delta限制、缓存新鲜度及完整关闭证明要求保持。真实队列保持与耗时改善待下一场实盘确认。
- [根因、边界与证据](reviews/20261019_repair_queue_followup/README.md)、[验收记录](reviews/20261019_repair_queue_followup/validation.json)。首版全量因补充迟到活动订单边界而中止，其记录不作通过依据；上述全量为最终源码的重新完整执行。RP-001继续暂缓。

## 明确拒单耗时修复验证（历史）

- 原流程在明确REJECTED后仍取消签名ID；重复DELETE不断推进缓存写入边界，导致关闭核对10秒超时，再进入2秒恢复退避。两次实盘各24次无效DELETE，拒单到下一次POST分别12.447秒和12.363秒。
- POST在途时继续取消所有可能ID；返回后仅排除明确拒绝/未提交的腿。ACK/UNKNOWN、混合批次和所有账户/关闭证明要求保留。
- 生产源码只改`rdp_sm_lifecycle.py`，未改策略参数、缓存轮询周期或超时。配置仅更新验证引用。
- 全量**2007 passed、2 skipped，315.15秒**。新增测试覆盖生产轮询节奏和0.2秒请求延迟、拒单后真实SL重试及配平、混合腿拒单、ACK丢失与模糊响应；最终全量中的明确拒单核对约0.789秒、0次该ID DELETE。此为模拟实测，实盘延迟待下一场确认。
- [根因与执行差异](reviews/20261019_repair_rejection_latency/README.md)、[验证记录](reviews/20261019_repair_rejection_latency/validation.json)。暂缓的RP-001未改变。

## 首场实盘修复验证（历史）

- 失效旧证明只触发一次重新核对，失效签名随即移除，避免四个旧订单每轮插队饿死新订单；并发安装的新证明受到锁内比较保护。
- 未改策略参数、订单详情4个/轮上限、资金约束或实际仓位/关闭证据要求。
- 两个最终复现测试在旧方法下均失败，其中真实缓存执行复现`SYNC_CACHE_NOT_READY`。修复后全量**2002 passed、2 skipped，309.39秒**；最终缓存/执行专项**62 passed，54.62秒**。
- 已记录首场实盘的TP预热与首次报价时浮亏现象，保持现有状态锁存与报价规则，未在缓存修复中修改策略语义。
- [时间线与根因](reviews/20261019_repair_first_live/README.md)、[验证记录](reviews/20261019_repair_first_live/validation.json)。

## 首次上线时执行验收记录（历史）

- 全量回归：**1999 passed、2 skipped，285.59秒**。
- 最终专项回归：**129 passed，53.40秒**，包含9个新增执行验收场景，以及生产轮询间隔的缓存读取整场与更长TP队列测试。全量回归收集后仅扩展测试夹具，生产代码没有改变。
- 两套本地就绪检查均通过。Ruff与git diff --check通过。
- [逐项验收及交接](reviews/20261019_repair_release/README.md)、[机器可读结果](reviews/20261019_repair_release/validation.json)。

## 切换前实现阶段验证记录（保留原始证据）

- 最终生产代码全量回归：**1991 passed、2 skipped，286.34秒**。
- 随后修正测试夹具的虚拟网络探测，最终定向复验：**97 passed，18.88秒**；此间没有再修改生产代码。
- 47市场704路径真实策略信号对照：**0个差异**；原问题市场第11个可见输入点SL。
- 当时修改文件Ruff检查通过，git diff --check通过；切换前只读CLI返回2，新策略未选择、执行验收未声明完成。当前状态以上节及上线交接记录为准。
- [机器可读验证记录](reviews/20261019_repair_implementation/validation.json)、[源文件哈希](reviews/20261019_repair_implementation/source_manifest.json)、[就绪检查](reviews/20261019_repair_implementation/readiness.json)。

所有命令均未访问实盘交易所或发真实单。测试的虚拟server_time同时记录其模拟GET探测，单独调用控制器的夹具补齐生产loop负责的网络预热；没有为通过测试放宽生产网络准入。全量回归的临时文件使用数据目录，避开本机/tmp用户配额。对生产代码全量验证和最终夹具定向复验均已通过后，停止了重复的第二个全量运行，不将未完成运行作为通过结果。

复验命令（TMPDIR/--basetemp使用有空间的独立测试目录）：

```sh
PYTHONPATH=tests .venv/bin/python -m pytest -q --tb=short
PYTHONPATH=tests .venv/bin/python -m pytest -q \
  tests/test_repair_policy.py tests/test_rdp_sm_path_repair.py \
  tests/test_repair_marketdata.py tests/test_config_aliases.py \
  tests/test_rdp_sm_cli_logging.py --tb=short
.venv/bin/python docs/development/reviews/20261019_repair_implementation/replay.py
```

# REPAIR 输入与当前代码对照

> 本文保留开始HD/TP/SL开发前的输入审计。新开发版已补充部分字段、连续模式和行情历史，最新实现与尚未完成的实盘条件见[开发状态](REPAIR_HD_TP_SL_IMPLEMENTATION_20261018.md)。下列“没有/尚不存在”为本次开发之前的快照。

日期：2026-10-18。范围：当前 `rdp-sm-run` 的 `obi_v1 -> RepairController` 路径。只审计最新HD/TP/SL输入，不修改交易实现，不重新引入结算风险/波动风险输入。

结论：基础实时事实基本齐全；完整的新输入尚未齐备。主要缺口是持续的PnL/行情历史、episode归因、变化前sigma与历史比较，以及HD/TP/SL连续状态。已有数据能在本地补充，基础实时输入无需新增外部行情接口；历史上没有记录的价格细节不能从稀疏日志恢复。

## 1. 逐项对照

“已有”指当前执行/行情底座有可靠来源，不代表已全部装入旧 `RepairState`。当前状态字段仍只有p、sigma、elapsed、yes/no、cost、cash、修复侧bid/ask/tick、数量边界和原始方向。

|需要的输入|状态|来源与具体缺口|
|---|---|---|
|原始方向|已有|`repair_episode.side`，并传入`RepairState.original_side`|
|原始实际量Q0|已有，需接入新输入|`repair_episode.original_qty`按opening_window关联真实经济成交更新；旧RepairState没有Q0字段|
|原始成交成本/均价|可计算，需标准化|按opening_window对应订单与economic_fills价量求和；episode没有独立C0/均价字段|
|YES/NO实际仓位与session Delta|已有且已接入|`economic_inventory()`，`RepairState.yes/no/delta`；兼容仓位证明与后到CONFIRMED去重|
|session累计买入成本|已有且已接入|`state()`对economic_fills价量求和，作为cost；当前范围是Merge前|
|session当前mark PnL|已有计算|`RepairState.mark = p*yes+(1-p)*no-cost`，是session口径，不是独立episode口径|
|episode当前PnL|部分具备|原仓有opening归属，需把本episode修复成交、跨episode尾差和历史配对归因明确接入；不能直接用session mark代替|
|PnL历史、峰值、回吐|当前REPAIR路径没有|没有持续维护/持久化对应路径和状态；`mark`只计算当前值，决策日志是稀疏快照|
|当前条件胜率p|已有且已接入|`state()`取YES bid/ask中点，另检查与NO中价互补的一致性；不取best bid作为公平价|
|当前sigma|已有，有热身条件|过去30秒真实概率采样，排除当前未完成秒，至少10个有效相邻对且覆盖20秒；不足返回None|
|过去p路径|部分具备|内存`probability_points`最多128条；仅YES中间价、同一秒覆盖为最后一条，重连清空；不完整保留秒内走势|
|历史sigma/as_of|没有独立序列，可补算/保存|缓存只保存概率点，不保存sigma历史；在历史覆盖足够时可按过去时点重算，需要处理覆盖/缺口并保留事件前版本|
|过去双侧bid/ask路径|缺少专用连续历史|`books`保留最新盘口；`probability_points`只有p；signal_changes只存信号标签等，不是BBO轨迹|
|实际变动与同尺度历史比较|需新增内部计算|有实时原料及部分概率点，但没有统一观察尺度、事件前基线、排名/最大值及覆盖有效性的状态接口|
|当前双侧盘口、tick与数量/深度|底座已有，旧接口只用部分|`TokenBook/PairSnapshot`有双侧BBO、档位、时间戳；旧RepairState只传修复侧bid/ask/tick，没有双侧深度/数量|
|市场elapsed及距T240/T300|已有/可直接计算|官方time_anchor与monotonic对齐，当前state已提供elapsed；硬截止240已实现|
|持仓/模式持续时间|部分/缺失|订单和journal有观察时刻；原仓持有起点尚需统一实际成交/观察口径，HD/TP/SL进入时间尚不存在|
|活动单、成交/剩余、撤单与未决状态|底座已有，需适配|attempts、processor、proofs、terminal及review_active；旧RepairOrder只有side/price/remaining/age/cancel_at|
|排队年龄|有明确代理|REPAIR ACK后记录`repair_order_started`；来源为LOCAL_ACK_PROXY，不是真实交易所队列位置|
|当前HD/TP/SL及其计划|尚不存在|当前plan是旧BUY/HOLD/KEEP/CANCEL等结果，无HD/TP/SL状态、模式时间、TP目标与恢复状态|
|预算、预留与交易约束|已有|`committed()`含已花/未决最大承诺；cash、账户spendable、Delta、最小量/金额/精度与T240守卫已有|
|数据质量|底座有，需统一字段|snapshot.valid/reason、generation、连接健康、账户/经济证据已有；历史覆盖和PnL特征可信度需补|

## 2. 关键代码位置

- `src/polymaker/rdp_sm_repair.py:37`：当前sigma估计，窗口/热身/缺口条件。
- `src/polymaker/rdp_sm_repair.py:51`：当前输入组装，p、实际仓位、成本、资金、时间和盘口。
- `src/polymaker/repair_model.py:22`：旧RepairState实际字段；`:42`当前mark计算。
- `src/polymaker/rdp_sm_execution.py:996`：经济成交；`:1002`实际仓位；`:1014`episode；`:1037`资金承诺。
- `src/polymaker/rdp_sm_marketdata.py:22`：当前双侧盘口结构；`:129`概率缓存；`:216`重连清空；`:453`每秒覆盖采样。
- `src/polymaker/rdp_sm_repair.py:152`：活动单实际余量及未决成交管理。
- `src/polymaker/rdp_sm_lifecycle.py:623`：接受时间本地ACK代理。

## 3. 不能把局部现成字段当作新方案已齐

### 3.1 旧mark不是完整PnL路径

当前mark足够得到session此刻的浮盈。新方案还需要知道从何处变化而来、最近幅度、历史峰值、变化发生时实际净仓，以及部分修复成交带来的变化。

economic_fills按成交身份排序，不保证真实成交时间顺序。本场journal的sm_trade样本原生ts=0；journal外层时间是本地观察/记录时间。因此不能根据现有成交列表凭空还原精确交易所撮合时间。新输入需区分实际源时间、观察时间和未知，以事件顺序归因并保留质量标记。

仓库其他策略/研究代码存在peak_locked_pnl、peak_quoted之类字段，但它们是报价配对可锁收益峰值，且不在当前SM RepairController输入路径中。不能直接视为本需求的episode mark PnL峰值。

### 3.2 已有概率缓存不等于完整历史

当前每秒仅留下最后一个有效YES中价，秒内大幅往返会丢失。缓存不存NO bid/ask、每次sigma或实际持仓变化，重连即清空。它能支持当前sigma和部分秒级比较，不能宣称能验证任意秒内异常或完整30秒纪录。

如果新模型只用秒级观察，可复用缓存作为部分基础；若需要更细变化，必须保留足够精度的事件/极值，并为模型、日志和回放提供一致采样契约。不能用更多本地读取制造更多市场样本。

### 3.3 旧HOLD不等于新HD

旧HOLD是有限rollout里“暂不下单、两秒后可能使用一次尝试”的结果。不能只改名当作新HD，也不能用旧plan替代TP/SL连续状态。

## 4. 最小补齐工作

1. 输入适配：统一从执行账本、episode、账户、PairSnapshot和时钟构造新输入，明确session/episode成本、数据质量和原始量。
2. 历史与PnL状态：持续记录足够精度的概率/BBO、sigma as_of、成交/仓位修订，维护PnL路径、价格/交易贡献、峰值与回吐；必要状态持久化用于恢复和回放。
3. 模式状态：新增HD/TP/SL、进入时间、原因、TP价格目标/执行计划及切换状态，接入活动单/撤单核对。
4. 历史特征：由同一历史来源内部计算实际变化、变化前sigma及同尺度比较，并明确热身、缺口和重连行为；不新增结算风险或波动风险输入。

以上属于本地数据组织和状态管理，不需要先增加另一套外部市场预测接口。无法观测的真实队列位置和未校准成交概率仍不能当成已知事实；成交模型研究另行处理。

## 5. 验证

只运行现有离线测试，未发出实盘订单，未修改交易代码：

```bash
PYTHONPATH=tests .venv/bin/python -m pytest -q \
  tests/test_repair_marketdata.py \
  tests/test_rdp_sm_repair.py::test_actual_matched_position_unlocks_and_late_confirmed_counts_once \
  tests/test_rdp_sm_repair.py::test_partial_repair_keeps_only_remainder_and_preserves_absolute_deadline \
  tests/test_rdp_sm_repair.py::test_session_delta_counts_dust_and_does_not_reset_per_episode \
  tests/test_rdp_sm_repair.py::test_three_completed_episodes_share_one_15_usdc_budget
```

结果：6 passed in 2.84s。覆盖秒级sigma/重连热身、安静盘口健康、实际MATCHED仓位和后到确认去重、部分成交余量、跨episode Delta及共享session预算。

这些测试支持已有底座，不意味着缺失的新历史输入或HD/TP/SL模型已经实现。

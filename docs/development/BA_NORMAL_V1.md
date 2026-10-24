# BA-NORMAL v1 / 人工 MicroLive 操作说明

开发与确定性测试均未启动真实交易。当前 `config/` 按用户选择使用 `ba_v1`，
并已应用下述三秒与网络准入修订；`livecfg/` 仍使用 `obi_v1`。
原有 OBI 参数及 HD/TP/SL 策略与参数未修改。

## 当前三秒修订（2026-10-24）

- 固定期限为首次有效 SELL 的 T0+3 秒；ACK、重试、新 SELL、补单均不重置。
- BA 本地盘口及两侧时间偏差各为 200ms；源时间差按毫秒比较，不截断小数秒。
- 首次 POST 须在 T0+1000ms 内，并同时满足 SELL 源证据 500ms 与观察 750ms。
- BA 不再向 OBI 的 100ms 信号年龄检查传入自己的 T0 年龄，现有网络健康检查继续生效。
- 下单预算取 `max(750ms, 最近5秒POST各操作最大耗时)`；剩余期限必须至少覆盖该预算及 500ms 等待预算。
- 最近5秒有 DELETE 超过 750ms 则暂停 BA 新开仓；已有撤单及 REPAIR 不受此新增条件阻止。
- 无近期写入样本标记为 UNKNOWN；不以 GET 成功证明 POST/DELETE 已恢复。
- `sm_ba_wait.ba_admission` 与 `sm_ba_final_decision.intent.ba_admission` 记录准入预算、年龄、两侧时间差和拒绝原因。

上述阈值是试运行初值，未证明收益改善。完整规则见
[三秒及网络修订方案](reviews/20261024_ba_latest6/BA_NETWORK_REVISION_PROPOSAL.md)。

## 交付文件与 Git 状态

分支：`codex/ba-normal-v1`。进入任务时 HEAD：`10c2d7e`。
原现场有大量未提交改动，本轮没有把这些改动混入新 commit，代码保留在工作区。
`/tmp/ba-normal-v1-task.patch` 单独保存相对任务开始现场的本次增量，便于独立审阅。

| 文件（项目根目录下） | 本次用途 |
|---|---|
| `src/polymaker/rdp_sm_ba.py` | 独立 SELL / depletion / refill 状态、事件去重、generation、期限及一次性 claim |
| `src/polymaker/config.py` | RDPBAConfig、模式选择及配置校验 |
| `src/polymaker/rdp_sm_marketdata.py` | BA 专用逐事件 WS 接入；OBI 的整帧发布保持原行为 |
| `src/polymaker/rdp_sm_normal.py` | BANormalController；复用原有 bid 报价和 sizing |
| `src/polymaker/rdp_sm_lifecycle.py` | 轻量模式分发、BA 最终 Guard、独立年龄和网络预算、固定期限与成交撤单 |
| `src/polymaker/rdp_sm_network.py` | 不改变共享熔断规则，提供不可变的近期写入耗时摘要 |
| `src/polymaker/rdp_sm_execution.py` | 共享安全读入/所有权与资金边界、成交后创建 REPAIR、BA 成交关联日志 |
| `src/polymaker/rdp_sm_loop.py` | 选择 NORMAL controller，保持原有 REPAIR controller |
| `src/polymaker/rdp_sm.py`、`rdp_sm_entry.py`、`cli.py` | BA 复用 WS preflight、账户 fence、资金模型和命令入口 |
| `tests/test_rdp_sm_ba.py` | 必要的 BA 确定性测试；官方网络边界均替换为现有 mock |
| `tests/test_rdp_sm_ba_network.py` | 三秒期限、延迟确认、SDK 最终 Guard、预算和时间边界 |
| `tests/test_rdp_sm_obi_execution.py`、`test_rdp_sm_obi_entry.py`、`test_rdp_sm_obi_capital.py`、`test_rdp_sm_cached_reads.py` | 修复旧 fixture 缺少现有 Sigma 预热、冻结官方时间及未隔离 cooldown 的条件；复用入口检查 BA |
| `tests/test_rdp_sm_normal_signal_only.py`、`test_rdp_sm_normal_confirmation.py`、`test_rdp_sm_marketdata_realities.py` | 保留冷启动检查，匹配现场已显式配置的确认数，为纯 sizing 检查提供明确 Sigma 输入 |
| `docs/development/BA_NORMAL_V1.md` | 配置、事件解释、Journal 核对和人工单市场命令 |

上述以“本次增量”为准。工作区中原本已有 REPAIR 和配置改动，它们没有被本任务覆盖或回退。
HD/TP/SL/ledger/policy/cooldown/abandon 文件已与任务开始源码逐字节比对一致。

## 验证范围

已运行 BA 定向测试，以及现有 OBI 信号、确认次数、Maker 执行、取消/reversal、preflight、
资金边界、网络、sizing、WS frame、周期缓存读入和 Path REPAIR 回归。
相关集合 525 项通过；最后增加了“排队帧不能在观察期限后确认”及
“成交去重记录淘汰后仍拒绝不确定重复”的 BA 检查，并单独重跑全部 56 项 BA 定向测试。
Ruff 与 `git diff --check` 通过。
所有交易边界使用现有 fixture / MockTransport / 假 socket，未做历史回放、参数搜索或真实交易。

## 配置

在选定配置目录的 `config.toml` 中设置以下内容；已有 TOML 表应合并，不能重复声明。

```toml
[rdp_sm]
normal_entry_mode = "ba_v1"
# base_size / target_delta / favorite_threshold / max_order_size 保留现有值。

[rdp_sm.ba]
min_sell_shares = "5"
min_depletion_shares = "5"
min_refill_shares = "5"
observation_sec = 0.75
signal_lifetime_sec = 3.0
max_sell_age_ms = 500
max_book_age_ms = 200
max_pair_skew_ms = 200
max_entry_age_ms = 1000

[rdp_sm.ba.network]
post_budget_floor_ms = 750
min_live_budget_ms = 500
max_cancel_rtt_ms = 750

[rdp_sm.repair]
policy = "hd_tp_sl_v1"
# 其他 REPAIR 参数和 TP/SL 开关保持原配置。
```

切回 OBI 只需将 `normal_entry_mode` 改回 `"obi_v1"`，下一次启动生效。
`legacy` 继续兼容原有 `ev_legacy`。未知模式和非法 BA 参数加载即失败。
观察期限必须短于订单期限，数量、期限和新鲜度必须为有限正数。
网络健康与取消重试仍复用 `rdp_sm.obi` 中的现有执行安全参数；
BA 两侧时间偏差使用自身 `max_pair_skew_ms`；
其方向阈值、确认次数和信号 horizon 不参与 BA 判断。

`max_book_age_ms` 按现有 SessionFeed 语义校验本地 WS 接收后的 monotonic 年龄，
包括处理排队时间；盘口及成交的源证据年龄另外受 `max_sell_age_ms` 限制。
不能把 venue 到本机的传输耗时同时套入本地盘口租期。
时间戳反向、未来时间、重复和 generation 不明仍然作废。

2026-10-24 人工 17:35–17:40 会话排查：收到 563 笔公共成交（160 SELL），
BA episode/POST 均为 0。进场时段 T49.688 的样本中 SELL=Best Bid=0.8，
本地盘口年龄 0.496ms、源盘口年龄 144.456ms、成交源年龄 143.456ms。
旧 BA 实现额外将源盘口年龄也限制为 100ms，导致该有效 SELL 无法建档；
现已对齐本地盘口与源证据两个时间预算，未修改参数数值或 REPAIR。
146 条 `BA_SELL_NOT_FRESH_AT_BID` 是多个检查共用的拒绝计数，
其中也可能包含价格不等于 Best Bid、缺少盘口等情形，不能全部归因于源时间。
另有成交/深度时间戳回退，继续保留 Fail-Closed；本次修复不保证随后必然形成完整 BA。
下次人工启动仍使用当前 `config` 中的 `normal_entry_mode = "ba_v1"`。

## 信号与订单生命周期

只处理官方 market WS 的 `last_trade_price` 成交，按 token 独立记录。
SELL 必须带有效数量、价格及毫秒时间戳，且成交在此前新鲜、非交叉盘口的合法 Best Bid。
`price_change.side=BUY` 仅表示 Bid 深度更新，绝不能当作 SELL 成交。
官方字段参考：[market stream](https://docs.polymarket.com/market-data/realtime-data)、
[官方原始 WS schema](https://github.com/Polymarket/ts-sdk/blob/main/packages/bindings/src/subscriptions/clob.ts)。
方向校验同时要求官方 SELL 标记及在非交叉 Bid 上执行；不从深度下降推测主动卖出。

从第一次有效 SELL 的本地 monotonic 接收时间 T0 冻结 token、outcome、Bid 价格/数量、
generation、episode ID 及两个绝对期限。SELL 达到门槛后，同价位依次累计消耗和补单。
在 T0+0.75 秒前补单达到门槛即确认；不需要深度完全恢复，不需要 OBI 或 Sigma 信号。
这条 WS socket 的接收次序必须与每个 token 非递减的官方时间戳一致；
同一毫秒内用原始 socket/数组序号区分顺序，不排序或补造事件。
批量帧的每个深度更新都检查，因此不会把先减少、后恢复合并成零变化。

确认后走现有 NORMAL 的账户/库存、资金、TargetDelta、Favorite、动态 tick、最小数量/金额、
网络、T+15 至 T+120、签名、持久化 WAL、最终 Guard 和原生 POST 流程。
仅 BUY / GTC / post-only，价格必须仍是 anchor 对应的当前合法 Best Bid。
准备和签名不提供期限豁免；原生 POST 前再次校验期限、SELL 新鲜度、generation 和 anchor。
BA 不等待额外盘口事件来激活已经确认的信号。

单个 BA episode 最多发起一次 POST，包括拒绝或 ACK 未知的提交。
双侧有效时同时作废，不能在一侧过期后利用另一侧残留信号开仓。
新 SELL、补单、重复读取和重试不延长原期限。
T0+3 秒触发本地取消；anchor 失效、数据异常、WS 断开/重连、原有风险条件失效、
NORMAL 时段结束或确认成交会提前终止。BA 不使用 OBI 的 `SIGNAL_REVERSED`。

取消请求及取消 ACK 都不是最终结清证明。沿用原有签名订单身份、FillTruth、实际仓位、
资金和订单终态校验；未知状态禁止新订单。到期后仍可能有迟到成交或迟到确认。
实际成交进入现有库存账本、撤销余量并交给现有 REPAIR；部分成交也保留真实数量。
未成交且已完整终态确认的机会直接结束，不创建 REPAIR episode。
REPAIR 的成交成本基线保持在 BA 提交前，避免迟到成交进入错误成本基线。

## Journal 核对

所有记录仍在当前配置的现有 Journal 中，不启用独立研究平台。

1. `sm_session.normal_entry_mode` 和 `ba` 确认实际模式与参数。
2. `sm_ba_episode` 包含 STARTED / SELL / CONFIRMED / SUBMITTED / INVALIDATED，
   以及 market_id、episode_id、token/outcome、anchor 价格/深度、三种累计数量、
   T0、确认时间、generation、order_deadline、invalid_reason。
3. `sm_ba_final_decision.intent.claim[0]` 对应 episode_id；window_id、quotes、
   post_started、deadline 与 `sm_prepared`、`sm_placement_result` 关联签名订单。
4. `sm_ba_normal_execution` 直接给出 episode_id、opening_window_id、order_id、
   POST 的 monotonic 时间、confirmed_fill_qty、first_fill_time、fill_time_source、
   signal_age_at_fill（秒）。时间源沿用现有 venue timestamp / 首次本地观察的标记。
   经济数量包含既有实际仓位及资金对账确认的成交，不冒充原生 CONFIRMED 状态。
5. `sm_ba_close_requested`、`sm_cancel_ack`、`sm_window_result.close_barrier` 核查
   触发原因、取消延迟和 SAFE_TO_CONTINUE；只有完整终态证明才可继续。
6. 用 `opening_window_id` 关联现有 `sm_repair_episode.opening_window`、
   REPAIR 的 window / chain 及其既有路径输出，按现有 TP / TP-SL / SL / OTHER 分类。
   BA 模块不重新计算路径，也不改变 REPAIR 的退出规则。

## 单市场人工启动

在 `/home/ubuntu/poly-maker` 执行下列准备步骤。它只复制 TOML，不复制运行数据或密钥，
不改变 livecfg。保留相同的 journal 路径和账户锁，避免用新目录绕过已有订单/restart fence。
继续使用项目根目录的现有 `.env` 和现有授权账户；不启动其他交易进程。

```bash
cd /home/ubuntu/poly-maker
.venv/bin/python - <<'PY'
from pathlib import Path
import re
import shutil
from polymaker.config import Config

source, target = Path('livecfg'), Path('bacfg')
target.mkdir(exist_ok=False)
for item in source.glob('*.toml'):
    shutil.copy2(item, target / item.name)
p = target / 'config.toml'
text = p.read_text()
text, count = re.subn(r'(?m)^normal_entry_mode\s*=\s*"obi_v1"\s*$',
                     'normal_entry_mode = "ba_v1"', text)
assert count == 1, 'Expected one existing obi_v1 mode'
assert '[rdp_sm.ba]' not in text, 'Merge an existing BA table explicitly'
text += '''
[rdp_sm.ba]
min_sell_shares = "5"
min_depletion_shares = "5"
min_refill_shares = "5"
observation_sec = 0.75
signal_lifetime_sec = 3.0
max_sell_age_ms = 500
max_book_age_ms = 200
max_pair_skew_ms = 200
max_entry_age_ms = 1000

[rdp_sm.ba.network]
post_budget_floor_ms = 750
min_live_budget_ms = 500
max_cancel_rtt_ms = 750
'''
p.write_text(text)
cfg = Config.load(target)
assert cfg.rdp_sm.normal_entry_mode == 'ba_v1'
assert cfg.rdp_sm.repair.policy == 'hd_tp_sl_v1'
assert cfg.execution.post_only
print('normal_entry_mode = ba_v1; one-market configuration prepared')
PY
```

人工先核查 REPAIR readiness，再明确启动一次：

```bash
.venv/bin/polymaker rdp-sm-repair-check --config-dir bacfg
.venv/bin/polymaker rdp-sm-run --config-dir bacfg --human
```

第二条命令会进行真实交易，必须由人工执行。本轮开发未运行。
`rdp-sm-run` 每次只运行启动时锁定的一个当前 BTC 5min 市场，结束后退出，
不会自动转入下一个市场。建议靠近新市场开始时运行，确保有 T+15 至 T+120 的观察时间。
资金上限沿用当前 hd_tp_sl_v1 的 20 USDC，数量与其他限制使用复制来的原配置。
如需第 2–3 个市场，逐场核对完整 Journal 与终态后，由人工再次执行同一命令。
不删除 fence、不改 journal 路径、不启动自动循环。

回到 OBI 使用原来的 `--config-dir livecfg`，或在 bacfg 中把 mode 改回 `obi_v1`。
模式切换不会清除同一市场的 restart fence，也不会解除未结清订单。

## MicroLive 仍需确认的事实

- 官方 WS 没有跨频道全局序列号；只接受同一 socket 上可验证的顺序。
  若 venue 实际发送 depletion 早于 SELL，机会被舍弃，不能倒排事件凑信号。
- 公共成交未必带唯一 trade ID；用 token/时间/方向/价格/数量/可选 transaction_hash
  去重。完全相同的合法成交也可能保守作废机会，不会多计成交量。
- 本地时钟与官方时间有偏差、排队、两侧 book 过旧或数据缺失会降低触发率。
  先核对实际事件及作废原因，不能为了提高触发率跳过检查。
- 3 秒是本地撤单触发期限；网络及交易所延迟会使最终撤单和成交确认更晚。
- 这些参数仅是第一轮初值，未证明盈利或路径改善。后续只使用人工真实市场结果迭代。

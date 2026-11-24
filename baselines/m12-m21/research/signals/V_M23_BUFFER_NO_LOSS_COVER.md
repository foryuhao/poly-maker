# M23 增频分支：加托扛卖，不限定成交覆盖损失

版本 `m23-buffer-retention-no-loss-cover-v1`，`SPEC_ONLY`。**先出现可见加托并留存，随后材料性真实SELL发生，缓冲仍有保留；在固定缓冲底线与原期限有效期间，于原Bid锚价成交。** 不再要求缓冲必须先被消耗到指定窄带，也不要求全部可见损失小于等于观察SELL。[M23原版](M23.md)及[先前缓冲留存版](V_M23_BUFFER_RETENTION.md)保留，Maker/Taker单列。

```json
{
  "document_type": "signal_sop_variant",
  "strategy_id": "M23",
  "variant_id": "M23_BUFFER_NO_LOSS_COVER",
  "version": "m23-buffer-retention-no-loss-cover-v1",
  "status": "SPEC_ONLY",
  "selection": "EXPLICIT_ONLY",
  "research_family_id": "F_PRELOADED_BUFFER",
  "baseline_version": "m23-preloaded-sell-buffer-retention-v1",
  "basis_file": "../signal_discovery/spec_archive/M7_M23_FREQUENCY_IMPLEMENTATION_BASIS_20261117.json",
  "basis_sha256": "510f0315d67092a6e42d624def47f5df7aedb7a8fa04706fbe097068a231a762",
  "main_sop_preserved": "M23.md",
  "calculation_contract": "maker-discovery-common-v1_WITH_M23_BUFFER_OVERRIDES",
  "common_overrides": [
    "NO_RF_ROUND_OR_MINIMUM_LOSS_GATE",
    "BUFFER_FLOOR_NOT_RF_FLOOR",
    "NO_RF_STABILITY_EXIT"
  ],
  "signal_contract": "fixed-anchor-signal-v1",
  "execution": "fixed-anchor-execution-v1",
  "entry_semantics": "FILL_AT_FIXED_ANCHOR_WHILE_SIGNAL_VALID",
  "execution_modes": [
    "MAKER",
    "TAKER"
  ],
  "preload_min_net_add_shares": "5",
  "preload_min_net_add_fraction": "0.20",
  "preload_hold_ms": 100,
  "preload_retained_add_fraction_min": "0.80",
  "pre_seed_sell_presence_before_lossy_normalization": true,
  "seed_confirmation_lateness_max_ms": 50,
  "first_sell_after_seed_max_ms": 750,
  "first_sell_gate": "RECEIVED_AND_KEY_AFTER_SEED_AND_RECEIVED_AVAILABLE_BEFORE_DEADLINE",
  "test_window_ms": 500,
  "test_min_sell_shares": "5",
  "test_min_sell_to_preload": "0.50",
  "test_min_depth_loss_to_preload": null,
  "visible_loss_zero_allowed": true,
  "test_sell_covers_depth_loss": false,
  "visible_loss_definition": "MAX_ZERO_Q0R_MINUS_MIN_OBSERVED_QUANTITY",
  "retained_preload_fraction_min": "0.20",
  "test_terminal_preload_fraction_max": null,
  "test_terminal_hold_ms": 100,
  "buffer_floor_from": "LAWFUL_FIRST_NEW_SELL_HANDOFF_THROUGH_END",
  "explicit_terminal_P_quantity_after_all_test_sell_inside_window": true,
  "publication_requires_complete_original_batch": true,
  "test_confirmation_lateness_max_ms": 50,
  "seed_hard_ttl_ms": 3000,
  "deadline_rule": "SEED_CONFIRMATION_PLUS_3000MS_NO_RENEWAL",
  "original_level_loss_invalidates": true,
  "root_policy": "ONE_PER_TOKEN_FROZEN_PRELOAD_FIRST_NEW_SELL_NO_REUSE",
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE",
  "tests_run": false,
  "baseline_definition_file": "V_M23_BUFFER_RETENTION.md",
  "baseline_definition_sha256": "2aa459358aced4161edc22e7c5c44385905350565864bdc38bd701260f1220d7",
  "revision_date": "2026-11-17",
  "loss_to_hit_ratio_role": "DIAGNOSTIC_ONLY_NO_GATE_NO_EXIT",
  "diagnostic_missing_invalidates": false
}
```

## 1. 组合与改动边界

`预加托留存 → 首个新SELL合法交接 → 完整500ms受卖及缓冲留存 → E(P,F,d; mode)`。加托不是BA/RF，真实H证明发生受卖，不由可见损失推断成交。

先前缓冲版已删除原版的`L≥0.50Δ`与末100ms`Q≤B+0.50Δ`。本版在其上仅删除**H≥L**筛选：真实挑战仍由H材料性证明，缓冲是否保留由固定F证明。L/H及H−L只描述观察成交与数量变化的关系，不承担筛选、择根、排序、确认或退出。没有RF最少损失、恢复率或末段上限。

种子100ms、80%底线、首SELL750ms、验证500ms、20%缓冲底线、真实H下限、原种子3s以及所有来源/原档规则保留。末100ms留存下限仍写明，但已由整个验证期的同一F覆盖，不重复算一份证据。保留旧版与历史XR备选身份，不默认调用XR或QR。

## 2. 预加托种子

空闲时，首个合格的明确原Bid报量更新在更新前best_bid P形成增量。P事前已存在，原量B>0，新量B+Δ，`Δ≥max(5,0.20B)`。新出现的更高档不作种子；price_change.size是新总量，必须用明确前后量相减。

前量来自连续、可追溯的同档数量链，后量由本事件明确报出。冻结P/B/Δ/generation、原始批次与事件键、两份数量来源，t0为增量事件received。后续增量不扩大Δ、不移动B、不重开计时。

从t0至合法首笔验证SELL前始终守`Fpre=B+0.80Δ`、原P正量与质量。种子窗`[t0,t0+100ms)`；水位与当前完整批次处理完成后一次检查发布，`t0+100ms≤cS≤t0+150ms`，不等后来回补择时发布。窗口终点到发布路径中曾破Fpre也失败。

从t0至cS（含边界），P上首次直接接收归属该区间的SELL必须为零，在镜像/方向过滤丢失前核对。与t0同接收时刻、同批较前行的SELL同样核对；已证实原始重发按首次接收归属，未知归属为UNKNOWN。晚发现此区间SELL在发现时结束，不倒删历史成交。不能仅凭深度覆盖证明零成交。

种子不授权成交，状态`PRELOAD → WAIT_FIRST_SELL`；同token最多一个root。

## 3. 首笔新SELL与底线交接

cS后P上的首笔可信直接SELL须received>cS、输入键>种子确认键，且received和available均严格早于cS+750ms。首笔不满足来源、阶段或截止即结束，不跳过它挑下一笔；到期无首笔也结束。

处理这笔SELL前Fpre必须一直有效；合法性成立后才切换为`F=B+0.20Δ`。此后直到root结束都守F。若深度先已破Fpre、SELL随后可得，不能用低F或事后source排序救回。无法证明输入顺序时UNKNOWN，同边界先处理已发生的失效再交接。

首SELL前冻结Q0R：明确P数量来源输入键更早、received不晚于SELL、source严格更早，连续链有效。不用B或B+Δ替代缺失的首SELL前量。令tR=首SELL.received，验证窗`[tR,e)`，e=tR+500ms；没有达到材料H之前不提前确认。

## 4. 真实受卖与缓冲留存

统计窗口内P上的去重直接SELL累计H，以及明确P数量事件中的最低量。可见损失定义`L=max(0,Q0R−Qmin_observed)`；没有明确数量路径为UNKNOWN，不能将缺失视为L=0。

完整窗口要求：

- `H≥max(5,0.50Δ)`；H与L不作大小门槛比较。
- 从合法首SELL交接起，全部已知路径`Q(P)≥F`，原档、generation与质量持续有效；末100ms同样守此下限。
- **没有最小L，没有数量上限，没有RF合格/稳定要求。** 即使全窗量不低于Q0R、L=0，也可继续检查其他条件。

窗口内必须有明确P数量事件，输入键晚于全部计量SELL，source严格晚于这些SELL最大source，received仍在`[tR,e)`内，关闭时已可得。其他档/BBO刷新不能代替；缺少末端证据不允许以“数量看起来还厚”补认。保持原窗内来源合同，不增加或删除逐笔100ms复核。

原始price_change按输入数组顺序检查中间状态；完整健康book视为一份状态，不把内部清空/赋值当消失。重建generation结束旧root。批次末态不能覆盖中途F跌破，原序不明为UNKNOWN。

在形成确认边界可记录`loss_to_hit=L/H`和`observed_sell_minus_loss=H−L`，因为通过材料性后H>0；仅派生诊断无法计算时记null，不因诊断缺字段取消已可证明的信号。必需H或数量路径缺失仍UNKNOWN。L>H允许，表示可见减损可含撤量、补量及不同消息时序的影响，不声称损失主要由SELL导致。零或小L也不证明隐藏挂量或参与者身份。真实H与自有成交、前置队列无关，不抵扣Maker队列。

## 5. 发布与原截止

水位越过e且完整原始批次处理完成后，首个合法边界检查发布，`e≤c≤e+50ms`且c<cS+3s。先处理全部已知失效、迟到的种子SELL反证、e到c的F路径与质量；不能只检查当前恢复量。窗内received事件仍按形成统计处理，封窗后迟到依质量合同，不能改称窗后成交绕过要求。

确认锚P，`deadline=cS+3000ms`。从种子而非验证确认、订单发送、ACK或补量计时。原时钟推导仍为`500ms<c−cS<1300ms`，名义剩余大于1700ms且小于2500ms；硬失效可随时提前结束。这只是期限关系，不能当作实测寿命或成交保证。

本版更高留存可通过，所以可能在旧版失败时更早占用root。删除最低L、末段上限和H≥L门槛扩大局部可接受路径，不证明整个样本信号数单调增加，不沿用旧M23结果身份。

## 6. 等待、执行及重启

确认后只维护固定F、原P存在、质量/generation/tick和原deadline；不重复要求形成H，不在任何阶段重验H≥L，不加入RF稳定退出。补量可继续存在，但不提高F、不重置Δ或期限；不把后续保量称为又一次独立确认。

失效或到期先于同边界新准入。Maker在P合法被动挂价后等待；Taker只在信号仍可引用且P有可主动取得的对手量时准入。正常未交叉同token盘口中，P的正Bid/F与同P主动买入无共同准入状态；按[固定锚价合同](FIXED_ANCHOR_CONTRACT.md)记录限制，不删底线、不改锚追价。

已知Q<F、原档消失、源序不可证明或到期立即结束并通知处理未成交部分。撤单在途、部分及实际价格不同的成交均如实保留，不因之后失效删账。历史执行预算、400ms余量和市场时段独立选择，不成为形成门槛。

结束后只有received与输入键均晚于结束的全新合格加托事件可另起root；旧增量及SELL不复用，不换Δ/基线续上。新root不自动解除执行额度。其他Ask只检查质量/结构与真实执行合法性，不新增Ask方向确认。

## 7. 确认与失效复核

| 场景 | 本版处理 |
|---|---|
| B=100、Δ=50、Q0R=150、H=30、最低及末量145 | L=5、F=110；若完整来源/时间/路径合格，可通过，不强求跌至125以下 |
| 同初量，H=30、最低110 | L=40>H，但H≥25且Qmin=F=110；其余完整证据及守卫成立时允许，不把L>H继续当失效 |
| 没有真实SELL，仅静态厚量 | H门槛失败；取消L下限不取消受击证明 |
| 首SELL前140的Fpre已破，后来SELL可得 | 旧root结束，不能切110救回 |
| 全窗及末100ms守F | 是同一量界，不再额外计算一份末段留存优势 |
| 末量增加、L=0 | 允许，但须有真实H与成交后明确数量来源，缺量不按零损失 |
| 后续好量或复核完成 | 不重置种子3s，过期等号不能新准入 |

必要输出：variant/version、来源hash、增量/种子/首SELL事件键、B/Δ/Q0R/H/L/Qmin、Fpre/F、源证明、cS/tR/e/c/deadline、反证阶段、执行模式与真实回执。只定义摘要与引用，不产生逐tick日志；未实现或测试，未证明增频或结算优势。

## 8. 语意代价与全路径一致性

本版接纳有材料性SELL且剩余缓冲仍有效、同时可见损失可能较大的路径；它降低了“损失不超过成交”的保守筛选强度，不证明优势增加。预筛选、合格轮判断、发布、结束回调均不得残留H≥L；不得从COMMON重新引入H/L双材料性，因为本版明确允许L=0。仍保留预加托→新SELL顺序、Fpre到F的合法交接、真实H、完整成交后数量来源与原种子3s期限。不得删除这些事实保护来取得成交。

# M21 新分支：一轮卖来供退

版本 `m21-single-joint-support-withdrawal-v1`，`SPEC_ONLY`。**一轮真实SELL获得Bid稳定承接，同时原Ask在无该档BUY的观察下净减；在新Bid底线与Ask退量上界有效期间，于固定Bid锚价成交。** Maker/Taker独立列出。保留[M21原版](M21.md)，本版不要求第二轮共同确认。

```json
{
  "document_type": "signal_sop_variant",
  "strategy_id": "M21",
  "variant_id": "M21_SINGLE_ROUND",
  "version": "m21-single-joint-support-withdrawal-v1",
  "status": "SPEC_ONLY",
  "selection": "EXPLICIT_ONLY",
  "research_family_id": "F_WITHDRAWAL_SUPPORT",
  "baseline_version": "m21-fixed-anchor-entry-v1",
  "basis_file": "../signal_discovery/spec_archive/M17_M23_DISPOSITION_BASIS_20261116.json",
  "basis_sha256": "c248f4745b7aa57fc10f4c1c2ea7ceb0be405383ca7bfcd2221dd36d2796c89f",
  "main_sop_preserved": "M21.md",
  "calculation_contract": "maker-discovery-common-v1_WITH_EXPLICIT_SINGLE_ROUND_COMPOSITION",
  "signal_contract": "fixed-anchor-signal-v1",
  "execution": "fixed-anchor-execution-v1",
  "entry_semantics": "FILL_AT_FIXED_ANCHOR_WHILE_SIGNAL_VALID",
  "execution_modes": ["MAKER", "TAKER"],
  "rounds_required": 1,
  "round_window_ms": 500,
  "ask_prequiet_ms": 250,
  "ask_prequiet_max_relative_deviation": "0.05",
  "bid_min_hit_shares": "5",
  "bid_min_hit_fraction": "0.10",
  "bid_min_loss_shares": "0.5",
  "bid_min_loss_fraction": "0.05",
  "bid_stable_recovery_min": "0.50",
  "bid_terminal_hold_ms": 100,
  "ask_min_net_loss_shares": "0.5",
  "ask_min_net_loss_fraction": "0.10",
  "ask_terminal_hold_ms": 100,
  "formation_direct_buy_at_ask_max_shares": "0",
  "zero_buy_until": "ACTUAL_COMPLETE_BATCH_PUBLICATION_INCLUSIVE",
  "bid_guard_recovery_floor": "0.40",
  "bid_guard_failure_operator": "LT",
  "ask_retained_net_loss_fraction": "0.50",
  "ask_guard_failure_operator": "GT",
  "guards_from": "ROUND_WINDOW_END_THROUGH_PUBLICATION_AND_WAIT",
  "terminal_bid_and_ask_sources_after_all_sell_inside_window": true,
  "direct_buy_presence_before_lossy_normalization": true,
  "publication_requires_complete_original_batch": true,
  "confirmation_lateness_max_ms": 50,
  "post_confirmation_ttl_ms": 3000,
  "deadline_rule": "FIRST_JOINT_ROUND_CONFIRMATION_PLUS_3000MS",
  "second_round_required": false,
  "cross_round_ratio_filters": null,
  "second_round_formation_deadline_ms": null,
  "new_direct_buy_after_confirmation_invalidates": false,
  "post_confirmation_buy_gate": "RECEIVED_AND_INPUT_KEY_STRICTLY_AFTER_CONFIRMATION",
  "late_formation_buy_action": "END_ON_DISCOVERY_KEEP_ALL_FILLS",
  "root_policy": "ONE_PER_TOKEN_FIRST_ELIGIBLE_SELL_FIXED_WINDOW_NO_REUSE_NO_RENEWAL",
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE",
  "tests_run": false
}
```

## 1. 组合与本次变化

`同一SELL轮 [S_C,BID ∥ 原Ask无BUY净减] → E(P,F,U,d; mode)`。这里的共同确认只用一轮已完成的双侧观察，不是先找旧S再等待新Ask证据。严格S是局部COMMON构件，不是两轮RF或BA。

保留原版每轮的材料性、前置Ask平稳、500ms观察、末100ms保持、来源与零BUY证明；删除第二轮、跨轮Q0/H/u/r比较、第二次前置平稳和c1+2s形成截止。新信号从第一轮实际确认c计3s，使用这一轮的新F/U；不能沿用旧版R2身份或把旧结果直接过滤成新版结果。

第二次共同响应不再是确认或维护条件。若外部研究另有重复性描述，只能在后续事实真正可得时追加为历史诊断，不回填首次确认特征、不延迟入场、不改变F/U或deadline。本版不要求实现新的重复计数器。

本版与M6共享撤供留托研究入口，但观察的是单Ask在SELL窗的净减，不是LW前三档残余。也不直接调用P_XW：没有其攻击资格后才退量、20%/5份、750ms寿命等规则。未证明增频或结算优势。

## 2. 固定起点、数量来源与前置平稳

输入沿[COMMON](COMMON.md)的数据、时钟和质量合同。只计同token直接成交；镜像映射不冒充另一份直接证据。空闲时按输入顺序检查每笔可信直接SELL；满足事件前best_bid数量与best_ask数量可证、P<A、原档正量及下述前250ms覆盖/平稳时，首笔合格SELL冻结P、A、generation、Q0、a0、事件身份和t0=received。已有root不重开或滑窗。

两侧初量须来自首笔前明确报量事件：输入键更早、received不晚于该笔、source timestamp严格更早，至首笔的更新链连续。全书时间戳、BBO或其他档回调不代替原档数量。不能证明则UNKNOWN/UNAVAILABLE，不用之后的量倒推Q0/a0。

Ask前置窗口`[t0−250ms,t0)`全部已知状态满足`abs(Qask(A,t)−a0)≤0.05a0`。a0是实测首笔前量，不在允许区间内择优另选；不要求该前置窗口零BUY，也不要求没有SELL。完整连续链可延续未变化量，不能用计时器补覆盖。起点条件不满足不建立root；下一次只能由新的真实SELL重新检查，不能重用失败事件。

形成窗固定`[t0,e)`，e=t0+500ms。P/A始终存在、不换档；原档后来不再最优也不移动锚，价格不提供方向确认。单token同时最多一个形成或等待root。

## 3. 单轮共同确认条件

Bid沿COMMON统计Qmin、Qend、H、L=Q0−Qmin、u=H/Q0、d=L/Q0、r=clip((Qend−Qmin)/L,0,1)。H只含P上的去重直接SELL。要求：

- H≥max(5,0.10Q0)，L≥max(0.5,0.05Q0)。这些下限覆盖COMMON材料性，不允许零冲击静态厚墙。
- 末100ms持续恢复≥0.50，封口r≥0.50；新低重置稳定段，不能混用旧滞回stable。
- Ask冻结g=max(0.5,0.10a0)，末100ms持续`0<Qask(A)≤a0−g`。封口aend、D=a0−aend≥g；g≥a0时无正量解，结束本root。

从t0至实际发布c（含边界），原A上的直接BUY存在性必须为零，包含形成窗、e到c及发布批次后行。必须在原始直接来源被镜像/方向过滤丢失前或等价完整来源集合上核对；过滤后空集和只有深度覆盖不证明零BUY。真实BUY为反证；无法证明完整性为UNKNOWN，不能补零。原始重发仅在身份可证时按首次直接接收归属。

两侧各须有一份明确报量事件：输入键晚于本窗全部计量SELL、source严格晚于它们最大的source，received仍在`[t0,e)`内，关闭时已可得。P、A可在不同事件报量，但BBO、别档刷新和断线重建快照均不能补足。这里保留窗内末量证明，不采用尚未定义的窗外复核改法，也不新增M17式逐笔100ms复核。

形成失败不等待下一个较好窗口，不挑第二轮补救。数量净减不等于可识别的逐单撤单，SELL与Ask退量同窗也不证明同一参与者或因果反应。

## 4. 发布、交接与固定期限

状态为`IDLE → JOINT_ROUND → CONFIRMED → END`。水位封口、当前原始批次完整处理之后，在首个合法边界进行一次发布检查；无原批次身份或等价原子边界则UNAVAILABLE。`e≤c≤e+50ms`，超过50ms不发布，不等未来数据或后续好轮。

冻结`F=Qmin+0.40L`、`U=a0−0.50D`。从窗口终点e起至实际发布边界全部已知路径须满足`Qbid(P)≥F`、`0<Qask(A)≤U`，同时原档、质量与零BUY有效。中途破界后回补仍拒绝；只能将received≥e的输入用于轮后守卫，形成窗内的按时输入仍属形成统计，封窗后迟到按质量合同处理。

完整批次的到期/失效优先于发布。通过后c为实际可得时刻，锚P，`deadline=c+3000ms`。不保留一个不存在的旧S或R2时钟，不从发送/ACK/新好轮重新给3s。理论新增观察500ms加至多50ms发布延迟；前250ms为已有历史覆盖，不机械相加为必须再等250ms。

## 5. 等待、失效与重启

任一`Qbid(P)<F`、后续同档合格Bid轮不严格稳定、`Qask(A)>U`、原P/A消失、tick/generation变化、质量未知或now≥deadline，结束并通知执行端处理未成交部分。Qbid=F与Qask=U允许。没有Ask RF退出、没有第二次Ask前置平稳要求，也不要求每笔后续SELL再触发Ask净减。

确认后真正新的BUY@A不自动结束，但必须同时received>c、输入键>确认键；仍守A存在及U。确认后才发现、首次直接接收归属于本root且received≤c的BUY是形成反证，发现时结束，不倒填过去的取消时间；时间等于c不能靠行序改称新阶段。已证实为t0前历史或原始重发的记录按其真实归属处理，来源不明为UNKNOWN。

新S、额外撤供、重复共同响应或执行回执均不换锚、不重设F/U、不续期。结束后仅received和输入键都晚于结束的全新SELL可重新检查起点；旧窗口和事件不复用，一次提交额度若由执行配置选定，也不随新root重置。

## 6. Maker/Taker执行

共同目标是信号有效期间在固定P成交，按[固定锚价合同](FIXED_ANCHOR_CONTRACT.md)。Maker须满足P上被动挂价合法后等待；Taker保持意向，仅在信号可引用且P有可主动取得的对手量时准入。原Bid正量/F条件保留，正常未交叉同token盘口下同价Taker买入没有共同准入状态，不能删F或换价来制造成交。

信号到期/失效先于同边界新准入。撤单请求不等于取消完成，在途及部分真实成交保留，不用后续结算删账。历史预算、市场时段、400ms发送余量等独立由执行配置选择，不反过来定义信号确认。本任务没有实现执行或适配器。

## 7. 确认与失效复核

| 场景 | 本版处理 |
|---|---|
| Bid封口r≥0.50 | Qend≥F+0.10L，有数量缓冲；e到c路径仍逐项检查 |
| Ask封口aend=a0−D、D>0 | aend<U，相差0.50D；U等号允许，严格大于退出 |
| 第一轮后Ask尚未再平稳、SELL立即继续 | 不再等待R2；新SELL按当前F和后续Bid承接规则处理，不能自动续期 |
| 发布批次后行出现BUY@A | 先反证形成，不能先发布再称确认后BUY |
| c后新BUY进一步消耗A | 本身不否决，原档消失或其他guard失败仍结束 |
| 缺少窗内成交后的P/A末量来源 | UNKNOWN，不以去掉第二轮为由补认 |
| 比较旧版信号数 | root更早确认及占用时间改变，不保证全样本频次单调增加，不能套旧版结果 |

必要输出包括variant/version、来源hash、root/round/初量与末量事件、P/A、Q0/H/L/r、a0/D/F/U、历史覆盖、零BUY证据范围、e/c/deadline、批次边界、质量、失效原因及真实执行回执。仅定义小型证据引用，不新增行情日志；未测试、未证明结算优势。

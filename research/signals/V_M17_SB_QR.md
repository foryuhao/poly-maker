# M17 分支：短退补回后扛卖

版本 `m17-sb-then-retained-sell-v1.1`，SPEC_ONLY。交易语意：**原Bid在低打单量下短退又补回，之后一段全新真实SELL仍未使其显著变薄；在这份验证和原补回量界有效期间，于固定锚价成交。** Maker/Taker单列，不新增M编号。v1.1补清量界覆盖与发布后含义，数值、状态转移和截止规则不改。

```json
{
  "document_type": "signal_sop_variant",
  "strategy_id": "M17",
  "variant_id": "M17_SB_QR",
  "version": "m17-sb-then-retained-sell-v1.1",
  "supersedes_version": "m17-sb-then-retained-sell-v1",
  "revision_kind": "SEMANTIC_CLARIFICATION_NO_NUMERIC_OR_STATE_RULE_CHANGE",
  "prior_definition_snapshot": "../signal_discovery/spec_archive/V_M17_SB_QR-445d4f4932d9ed9b.md",
  "quantity_guards_combination": "INTERSECTION_MAX_FLOOR_NOT_INDEPENDENT_EVIDENCE_COUNT",
  "status": "SPEC_ONLY",
  "selection": "EXPLICIT_FIXED_ANCHOR_VARIANT",
  "research_family_id": "F_RETAINED_DEPTH",
  "signal_contract": "fixed-anchor-signal-v1",
  "execution": "fixed-anchor-execution-v1",
  "execution_modes": [
    "MAKER",
    "TAKER"
  ],
  "entry_semantics": "FILL_AT_FIXED_ANCHOR_WHILE_SIGNAL_VALID",
  "direct_trades_only": true,
  "seed_primitive": "SB_BID",
  "seed_current_ttl_ms": 750,
  "first_sell_gate": "RECEIVED_AND_AVAILABLE_STRICTLY_AFTER_SEED_AND_BEFORE_ORIGINAL_SEED_DEADLINE",
  "first_sell_must_match_pre_event_best_bid": true,
  "seed_floor_continues_after_historical_handoff": true,
  "new_verification_window_ms": 750,
  "new_verification_uses_global_QR": false,
  "require_full_QR_hit_before_seed_deadline": false,
  "QR_min_hit_shares": "5",
  "QR_min_hit_to_initial_depth": "0.50",
  "QR_subwindow_ms": 250,
  "QR_min_distinct_hits": 3,
  "QR_occupied_subwindows_required": 3,
  "QR_max_single_hit_fraction": "0.80",
  "QR_path_floor_fraction": "0.70",
  "QR_terminal_and_publication_fraction": "0.90",
  "QR_terminal_hold_ms": 100,
  "post_hit_recheck_max_ms": 100,
  "confirmation_lateness_max_ms": 50,
  "root_hard_ttl_from_seed_ms": 3000,
  "publication_pending_policy": "RECORD_CANDIDATE_BLOCK_NEW_EXECUTION_UNTIL_RECHECK_CLEAR_WITHOUT_RESETTING_C_OR_D",
  "RF_stability_exit_used": false,
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE",
  "component_specs": [
    {
      "file": "P_SB.md",
      "version": "sb-short-depth-dip-replenishment-v1",
      "sha256": "08f7745d68fcce6cc62c49afe84fe21c64b302637f6ddbc1a7c70b708ef6a360",
      "snapshot": "../signal_discovery/spec_archive/P_SB-08f7745d68fcce6c.md"
    },
    {
      "file": "P_QR.md",
      "version": "qr-consumed-queue-retention-v1.1",
      "sha256": "cb28cf522e8452607c302029985ce6f053175cf88a7fac77d92c2e1fcf7d4030",
      "snapshot": "../signal_discovery/spec_archive/P_QR-cb28cf522e845260.md"
    },
    {
      "file": "M17.md",
      "version": "m17-fixed-anchor-entry-v1",
      "sha256": "b64a4cc44e640711bfebbbc81ef18a7054d5765e2efe52bef65e636917bd609a",
      "snapshot": "../signal_discovery/spec_archive/M17-b64a4cc44e640711.md"
    },
    {
      "file": "COMMON.md",
      "version": "maker-discovery-common-v1",
      "sha256": "c3ec4443e8651e8a6f685309bdd6142166bada3de6c0846d3c18531cdc803645",
      "snapshot": "../signal_discovery/spec_archive/COMMON-c3ec4443e8651e8a.md"
    },
    {
      "file": "FIXED_ANCHOR_CONTRACT.md",
      "sha256": "9dd080082d2d74035105e010d4b43afdc7d8a9a6e52420d81b2908b28dcad3d2",
      "snapshot": "../signal_discovery/spec_archive/FIXED_ANCHOR_CONTRACT-9dd080082d2d7403.md",
      "signal_contract": "fixed-anchor-signal-v1",
      "execution_contract": "fixed-anchor-execution-v1"
    }
  ],
  "calculation_contract": "maker-discovery-common-v1",
  "seed_selection": "FIRST_NEW_SB_PUBLICATION_WHILE_IDLE",
  "path_floor": "MAX_SB_BASELINE_MINUS_HALF_DROP_QR_70PCT_INITIAL",
  "terminal_publication_floor": "MAX_SB_BASELINE_MINUS_HALF_DROP_QR_90PCT_INITIAL"
}
```

## 1. 机制假说与归类

`SB_BID → 首个新SELL处将SB转历史 → 专属新QR_BID验证 → 原锚有效成交`。

SB说明可见买量曾在较少已知打单下退出又返回，不足以单独称为承接。随后QR提供真正受卖后的留存观察。如果补回量只是短暂报价，在新卖压下容易消失；本分支要求实际受卖后仍保量，并且没有再次跌破SB保留下来的绝对数量线。这是“需求返回后，是否经得起新卖压”的可否证假说。

若这一状态与更持续的需求有关，可能使相同锚价、相同剩余时间的实际成交具有不同结算倾向；这个联系尚未证明。短退可能是信息劣化、补回只是噪声，也可能没有任何超出普通QR的方向信息。本文不使用未来价格或结算筛选root，不推断参与者或订单身份。

归M17家族的前史分支。与历史QO→QR相比，前史是材料性退量后返回，且保留原SB绝对底线；QO是窄幅回摆、首SELL后解除原带宽。与M8 RD重建不同，本版不要求先有真实受击恢复后回吐，也不在新SELL轮内重建旧50%恢复界。与主M17相比，多了确定的前史和较强数量守卫，但重新选窗、起点和截止不同，不能声称实际成交是主M17结果的事后子集。

## 2. 输入与SB种子

使用同token、同generation、有序原始直接成交与固定Bid数量。按冻结[P_SB](P_SB.md)的BID版本取得已确认种子：原Bid从B减去材料性Δ，至少20ms保留80%减量，200ms内首次补至`B−0.20Δ`至`1.10B`并保持50ms，形成统计中直接SELL量不超过0.25Δ。完整形成、来源、末量和发布边界均依其冻结规格，不能只用首尾快照。

冻结原P、B、Δ、`F_SB=B−0.50Δ`、SB确认cS、原截止`dS=cS+750ms`及原始根身份。只取本策略空闲时首个全新发布且可引用的SB_BID；不在空闲后回选更早已发布的SB。同token最多一个本分支形成或当前root。种子确认不是入场确认，此时不提交订单。

种子发布到首笔新SELL之前，SB必须仍当前有效，P存在、Q(P)≥F_SB、质量与来源链有效。初减量或早期SELL不能重复计为新QR。晚处理但received属于SB形成阶段的记录按SB反证/质量规则处理，不改名为新验证流量。

## 3. 首笔新SELL与历史交接

取cS之后首笔P上的新直接SELL。其received及available必须严格晚于cS、严格早于dS，输入键严格晚于SB确认键；事件前P还须是best_bid，并且有明确前置Q0>0及连续数量来源，source严格早于SELL。首笔不满足即结束，不跳过它等后面更合适的一笔；到dS没有首笔也结束。

先检查这笔前已知的SB失效，只有合格首笔才启动专属验证窗。该处理点将SB转形成历史，**只解除原SB750ms自然到期的要求；F_SB、原档、质量及历史反证仍持续约束本root**。不能用交接救活已经跌破F_SB的SB。后来的新SB不替换或重设本参考。

新窗`[tR,e)`，`tR=首SELL.received`，`e=tR+750ms`。独立重算QR规则，不能取一个已经形成或从cS之前开始的全局QR，也不能滑动tR跳掉失败部分。本分支不要求完整QR成交规模在dS之前取得：原SB此后只是历史，真正确认必须等全新750ms验证完整完成；首笔小成交本身不授权入场。

## 4. 新验证、底线和复核

H只计新窗内P上的直接SELL。须H≥max(5,0.50Q0)、至少3条可辨成交、三个250ms桶都有正量、最大单笔≤0.80H。

从新窗开始至结束，数量必须满足：

`Q(P) >= F = max(F_SB, 0.70Q0)`。

最后连续100ms还须`Q(P)>=max(F_SB,0.90Q0)`。这是两个既有量界的交集，不计成两份独立方向证据。若F_SB≥0.70Q0，原SB量界已覆盖QR70%条件；若F_SB<0.70Q0，则新QR路径量界更强。终点超过SB原基线允许，不恢复其形成上界1.10B。

每笔计量SELL须在接收后严格100ms内有同P明确报量：输入键更晚、source严格更晚，received不早于成交且received/available均早于成交received+100ms；形成窗成交的复核received还须<e。来源相等、其他档或BBO回调不能替代；不删未复核成交重算H。到期先判超时，不能用后来的输入挽救。

e之后新的SELL不计形成H，但立即进入同样复核监控，覆盖发布交接与等待；其复核不再要求<e。缺口、无法证明直接来源、时钟或原始批次的情况为UNKNOWN，终止root。

## 5. 确认、截止与当前维护

水位封口后，在首个完整原始批次／相应计时器边界检查，`e<=c<=e+50ms`。形成条件、形成窗全部复核、e至c期间F连续有效、原档及质量均满足，且发布点`Q(P)>=max(F_SB,0.90Q0)`，才能确认。失败不等待下个更好发布点。

`deadline=cS+3000ms`，从SB确认计时；不使用新QR确认起再加3秒。因为`tR∈(cS,cS+750ms)`，确认间隔`c−cS∈(750ms,1550ms)`，名义剩余`deadline−c∈(1450ms,2250ms)`，仅是规则推导，不是成交预测。

e之后新成交尚在复核预算内时，可以记录候选，但`can_execute=false`；及时复核完成后可解除阻塞，原c和deadline不变。任一复核超时、Q(P)<F、P消失、generation/tick变化、质量或历史反证失败、deadline到达均结束。发布之后不再单独要求QR的90%门槛；若F_SB本来≥0.90Q0，持续的旧量界仍禁止跌至90%以下，不能以“90%解除”为由放宽F。没有RF稳定退出，不要求每750ms再生成一次QR。

一份SB root只试一次。失败、到期之后只有结束可得时刻及输入键更晚的全新SB根才能重启，旧SELL、旧低谷及旧补回不复用。

## 6. 执行方式

两种方式只在本分支已确认、可引用、P不变且deadline未过时申请成交，按[固定锚价合同](FIXED_ANCHOR_CONTRACT.md)：

- Maker：在P以post-only等待；订单生效后的流量与队列决定实际成交，形成H不抵扣队列。
- Taker：保持待执行意向，P有可成交对手量且信号仍可引用时才申请。正常未交叉同token盘口中，本版正Bid量界与同价Taker没有共同准入状态，须记录执行受限，不能放宽F或改价。

可显式选用旧COMMON的时间/额度参考；400ms发送余量属于执行条件，不是本分支确认条件。没有自动回退、改价、重发或从回执续期。失效结束未发送意向并通知处理在途订单；全部实际成交、真实价格和顺序未知保留，不事后删去不利暴露。确认后的本单成交不是新承接证明。

## 7. 逻辑核对与输出

定义算例（非行情测试）：SB的B=100、Δ=20，F_SB=90；cS之后首SELL前Q0=98，则F=max(90,68.6)=90，末段与发布量界max(90,88.2)=90。三个桶累计SELL例如20/20/20、量始终≥94、复核及时，数量门槛可以同时成立。SB基线与新Q0不同，两条量界不能直接比较百分比大小。

若新窗最低89，虽然仍高于0.70Q0，本分支因旧补回量界破坏而失败；以后补回不救活。若强行要求SB仍当前有效直到QR完成，则新窗开始晚于cS、耗时750ms，原SB必已到期，组合无解；本文明确使用历史交接避免这项定义冲突。

输出限于小型证据引用与摘要：variant/version、SB根、P/B/Δ/F_SB/cS/dS、首新SELL与Q0来源、tR/e/c/deadline、H/桶量/最大成交、F、形成及当前最低量、复核引用/待办、阶段/失效原因、执行模式及实际成交归属。不复制逐tick日志。输入能力尚未验证，缺字段UNAVAILABLE；未实现检测器或适配器，未做测试、交易数、胜率或PnL评价。

## 8. v1.1：哪些量界可以合并计算

记`x=F_SB/Q0`。首SELL前仍守SB量界，故`0<x<=1`。依本root冻结值可分三种情况：

| x | 形成全程／等待底线F | 末100ms及发布点底线 | 覆盖关系 |
|---|---|---|---|
| `0<x<0.70` | `0.70Q0` | `0.90Q0` | 新QR量界已覆盖SB数量底线；SB前史、首选事件与种子截止仍有作用 |
| `0.70<=x<0.90` | `F_SB` | `0.90Q0` | SB覆盖QR路径70%，QR末段／发布90%仍额外约束 |
| `0.90<=x<=1` | `F_SB` | `F_SB` | QR的70%与90%数量门槛都被SB底线覆盖 |

等号归上表对应区间，不重复计确认次数。用`max`统一计算与原规则完全相同；不是删除SB历史或整个QR验证。QR的真实H门槛、三桶持续性、单笔集中度、逐笔来源复核及750ms完整观察仍不可省略。

例如F_SB=90、Q0=98时x约0.918，两条QR百分比数量门槛都没有额外筛选力；增量在于“SB之后真正受卖且量界始终未破”。若Q0=150，则x=0.60，F=105、末段门槛135，此时QR数量规则更强；SB形成上界已解除，Q0较大并不与SB当前态矛盾。数值仅作定义推导，不表示市场出现过这类路径。

v1文本“发布后不继续要求90%”仅指QR独立末段门槛退出，v1.1明确其不覆盖更强的F_SB。首个发布机会、pending处理、3秒种子截止和执行限制均不变。旧版保留于[完整v1快照](../signal_discovery/spec_archive/V_M17_SB_QR-445d4f4932d9ed9b.md)，不得只按名称忽略文档版本和hash。

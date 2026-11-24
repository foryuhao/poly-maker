# RF 反复补给

本次仅补齐独立原语文档；原定义/实现存在不代表当前部署或已验证收益。

```json
{
  "document_type": "orderbook_primitive_sop",
  "primitive_id": "RF",
  "version": "rf-shared-bid-500ms-v1",
  "status": "DOCUMENTED_FROM_SOURCE",
  "origin": "EXISTING_PRIMITIVE_DOCUMENTATION_NOT_NEW_EDGE",
  "source_profile": "research.maker_signals.primitives.Rounds defaults + Refill",
  "inherits_execution_contract": false,
  "sides": [
    "BID"
  ],
  "consuming_direction": "SELL",
  "anchor_policy": "FIRST_ELIGIBLE_EXISTING_BID_LEVEL_NOT_REQUIRED_BEST",
  "round_window_ms": 500,
  "confirmation_lateness_max_ms": 50,
  "minimum_hit_shares": "0.5",
  "minimum_hit_depth_fraction": "0.0001",
  "minimum_loss_shares": "0.5",
  "minimum_loss_depth_fraction": "0.0001",
  "hold_cross_recovery": "0.50",
  "hold_maintenance_recovery": "0.40",
  "hold_ms": 100,
  "held_revoked_below": "0.20",
  "new_trough_resets_hold_and_held": true,
  "stable_end_recovery_minimum": "0.50",
  "required_stable_rounds": 2,
  "maximum_first_start_to_confirmation_ms": 15000,
  "minimum_median_recovery": "0.50",
  "current_ttl_ms": 8000,
  "qualified_failed_round_ends_all_current": true,
  "unqualified_round_skipped": true,
  "positive_new_rf_ends_prior_id": false,
  "strict_final_100ms_recovery_at_least_50_percent": false,
  "source_files": [
    "research/maker_signals/primitives.py",
    "research/maker_signals/feed.py",
    "research/signals/LEGACY_COMMON.md"
  ],
  "source_sha256": {
    "research/maker_signals/primitives.py": "249c711a58b6c33516f4d9383e0d039566d56cf4924b1ee994ba8c4f961ac522",
    "research/maker_signals/feed.py": "dc7eb44cdcdf2781c5d5d40cca7fa011e322072f1680b72d1d76db9da78795f0",
    "research/signals/LEGACY_COMMON.md": "e3857f08807717ceb24b9530c1a662bafe838b5907b3f2a2c72bcd56fdd0f2a4"
  },
  "implemented_by_this_task": false,
  "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

## 定义与主版本

RF记录同一挂单侧原档被多轮真实打单后仍有恢复能力。本文件主版明确选用共享research RF_BID：两轮500ms、确认后8秒。它不等于最初工具箱的三轮750ms双侧原型，也不等于M4频次版单轮Bid支撑或COMMON严格稳定单轮S。

主版只声明已有源码支持的Bid侧。原型RF_ASK的定义见本文末尾，不把Bid共享观察器机械翻面后冒称现有实现。

## 单轮计算与稳定条件

空闲观察器第一笔符合输入门槛的SELL命中当时存在的Bid档p，冻结p、前量Q0>0、t0；源码不要求p一定是当时买一。该观察器固定原档，不能每笔重新取最优档。

在[t0,t0+500ms)累计原档SELL量H，记录最低量Qmin和末量Qend。L=Q0−Qmin；m=max(0.5,0.0001Q0)。H≥m且L≥m为合格轮；r=clip((Q−Qmin)/L,0,1)，u=H/Q0，d=L/Q0。H≥L不是这个旧版的额外资格条件，不能从新COMMON补入。

合格后r≥0.50启动保持计时，连续100ms不低于0.40取得held；r<0.40清空正在计时，r<0.20撤销已取得held；新低同时清空计时与held。曾取得held后落到0.20–0.40区间，不会仅因此撤销已有held；封口仍须r_end≥0.50。这不等于最后连续100ms都≥0.50。

水位到达轮末、统计在随后50ms内可得且覆盖完整，封口稳定条件为合格、held=true、r_end≥0.50。未合格跳过；合格却不稳定会清空序列并终止该观察器已有RF。

## 两轮确认与维护

同原档连续两个合格稳定轮，第一轮start至第二轮确认≤15秒，恢复率中位数≥0.50，确认RF。轮次不能重叠；这两轮用完清空序列，不滚动复用第二轮配第三轮。若配对超时则不发RF。

c为第二轮统计可得时间，deadline=c+8秒。后续合格失败轮、原档消失/重建、generation或输入质量失效结束RF；到期等号失效。没有新冲击只自然到期，不认定防守失败。

共享Refill新正向RF可与旧事实ID并存，分别保持期限；不自动延长或替换旧ID。M1/M2等消费端是否替换候选由其策略定义，不能借整理原语统一掉这些差别。本版没有新增“持续守原40%floor”的独立维护条件，不能把COMMON S的等待守卫反套进来。

## 时序、例子与旧口径

连续两轮最快约1秒完成；实际可能等待下一笔SELL，配对跨度最多15秒。8秒是确认后的证据有效期，不是要观察8秒才确认。

Q0=100、最低80、末量92，则L=20、r=0.60；只有同时具备合格H和100ms held才稳定。另一轮r=0.70、时限合格可形成RF。只读两个末快照不能证明held。

2026-11-01原型RF_BID/ASK使用本目录P_AA所述750ms完整轮：最低H和L均max(5,0.10Q0)，触及档持续一致；最新三轮稳定、首轮开始至第三轮封口可得≤5秒、中位恢复≥0.70，确认后1秒。后续合格不稳定轮、锚变化或质量失效结束；无新冲击只到期，三轮不复用。原型不是本文件机器参数的默认版本，使用时须明确绑定原工具箱version与参数文件。M4单轮8秒支撑另见P_DF/M4；250ms局部轮不改写共享RF。

## 数据与输出

需要原档逐次明确数量、真实SELL、身份/输入键、水位、稳定保持路径和覆盖。主来源允许已核验互补视图，须连同feed身份保留；其他档更新时间不能补造原档恢复。

输出primitive/version、root/round IDs、p/Q0/Qmin/Qend/H/L/r/u/d、保持路径引用、轮末及确认时间、deadline、质量和结束原因。RF_BID只提供承接证据，Maker还需准入、原锚等待和撤单规则；RF_ASK表示供给补给，不是买方利多。

## 来源与交接

[primitives.py](../../research/maker_signals/primitives.py)；[feed.py](../../research/maker_signals/feed.py)；[LEGACY_COMMON.md](../../research/signals/LEGACY_COMMON.md)。来源hash在首个JSON中冻结。

相关定义：[原语索引](PRIMITIVES.md)、[旧版公共约定](LEGACY_COMMON.md)、[AP](P_AP.md)、[AA](P_AA.md)、[RF](P_RF.md)、[EX](P_EX.md)、[DF](P_DF.md)、[CA](P_CA.md)、[BA](P_BA.md)、[LW](P_LW.md)。本任务未运行检测器、信号测试、回测或模拟；未修改采集、适配器或交易实现。

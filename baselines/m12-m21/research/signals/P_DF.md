# DF 防守衰退

本次仅补齐独立原语文档；原定义/实现存在不代表当前部署或已验证收益。

```json
{
  "document_type": "orderbook_primitive_sop",
  "primitive_id": "DF",
  "version": "df-m4-frequency-ask-v2-sop-v1",
  "status": "DOCUMENTED_FROM_SOURCE",
  "origin": "EXISTING_PRIMITIVE_DOCUMENTATION_NOT_NEW_EDGE",
  "source_profile": "m4-frequency-v2 / M4FrequencySignals ASK component",
  "inherits_execution_contract": false,
  "upstream_definition": "m4-frequency-v2",
  "sides": [
    "ASK"
  ],
  "consuming_direction": "BUY",
  "round_window_ms": 500,
  "round_confirmation_lateness_max_ms": 50,
  "round_minimum_shares": "0.5",
  "round_minimum_depth_fraction": "0.0001",
  "reference_rounds": 1,
  "reference_stable_minimum_recovery": "0.50",
  "reference_ttl_ms": 15000,
  "weak_rounds": 1,
  "weak_minimum_loss_fraction": "0.05",
  "normalized_hit_ratio_range": [
    "0.10",
    "10"
  ],
  "normalized_loss_ratio_range": [
    "0.10",
    "10"
  ],
  "weak_recovery_maximum": "0.60",
  "weak_reference_recovery_fraction_maximum": "0.80",
  "current_ttl_ms": 5000,
  "exit_on_stable_new_round_recovery_at_least": "0.50",
  "reference_consumed_by_next_qualified_round": true,
  "clear_old_df_before_compare_and_create": true,
  "mirror_bid_profile_implemented": false,
  "source_files": [
    "src/polymaker/m4_frequency_signal.py",
    "src/polymaker/m4_rounds.py",
    "src/polymaker/maker_queue.py",
    "research/signals/M4.md"
  ],
  "source_sha256": {
    "src/polymaker/m4_frequency_signal.py": "a02dd229df64924ddca45e4138679fe0f4a65a5668f4a1738617af115378025d",
    "src/polymaker/m4_rounds.py": "bee3ff08ffb48395e3a9e575a2f2d3250521f7744a156e5f1710f1a68b6601a3",
    "src/polymaker/maker_queue.py": "0b3154610d65bd47ab17e91e326d02ef28591bf99674485a5b7787f5357fb58e",
    "research/signals/M4.md": "8008cba93e8e1d73c5fde67b20fa9baa80da2e0ed44f1687e0ccc8750d20740e"
  },
  "implemented_by_this_task": false,
  "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

## 定义与主版本

DF_ASK记录已有恢复参照之后，在可比冲击下Ask恢复转弱。本主版绑定M4当前文档选择的m4-frequency-v2：一个稳定参考轮加下一个可比弱轮，确认后5秒；不是原工具箱两参考加两弱轮。

与LW的区别是这里需要真实BUY受击与历史恢复基准；LW的残余减量并不要求恢复参照。DF_ASK本身不提供Bid承接，M4组合还需要单独的Bid支撑。

## 底层轮和参考

使用M4 FixedRounds同原档500ms轮、直接BUY、Q0/Qmin/H；L=Q0−Qmin，r=clip((Qend−Qmin)/L,0,1)，u=H/Q0，d=L/Q0。合格门槛H和L均≥max(0.5,0.0001Q0)。

稳定采用旧滞回：r≥0.50启动、100ms不低于0.40获得held；新低重置计时与held，r<0.20撤销held；封口还须r≥0.50。不等于COMMON末100ms都≥0.50。

第一份合格稳定Ask轮建立参考，冻结其u_ref/d_ref/r_ref和ID，参考deadline=c_ref+15秒。同一原档观察命名空间；原档消失重建、质量或generation改变即清空。这个M4原档不强制一直是最优Ask。

## 下一合格轮的比较与确认

下一份合格Ask轮会消费参考，不能跳过不利轮等待更好样本。该轮须在参考未到期时可得，且：

- u/u_ref和d/d_ref分别在[0.10,10]。
- d≥0.05。
- r≤theta=min(0.60,0.80r_ref)，等号通过。

全部满足，在该轮实际封口可得时生成DF，deadline=c+5秒。无须弱轮本身不稳定：r=0.55且稳定，在r_ref=0.80时仍可能满足相对减弱。若比较失败，只消费参考，不凭失败自动结束一份仍有效的旧DF；旧DF按下面的独立退出规则判断。不合格轮不会进入此消费步骤。

若当前轮稳定且r≥0.50，还可成为下一份新参考；先比较旧参考再设新参考，不能将本轮和自己比较。

## 当前态、退出及顺序

每次处理新合格轮，先处理期限；若旧DF存在且新轮稳定、r≥0.50，先清掉旧DF；然后才用旧参考比较当前轮，可能生成新的DF ID，最后再建立新参考。新事实的5秒不是旧事实续期。锚消失/重建、数据失效以及deadline等号到期也结束。

主版恢复退出阈值0.50与确认允许的最大0.60存在交集，这是已有状态顺序，不在整理中修改。M4文件另有m4-relative-exit-v1显式备选，按冻结theta恢复退出，不能拿该备选替代本主版。

本原语没有COMMON弱供固定数量上界；不得静默加进去。底层来源FixedRounds在封口时读取当前book量，其depth处理没有research Rounds相同的received<end过滤；如复现来源，须保留原事件/封口调度与源码身份。将其改成严格事件窗末态是另一个版本，不能宣称历史样本等价。

## 时序、例子与历史差异

两轮连续时最少约1秒形成，新弱轮也可能在参考的15秒期限内较晚出现。5秒是DF确认后寿命；M4 Bid单轮支撑的8秒是另一个组件寿命，不是DF确认时间。

参考r_ref=0.80，则theta=0.60；下一轮d=0.10、两个强度比均1、r=0.55可确认。d=0.01即使r=0也因可见损失过小不确认。

原工具箱DF_BID/ASK采用750ms轮、最近两稳定参考中位r≥0.70、参考相对强度取中位；随后5秒内连续两合格轮强度比[0.5,2]、r≤min(0.30,0.50r_ref)才确认，寿命1秒，后续稳定r≥0.70退出。原型与旧M4两加两实现的轮长也不同；须绑定各自来源，不能与本版混池。

## 数据、输出与组合

输出原档、参考/弱轮ID、各轮Q0/H/L/r/u/d、theta、参考期限、c/deadline、状态替换原因及数据质量。M4来源直接成交、非严格最优档的原档跟踪，与共享RF/最初AA触及档规则不同；主版只声明Ask已有实现，Bid对称推广须另定版本。

Bid支撑 + DF_ASK可表达需求恢复尚好而供给恢复转弱；既有支撑和新DF必须实际重叠，组合截止取较早原期限。没有Bid证据时，DF不能直接冒充完整Maker策略。

## 来源与交接

[m4_frequency_signal.py](../../src/polymaker/m4_frequency_signal.py)；[m4_rounds.py](../../src/polymaker/m4_rounds.py)；[maker_queue.py](../../src/polymaker/maker_queue.py)；[M4.md](../../research/signals/M4.md)。来源hash在首个JSON中冻结。

相关定义：[原语索引](PRIMITIVES.md)、[旧版公共约定](LEGACY_COMMON.md)、[AP](P_AP.md)、[AA](P_AA.md)、[RF](P_RF.md)、[EX](P_EX.md)、[DF](P_DF.md)、[CA](P_CA.md)、[BA](P_BA.md)、[LW](P_LW.md)。本任务未运行检测器、信号测试、回测或模拟；未修改采集、适配器或交易实现。

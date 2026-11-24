# 第3批 M7 / M9：信号测试模块交接说明

日期：**2026-11-17，Asia/Shanghai**；修订v2。当前入口为`M7_PAUSE_RELEASE`与`M9_EX_VALIDITY_OVERLAP`，各有独立新版SOP，直接旧分支保留。统一语意为**信号有效期间在固定锚价成交**，Maker/Taker单列。M9首SELL提前交接未实施。

批次只是后续模块核对顺序，不表示已启动，也不要求前批先完成收益验收或24小时测试。

```json
{
  "document_type": "signal_test_handoff",
  "version": "batch3-m7-m9-handoff-v2",
  "date": "2026-11-17",
  "timezone": "Asia/Shanghai",
  "status": "DOCUMENTATION_ONLY_NOT_TESTED_OR_STARTED",
  "batch": 3,
  "signal_contract": "fixed-anchor-signal-v1",
  "execution_contract": "fixed-anchor-execution-v1",
  "entries": [
    {
      "strategy_id": "M7",
      "variant_id": "M7_PAUSE_RELEASE",
      "version": "m7-total-pause-released-reattack-v1",
      "sop_file": "V_M7_PAUSE_RELEASE.md",
      "sop_sha256": "21fffffb96001acac2264bd3b0cd6f042b87456448780ce61bf4ba645d5b2acc",
      "parameters_source": "FIRST_JSON_BLOCK",
      "parameters_sha256": "904a7e3f896dfa75ad12cca7d2c4a3686f9c1b79c372bc8189940d297cb286d5",
      "baseline_version": "m7-pause-then-parallel-support-reattack-v1",
      "baseline_definition_file": "V_M7_PARALLEL_CONFIRM.md",
      "baseline_definition_sha256": "8cbd42c347af6ce37b51a2f58bad49fad784ff7889e2b9c9b3963db87633f0d7",
      "upstream_baseline_version": "m7-local-ap-short-pause-v7",
      "basis_file": "../signal_discovery/spec_archive/M7_M10_M13_DISPOSITION_BASIS_20261116.json",
      "basis_sha256": "1e7d8dcbd850636d29aa707eb7437b1d8e77fb216d5cdc9e5d424ad34ed07107"
    },
    {
      "strategy_id": "M9",
      "variant_id": "M9_EX_VALIDITY_OVERLAP",
      "version": "m9-continuous-ex-overlap-handoff-v1",
      "sop_file": "V_M9_EX_VALIDITY_OVERLAP.md",
      "sop_sha256": "0af9c4415088218669a2f6bdf15827f66203d3123dde5703ba174c1f6c149122",
      "parameters_source": "FIRST_JSON_BLOCK",
      "parameters_sha256": "8d28d14ca90520e21d08080494ba9f6b2ab173f88557ed5e781f082c82234609",
      "baseline_version": "m9-ex-history-handoff-v1",
      "baseline_definition_file": "V_M9_EX_HANDOFF.md",
      "baseline_definition_sha256": "56d68b292e3a2a7e5883171af3eaaa56f814bd9c5d54b0842fcc4d757c85aefa",
      "upstream_baseline_version": "m9-local-ex-partial-support-retest-v8",
      "basis_file": "../signal_discovery/spec_archive/M9_M12_FREQUENCY_BASIS_20261116.json",
      "basis_sha256": "03ce596aa7a330f63e177748033c809bc7a0720cc6c307beb5ceccf782f43b75"
    }
  ],
  "original_sops_changed": false,
  "new_sop_variants_added": 2,
  "adapters_implemented": false,
  "tests_started": false,
  "runtime_changed": false
}
```

`parameters_sha256`针对首JSON，按UTF-8、ensure_ascii=false、sort_keys=true及紧凑分隔符序列化；全文另核对sop_sha256。先读本交接，再读完整新SOP、直接旧分支、其冻结basis与共用计算合同；新SOP明确覆盖项优先。所有入口为SPEC_ONLY、EXPLICIT_ONLY、adapter=null，未支持应报告未适配，不静默套主版或旧分支。

本交接中的场景是定义预期，未运行信号测试、回放、模拟或适配器。只留小型摘要和证据引用，不生成行情或逐tick日志。旧结果绑定旧版本，不改名成新版结果，不预测信号数量、胜率或PnL。

## 1. 入口与版本边界

| 项目 | M7 | M9 |
|---|---|---|
| 最新SOP | [V_M7_PAUSE_RELEASE.md](V_M7_PAUSE_RELEASE.md) | [V_M9_EX_VALIDITY_OVERLAP.md](V_M9_EX_VALIDITY_OVERLAP.md) |
| 直接保留旧版 | [V_M7_PARALLEL_CONFIRM.md](V_M7_PARALLEL_CONFIRM.md) | [V_M9_EX_HANDOFF.md](V_M9_EX_HANDOFF.md) |
| 本次两项变化 | 暂停单桶35%诊断化；暂停后解除冻结AP1占用 | EX确认即建3秒代理；EX单桶30%诊断化 |
| 上游形成口径 | 冻结v7私有AP、100ms/10ms/40%支撑 | 冻结v8的500ms/10ms/20%支撑及复验 |
| 保持的核心 | 暂停后新AP2、S/AP2当前交集、原截止 | q仍为H/L双资格；q同输入50%先退出，q后95% |

[M7较早交接](HANDOFF_M7_PARALLEL_CONFIRM_SIGNAL_TEST.md)、[M9较早交接](HANDOFF_M9_M12_SIGNAL_TEST.md)及本批v1仅绑定旧版，不能覆盖本版明确变化。目录主版、外部调参版及旧V的结果均保留原身份。

两者沿各自冻结feed的已核验互补映射与去重；不能照搬M21仅直接成交，也不能双计镜像。received用于统计窗，K=(available,input_seq,subindex)用于实际可得顺序，完整原始frame后才发布。质量、来源、覆盖、原档或因果次序未知仍UNKNOWN，不补零、不加另一套received跨阶段门槛。

## 2. M7：差异、顺序与期限

原两条链仍为`AP1→新S`和`AP1→pause→新AP2`。S可以早于暂停完成，可以与AP2并行，也可晚于AP2确认，只要联合时两者当前有效；不要求统计窗正时长重叠。

- **暂停**仍从c1观察完整500ms，总BUY≤0.50V1。两个250ms桶各≤0.35V1只诊断，不早退、不阻断历史确认。必要成交覆盖不能省略。
- **新观察资格**：cp/Kp之后，唯一M7私有BUY形成槽空闲且其余守卫有效时，首个全新BUY可开始AP2，即使冻结AP1还在原750ms寿命内。仅忽略冻结AP1占用；不删除AP1、不延长它，不绕过其他当前事实或已有watch。
- **不重复开窗**：这次输入由同一个本分支形成槽消费；旧自动观察路径不能再开第二份。AP1自然到期不取消合法新watch。AP2首笔仍须严格晚于Kp，不切窗、不补用暂停内成交。
- **AP资格不变**：750ms，V≥max(1,0.0001D)，份额≥65%，至少两笔、至少两个250ms桶且末桶有量，最大单笔≤95%；确认后750ms有效，原反向AP及质量退出保持。没有增加共享AP的滚动重检。
- **S不变**：首个AP1后可匹配SELL冻结P，100ms轮、m=max(0.5,0.0001Q0)，40%/10ms滞回（32%清计时、16%撤held、新低清两者）。未合格轮可按旧规跳过；任一合格失败轮结束；首个成功S冻结8秒，不换ID或续期。
- **联合确认**：首次完整边界c，`c<min(cS+8s,c2+750ms,c1+20s)`。不从联合点重新计时，AP2不能转历史再等S，第一合法合格AP2不换AP3。15秒来源上限等其余规则保持。

| 编号 | 重点场景 | 本版预期 |
|---|---|---|
| M7-01 | V1=100，暂停桶40/5 | 总量45通过，单桶诊断false；不能沿旧35%路径结束 |
| M7-02 | 暂停总量=50／>50 | 等于允许，大于结束；诊断化没有删除整体回落 |
| M7-03 | cp=0.50，AP1到期0.75，新BUY=0.55 | 可开唯一AP2 watch，完整到1.30确认，不等AP1到期 |
| M7-04 | 暂停未确认、首笔键≤Kp、或顺序不明 | 不使用占用例外；旧窗不能重新贴再攻标签，未知不推定合格 |
| M7-05 | 已有watch或另一个非AP1同向当前事实 | 原阻挡仍在；不得双开、清窗或复用成交 |
| M7-06 | AP1在新watch形成期间自然到期 | 不因历史AP1到期取消；质量/历史真实性反证仍结束 |
| M7-07 | S早于暂停完成、晚于AP2开始或确认 | 均允许，只在S/AP2共同有效的完整边界确认 |
| M7-08 | AP2先完成，S恰在d2才可得 | 到期先处理，不发布、不补750ms |
| M7-09 | 未合格支撑轮／合格恢复失败轮 | 前者按旧规跳过不换P；后者立即结束，不等好轮救活 |
| M7-10 | 暂停前反向AP／暂停后尚无AP2／AP2已确认后反向AP | 分别结束root／不单因反向AP删暂停历史但继续守S／结束所需AP2及root |
| M7-11 | 同frame先S或AP2成立，后原P丢失或质量失败 | 最终不发布；不得抢先执行 |
| M7-12 | 暂停单桶诊断缺失／必要500ms覆盖缺失 | 前者不独立阻断，后者UNKNOWN；原AP分桶资格未诊断化 |

## 3. M9：差异、连续维护与资格交接

本版局部EX必须按新规则计算：AP_SELL保持原750ms资格；其后完整750ms EX窗总SELL≤0.49V0，三桶各≤0.30V0只诊断。不能继续使用旧30%过滤器产出的稀疏EX事实再声称入口已放宽；共享EX原语不改。

**代理从EX实际确认cEX开始，而非从配对开始。** 冻结EX/AP/V0/generation和dEX=cEX+3s；从当时完整滚动buffer持续检查50%卖量界、新AP_BUY或另一AP_SELL量≥50%等旧硬退出、历史真实性和质量。原生750ms仅自然到期可由它承接；曾失效、缺连续记录或退出原因不明均不可配对、补建或复活。

S仍在cS+6s内有效。S与局部代理任一先形成，首次可引用且两者有效时按旧顺序配对，`cSeed<dSeed=min(cS+6s,cEX+3s)`；原生EX此时可以已自然到期。一个S.id|EX.id只试一次，单活动root、忙时已见不得回选、20秒组合跨度均保留。配对不重新给3秒。

种子cursor后的首个新P SELL开固定500ms R。**q仍为首次因果可得H/L同时≥m=max(0.5,0.0001Q0)**，q严格早于dSeed及dForm=cSeed+8s。触发q的同输入先更新流量并执行交接前退出，SELL达到50%即结束；首SELL、仅H达标、仅L达标或未来合格都不能提前交接。

合法q之后旧EX才作历史，市场阶段变化/自然到期不独立否决；原档、质量、历史真实性、新R以及全token滚动750ms SELL<95%仍守。第一R须完整20%/10ms滞回复验，封口不合格即结束，不跳轮。确认cR后`d=min(cR+8s,cAP+20s)`，dForm不再作为额外当前截止。q不重置原时钟或滚动buffer。

| 编号 | 重点场景 | 本版预期 |
|---|---|---|
| M9-01 | V0=100，EX三桶35/5/5 | 总45≤49可确认局部EX；30%标签false不能否决或提前丢窗 |
| M9-02 | EX总量=49／>49；反向AP在形成中出现 | 前者可继续，超49或原反向AP反证仍失败 |
| M9-03 | cEX=0，原生0.75到期，S在1.20确认 | 代理连续有效可配对；dSeed=min(7.20,3)=3，不是4.20 |
| M9-04 | 0.90已触发50%退出，1.20当前快照重新很弱 | 已终止，不能补建/复活，不只审配对时快照 |
| M9-05 | 0.75以后维护记录不可证，S才出现 | UNKNOWN/UNAVAILABLE；不能把缺记录当持续无卖盘 |
| M9-06 | S先确认、EX后确认／cSeed=dSeed | 有效交集允许；等于截止拒绝；没有新1.5秒或750ms配对上限 |
| M9-07 | 原生TTL清理带走局部代理或其必要buffer | 不符合本版；当前代理独立维护至原3秒，禁止无界保存 |
| M9-08 | 首SELL或仅H/L之一达标 | 尚未交接，继续50%等旧规则；不得提前改成历史 |
| M9-09 | 触发H/L资格的同输入令SELL达到50% | 旧退出先发生，结束，不先交接救活 |
| M9-10 | q前40%合格，q后升至70%／95% | 70%可在其他守卫合格时继续；95%等号结束，滚动窗含q前及其他档SELL |
| M9-11 | q=dSeed或dForm；cR=dForm | 到期等号优先，不交接/不确认 |
| M9-12 | q后仅旧EX到期或正常市场阶段退出 | 不独立结束；所有on_fact/source-end/代理/引用路径同一阶段政策 |
| M9-13 | q后旧AP/EX形成被证伪、P消失或质量失败 | 仍结束，不能笼统忽略HARD_INVALID |
| M9-14 | 原3秒代理结束/清理，但root已合法q转历史 | 结束新配对资格；保留必要历史引用与root当前维护至原截止，不取消已合法交接root |
| M9-15 | 第一R未合格／恢复失败；后续未合格轮 | 第一R结束不跳过；后续未合格轮按旧规不自动否决，其他守卫继续 |
| M9-16 | q后旧S纯到期／旧S已记录硬反证 | 前者允许新R接续；后者仍结束，不替换历史S |

## 4. 最小诊断与残留门槛排查

| 对象 | 需要区分的摘要与引用 |
|---|---|
| M7 | AP1/pause/S/AP2身份、Kp、首笔与唯一watch、暂停总量及35%标签、是否用AP1占用例外、c1/cp/cS/c2/c/d、各退出阶段 |
| M9 | AP/EX/S/R身份、三桶/49%总量/30%标签、cEX/dEX/cSeed/dSeed/dForm、从EX确认起维护范围、原生退出原因、q的H/L与50%审核、cR/d、95%滚动量 |
| 共通 | 诊断false/null与必要证据UNKNOWN分开；不能在预筛、形成器、发布、回调、清理及候选计数中保留旧隐式筛选 |

M7从“两个小窗分别受限”变为“整体回落后即可观察再攻”；M9从“必须赶在原生750ms内相遇”变为“与连续维护中的3秒衰竭证据相遇”，并允许EX桶量分布不均。两者都没有删除阶段顺序、必要当前证据、数量来源或原截止。

## 固定锚价执行与交付边界

统一目标为信号有效期间在固定P成交，按[固定锚价合同](FIXED_ANCHOR_CONTRACT.md)。Maker按真实post-only准入等待；Taker仅在P有真实可主动取得的对手量且信号有效时准入。原Bid正量/数量守卫保留，正常未交叉同token盘口下同P Taker买入没有共同准入状态；不能改价、删守卫或忽略原档消失制造成交。

400ms发送余量、金额、市场时段、队列、提交次数及取消延迟属于独立执行配置；信号寿命短于执行余量应记录已确认但执行受限，不能删去信号或延长寿命。失效先于同边界新准入，撤单请求不等于取消完成；真实部分成交、在途成交、实际价格与顺序未知均保留。不自动切换方式、重发、补单、续期或定义平仓。

反馈至少包含variant/version、SOP/参数/basis hash、实际输入/实现身份、原始事件引用、各时间与输入键、当前所缺证据、确认/退出原因及阶段、执行方式及真实归属。缺事实不补零。单活动root、首选事实和占用变化意味着放宽条件不保证全局频次单调增加，结算优势也未验证。

本次只交付文档与索引；M4、M6、M16及全部旧SOP、原语和共用合同保持，未启动任何测试、采集或进程操作，每小时汇报继续关闭。

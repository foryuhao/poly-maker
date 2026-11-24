# 第1批 M12 / M23：信号测试模块交接说明

日期：**2026-11-17，Asia/Shanghai**，交接修订v3。本批现交接 `M12_VALIDITY_OVERLAP` 和 `M23_FIRST_SELL_WHILE_VALID` 两个明确版本。M12沿用重叠版；本轮M23新增首SELL750ms仅诊断版，保留其原主版及两份旧分支。共同语意为**信号有效期间在固定锚价成交**，Maker/Taker单列。优先解除非必要的额外时间筛选以取得可评价样本，不额外加入新的降频条件。本次只更新文档与显式入口，不启动测试、回测、模拟、适配开发、采集或运行进程。

```json
{
  "document_type": "signal_test_handoff",
  "version": "batch1-m12-m23-handoff-v3",
  "date": "2026-11-17",
  "timezone": "Asia/Shanghai",
  "status": "DOCUMENTATION_ONLY_NOT_TESTED_OR_STARTED",
  "batch": 1,
  "signal_contract": "fixed-anchor-signal-v1",
  "execution_contract": "fixed-anchor-execution-v1",
  "entries": [
    {
      "strategy_id": "M12",
      "variant_id": "M12_VALIDITY_OVERLAP",
      "version": "m12-live-support-weak-ask-overlap-v1",
      "sop_file": "V_M12_VALIDITY_OVERLAP.md",
      "sop_sha256": "99c100e5d03e51f648a62f63090f5b45cbc38e31864086bac2c76effccf7b1e0",
      "parameters_source": "FIRST_JSON_BLOCK",
      "parameters_sha256": "dec8201b4b49bd91de9347c79aaeed8ef13c87d8a14a0f6813942e9e75d37c7e",
      "baseline_version": "m12-shock-comparable-response-divergence-v1",
      "baseline_definition_file": "V_M12_SHOCK_COMPARABLE.md",
      "baseline_definition_sha256": "ff39c591ea860c392b2e6e4416b85daaade4467f7e1d7082d161239b3ddfe056",
      "basis_file": "../signal_discovery/spec_archive/M9_M12_FREQUENCY_BASIS_20261116.json",
      "basis_sha256": "03ce596aa7a330f63e177748033c809bc7a0720cc6c307beb5ceccf782f43b75"
    },
    {
      "strategy_id": "M23",
      "variant_id": "M23_FIRST_SELL_WHILE_VALID",
      "version": "m23-buffer-first-sell-while-valid-v1",
      "sop_file": "V_M23_FIRST_SELL_WHILE_VALID.md",
      "sop_sha256": "498e3d1af5e710e6c79e82a7836282ac61cb7e320f67de081d22cc70b80f1b09",
      "parameters_source": "FIRST_JSON_BLOCK",
      "parameters_sha256": "72a82c851bbb00008f7e6b0bfb97209c87bbe850e04b82aaddb59cc0fbf6ea55",
      "basis_file": "../signal_discovery/spec_archive/M7_M23_FREQUENCY_IMPLEMENTATION_BASIS_20261117.json",
      "basis_sha256": "510f0315d67092a6e42d624def47f5df7aedb7a8fa04706fbe097068a231a762",
      "baseline_version": "m23-buffer-retention-no-loss-cover-v1",
      "baseline_definition_file": "V_M23_BUFFER_NO_LOSS_COVER.md",
      "baseline_definition_sha256": "3f85aba4805729bdc64570777b9729446c18161b7a850c2da6d39b5df4443944"
    }
  ],
  "sops_changed": false,
  "new_sop_added": "V_M23_FIRST_SELL_WHILE_VALID.md",
  "adapters_implemented": false,
  "tests_started": false,
  "runtime_changed": false
}
```

## 1. 入口、版本及旧交接的适用范围

| 项目 | M12 | M23 |
|---|---|---|
| 本批唯一SOP入口 | [V_M12_VALIDITY_OVERLAP.md](V_M12_VALIDITY_OVERLAP.md) | [V_M23_FIRST_SELL_WHILE_VALID.md](V_M23_FIRST_SELL_WHILE_VALID.md) |
| 别名 | 承接仍在、弱供未复 | 托在再验，首卖750ms仅诊断 |
| 直接改动基线 | [V_M12_SHOCK_COMPARABLE.md](V_M12_SHOCK_COMPARABLE.md)，上游数值为frequency-revision-3 / raw-path-r3 | [V_M23_BUFFER_NO_LOSS_COVER.md](V_M23_BUFFER_NO_LOSS_COVER.md)，`m23-buffer-retention-no-loss-cover-v1` |
| 保留原主SOP | [M12.md](M12.md) | [M23.md](M23.md) |
| 组合 | 两侧可比真实冲击形成承接/弱供证据；各自原有效期重叠时确认 | 预加托留存→合法首SELL→完整受卖验证，缓冲仍保留 |
| 当前维护 | Bid floor与Ask恢复上界、后续合格轮反证、质量及期限 | 固定缓冲floor、原档、质量及种子原期限；不加RF退出 |

读取顺序为本说明→对应完整SOP→各自冻结基线→[COMMON计算合同](COMMON.md)与[固定锚价合同](FIXED_ANCHOR_CONTRACT.md)。形成规则以选定SOP的明确覆盖项为准；COMMON中的旧执行参考不反向变成两者共同的信号门槛。M23明确覆盖了RF轮资格与RF稳定退出，不能继承M12的H/L双门槛。

`parameters_sha256`针对首JSON按UTF-8、`ensure_ascii=false`、`sort_keys=true`、紧凑分隔符序列化计算；全文另验`sop_sha256`。catalog中条目即使仅列文件和全文hash，也须读取该文件完整参数，不以缺少内联参数为由使用主版默认值。两版都是`EXPLICIT_ONLY`、`adapter=null`；文件存在不代表模块已经适配，不支持时应明确报告，不静默回落到旧版。

以下共用文件身份在交接时核对：

| 文件 | SHA256 |
|---|---|
| COMMON.md | `c3ec4443e8651e8a6f685309bdd6142166bada3de6c0846d3c18531cdc803645` |
| FIXED_ANCHOR_CONTRACT.md | `9dd080082d2d74035105e010d4b43afdc7d8a9a6e52420d81b2908b28dcad3d2` |

[旧M9/M12交接](HANDOFF_M9_M12_SIGNAL_TEST.md)中的M12绑定保留的SHOCK_COMPARABLE，仍有1.5秒硬上限；当前第1批不包含M9。[旧M21/M23交接](HANDOFF_M21_M23_SIGNAL_TEST.md)中的M23保留H≥L和首SELL750ms，本文v2所指NO_LOSS_COVER虽删H≥L仍保留750ms，两者均不能原样用于本批最新M23。此前开发、验收或假设收益不改贴为本批结果。本轮只在原第1批位置更新M23文档入口，M12及批次顺序不变；批次不自动启动任务，也不要求前批先完成收益或24小时验收。当前来源见[本轮设计落实](M7_M23_FREQUENCY_DISPOSITION_20261117.md)与[索引](README.md)。

## 2. M12：区分数值基线、去损失比版和本次重叠版

目录主版的Ask30%/5%、750ms配对及0.25–4比值，已在冻结第三轮变为40%/2%、1500ms及0.10–10。先前SHOCK_COMPARABLE只删除损失比与派生Bid下限；本次在该版上进一步将独立1500ms配对限制降为诊断，形成数值、各自3秒TTL和数量守卫不变。

| 项目 | 冻结第三轮 | 保留的SHOCK_COMPARABLE | 本批VALIDITY_OVERLAP |
|---|---|---|---|
| 轮长、Bid稳定 | 500ms、末100ms恢复≥50% | 相同 | 相同 |
| Ask恢复/自身损失 | ≤40% / ≥2% | 相同 | 相同，材料性保留 |
| 组件确认间隔上限 | 1500ms | 1500ms | **无独立上限**；1500ms仅历史诊断 |
| 跨侧uA/uB | [0.10,10] | 保留 | 保留，两端含等号 |
| 跨侧dA/dB | [0.10,10] | 已删除，仅诊断 | 继续删除 |
| 派生Bid损失下限 | dB≥0.002 | 已删除 | 继续删除，仍保留LB≥mB |
| 组件TTL/当前守卫 | 各3s、Bid40%/Ask50% | 不变 | 不变，不从配对重计 |
| 组合语意 | 近时可比冲击的恢复差异 | 同左，允许损失不相近 | 已验证承接与弱供当前重叠，不宣称同期冲击 |

仍保留归一化输入冲击可比，不要求两侧绝对成交量/深度相等。先承接后弱供、先弱供后承接都允许；先后顺序及gap仅诊断，不要求两轮观察窗重叠。时间距离增加会减弱同期响应比较的解释力，但不删除连续有效性要求，也不重新做整轮验证来替代旧gap筛选。

## 3. M12：确认、配对与失效检查项

每侧500ms received窗`[t_i,e_i)`，同token直接SELL击原Bid P、直接BUY击原Ask A，P<A；全程固定档、generation及质量可用。计算`L=Q0−Qmin`、`m=max(0.5,0.0001Q0)`、`u=H/Q0`、`d=L/Q0`、`r=clip((Qend−Qmin)/L,0,1)`。两侧均H≥m、L≥m；Bid末100ms严格恢复≥0.50，Ask最终r≤0.40且d≥0.02。没有过去强Ask参考或额外AP门槛。

组件实际可得时刻cB/cA均不得晚于各自e+50ms，且发布前先审各自e之后的已知守卫路径。当前有效且u可比的组件按最早合法配对，**不再检查确认间隔≤1500ms**；同可得边界按`(较晚组件键,Ask键,Bid键)`排序，组件键为`(confirmed_available,input_seq,subindex)`。一个Ask组件只发布一个root，不能按更小gap、更新组件、更长余量、更小Bid损失或更大恢复差挑参考。

组合c为首个完整且两份事实可共同引用的实际发布边界，不能倒填；`deadline=min(cB+3s,cA+3s)`，要求c<deadline。

令`Δ=abs(cA−cB)`、`δ=c−max(cA,cB)`，剩余寿命为`3s−Δ−δ>0`。Δ<3s只是原期限推导的必要条件，不是新增“≤3s配对”开关；完整发布前到期仍不能确认。gap_ms、gap_le_1500ms及先确认侧仅诊断；不得在缓存清理、预筛选、引用结束或维护路径保留旧1.5s门槛。

| 编号 | 重点场景 | 应按SOP处理 |
|---|---|---|
| M12-01 | 双侧满足u可比，但d比超旧上限 | 允许继续核对；预筛选、通用comparable、配对、排序、发布、当前维护均不得残留d门槛 |
| M12-02 | Bid损失低于旧派生0.002或0.0125 | 不因此拒绝；仍须LB≥mB，零损失或无成交仍不合格 |
| M12-03 | Bid只有旧滞回held=true | 不能代替末100ms持续≥50%的严格路径；新低或低于50%按新稳定规则重置 |
| M12-04 | Ask rA=0.40、u比恰0.10或10 | 形成端点允许，仍检查材料性、保持、时序及所有守卫 |
| M12-05 | 配对间隔恰1500ms或超过1500ms | 数值仅诊断；只要实际c时两侧仍有效且其他条件合格就可配。早组件原3s继续消耗，不从组合点重计 |
| M12-06 | Bid `FB=QminB+0.40LB` | Qbid=FB仍有效，Qbid<FB立即结束组件及依赖候选；补回不复活 |
| M12-07 | Ask `FA=QminA+0.50LA` | **Qask≥FA即失效，等号也结束**；原Ask消失也结束，不将完全消耗解释为可继续追价 |
| M12-08 | e_i到c_i之间曾破FB/触FA，发布时又恢复 | 不发布该组件；之后变好不能掩盖已知路径失效 |
| M12-09 | 较晚处理的事件received仍在形成窗内 | 归形成计算；不得错套轮后guard。封窗后的迟到按冻结质量合同处理，不回填救活 |
| M12-10 | 组件等待配对时已失效，再出现对侧轮 | 已结束组件不可配；新完整证据按原规则另行处理，旧root不复活 |
| M12-11 | 后续合格Bid轮严格恢复失败，或Ask合格稳定恢复≥0.50 | 对应组件及组合结束；不合格轮不自动等于“恢复失败”，即时数量guard仍检查 |
| M12-12 | 新好轮、执行回执、等待对侧或模式切换 | 不替换已绑定组件或重计deadline；now=deadline先失效 |
| M12-13 | cB=0、cA=2.2s且全路径通过，c=2.2s | 允许确认，deadline=3s，剩余0.8s；两侧先后对调同样适用 |
| M12-14 | Δ=3s或实际c已到较早组件expiry | 到期等号先失效，不发布；即使Δ<3s，也不能跳过实际完整边界检查 |
| M12-15 | Δ=2.9s、δ=0且其他条件通过 | 可确认但仅余0.1s；若执行配置要求400ms则执行受限，不删信号、不延长期限 |
| M12-16 | 缓存在1.5s删除未失效组件，或按gap优先选较新证据 | 均残留旧筛选/改变首选规则，不符合本版；保留到原截止不等于延长TTL |
| M12-17 | 同frame中对侧刚确认，后行又令较早组件破界 | 完整frame失效优先，最终不发布；不能抢先配对 |
| M12-18 | 仅诊断标签缺失／确认键或连续路径不可证 | 前者不独立否决，后者仍UNKNOWN；不能借“仅诊断”跳过必要因果时钟 |

形成量界有缓冲：Bid终点至少高于FB的0.10LB；Ask r≤0.40时终点低于FA至少0.10LA。这个算术交集不免除e→c及当前路径检查。

**差异算例，仅用于定义核对：**两侧Q0=100、H=20，u比=1；Bid最低99、末量99.8，LB=1、rB=0.80；Ask最低80、末量84，LA=20、rA=0.20。d比=20，旧第三轮拒绝，本版在完整保持/来源/时序也合格时允许；FB=99.4、FA=90。若Ask在发布前曾达到90，仍拒绝，不能用删除d比覆盖供给恢复。

上述数量算例体现继承的去损失比效果；本次新增的时间路径见M12-13至M12-15。即使2.2s时快照合格，较早组件在1.8s曾破界又恢复，也不能配对。没有实际重受击的新证据，不会获得新的3s。

## 4. M23：本批取消首SELL750ms硬限制，先前数量放宽继承

M23最新版本在NO_LOSS_COVER上**只将独立首SELL750ms限制降为诊断**。BUFFER_RETENTION已取消最低损失及末段上限，NO_LOSS_COVER已删除H≥L；这些继承，不重复计为本次改动。首SELL前Fpre持续有效，之后完整500ms验证仍须在原cS+3s之前确认。

| 项目 | 原主版及先前变更 | 直接基线NO_LOSS_COVER | 本批FIRST_SELL_WHILE_VALID |
|---|---|---|---|
| 预加托净增 | Δ≥max(5,0.20B) | 保留 | 保留 |
| 种子保持及首SELL前 | 100ms、Q≥B+0.80Δ | 保留 | 保留 |
| 首SELL与验证 | 原主版/缓冲版均750ms内首SELL、500ms验证 | 保留 | **750ms仅诊断**，仍首笔、仍完整500ms、原3s内完成 |
| 真实SELL门槛 | H≥max(5,0.50Δ) | 保留 | **保留** |
| 最低可见损失 | 原L≥0.50Δ，缓冲版已删 | 已删除 | 继续删除，允许已知L=0 |
| 形成末段上限 | 原Q≤B+0.50Δ，缓冲版已删 | 已删除 | 继续删除，更多留存不拒绝 |
| 成交覆盖损失 | 原主版与缓冲版保留H≥L | 已删除，仅诊断 | 继续删除，仅诊断L/H、H−L |
| 缓冲底线 | 合法首SELL后F=B+0.20Δ | 保留 | 保留，不能提前切换 |
| 原期限 | cS+3s，从种子确认计 | 保留 | 保留，不从最终确认计 |
| RF恢复率/最小损失/后续RF退出 | 不采用 | 不采用 | 不采用，不能由共用模块暗中添加 |

H仍证明材料性真实挑战；F证明新增缓冲的一部分仍保留。L>H允许意味着减损可含撤量、补量及消息时序影响，本版不宣称损失主要由SELL造成。L/H仅诊断不等于精确成交冲击系数。

语意由“加托后迅速受挑战”改为“加托持续有效期间受挑战”。记录首SELL的received/available距cS的两种间隔；旧750ms标签要求两者均严格<750，等于或超过只记false，不筛选、排序、退出或重新选择root。待SELL计时器、缓存和引用结束回调不得残留750ms清理逻辑。

## 5. M23：分阶段确认和失效检查项

| 阶段 | 必须有的证据与数量条件 | 原时钟与可得边界 |
|---|---|---|
| 预加托 | 已存在原最佳Bid P由B增至B+Δ，Δ≥max(5,0.20B)；冻结原档、B/Δ、事件及数量来源 | 增量received=t0，不以新出现的更高价档为原P；后续增量不扩大Δ或重计时 |
| 种子形成 | 连续守Fpre=B+0.80Δ；从t0至实际cS含边界，P直接SELL存在性为零 | 100ms观察，水位及完整批次可得后发布；t0+100ms≤cS≤t0+150ms；种子尚不授权成交 |
| 首SELL交接 | cS后P上的首笔可信直接SELL；首笔前数量Q0R有更早输入键及严格更早source；Fpre之前一直有效 | received>cS、输入键>种子确认键，且received与available都<原D=cS+3s；750ms仅诊断。合法后才切F=B+0.20Δ，首笔很小也不跳过 |
| 新受卖验证 | `[tR,e)`，e=tR+500ms；H≥max(5,0.50Δ)，全已知路径Q≥F；L=max(0,Q0R−Qmin_observed)，不作L门槛 | 完整窗口后才可能确认；H提前达到不提前发信号；没有RF恢复要求 |
| 末量来源与发布 | 窗内明确P报量，输入键晚于全部计量SELL、source严格更晚、received<e；e到c路径仍守F | 完整原始批次后实际发布，e≤c≤e+50ms且c<cS+3s；缺末量不能延窗等未来 |
| 有效等待 | 只守F、P存在、质量/generation/tick与原期限 | deadline=cS+3s；不重新要求H、H≥L或RF，不因补量/回执续期 |

零SELL必须在直接来源未被镜像去重或方向过滤丢失前核对；只有深度覆盖或过滤后空集不能证明零成交。与t0同接收时刻、本批排在增量前面的SELL也在核对范围内。未知不填零；晚发现属于种子期的SELL在发现时结束，保留先前真实订单及成交，不倒删记录。

| 编号 | 重点场景 | 应按SOP处理 |
|---|---|---|
| M23-01 | price_change给出新总量 | Δ用前后量相减；不能把size当增量，不能用BBO/其他档刷新代替P报量 |
| M23-02 | 种子发布批次含首次直接接收归属[t0,cS]的P上SELL，或拟作新SELL的received=cS | 种子不成立/历史反证；不能先发布，再把同边界事件叫新SELL。已证明归属t0前或原始重发的事件按真实归属处理 |
| M23-03 | 首SELL的received或available=cS+750ms | 旧严格750ms标签false，但不因此结束；仍检查种子当前有效、完整来源与原D |
| M23-04 | 深度先破Fpre，随后SELL才可得 | 旧root已结束，不能切较低F或事后排序救回；顺序不可证为UNKNOWN |
| M23-05 | 首SELL合法且此前Fpre有效 | 从合法交接起改守F，允许受卖降至F；不继续叠加Fpre，但不能抹掉交接前的失败 |
| M23-06 | L>H、H达材料性且全路径守F | 允许继续核对；预筛选、轮次资格、发布、结束回调均不得残留H≥L |
| M23-07 | 已知完整路径L=0、H合格 | 允许；不调用COMMON中要求H/L均≥m的RF资格，不除以L算恢复率 |
| M23-08 | 缺数量路径，却把L设为0 | UNKNOWN，不允许；仅派生诊断缺字段可null，必需H/数量证据缺失仍结束 |
| M23-09 | 明确末量source与最后SELL相等，或末量received≥e | 不满足原严格后序/窗内证明；不能靠其他档更新或窗外快照补认 |
| M23-10 | 验证及等待Q=F；或Q<F后回补 | 等于有效；曾低于F立即失效，不因同批末态回补复活 |
| M23-11 | 完整500ms后，仅e→c之间曾破F | 不发布，即使窗内全部达标且当前又恢复 |
| M23-12 | 后续新SELL、补量或合格但不稳定RF | 本版没有重复H或RF退出；真实数量破F、质量/原档失效仍结束，不能用“无RF退出”豁免事实问题 |
| M23-13 | 候选确认较晚、执行等待或ACK返回 | 都不从c/ACK重给3s；now=cS+3s先结束，不回收旧增量或SELL建立新root |
| M23-14 | cS=0、首SELL1.20s、完整发布1.72s且全路径合格 | 可确认，D仍3s、余1.28s；0.75s不应已结束或释放root |
| M23-15 | 首SELLreceived=0.74s、available=0.76s | 其他来源/质量通过可继续，旧750ms双时钟标签false；不能仅按received记true |
| M23-16 | 首SELL2.40s、实际完整发布2.92s | 可确认且仅余80ms；若所选执行配置要求400ms，单列执行受限，不删信号 |
| M23-17 | 首SELL恰2.50s，或e<D但实际发布c=D | 到期等号先结束，不能缩500ms、回填发布或重计3s |
| M23-18 | 到0.75s无首SELL，或其后出现新加托 | 原root可等至D但仍守Fpre；新加托不替换B/Δ/cS，不重开root或提前切F |
| M23-19 | 首笔小SELL的第一窗H不足，之后大SELL才出现 | 第一窗失败，不移窗、不跳首笔、不用窗后成交补H；完整500ms仍必需 |
| M23-20 | 500ms验证后再等100ms，或隐藏保留1700ms最小余量 | 均不符合规格；末100ms是窗内同一F，无额外信号余量下限 |
| M23-21 | 750ms诊断字段无法导出／必要cS与事件键不可证明 | 前者null不独立否决；后者UNKNOWN，诊断化不放宽必要因果证明 |

**差异算例，仅用于定义核对：**B=100、Δ=50，则Fpre=140、F=110、最低H=25。合法首SELL前Q0R=150，验证H=30、最低及末量110时，L=40>H；先前缓冲版拒绝，本批在完整来源/路径/时间也合格时允许。若最低109则仍因破F失败；没有SELL而只留厚量也失败。若全窗数量不低于150，已知L=0且真实H达标，则继续核对，不凭无损失拒绝。

设s=tR−cS>0、δ=c−(tR+500ms)，仍要求0≤δ≤50ms及`s+500ms+δ<3s`，剩余为`3s−s−500ms−δ>0`。旧“1300ms内确认、至少1700ms余量”不再适用。约2.5s是完整500ms和原3s推导的必要边界，不另设2.45s/2.5s提前清缓存或400ms信号门槛；没有首SELL可守Fpre等待到原D。首SELL虽在D前，完整验证仍可能来不及，不能借机延窗或续期。

## 6. 两版不能混用的规则与执行限制

| 项目 | M12 | M23 |
|---|---|---|
| 材料性 | 两侧各有H与L下限 | 只有该验证窗真实H门槛；L=0或L>H均可 |
| 稳定 | Bid严格末100ms恢复≥50% | 不要求RF恢复；全程守绝对缓冲F |
| 供给证据 | 必须有原Ask弱响应及恢复上界 | 没有额外Ask方向确认，Ask只按输入质量/执行结构处理 |
| 等号 | Bid=FB有效；Ask=FA失效 | Q=F有效；750ms等号只令诊断false；原deadline等号仍失效 |
| 截止 | 两组件各自确认+3s，取较早 | 原种子确认cS+3s |

两版均要求只用当时已可得的信息，先处理同输入已知失效和到期，再发布/准入。received窗内合法事件归形成，迟到或无法排序按各自合同UNKNOWN；不读取未来封口后的数量来救活旧观察，不把无输入解释为零成交。只有旧stable或摘要不能冒充完整路径。

Maker在合法P以post-only等待；Taker持有待执行意向，只有信号当前可引用且P有真实可主动取得对手量才准入。两版都保留原Bid正量/正floor；在正常未交叉同token盘口下，与同价Taker买入没有共同准入状态。记录执行受限，不删除原档保护、不追价，也不把该限制报成“信号未确认”。

预算、每市场额度、T15–T240、400ms发送余量和等待上限属于显式执行配置；本交接不选取或修改它们。若采用历史配置，信号成立但发送余量不足仍应分开记录。确认、发送、ACK、部分成交、全部成交与官方结算不能互换；自有成交不追加一次承接证明，取消请求也不抹掉在途成交或非精确P成交。不自动切模式、补单、续期或定义新的平仓规则。

## 7. 后续模块的最小反馈口径

本节只是接口说明，本任务不生成测试样本、统计或日志。后续反馈应分策略/分支/版本及输入、执行模型，且保留完整场、局部UNKNOWN和隔离范围；同一批反复调参不能作为额外独立样本。

| 类别 | 建议保留的摘要字段 |
|---|---|
| 身份 | strategy/variant/version，SOP及参数hash，基线hash，共用与输入合同，实际实现身份，显式选择是否受支持 |
| 形成漏斗 | 观察开始、材料性可证、完整组件/验证窗确认、组合发布；已知不满足与UNKNOWN/未适配分别列示 |
| M12专用 | B/A轮与原档ID，Q0/H/L/u/d/r、严格保持路径引用，u比、仅诊断d比与gap_ms/gap_le_1500ms，先确认侧、发布时组件年龄/剩余寿命，eB/eA/cB/cA/c、配对排序键、FB/FA、各自expiry及原deadline |
| M23专用 | 原增量/种子/首SELL ID与输入键，B/Δ/Q0R/H/L/Qmin，零SELL范围及末量来源，Fpre/F、t0/cS/tR/aR/e/c/deadline、合法交接状态；received/available首SELL等待间隔、严格750ms对照、发布延迟/剩余寿命及L/H、H−L只诊断 |
| 失效 | 首个失效实际可得键、发生阶段、必要事实、数量/质量/期限原因；不将多项同时失败简单相加为独立root |
| 执行 | 信号有效与执行准入分开，mode/配置、发送与撤单状态、真实回执/模拟/假设身份、实际成交价及有效/失效后/顺序未知归属 |

应特别检查“删除条件的全部路径已移除”：M12的1.5s筛选/提前清缓存/隐含最小余量，以及已删除的d比/派生Bid下限；M23的750ms首SELL/计时器/缓存/结束传播/重启筛选、隐含1700ms余量，以及已删除的H≥L/最低L/末段上限及RF继承条件。优先放开非必要附加筛选、取得可评价样本，后续筛选另按用户模块证据决定；此交接不先加回时间或价格过滤。保留必要守卫，时间容差扩大不等于信号翻倍，旧拒绝次数不能直接当新增信号数，未测试机制不称已提高结算概率。

## 8. 本次交付范围

本次新增M23_FIRST_SELL_WHILE_VALID独立SOP，更新本交接、README、当前入口/原语映射索引及catalog文档登记。M12_VALIDITY_OVERLAP及本交接M12第2/3节保持；M23原主版、两份旧分支、其他SOP/原语/共用合同和历史结果均保留，M4/M6/M16不变。只替换第1批M23的文档跟进入口，批次顺序和其他策略安排不改。未启动测试、回测、模拟、行情任务、适配开发或任何定时器，未查询、变更或停止运行进程。以上为后续核对要求，不代表已完成测试或验证增频。

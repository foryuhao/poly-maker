# 第2批 M21：信号测试模块交接说明

日期：**2026-11-17，Asia/Shanghai**；修订v2。当前唯一入口改为`M21_ENDPOINT_COFLOW`。批次表示用户模块后续调试与核对优先顺序，不表示已启动，也不要求前批完成收益验收或24小时测试。交易语意为**信号有效期间在固定锚价成交**，Maker/Taker单列。

```json
{
  "document_type": "signal_test_handoff",
  "version": "batch2-m21-handoff-v2",
  "date": "2026-11-17",
  "timezone": "Asia/Shanghai",
  "status": "DOCUMENTATION_ONLY_NOT_TESTED_OR_STARTED",
  "batch": 2,
  "signal_contract": "fixed-anchor-signal-v1",
  "execution_contract": "fixed-anchor-execution-v1",
  "entries": [
    {
      "strategy_id": "M21",
      "variant_id": "M21_ENDPOINT_COFLOW",
      "version": "m21-endpoint-recovery-withdrawal-v1",
      "sop_file": "V_M21_ENDPOINT_COFLOW.md",
      "sop_sha256": "a7e36318bb3d0098c0a7c6a0425925c51d7dbdd9ba21eba7c79f54cd76b7ee0c",
      "parameters_source": "FIRST_JSON_BLOCK",
      "parameters_sha256": "f94e8d3b8c7c0e171294f2eec1d8ed664eae2da345b7fd32ff03ab997525f7f2",
      "baseline_version": "m21-single-joint-withdrawal-no-prequiet-v1",
      "baseline_definition_file": "V_M21_COFLOW_NO_PREQUIET.md",
      "baseline_definition_sha256": "0d4adbc48d17efd5595902d460104d48c80ac53e272adde8a32b2bfb9d8e115a",
      "basis_file": "../signal_discovery/spec_archive/M7_M23_FREQUENCY_IMPLEMENTATION_BASIS_20261117.json",
      "basis_sha256": "510f0315d67092a6e42d624def47f5df7aedb7a8fa04706fbe097068a231a762"
    }
  ],
  "original_sops_changed": false,
  "new_sop_variants_added": 1,
  "adapters_implemented": false,
  "tests_started": false,
  "runtime_changed": false
}
```

`parameters_sha256`针对首JSON，按UTF-8、ensure_ascii=false、sort_keys=true及紧凑分隔符序列化；全文另核对sop_sha256。先读本交接，再读完整新SOP、直接旧分支、其冻结basis与共用计算合同；新SOP明确覆盖项优先。所有入口为SPEC_ONLY、EXPLICIT_ONLY、adapter=null，未支持应报告未适配，不静默套主版或旧分支。

本交接中的场景是定义预期，未运行信号测试、回放、模拟或适配器。只留小型摘要和证据引用，不生成行情或逐tick日志。旧结果绑定旧版本，不改名成新版结果，不预测信号数量、胜率或PnL。

## 1. 入口、旧版与差异

最新完整SOP：[V_M21_ENDPOINT_COFLOW.md](V_M21_ENDPOINT_COFLOW.md)。直接旧版[V_M21_COFLOW_NO_PREQUIET.md](V_M21_COFLOW_NO_PREQUIET.md)、[单轮版](V_M21_SINGLE_ROUND.md)、[M21主版](M21.md)均保留。本说明v1绑定无前置平稳版；[更早交接](HANDOFF_M21_M23_SIGNAL_TEST.md)绑定单轮版，均不能替代当前规格。

| 条件 | 直接旧版COFLOW_NO_PREQUIET | 当前ENDPOINT_COFLOW |
|---|---|---|
| 首轮Bid材料性 | H≥max(5,10%Q0)，L≥max(0.5,5%Q0) | H≥5、L≥0.5；10%/5%仅诊断，不在首轮叠加隐式比例过滤 |
| Bid恢复 | 末100ms持续≥50%，封口≥50% | 封口≥50%必需；100ms仅诊断 |
| Ask本轮净减 | g=max(0.5,10%a0)，末100ms持续达标，封口D≥g | g及封口D≥g保留；100ms仅诊断 |
| 后续合格Bid轮 | 不严格稳定即结束 | 只因未保持100ms不结束；封口恢复<50%仍结束，实时F不豁免 |
| 后续轮材料性 | 沿COMMON基础资格 | 不变：H/L≥max(0.5,0.0001Q0_new)，不冒充已删的首轮10%/5% |
| 共同形成窗、来源、零BUY | 500ms，两侧末量在全部SELL之后，零BUY至实际发布 | 不变 |
| 前置平稳／第二轮 | 已取消／不存在 | 不变，不重复算成本次删除 |
| 当前数量界及期限 | 原F/U、c+3s | 不变，不换锚、不续期 |

语意从“双方在末100ms都稳定”变为“受卖后确有补回、当前供给净减，随后连续守住数量界”。接受刚恢复及相对初量较小的冲击，不称为COMMON严格稳定S，也不宣称同窗SELL导致撤供。仍是单档共同轮，未改成M6多档LW或任意旧组件相遇。

## 2. 完整过程及期限

1. **起点**：空闲首笔可信直接SELL冻结事件前P/A、Q0/a0及t0；P<A，两档正量。各初量的键更早、received不晚于SELL、source严格更早，连续更新链可证。固定500ms，不滑窗、不取过去更高a0。
2. **形成**：H≥5、L≥0.5、封口r≥0.50；Ask封口正量、D≥g。末100ms两侧标签和Bid10%/5%只诊断；前250ms平稳继续仅诊断。必要数量、质量覆盖缺失仍UNKNOWN。
3. **来源**：P/A各有窗内明确末量，键晚于全部计量SELL，source严格晚于最大SELL source。删除保持不删除末量证明。原A零BUY从t0至实际完整批次发布c（含边界），原始来源存在性不能在有损过滤后检查。
4. **发布**：首次合法完整边界e≤c≤e+50ms；e→c全路径守F=Qmin+0.40L、U=a0−0.50D、原档/质量/零BUY。中途破界再恢复不确认。
5. **等待**：d=c+3s，Bid<F、Ask>U、后续合格轮封口恢复失败、原档丢失、质量异常或now≥d结束。Bid=F、Ask=U允许。后续轮采用新端点恢复判定，不转发仅因旧100ms稳定不足的退出；真正硬反证保留，不笼统忽略HARD_INVALID。
6. **BUY阶段**：真正c后新BUY需received>c且键>确认键，本身不结束；后来发现归属received≤c的形成BUY则在发现时结束，保留真实成交。新轮/回执不续期，不要求后续每笔SELL都再使Ask净减。

## 3. 确认／失效重点检查

| 编号 | 场景 | 应有处理 |
|---|---|---|
| M21-01 | Q0=1000，H=5，L=1，Qend=999.6 | r=0.60，绝对材料性通过；10%/5%标签false不阻断，其余条件仍审 |
| M21-02 | 双侧仅最后20ms达标 | 100ms标签false仍可确认，不额外等100ms |
| M21-03 | H<5或L<0.5，或封口r<0.50 | 首轮失败；不能只看诊断已降级就通过 |
| M21-04 | g≥a0或aend≤0，或本窗D<g | 无正量解/失效/净减不足，不用前史净减代替 |
| M21-05 | 只有旧stable布尔值，无法证明端点或末量顺序 | UNKNOWN；既不沿旧stable否决，也不凭缺失补认 |
| M21-06 | 双侧末量缺一、在窗外、早于计量SELL或只有BBO | UNKNOWN/UNAVAILABLE，末量来源未降级 |
| M21-07 | 原始BUY被过滤，或发布批次后行有BUY | 反证形成；零BUY覆盖到完整c |
| M21-08 | e→c曾越F/U，发布时恢复 | 不确认；Bid=F、Ask=U本身允许 |
| M21-09 | 后续基础材料性合格，封口r≥0.50但100ms未稳定 | 不仅因此结束；当前F/U与原截止继续维护 |
| M21-10 | 后续基础材料性合格，封口r<0.50 | 结束，即使原F暂未跌破；这是新失败证据 |
| M21-11 | c后真正新BUY／晚发现形成BUY | 分别按当前guard／历史反证处理，不倒填取消时间 |
| M21-12 | c=e+50ms／now=d；新好轮或ACK | 前者可发布，等于d先到期；任何新事实不换F/U或续期 |
| M21-13 | 可选前史/诊断缺失，与当前必要覆盖缺失 | 前者null不阻断，后者UNKNOWN；两者不能混淆 |
| M21-14 | 首轮在发布时又被旧COMMON stable当成失败后续轮 | 不允许重复归属；所有消费路径使用本版端点判定 |

数量交集保留：Bid端点比F至少高0.10L，Ask端点比U低0.50D。此算术关系不替代e→c全路径检查。重新起点仍需received及键均晚于旧root结束的新SELL，不复用旧窗。

本分支反馈另需H/Q0、L/Q0、双侧100ms标签及可得性、a0/g/D/F/U、初量末量事件、e/c/d、零BUY证据、后续轮资格/端点及退出原因。既不让诊断不足阻断信号，也不把真实反证改成诊断。

## 固定锚价执行与交付边界

统一目标为信号有效期间在固定P成交，按[固定锚价合同](FIXED_ANCHOR_CONTRACT.md)。Maker按真实post-only准入等待；Taker仅在P有真实可主动取得的对手量且信号有效时准入。原Bid正量/数量守卫保留，正常未交叉同token盘口下同P Taker买入没有共同准入状态；不能改价、删守卫或忽略原档消失制造成交。

400ms发送余量、金额、市场时段、队列、提交次数及取消延迟属于独立执行配置；信号寿命短于执行余量应记录已确认但执行受限，不能删去信号或延长寿命。失效先于同边界新准入，撤单请求不等于取消完成；真实部分成交、在途成交、实际价格与顺序未知均保留。不自动切换方式、重发、补单、续期或定义平仓。

反馈至少包含variant/version、SOP/参数/basis hash、实际输入/实现身份、原始事件引用、各时间与输入键、当前所缺证据、确认/退出原因及阶段、执行方式及真实归属。缺事实不补零。单活动root、首选事实和占用变化意味着放宽条件不保证全局频次单调增加，结算优势也未验证。

本次只交付文档与索引；M4、M6、M16及全部旧SOP、原语和共用合同保持，未启动任何测试、采集或进程操作，每小时汇报继续关闭。

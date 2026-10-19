# RP-002：SL 恢复阻塞的三处修复

日期：2026-10-19。用户授权先修执行与诊断，再看策略。修复依据为[18:25–18:30 实盘复盘](../20261019_sl_execution_live_1825/README.md)。本轮没有启动或重启实盘进程，没有发送真实订单。

**修复及离线验收完成：最终完整重跑2,092项通过、2项跳过、0失败、0错误。** [完整日志](regression.log)、[JUnit](regression.xml)、[验证凭据](validation.json)。Ruff、`git diff --check`及config/livecfg只读配置检查通过。可进入下一场有人观察的单市场实测，实际修复后的实盘结果仍待该次测试确认。

## 修改

1. **单订单错误只退避该订单详情。** `rdp_sm_sync.py` 为每个 ID 单独记录重试次数与到期时间，保留失败的原始证据及未决状态。HTTP200无效订单、404、503等详情失败不再使整个orders lane退避；`/time`、完整订单列表、完整成交列表照常刷新。集合自身读取失败仍保留原退避，网络冷却与恢复门槛未改。
2. **SL及时消费严格的已撤提交证明，退役后台工作。** SL POST已结束，且响应完整匹配本次签名ID的`Already canceled`时，停止重复DELETE，但保留全部预占。后台获得本单写入边界之后的新鲜完整订单/成交页，才复用原`_verify_canceled_submission`证明路径确认未接受、释放占用，不等待最终全session关闭。非缓存路径也在独立后台执行相同证明。执行器证明成立后，后台删除该ID的详情要求并停止继续查无效详情；订单归属及观察历史仍保留。迟到订单/成交能重新排队并撤销拒绝证明，恢复资金/Delta预占。
3. **SL阻塞返回具体原因。** `admission_reason`区分网络暂停/恢复、资金、Delta、分页、盘口、时钟、隔离等，`allowed`复用同一判断。`sm_sl_wait`额外记录待执行目标的方向、价格、数量及盘口代次；同一原因但目标价量变化时也记录。原生POST前guard也记录相同具体原因，不再把非资金失败统一显示成Delta不足。

新增日志：`sm_sl_canceled_submission_proven`提供已撤证明的来源版本、写入边界及完整读取ID；`sync_detail_retry`记录单ID的退避。HTTP200无效详情或DELETE not found本身均不是资金/仓位证明。

## 保持的边界

没有修改NORMAL信号、HD/TP/SL阈值、TP/SL定价与持有策略、网络熔断参数、T120/T180/T240边界、Merge策略、20 USDC session额度、全在途Delta5及小残仓规则。只有严格证明成立才释放被拒提交；未知、成交冲突、FAILED和隔离仍保留事实约束。RP-001继续暂缓。

生产修改限于：`rdp_sm_sync.py`、`rdp_sm_cached_reads.py`、`rdp_sm_execution.py`、`rdp_sm_sl.py`、`rdp_sm_sl_ledger.py`。配置仅在验收完成后更新验证记录引用。

## 验证记录

新增 [test_repair_sl_recovery.py](../../../../tests/test_repair_sl_recovery.py) 20项已通过，[日志](new_tests.log)、[JUnit](new_tests.xml)：

- HTTP200 null/无效对象、404、503逐单重试不阻塞完整集合和健康时钟；正常订单仍能获得详情。
- 真实`NetworkHealth`在单ID无效详情持续存在时，可以满足原恢复规则进入PROBE；没有绕过冷却。
- 严格证明后后台任务退役，后续真实订单重新出现能恢复查询。
- 真实签名/SDK/网关/跟踪器配合模拟交易所I/O：已撤SL在最终全量收尾前得到证明，另一张SL正常成交完成配平，避免反复DELETE。
- 不完整、过期的完整集合、错误ID、仅not found、已观察成交或隔离，均不能变成拒绝证明或释放预占。
- 已证明拒绝后迟到LIVE订单使拒绝证明撤销，完整恢复风险并阻止新BUY。
- 网络、资金、Delta、分页、官方时钟五类阻塞各自记录准确原因和目标价量。

最初相关既有回归69项通过，见[initial.log](initial.log)。最终全量结果及文件指纹以 [validation.json](validation.json) 为准。

首次全量中该修复的20项专项和其余2,071项通过，仅旧缓存端到端测试失败；单独重跑该测试通过。检查失败journal后确认：模拟交易所`Wire`在POST中读正在追加的journal末尾半行，引发JSONDecodeError，导致模拟订单根本没有被接收，随后详情读取因不存在而报KeyError。测试桩现只解析已换行完成的记录，仍严格检查本次持久化prepared请求hash；生产WAL及其要求未变。[独立检查](harness_check.json)确认完整损坏JSON行和不匹配prepared hash仍会失败。

测试桩最初补丁遗漏字符串路径兼容，该次中止的全量运行保存在`regression_harness_superseded.log`，不作为验收依据；补齐`Path(...)`后，相关29项（资本、缓存交接、新SL恢复）通过，见[harness_and_new.log](harness_and_new.log)。最终验收使用修正后的完整重跑结果，不合并失败或中止运行的数量。

验证只替换交易所I/O和测试时钟；没有用实盘订单验收，没有证实修复后的实际网络耗时或成交收益。下一次新进程仍从原`uv run polymaker rdp-sm-run`入口加载；本轮不操作用户正在运行的进程。

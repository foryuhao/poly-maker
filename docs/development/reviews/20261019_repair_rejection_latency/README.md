# 明确拒单后的关闭核对耗时修复

市场 `btc-updown-5m-1792349700`，session `f42def203ba8435497ed8ae9c90331f6`。本次沿用既定HD/TP/SL策略，仅处理两次明确Post-only拒单后的执行等待。未发送真实订单，未启动交易进程。

## 实盘证据

|窗口|POST开始至返回|拒单返回至窗口BLOCKED|拒单返回至下一次POST|针对拒单ID的DELETE次数|DELETE平均SDK耗时|
|---|---:|---:|---:|---:|---:|
|w9：YES 5@0.80|0.543秒|10.393秒|12.447秒|24|0.211秒|
|w10：YES 5@0.81|0.217秒|10.308秒|12.363秒|24|0.208秒|

两次响应均为 `success=true`，但同时有明确 `invalid post-only order: order crosses book`、空orderID/status/amounts。原有严格分类器已经将其判为REJECTED，没有登记为已接受订单；本次没有修改响应分类器。48次DELETE均返回 `order can't be found - already canceled or matched`，关闭核对均等到 `SYNC_CACHE_NOT_READY` 后才结束。

BLOCKED返回至下一次POST另有约2.05秒，主要对应 `SMTradingLoop._pause` 在失败恢复时的2秒退避。修复后明确拒单可以正常SAFE关闭，因本次超时触发的额外退避也随之避免；没有更改全局失败恢复策略。

上述耗时用单调时钟POST/DELETE边界计算。`sm_placement_result`在原流程首次DELETE之后才记日志，不能把其日志时间误当成POST返回时间。[提取脚本](analyze_latency.py)可从原始journal重新生成[逐次证据](live_latency.json)，其中保留来源SHA256。整场会计和时间线见[原报告](../20261019_repair_live_0255/README.md)。

## 根因

`BatchLifecycle._send`在POST已返回REJECTED后，仍将全部预签名ID传给撤单watcher。缓存的 `terminal_observed`需要已登记订单身份及POST后的订单详情，明确拒单ID没有这种身份，因而watcher永远不能凭详情停止。

同时，每次DELETE结束都会推进 `CachedExecutionReads.last_write`。关闭屏障要求time、open_orders、trades、capital、positions的读取开始时间都晚于最后写入，且没有在途写请求。约0.2秒DELETE加0.25秒重试间隔不断推进边界，订单1秒、账户2秒的周期采集无法取得满足所有条件的整组新证据。watcher直到10秒核对超时后才被收尾流程停下。

这构成撤单与缓存关闭核对之间的相互等待。没有必要通过降低缓存新鲜度、缩短超时或把not-found当成关闭证明来解决。

## 修改

- POST未结束：保留所有可能提交的签名ID，继续处理早撤/晚接受。
- POST结束后：仅剔除已明确分类为REJECTED或未提交ABORTED的腿；ACK、UNKNOWN等可能成立的腿继续取消和核对。
- 主提交任务和watcher共用筛选；混合批次只取消可能成立的腿。没有可取消ID时停止watcher，仍执行原有完整账户/关闭屏障。
- 关闭后保留SL意图，按最新盘口和实际仓位重新准备订单。未缩短原有轮询周期或10秒失败超时，没有绕过关闭证明。

生产修改仅限 `src/polymaker/rdp_sm_lifecycle.py`。本次前的该文件可由[差异](lifecycle_change.patch)反向还原，SHA256与上一轮发布记录一致，见[修改范围](change_scope.json)。此前缓存详情饥饿修复保留；[RP-001 TP预热问题](../../REPAIR_OPEN_ISSUES.md)仍按用户要求暂缓，TP/SL阈值和NORMAL规则均未改变。

## 验证

新增5个执行回归场景，均使用实际控制器、签名/SDK、缓存、会计和关闭屏障，交易所HTTP为本地MockTransport，夹具禁止真实网络。

1. 实际NO缺口SL，注入与实盘完全相同的Post-only拒单、1/2秒轮询和GET/DELETE各约0.2秒延迟。旧代码在测试4.5秒失败超时下被 `SYNC_CACHE_NOT_READY` 阻塞，发出13次无效DELETE；修复后拒单返回至关闭约0.784秒、0次拒单ID DELETE。测试同时断言全部账户来源都在最后写入之后开始。
2. 同一场景关闭后重新挂SL，接受并成交，实际YES/NO配平，没有重复开仓或放弃原SL意图。
3. 丢失ACK仍按签名ID撤单和核对。
4. 模糊内部错误仍按UNKNOWN处理和撤单。
5. 双腿批次分别拒绝第一腿/第二腿时，只取消另一条已接受腿。

以上是5个参数化测试：第1项和第2项合在同一测试，第5项运行两个参数场景。旧代码复现结果见[before_summary.txt](before_summary.txt)、[before.xml](before.xml)；首次修复定向结果见[after_summary.txt](after_summary.txt)、[after.xml](after.xml)。随后补充了实际重试和混合批次验收，执行专项 **145 passed，110.68秒**，见[专项结果](focused_summary.txt)。全量结果以[validation.json](validation.json)为准。

专项进程加载的是首次修复版本；最终源码还明确保留POST在途期间的整组预签名ID，与原有早撤逻辑一致。最终源码的验证以全量回归为准；没有改策略参数或缓存证据要求。

最终源码全量 **2007 passed、2 skipped，315.15秒**，[完整结果](full_summary.txt)。其中拒单核对耗时约0.789秒、0次拒单ID DELETE，且后续SL重试完成配平。两套配置本地就绪检查均通过，配置仅更新验证引用；见[默认配置检查](readiness_config.json)、[livecfg检查](readiness_livecfg.json)。Ruff和`git diff --check`通过。

约0.784秒是特定轮询相位下的模拟实测，不是实盘延迟承诺。修复后仍需等待完整的新账户快照，具体取决于轮询相位、网络与交易所数据延迟。已有活动订单、ACK丢失或真实迟到成交仍可能需要更久。快速行情造成的Post-only拒单本身仍可能发生，本次消除了明确拒单后的无效撤单循环；实际收益和重试耗时需下一场实盘日志验证。

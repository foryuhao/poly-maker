# TP确认更新与取消提交核对：03:25实盘后续修复

来源市场 `btc-updown-5m-1792351500`，session `ac8db770a1be45ab912b3a9a576d0f36`。[原始时间线与来源哈希](../20261019_repair_live_0325/README.md)保持不变。本次没有运行实盘交易进程或发送真实订单。

## 1. TP被原开仓成交的CONFIRMED更新打断

实盘NO 5@0.29已通过实际仓位与抵押品证明，REPAIR随后接受YES 5@0.48。原NO成交MINED→CONFIRMED时，旧代码删除原订单证明，watcher据此触发 `SESSION_ECONOMICS_CHANGED`，将TP撤销。重新核对后再挂同价TP，两个ACK相隔3.391秒。

修复仅识别以下情况：

- 此trade/order身份已有 `position_reconciled`实际经济证明，原订单持有 `POSITION_RECONCILED`关闭证明。
- 处理器现有成交为MATCHED或MINED，新事件为CONFIRMED。
- 新事件与实际证明、处理器旧记录的市场、token、方向、价格、数量、trade/order身份完全一致。仅允许状态和成交时间标签不同，不以数量或价格容差模糊判定。
- 事件处理后仍已应用，且无隔离、失败或未解决的成交身份。

在这种情况下保留原订单经济证明，供活动Maker队列判断经济敞口未变；仍推进session revision并使 `proof_revision`失效，所以新单准入与最终关闭仍须重新核对。其他真实变化继续删除订单证明并触发原有取消/核对。新增 `sm_trade_confirmation_advanced`诊断明确标注 `queue_economics_unchanged=true`、`close_recheck_required=true`。

REST和WS两条集成场景中，原开仓MINED→CONFIRMED后，同一张TP保持LIVE并成交，最终配平；中间session并未提前变为SAFE。价格修正、数量修正、新成交身份、FAILED和缺少实际仓位证明的负例均继续使订单证明失效。

## 2. 带签名ID的Already canceled响应仍重复撤单

实盘w22在官方T119.346开始POST，T120边界到达后启动早撤。POST约0.871秒后返回精确 `order <expected_signed_id> is invalid. Already canceled.`。旧流程保留UNKNOWN及最大风险预留，并持续DELETE；27次窗口内DELETE、1次恢复DELETE均not-found。POST返回至窗口BLOCKED约10.986秒，T135.20恢复SAFE。

已有 `_verify_canceled_submission`支持该身份绑定响应，但必须先获得完整新快照排除冲突；watcher重复DELETE不断推进缓存写入边界，使证明所需快照迟迟无法满足新鲜度条件。

修复沿用原有严格 `_canceled_submission_response`识别器。POST结束后，只有精确响应匹配当前签名ID、所有接受/成交字段为空，且没有已观察到的订单/成交矛盾，才暂停主任务/watch派发的重复取消。attempt仍为UNKNOWN，保留订单身份、全部最大资金预留和关闭屏障；现有 `_verify_close(cancel=True)`仍保留一次保守取消，再等待完整新快照完成原有证明。watcher保持被动监视：若等待期间才出现迟到活动订单或成交，立即重新进入取消流程。没有把DELETE not-found或缺少订单详情当成关闭证据。

POST在途早撤保持；模糊错误、错误签名ID或实际成交/订单证据继续走普通UNKNOWN/订单核对。分页失败时保持UNKNOWN及全额预留，恢复完整证据后才释放；部分/全部真实成交即使伴随取消响应也必须计入库存和成本。现有迟到WS/REST成交撤销取消提交证明的规则保持。

## 验证

新增13个场景，全部使用真实控制器、签名/SDK、会计、缓存和关闭逻辑，交易所通过MockTransport注入，夹具禁止外部网络。

|项目|旧代码复现|修复后定向验收|
|---|---|---|
|实际证明后的REST确认更新|TP变CANCELED，测试失败|同一TP保持并成交|
|实际证明后的WS确认更新|TP变CANCELED，测试失败|同一TP保持并成交|
|取消提交：订单/账户1/2秒轮询，GET/DELETE约0.2秒，POST约0.87秒|测试4.5秒失败期限下SYNC_CACHE_NOT_READY；POST返回后13次DELETE|约1.109秒完成；POST返回后1次DELETE；最终完整新快照证明REJECTED|
|真实经济/失败/无证明负例|保持原语义|5项通过|
|部分/全部成交、分页失败、错误签名ID|保持原语义|4项通过|
|等待取消提交证明期间才出现LIVE订单|首版修复会停止watcher并返回ORDER_NOT_TERMINAL|最终修复恢复DELETE，取消后按真实订单核对|

首版定向 **12 passed，13.20秒**，见[after_summary.txt](after_summary.txt)及[after.xml](after.xml)。复核时新增迟到活动订单场景，首版暴露watcher过早退出，见[late_open_before.txt](late_open_before.txt)；补齐被动监视后，最终专项 **54 passed，42.86秒**，覆盖13个新场景及原有明确拒单、取消提交和迟到成交回归，见[focused_summary.txt](focused_summary.txt)。旧代码三个目标用例均失败，见[before_summary.txt](before_summary.txt)、[before.xml](before.xml)。旧版本仅在隔离测试进程中从两份带SHA256的源码快照加载对应类，没有回退工作区生产源码；可用[复现脚本](reproduce_before.py)重复，预期退出码1。[生产差异](execution_changes.patch)、[修复前哈希](source_before.json)、[修复后哈希](source_after.json)。

首版全量运行因新增上述边界而被主动停止，不作为验收证据；保留为superseded_full记录。最终源码另行完整回归。

最终全量结果以[validation.json](validation.json)为准。模拟耗时受轮询相位影响，不是实盘承诺；需下一场日志确认TP是否保持同一订单，以及该特定取消提交分支是否避开超时。普通UNKNOWN、真正的订单/成交冲突或服务故障仍可能需要更长核对。

最终源码全量 **2020 passed、2 skipped，327.79秒**，见[full_summary.txt](full_summary.txt)、[full.xml](full.xml)。最终全量的取消提交场景约1.106秒完成、POST返回后1次DELETE。Ruff及git diff --check通过；[默认配置](readiness_config.json)与[livecfg](readiness_livecfg.json)本地就绪检查均通过，仅更新配置的validation_reference。

仅修改 `rdp_sm_execution.py`和`rdp_sm_lifecycle.py`的执行判定。策略参数、NORMAL入场逻辑、资金/Delta限额、T120/T180/T240阶段规则、轮询周期、失败超时和缓存完整新鲜度条件均保持。RP-001 TP预热/首次报价问题继续暂缓。

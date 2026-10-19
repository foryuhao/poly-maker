# REPAIR市场时间线：2026-10-19 03:25–03:30

市场 `btc-updown-5m-1792351500`，session `ac8db770a1be45ab912b3a9a576d0f36`。策略 `hd_tp_sl_v1`，本场启动日志已引用上一轮拒单耗时修复的validation。

本场完成一轮实际开仓和TP配平：NO 5@0.29 + YES 5@0.48，成本3.85 USDC，Merge 5对回收5 USDC，已实现盈利 **1.15 USDC**。最终Delta=0、残仓=0，`COMPLETED / SAFE_TO_CONTINUE`。22次提交尝试，其中20次NORMAL、2次REPAIR；2笔独立实际成交。`active_cycles=2`统计成交所在的两个window，不代表两轮独立开仓/REPAIR。

下表T为日志wall时间减市场起始epoch。模型elapsed按官方时间锚点计算，两者有约0.4–0.65秒差异。成交时间均为本地首次观察；本场原始trade时间字段为0，不代表真实交易所撮合时间。MATCHED、MINED、CONFIRMED按trade/order身份去重。

|T秒|事件及结果|
|---:|---|
|16.17–21.78|启动及预热，出现短暂NETWORK_RECOVERING和报价变化等待|
|21.78–91.43|w1–w16共16张NORMAL单，无成交；14次信号反转撤单、2次订单到期关闭|
|93.50 / 93.73|w17 NORMAL提交NO 5@0.29 / 收到ACK。T93.78信号反转，请求撤单|
|94.61 / 96.59 / 99.15|观察到w17 MATCHED / MINED / 实际NO 5仓位及抵押品变化证明；T99.18开仓窗口安全关闭，进入REPAIR|
|99.21|恢复开仓后的历史行情路径，状态HD→TP的历史模型点在T96.3747；当前决策输入elapsed=98.8073、pYES=0.545、sigma≈0.053026，决定YES 5@0.48|
|100.21|w18 TP单YES 5@0.48挂单ACK，首次POST耗时约0.980秒|
|100.60–103.06|w17原NO成交变为CONFIRMED，撤销其旧关闭证明，watcher触发SESSION_ECONOMICS_CHANGED，取消w18 TP；w18无成交，T103.06安全关闭|
|103.08 / 103.60|仍处于TP，按已经锁存的0.48再次提交YES 5股 / 收到w19 ACK，POST耗时约0.510秒|
|103.60–105.59|网络进入NETWORK_REPEATED_SLOW暂停状态，但已接受TP保持；15次KEEP观测中pYES约0.405–0.545，已知sigma约0.0525，没有触发SL，继续原价排队|
|105.65 / 107.61 / 111.16|观察w19 YES 5@0.48 MATCHED / MINED / 实际仓位及抵押品变化证明；T107.62因REPAIR_FILLED请求关闭，T111.19安全完成配平，YES 5、NO 5。ACK至首次本地观察成交约2.053秒|
|111.23 / 112.13|网络进入PROBE；YES成交随后变为CONFIRMED，不重复计入库存或PnL|
|112.88–119.08|w20 YES 5@0.31、w21 NO 5@0.69两张NORMAL单，均因信号反转撤单，无成交；网络T115.07恢复NORMAL|
|120.00–120.87|w22 NORMAL提交NO 5@0.67，提交守卫采用的官方elapsed为119.346，符合T120前开仓限制。POST在途时T120.65触发MARKET_SUBMISSION_CUTOFF并开始撤单；T120.87 POST返回带签名ID的Already canceled错误，分类UNKNOWN|
|121.07–131.85|持续撤w22并核对。T131.44关闭核对记录SYNC_CACHE_NOT_READY；T131.85该窗口BLOCKED返回。该窗口内27次DELETE，均not-found|
|134.08–135.20|退避后再发1次DELETE，重新获取完整新快照；T135.20恢复SAFE_TO_CONTINUE。w22最终没有新增成交，实际YES/NO仍各5股|
|135.20–240后|NORMAL截止后保持配平仓位，等待Merge；无需T180强制SL|
|246.71 / 247.29 / 255.96|记录Merge意图 / 提交 / CONFIRMED，销毁5对YES/NO、回收5 USDC。意图保存的准入elapsed为T240.906，外部准备读取完成后才记意图|
|300.91|最终结果COMPLETED、SAFE_TO_CONTINUE，残仓和Delta均为0|

## TP如何决定

原始仓位为NO 5@0.29。历史模型点elapsed=96.374727时，pYES=0.67，即pNO=0.33，每股浮盈 `0.33−0.29=0.04`。本地首次成交elapsed=94.212988，持有约2.161740秒；每股目标 `0.002×2.161740≈0.004323`，0.04严格超过目标，因此进入TP。此历史触发在T99.21核对开仓完成后才持久化和发出实际决策，不能把T96.37称为实际提交时间。

首次TP报价的YES best bid=0.54，tick=0.01，sigma≈0.053026；N=ceil(sigma/tick)=6，故挂 `0.54−6×0.01=0.48`。w18取消后，已接受TP价锁存为0.48，w19重挂仍沿用原价。最终配对PnL为：

`5×(1−0.29−0.48)=1.15 USDC`。

本场17条REPAIR决策均为TP（2次BUY、15次KEEP），无SL切换。已记录决策中的最大回吐为0.01/原始股，小于0.06，已观测短窗冲击未触发。开仓成交时间未知及经济证据晚到使早期路径标记PARTIAL_PATH/INVENTORY_PATH_UNKNOWN；旧区间没有伪装成完整DD历史，恢复后的可靠局部段继续计算。

## 执行中值得跟进的两点

**第一张TP多经历一次取消和重挂。** T99.15已有实际仓位证明，允许REPAIR；T100.60原成交MINED→CONFIRMED时，`_SMProcessor.on_trade`仍执行 `invalidate("TRADE_EVIDENCE_CHANGED", original_order_id)`，移除原订单证明。`BatchLifecycle.watch`检测到非当前批次旧订单缺少证明，因此触发SESSION_ECONOMICS_CHANGED。日志没有新增原始成交量；这是当前保守核对机制对状态更新的处理，造成已接受TP队列中断。w18 ACK到w19 ACK约3.391秒，中间需要重新核对后挂单。

**NORMAL截止附近的UNKNOWN分支仍有长等待。** w22属于POST在途早撤、返回 `order <expected_signed_id> is invalid. Already canceled.` 的取消提交场景。它需要完整新快照排除冲突后才能确认为安全关闭。27次在窗口内的DELETE加1次后续恢复DELETE均返回not-found；POST返回至窗口BLOCKED约10.986秒，直至T135.20才恢复安全状态。结合现有代码，UNKNOWN watcher持续撤单仍不断推进缓存写入边界，延后取消提交证明所需的新快照。不能仅凭not-found跳过核对。该分支没有在上一轮明确Post-only拒单优化中豁免，本次仅记录事实和代码关联，没有修改执行逻辑。

本场没有 `invalid post-only order: order crosses book` 明确拒单，因而不能据此宣称上一轮针对明确REJECTED分支的实盘延迟已经验证。两类响应需要分开看待。

后台缓存无fatal、无事件/引用丢失，退出时I/O已排空；账户150轮均完整。存在1次订单详情ValueError，最终核对恢复。没有重现旧证明长期占满详情名额而阻止REPAIR的情况。

证据：[22次提交明细](orders.csv)、[决策/成交/撤单/状态时间线](timeline.json)、[会计及来源哈希](summary.json)。[提取脚本](extract.py)只读取原始journal，生成本目录派生报告，并断言成本、Merge损益及最终零残仓一致。本轮没有改动交易代码、策略或配置。

# HD/TP/SL执行验收与实盘测试交接

2026-10-19。用户授权“执行验收和上线切换，然后交给我测试”。本记录的上线范围为：当前工作目录中的新版代码和运行配置交付有人值守的单市场实盘测试。开发过程没有连接实盘交易所、发送真实订单或启动后台交易进程。

最终通过状态、回归数量和证据哈希见[validation.json](validation.json)；实际源码及运行配置哈希见[source_manifest.json](source_manifest.json)。就绪标记表示工程执行验收通过，不表示已经取得真实Maker成交样本或证明正EV。

## 执行验收

[验收测试](../../../../tests/test_repair_execution_acceptance.py)使用真实生产入口、状态协调器、策略、SDK签名、提交守卫、异步WAL、订单追踪、实际仓位核对和Merge会计。仅HTTP、WS、结算SDK/RPC等外部边界及测试时钟为模拟；底层夹具禁止真实网络连接。

|场景|验收内容|
|---|---|
|整场，直接账户读取|NORMAL实际成交→默认HD→TP→行情恶化SL→配平→下一轮NORMAL→T120禁止新NORMAL→T180强制SL→T240撤单→Merge→T300退出|
|整场，缓存账户读取|同一流程，启用真实SyncCompanion/ObserverReader，使用运行配置的1秒订单/2秒账户轮询间隔|
|TP长挂|生产默认2秒复核；等待超过旧版3秒TTL，盘口和sigma变化后仍保留原订单；随后路径SL强制撤换|
|部分/迟到成交|TP先成交2股，撤单期间再成交1股；SL按真实剩余2股和交易所5股最低量提交5股，只成交2股后撤余量；最终配平，不重叠补仓|
|MATCHED不确认|最初YES成交保持MATCHED，凭实际仓位解锁；后续CONFIRMED更新不重复计量|
|TP成交但ACK丢失|按预先持久化签名订单ID撤单/核对；补计部分与迟到成交，确认关闭后才再次挂TP，最后正常收尾|
|T240缺行情/缺sigma|独立截止定时器撤单；T240后调用REPAIR不产生新POST|
|撤单暂时失败|HTTP 200但not_canceled不作为关闭证明；保留资金占用并阻止新单，继续核对直至安全关闭|
|Post-only拒单|保留原始实际仓位，不伪造成交、不自动改成Taker|
|断线/运行中取消|POST仍在途时先请求撤单；ACK迟到后继续关闭，不遗留新挂单|
|准备后、ACK落盘前崩溃|重启读取持久化候选ID，撤销可能已发送的订单，同市场禁止重新开仓，不自动续跑|

整场同时断言：每笔均为BUY/GTC/Post-only；实际session Delta不超过5股；成本与在途占用不超过15 USDC；下单时间符合阶段边界；最终仓位、释放成本及Merge PnL与成交账本相符。专项回归还覆盖预算耗尽、最低金额、被迫反向残仓、实际仓位不一致、行情断档和签名前行情变化。

测试中的价格路径、成交和故障由模拟场景明确给出，不用于估算Maker成交率或交易EV。空闲阶段跳时用于加速T120/T180/T240/T300；独立的T180/T240定时测试验证无新行情时撤单，不把跳时导致的WS重建当成定时器证据。

## 可检查证据

- `session_direct.json`：直接账户读取整场，订单、模式、仓位轨迹、最终账本。
- `session_cached.json`：缓存读取整场。
- `session_lost_ack.json`：ACK丢失整场。
- `execution_trace_*.json`：策略决策、实际提交结果、撤单请求/ACK、关闭屏障与Merge的原始日志摘录，保留monotonic时点。
- `acceptance_cases.json`、`focused.xml`、`full.xml`：逐项测试结果。
- `readiness_config.json`、`readiness_livecfg.json`：切换后的本地只读就绪检查。

`decision_to_mock_post_ms`仅测量本机模拟边界中的决策到POST；撤单/重挂也可从执行轨迹复核。它们验证执行链已接通，不代表真实交易所往返时延。真实时延与成交表现由接下来的实盘测试采集。

## 启动与停止

仓库根目录的默认配置及`livecfg`配置均已选择`hd_tp_sl_v1`，显式记录已确认参数及本验收引用。两套本地就绪检查均返回0，`policy_selected=true`、`ready_for_live=true`。切换只改变REPAIR相关配置，既有NORMAL、钱包、资金与路径设置保持原值；对下一次启动生效，没有重启已有进程。实际网络、资金和账户状态仍由运行时预检判定。

```sh
uv run polymaker rdp-sm-repair-check
uv run polymaker rdp-sm-run
```

使用既有另一套配置时：

```sh
uv run polymaker rdp-sm-repair-check --config-dir livecfg
uv run polymaker rdp-sm-run --config-dir livecfg
```

每次命令只运行当前一个市场。人类日志显示`REPAIR: ... | Mode=HD/TP/SL`，最终JSON包含`repair_readiness.selected_policy=hd_tp_sl_v1`。T120停止NORMAL新开仓，T180未配平强制SL，T240撤剩余REPAIR单后核对迟到成交并按关闭屏障进入Merge。

Ctrl-C执行已有退出/撤单核对流程。断电、SIGKILL或未知写入不能承诺即时撤净；同市场再次启动只做候选ID清理并阻止重新交易，避免丢失SL意图后重新开仓。不要删除fence或journal来绕过这条保护。本轮没有实现崩溃后自动交易续跑。

## 复验

为避免本机`/tmp`配额，指定有空间的独立临时目录：

```sh
PYTHONPATH=tests TMPDIR=/path/to/test-tmp uv run --no-sync pytest -q --tb=short \
  --basetemp=/path/to/test-tmp/full --junitxml=/path/to/test-tmp/full.xml
PYTHONPATH=tests TMPDIR=/path/to/test-tmp uv run --no-sync pytest -q --tb=short \
  tests/test_repair_execution_acceptance.py tests/test_repair_policy.py \
  tests/test_rdp_sm_path_repair.py tests/test_repair_marketdata.py \
  tests/test_rdp_sm_repair.py tests/test_rdp_sm_obi_faults.py tests/test_rdp_sm_merge.py \
  --basetemp=/path/to/test-tmp/focused --junitxml=/path/to/test-tmp/focused.xml
```

整场测试会在各自临时目录生成`acceptance_result.json`，包含实际订单序列、仓位轨迹、最终结果及journal哈希；所有模拟日志均使用虚拟钱包资料。

# 原单市场命令托管同步旁路

日期：2026-10-17。用户确认：外层shell启动12次，每次`rdp-sm-run`只处理当前单市场。本改动不增加程序内部批次或自动选下一市场。

## 使用方式

仓库两份运行配置均开启了第一阶段旁路，订单目标周期1000ms，资金与持仓2000ms。下一次在`/home/ubuntu/poly-maker`执行原命令时，自动启动本次Session独立的同步线程与记录线程：

```bash
for i in {1..12}; do
  uv run polymaker rdp-sm-run
done
```

这段循环仍由用户执行，本次开发和验证不启动真实交易。正在运行的旧进程不会动态加载新线程；新调用加载新代码。

每次主进程发现市场后，单向绑定经过原发现逻辑验证的slug、condition ID、tokens与Session。旁路固定观察这一市场；主进程跨过T300仍在收尾时也不自行切到下一市场。下一次shell调用有新的进程、Session、线程、快照版本及文件目录。

主进程正常结束、预检失败、异常或Ctrl+C时，在原有订单关闭与清理路径之后停止旁路，排空在途HTTP并保存最终记录再退出。旁路启动、绑定、采集、写盘或退出异常不会变成交易授权，也不改变原策略返回结果；观察结果会标记失败、不完整或缺口。

原来的预检、NORMAL/REBALANCE策略、资金账本、关闭证明与NetworkHealth继续使用原路径。执行器记录钩子只向有界内存队列传递精简事件，不发HTTP，不等待旁路，不采用旁路数据。旧人工日志与交易journal格式保持；新增独立`SYNC_OBSERVER_STARTED/STOPPED/FAILED`日志便于定位记录目录。

## 数据保存

每次运行保存到：

```text
research_outputs/periodic_sync/<session_id>/
  manifest.json
  events.jsonl
  snapshots.jsonl
  strategy_refs.jsonl
  summary.json
```

- 内存保留本Session最新不可变snapshot和最多1024版本历史；源数据保留实际请求起止时间、完整/失败状态与last-good原采集时间。
- `snapshots.jsonl`保存每个发布版本的完整快照，包括Session、市场、订单、成交、资金/持仓证据与源时间。等待主市场上下文时明确标为`AWAITING_MAIN_MARKET`，不会冒充可交易快照。
- `strategy_refs.jsonl`关联真实策略事件与当时已发布的snapshot：提交、撤单、成交记录、窗口及关闭核验。原事件含POST/DELETE单调时钟时分别关联；没有原操作时间时只标记记录时间。
- 关联规则只选`published_monotonic <= event_monotonic`的版本。缺历史、缺本场数据或无对应版本会明确为空，不使用后来生成的快照补齐过去。每个来源还记录实际数据年龄与是否在事件前完成。
- `manifest.json`含Session、市场、PID、配置周期、对应原策略journal路径及同步源码哈希。凭证、签名订单原文、请求头和原始API密钥响应不复制到旁路记录。
- `summary.json`含采样数量、完整性/失败/覆盖、缺失关联、丢记录、刷新及HTTP延迟。资源指标明确为整个主进程，不能误当作同步线程单独消耗。`RECORDED`只表示记录流程完整，不代表数据足够授权交易。

在正常退出时，最后的`STOPPED`快照也会写完。读取未排空、事件丢失或没有本场快照时不声称采样完整。所有快照仍为`trading_authorized=false`。

## 一小时后如何检查

按Session和实际market ID对齐原交易journal及旁路记录，重点看真实下单/撤单时是否已有完整且足够新的数据、是否覆盖对应订单和操作、库存与成交是否对齐，以及若要等待新快照还需多久。比较原策略POST/DELETE延迟、网络暂停和窗口收尾，判断额外读取是否拖慢交易。

第一阶段仍保留原查询，会暂时增加网络流量；第二阶段才会移除重复查询并用快照授权执行。不能因为记录到了快照就认定它能够支撑未来执行。

shell中的12次调用不强制对齐12个完整或不同市场：如果某次提前失败，下一次可能仍发现同一市场；若首场从中途开始则是不完整场。报告按实际market ID与Session状态统计，不伪称已完成12场或严格3600秒。

## 关闭旁路

在实际使用的TOML中设：

```toml
[rdp_sm.sync_observe]
enabled = false
```

下次调用恢复无旁路的原路径。模型默认关闭以保留未配置环境的行为，仓库`config/config.toml`和`livecfg/config.toml`显式开启；不影响`rdp-sm-preflight`或仅查看`--help`。

## 验证

针对性：**41 passed**，覆盖原命令入口、失败隔离、固定市场、历史版本关联、完整快照落盘、Session隔离与取消后的排空。离线保护：**0 network/DNS attempts，workspace credentials denied**。变更代码及测试Ruff、空白检查通过。

全量：`PYTHONPATH=src:tests /home/ubuntu/poly-maker/.venv/bin/python -m pytest -q --tb=short --show-capture=no`，**1881 passed，2 skipped，182.71秒**。发布后将实际运行目录快进到已验证提交；`Pilot-x12`标签继续保留原基线，不移动标签。

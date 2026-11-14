# M4 实盘 — 2026-11-14 完整代码快照

Git 标签：`M4-实盘`，标签说明：`M4 实盘`。
发布分支：`codex/m4-live-release-20261114`。

此版本收录当前工作区累计的源码、测试、配置、运维脚本和研究规范，
包括 M4 Taker 实盘及此前尚未提交的 M1、模拟研究和看板改动。
真实凭据、运行账本、订单流水、余额观察、停机标记及生成的研究结果保留在本机。
发布代码不安装服务、不启动或重启实盘、不解除停机标记，也不执行真实订单。

## M4 Taker 执行约定

- BUY-only FAK；共享 `config/m4.toml`，当前最低锚定价 0.50。
  历史场按各自账本保存的 settings 恢复。
- 单信号计划含费预算 5 USDC，单场风险上限 10 USDC，
  每方向一个已 POST 独立信号；数量步长 0.1，BUY 本金必须满足 USDC 分精度。
  在剩余预算内向下选择最大合法数量；至少 5 shares 或名义金额严格大于 1 USDC。
- 信号产生后前 10 秒按原锚定价加当时 1 tick；其后每次新尝试按新鲜同 token
  Best ask 定价且价格低于 1。不可变定价证据和计时原点持久保存。
- 已准入任务不再以本地行情队列积压、盘口新鲜度或本地 ask 不高于限价作为发送前置条件。
  信号生成质量检查、官方 tick、网络、资金、UNKNOWN、停机及 T240 保护继续保留。
- UNKNOWN 先核对；ACK、SETTLING 不算最终成交。待确认匹配保留预算，
  只补明确余款；T240 后不再新增或追加订单。
- 保留已接受的实际价格改善增量成交与计划含费成本之间的差异。
- 已确认且不足 1 美分的余款按 DUST 收尾；无合法数量时不保存新的定价证据。
- SDK 签名 tick 使用本场同 token 已官方核验值，避免永久缓存的旧 tick 阻断签名。
- 新场启动准备截止为保守 T240；T15 仅为最早交易时点。
  延迟接入保存观察缺口，不补造未收到的信号。

## 运行时加载标识

必须同时核对进程 PID、启动时间和运行日志中的标识；磁盘源码哈希不证明进程已加载。

```text
pricing_policy=ANCHOR_10S_THEN_BEST_ASK_V1
post_marketdata_policy=ADMITTED_FAK_ANCHOR_10S_THEN_BEST_ASK_V1
remainder_policy=LEGAL_ORDER_ONLY_QUOTE_CAPTURE_CENT_DUST_V1
sdk_tick_policy=OFFICIAL_VERIFIED_BOOK_TICK_V1
startup_entry_policy=NEW_MARKET_BEFORE_T240_V1
amount_precision=FAK_BUY_CENTS_SHARES_0_1_V1
```

## 巡检与离线验证

`polymaker-m4-taker-watch.timer` 每 15 分钟运行一次，
北京时间每小时 00、15、30、45 分的第 20 秒触发。
每轮仍按 5 分钟市场逐场复核全部未复核及未解决场次。
SQLite 使用 `mode=ro` 和 `PRAGMA query_only=ON`；
巡检状态通过排他 `flock` 重读并原子保存。

M4 测试使用隔离配置，避免与根测试目录的同名辅助模块混淆：

```bash
bash ops/test_m4.sh -q
python -m pytest -q --ignore=tests/m4
```

最终离线验证结果随本次发布提交记录；测试不得开启真实交易集成选项。
基线、M1 和旧 Maker 巡检的历史文件保留，发布操作不恢复已停用服务。

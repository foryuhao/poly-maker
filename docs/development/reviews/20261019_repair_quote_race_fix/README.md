# 下单准备时盘口变化导致 REPAIR 退出的修复

来源：[10:45 场复盘](../20261019_repair_live_last3/README.md)，session `cdfc165b84be4cfcb9154efe427e8129`，市场 `btc-updown-5m-1792377900`。

该场已有 NO 5 @ 0.39，T51.512 触发 SL。第一份 YES 5 @ 0.73 候选在激活前正常失效；第二次计划 YES 5 @ 0.74，在准备阶段校验抛出 `QUOTE_WOULD_TAKE`，异常向上传到 session，导致整场停止，退出时仍有 NO 5 股。没有 REPAIR POST。

## 修改

生产代码只修改 `PathRepairController.repair`：

- 仅捕获 `lifecycle.prepare` 阶段的 `_Issue("QUOTE_WOULD_TAKE")`。prepare 只校验和准备签名，不发送订单。
- 记录 `sm_repair_quote_invalidated`，包含窗口、状态、方向、失效价格/数量以及 `post_started=false`。
- 清除失效计划及缓存候选，把下次复核设为立即可执行，返回主循环；下次按新盘口、实际仓位和预算重新计算。
- 保留 episode 和已经锁定的 SL/TP 状态；已经准备过的签名 ID 仍保留在生命周期的已准备集合中，避免复用。
- 身份、数量、关闭证据等其他 `_Issue` 原样抛出。`activate` 不在新捕获范围内，避免将可能涉及发送的异常当成未发单重试。

策略参数、Maker Only、Buy Only、session 15 USDC、Delta 5、T180/T240、实际仓位及完整关闭证据要求均未改变。已接受 TP 的固定报价/队列规则未改变，RP-001 继续暂缓。

## 回归场景

新增 `tests/test_repair_preparation_quote_race.py`，使用实际控制器、签名、异步日志、网关和仓位核对，HTTP/盘口为模拟边界：

1. NO 5 @ 0.39，经可靠价格回撤进入 SL；日志 flush 期间 YES ask 从 0.74 降至 0.73，让旧买价 0.73 失效。没有新 POST，不丢失 SL；随即按新盘口 YES 5 @ 0.72 重试，最终配平。
2. 首次 TP 尚未发出时，NO ask 降到旧报价 0.46；放弃旧候选，保留 TP，按最新 bid 和 sigma 重算至 0.42 并成交配平。
3–5. 准备阶段身份、数量、前一窗口未解决错误继续抛出。
6. 激活阶段即使出现同名异常，也不能被当作“尚未发送”局部恢复。

修改前，前两个实际盘口复现均失败，异常栈与实盘一致：`repair → prepare → _validate → QUOTE_WOULD_TAKE`，见 `before.txt`。修改后新增 6 个用例全部通过，见 `after.txt` / `after.xml`。

扩展执行回归 **128 passed，187.64 秒**，覆盖策略、报价准备、NORMAL/REPAIR、队列确认、缓存核对及拒单处理；本次未另跑全量测试。详细结果见 `validation.json`、`focused.txt` / `focused.xml`。两条既有 record_property/JUnit 格式警告不影响断言结果。`source_manifest.json` 保存生产改动、测试、冻结风险模型和两套配置的 SHA256，验证结束后逐一核对未变化；`rdp_sm_path_repair.py.before` 为本次修改前的文件，`production.patch` 为本次生产差异。Ruff、git diff --check 均通过。两套本地 `rdp-sm-repair-check` 均为 policy_selected=true、ready_for_live=true，结果已保存。

扩展运行中，既有 `test_sl_cancel_happens_even_if_minimum_replacement_cannot_be_funded` 的部分成交触发仓位路径断点，等待到 T180 强制 SL 后继续。没有为了缩短测试修改该用例或策略；该用例的通过不单独作为路径型 SL 快速触发的证据。本次新增 NO 原仓回归通过可靠回撤实际进入 SL，并验证报价失效后重算和配平。

## 运行范围

没有执行 `rdp-sm-run`、发送真实订单或取消真实挂单。下一次启动加载本地修复；已运行进程不会热更新。只修复后续运行的执行缺口，不自动接管已经退出的 10:45 场残仓。验证为模拟边界下的工程回归，修复后真实市场表现仍需用户测试。

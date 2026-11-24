# M12 基线跟随执行器

跟随器在同一台 Linux 主机上读取权威基线 SQLite 已提交的 M12 `INTENT`。WAL 文件变化唤醒读取，短延迟复查处理尚未可见的提交，读端持有只读连接。每场按 `audit.seq` 选择最新意图，并在发送前再次核对。收到意图后才建立执行行情连接，只订阅该意图的 token，反向意图到达时切换订阅，T240 关闭行情连接。跟随器用有效盘口判定报价，并以复制的 M42112 执行器下单。固定的框架来源是 903e66ee31917f0ced58b1d9e022688f01f3f57a。

## 离线检查

在项目根目录执行。此命令只核对本地框架和策略配置。

```bash
.venv/bin/python -B -m m12_follow.live --check
```

离线用例使用临时目录、合成 SDK 账户和模拟 HTTP。路径中把复制件放在原项目包前面。

```bash
PYTHONPATH="$PWD/m12_follow/framework/src:$PWD:$PWD/tests/m4" \
  .venv/bin/python -B -m pytest -q -p no:cacheprovider m12_follow/tests
```

## 独立用户服务

| 用户单元 | 进程入口 | 作用 |
|---|---|---|
| `polymaker-m12-follow-publisher.service` | `m12_follow.publisher run` | 启动下一轮权威模拟基线，在 M12 意图落账时发布到本机频道 |
| `polymaker-m12-follow.service` | `m12_follow.live --live --baseline-db-run` | 用独立进程和账本读取现役基线意图、核对订单和执行买入 |

systemd 为两者分别分配 cgroup，可用 `journalctl --user -u 单元名` 分别查看日志。SQLite 模式使用公开的 `source.env`、专属的 `wallet.env` 和只读挂载的基线目录，可以在源暂时离线时继续核对自己的未决订单。发布器频道入口保留用于需要实时信号有效期的模式。

### 观察运行

复用本机 M4/M21 时，`wallet.env` 保存原部署和原凭据文件的路径。执行器在本机内存读取原钱包和连接设置，并把资金地址与 M4、M21 的账本身份核对。钱包写者排他锁、实时余额和人工停机是账户级保护。每场只检查该场的订单、占款和禁买记录。同场存在未决订单时禁止继续发单，其他市场的交易历史不参与本场准入。

试跑期间，从服务外查看进程日志，逐笔执行事件在 `framework/research_outputs/m4_live/m421-journal/`，成交状态以 `framework/research_outputs/m4_live/m421-ledgers/` 中本场账本为准。观察完成后，通过 systemd 停止独立跟随单元。

```bash
journalctl --user -u polymaker-m12-follow.service -f
systemctl --user stop polymaker-m12-follow.service
```

以下命令仅注册单元。首次部署从 `source.env.example` 复制公开配置到服务账号的配置目录。升级时保留已有配置。

```bash
systemctl --user link "$PWD/m12_follow/polymaker-m12-follow-publisher.service" \
  "$PWD/m12_follow/polymaker-m12-follow.service"
systemctl --user daemon-reload
```

## 可选的发布器频道

在下一轮权威基线的正常启动位置使用下列入口。RUN_ID 是新一轮基线的 run-name。现役 168 小时基线已完成接续，原进程没有加载发布器；本版本不会接管其 PID、补播旧场或再开一份行情采集。新轮仍使用基线原有的运行锁、冻结代码、磁盘保护和三策略账本。

```bash
.venv/bin/python -B -m m12_follow.publisher run \
  --baseline-runtime "$BASELINE_RUNTIME" \
  --storage-root "$BASELINE_STORAGE" --run-name "$RUN_ID" \
  --channel-directory "$XDG_RUNTIME_DIR/m12-follow-feed"
```

BASELINE_RUNTIME 指向含 manifest.json 的冻结运行目录。BASELINE_STORAGE 指向原有基线存储根目录。服务方式启动时，发布端使用该存储根的原生排他锁，在用户运行时文件系统创建仅服务账号可访问的频道。换主机或重启系统后不能继续使用旧的单调时钟。源进程退出、频道失去有效水位或该场结束时，未发送的新单与补单停止。服务只认显式绑定的 run-name。

## 实盘入口

独立配置位于 framework/config。交易账本和审计日志保存在 framework/research_outputs/m4_live，停机状态保存在 framework/runtime/trading-control。即使使用相同的旧文件名，这些路径也位于新框架下。已发送的 FAK 继续根据本执行器订单证据核对。保护停机需要沿用原来的人工处理流程。

单笔目标本金 5 USDC，手续费另行预留，每场风险额度 10 USDC，YES 和 NO 各有一个方向额度。SQLite 模式收到意图便开始处理，取合法的最高限价买入，签单价满足 `0.50 ≤ p < 0.90` 且不高于锚价加配置的 tick 数。参考 tick 冻结在首次核实的计划中，默认允许偏离 5 tick。可挂盘时发非 post-only GTD，立即撮合可用卖量，余量挂到 T240。GTD 的签名到期时间为 T240 加 60 秒。扣除官方时钟不确定度后距 T240 至多 120 秒，或余量低于场次挂单最小份数时，改发符合原最小金额规则的 FAK。新意图替换旧意图时，先撤旧挂单，等待精确订单和成交明细证明余量终止，再按既有额度执行。已成交仓位保留。

每场报告的 `by_follow_intent` 保留同一批读取中被覆盖的准入意图。分别按确认成交本金除以 5 USDC、确认份额除以基线目标份额统计两种完成率，另列确认份额、本金、费用、含费成本、含费均价和相对锚价的成本差额。挂单部分成交也按成交 ID 去重。

部署目录为 `~/.config/polymaker-m12-follow/`。使用前确认公开配置中的轮次与权威基线一致，并准备仅运行账号可读的 `wallet.env`（权限 0600）。

| 文件 | 变量 | 用途 |
|---|---|---|
| source.env | M12_FOLLOW_RUN_ID | 两端绑定的同一基线轮次，每一新轮使用新名称 |
| source.env | M12_FOLLOW_BASELINE_RUNTIME | 发布器验证并加载的冻结运行目录 |
| source.env | M12_FOLLOW_BASELINE_DB_RUN | SQLite 模式读取的权威基线轮次目录 |
| source.env | M12_FOLLOW_MAX_OVERPAY_TICKS | 相对锚价允许向上偏离的 tick 数，默认 5 |
| wallet.env | M12_FOLLOW_PRIVATE_KEY | 跟随专用签名凭据 |
| wallet.env | M12_FOLLOW_BROWSER_ADDRESS | 跟随资金地址 |
| wallet.env | M12_FOLLOW_EXISTING_PROJECT | 本机既有实盘的实际部署目录 |
| wallet.env | M12_FOLLOW_EXISTING_FUNDER | 与旧部署只读账本核对的资金地址 |

前台运行使用相同参数。此命令会启用真实下单能力。

```bash
.venv/bin/python -B -m m12_follow.live --live \
  --baseline-db-run "$M12_FOLLOW_BASELINE_DB_RUN" \
  --funder "$M12_FOLLOW_BROWSER_ADDRESS" \
  --existing-project "$M12_FOLLOW_EXISTING_PROJECT" \
  --existing-funder "$M12_FOLLOW_EXISTING_FUNDER"
```

同钱包只支持本机单写者切换。跟随器需要取得旧部署相同的运行锁并检查交接状态；若旧进程持锁，跟随器拒绝启动。旧部署或跟随部署的人工、余额停机标记都会阻止买入。独立钱包可并行，但两个钱包购买同一市场仍会叠加经济敞口。使用 --once 可把一次运行限定在一个市场。

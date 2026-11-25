# M12 Baseline V2

该快照对应 2026 年 11 月 25 日启动的模拟基线 V2。每个进程选择一个策略，当前支持 M12、M21、M24。实例独立采集公开行情，按原锚价模拟成交，使用独立的 SQLite 账本和逐场报告。Polymarket V1/V2 按市场识别。基线 V2 无需钱包或私钥，省去逐帧行情归档和回放。封账后的压缩账本可用于审计，待官方结算的交易由汇总继续核验。

## 离线验证

从 `baselines/m12-m21` 运行。需要 Python 3.12、uv 和 zstd。

```bash
uv sync --locked --extra dev
PYTHONPATH=ops:research:src:tests .venv/bin/python -m pytest -q \
  tests/test_sop_shared_protocol.py tests/test_sop_shared72.py tests/test_sop_settlement_closeout.py
```

`manifest.json` 给出全部运行输入文件的 SHA-256。测试覆盖独立启动、实例锁、V1/V2 资产身份、场中 INTENT、原锚价模拟成交、官方结算和恢复。

## 每个进程启动一个策略

将 `/path/to/polymaker` 替换为部署项目根目录。项目根目录下应已有 `research_outputs/m122124_baseline`，也可在 `--storage-root` 指定另一个已存在的专用目录。

```bash
PYTHONPATH=ops:research:src .venv/bin/python ops/baseline_v2.py check \
  --strategy M12 --project-root /path/to/polymaker
PYTHONPATH=ops:research:src .venv/bin/python ops/baseline_v2.py run \
  --strategy M12 --project-root /path/to/polymaker --run-name m12-v2-example
```

在独立进程选择 `--strategy M21` 或 `--strategy M24`，并使用各自的运行名。按原计划切换时，可用 `--end-epoch` 传入原收盘边界的 Unix 秒时间戳。用相同的策略和运行名执行 `report` 读汇总，或用 `resume` 接续同一冻结计划。历史 M12 目录仍可用 `m1223_pair_phase.py --m12-only` 在新场接续。Follower 已有的 SQLite 读取接口沿用原字段。

## 新策略接口

策略模块提供冻结参数、变体 ID、SOP 与文档参数 SHA-256，并注册到 SOP Feed、参数解析和 `m1223_shared72.VARIANTS`。信号通过 `SIGNAL_CONFIRMED` 发布，提供信号 ID、策略版本、token、原锚定价、确认时间、截止时间、质量和失效信息。接入用例应验证单策略的完整市场从预热、INTENT 到结算和封账。

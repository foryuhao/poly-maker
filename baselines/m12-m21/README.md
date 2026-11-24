# M12 Baseline V1/V2

M12 单策略模拟基线，源自 2026-11-24 15:00（北京时间）起运行的冻结版本 `m12-only-v2-a2ef175084ace139`。发布沿用 `baselines/m12-m21` 路径，以便从 `M12-21-Baseline` 审查增量。模拟成交继续使用原锚定价，盈亏按官方结果结算。

每场按 Polymarket Gamma 的 `version` 选定资产。V1 使用 `clobTokenIds`，V2 使用 `positionIds`。预热、行情、回放和官方结算使用同组资产。公开证据出错时按当场协议重试。无法预热的单场记缺失。历史归档按原口径读取。

M12 单策略场次直接封账，历史双策略场次仍可用原冻结版封账。汇总和收尾脚本从账本读取协议版本。既有 M12 跟随器仍可沿用原模拟账本路径及历史交接记录。

## 离线验证

在本目录运行。依赖 Python 3.12、uv 和 zstd。

```bash
uv sync --locked --extra dev
PYTHONPATH=ops:research:src:tests .venv/bin/python -m pytest -q \
  tests/test_sop_shared_protocol.py tests/test_sop_shared72.py \
  tests/test_sop_settlement_closeout.py
```

测试覆盖 V1→V2→V1、错误版本和错资产证据、单场失败隔离、单策略封账、历史双策略恢复及汇总。使用 `manifest.json` 核对运行源码和配套汇总代码。

## 模拟基线接续

`ops/m1223_pair_phase.py` 支持在原模拟基线目录用 `--resume-at` 指定未来场次的 Unix 时间戳，用 `--previous-runtime` 指定上一冻结源码目录，并用 `--m12-only` 进入 M12 单策略模式。加 `--check-resume` 执行只读预检。

生产切换需要目标机原有的公开模拟基线计划和交接记录。旧实例释放独占锁后才能启动新实例。接续由目标机操作者授权和执行。

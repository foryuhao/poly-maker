# M12 / M21 Baseline

这是 2026-11-23 18:05（北京时间）开始接续的 M12 / M21 模拟基线的冻结源码。
运行快照标识为 `m12-m21-825e56b7f545275f`。原 `manifest.json` 中的 381 个文件逐字节保留。
同目录附带依赖锁文件、只读汇总入口和离线测试。基线按原锚定价假设全量成交，按官方结果结算。

## 离线验收

在本目录执行。系统需要 Python 3.12、uv 和 zstd。

```bash
uv sync --locked --extra dev
PYTHONPATH=ops:research:src:tests .venv/bin/python -m pytest -q tests/test_sop_shared72.py
```

回归测试覆盖同一份行情上的两策略与三策略封账，以及保留原 M12 账本、只回放 M21 的旧积压恢复。

## 接续入口

`ops/m1223_pair_phase.py` 用于从现有三策略基线接续 M12 和 M21。它依赖既有公开模拟基线的计划、行情归档和对应的用户级 systemd 服务。接续会在新场完成预热、旧场采到收盘后停止旧基线服务。运行前请先在目标服务器核对这些部署前提并取得切换授权。

`ops/m1223_shared_worker.py` 负责从封存行情生成回放账本。`ops/m122124_hourly_watch.py` 的 `collect()` 按市场时间衔接原三策略、旧积压补封和新两策略账本。离线验证无需启动采集服务。

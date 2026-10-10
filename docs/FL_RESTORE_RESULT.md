# FL Restore Result

FL_CODE_RESTORED = YES
FL_CONFIG_RESTORED = YES
OFFLINE_TEST_PASS = YES
HISTORICAL_DATA_PRESERVED = YES
RDP_REQUIREMENTS_PRESERVED = YES
OLD_IM_RDP_REMOVED = YES
EXTERNAL_INTERFACE_COMPLIANT = NO

RUNTIME_VALIDATION = PENDING_USER
LIVE_VALIDATION = PENDING_USER

恢复基线：`97ab0f98168f989b1878e4058022044c8372dcd3`。恢复分支：`fl-restored-97ab0f9`。
恢复前 HEAD：`63f1d8754e3d19d1c6530926cfeb1977b69378f8`；原 Git 历史保留，采用新增恢复提交。

源码、现有测试、六个 TOML 配置、Python 版本声明、`pyproject.toml` 和 `uv.lock` 与基线逐字节一致。`.env` 原样保留，并在权限为 700 的 `~/poly-maker-data` 内保存权限为 600 的副本；未输出凭据内容。使用现有 `.venv`，执行 `uv sync --frozen --extra dev`，检查 81 个包；未创建新环境。

42 个历史 FL Session 及研究输出、审计、日志、journal、状态文件和配置已保存至 `~/poly-maker-data`，共 8413 个文件通过源文件与副本 SHA256 核对，清单为 `preservation_manifest.json`。项目中的研究、日志和 journal 目录通过符号链接复用保存的数据。原始工作目录未删除。恢复前的已跟踪开发证据保存为 `pre_restore_tracked_evidence.tar`，本地框架文件保存为 `local_framework_evidence.tar`。

冻结 SOP 保留于 `docs/strategy/RDP_PHASE0_FROZEN.md`，与恢复前 HEAD 完全一致；SHA256：`92e455097c7567007ed8688faae81241588d77af2f614cd3f8a663486856d32d`。旧 IM/RDP 实现、测试及多 Agent 框架从当前工作树移除，历史记录仍可从 Git 查阅。

离线检查：
- `env -u POLYMAKER_LIVE .venv/bin/python -m pytest -q --ignore=tests/test_live_marketdata.py`：655 passed，68.69 秒。实时连接测试明确排除。
- `.venv/bin/python -m compileall -q src tests`：通过。
- `.venv/bin/ruff check --no-cache --select F821,F822,F823,E9 src tests`：通过。
- 完整 `ruff check src tests --output-format concise`：基线已有 91 项告警，包括格式、未使用导入及 B023 闭包绑定告警；未修改历史代码。一次使用缓存的检查遇到磁盘配额错误。
- `git diff --check` 和 `git diff --cached --check`：通过。

数据校验曾因整文件读取导致进程被系统终止，随后使用分块 SHA256 重新完成全部校验；未丢失原始数据。

外部接口为 NO：原始基线 `config/config.toml`、`livecfg/config.toml` 和 `config.py` 配置第三方 Polygon RPC，`merge.py`、`settlement_sweep.py` 存在 Web3 HTTPProvider 调用。不符合仅允许 Polymarket 官方 HTTP/WebSocket 的要求；本次按授权恢复基线，未改写此历史行为。

发现并记录已有 `polymaker settlement-sweep --once` 进程，非本次启动；未操作该进程，未检查账户或提交/撤销订单。本次没有 FL 启动、HTTP/WebSocket 实时测试、smoke test、dry-run、paper、micro live 或交易结算验证。恢复工作结束；后续启动与实盘验证由用户负责。

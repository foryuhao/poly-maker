# 恢复 Pilot-x100，继续 Buy Only / Maker Only

用户于2026-10-21确认放弃SL Taker，恢复GitHub标签Pilot-x100。

- 目标提交：`10c2d7ea2ee3e19aaebb0d1dd95996b59222b3e0`。
- GitHub实际查询：`rdp-size-001`分支已在该提交；远程Pilot-x100注解标签也指向该提交，无需修改远程分支或推送新提交。仓库默认分支main不是本次策略工作分支，未变更。
- 本地33个已修改受版本管理文件恢复为标签原文，22个新增源码/测试文件移出运行工作区。全部受版本管理文件与标签一致，src/tests/config/livecfg无额外未跟踪文件。
- 两份运行配置均恢复标签版本，`execution.post_only=true`；RDP-SM报价校验BUY，提交强制post-only，SL恢复原Maker执行。
- 完整备份：`/home/ubuntu/poly-maker-backups/pre_pilot_x100_restore_20261021_224530/`。`manifest.json`保存路径和SHA256，`files/`保存55个文件原副本，`retired/`保存22个移出的新增文件原件，另有原始git差异和状态。
- 交易记录、账户恢复状态、数据库、凭据、历史复盘及日志清理设施保留。未启动交易程序或发单。
- 核对范围：Git树一致性、TOML配置、BUY及post-only提交约束、备份SHA256；未执行单元测试或实盘验证。

这是恢复指定代码标签。此前报告已说明，Pilot-x100代码标签与100场实测运行模块并非全部逐文件一致；本次未将后来补丁混入该标签。

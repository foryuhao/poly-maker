# REPAIR参数历史研究（2026-10-19）

本目录是供确认的研究附件；不会改交易配置、连接交易API或发单。主要方案见[待确认方案](../../REPAIR_PARAMETER_PROPOSAL_20261019.md)。

推荐起始参数：r_TP=0.002 USDC/(原始share·秒)，短窗SL=2秒/1.5事前sigma/至少2 tick，累计价格贡献峰值回吐SL=0.06 USDC/原始share。参数尚未通过实际Maker执行/完整session最终收益验证，不标记实盘就绪。

## 复算

在独立研究环境安装`numpy orjson matplotlib nbformat nbclient ipykernel`，不改变项目依赖或交易环境。本文执行时使用`/tmp/repair-parameters-research/bin/python`；重新建环境后用对应Python替换即可。

```bash
# 可选：从本地47份原始事件重建缓存。逐行读取约21.16GB。
OPENBLAS_NUM_THREADS=1 /tmp/repair-parameters-research/bin/python docs/development/reviews/20261019_repair_parameter_research/extract.py

# 从保留的因果缓存重算参数敏感性及检查。
OPENBLAS_NUM_THREADS=1 /tmp/repair-parameters-research/bin/python docs/development/reviews/20261019_repair_parameter_research/analyze.py
/tmp/repair-parameters-research/bin/python docs/development/reviews/20261019_repair_parameter_research/validate.py

# 创建并从头执行Notebook，包含同样的计算、检查和两张图。
OPENBLAS_NUM_THREADS=1 /tmp/repair-parameters-research/bin/python docs/development/reviews/20261019_repair_parameter_research/build_notebook.py
```

## 口径与结果

- 原始输入：47市场，17,571,689条事件，21,155,061,498字节。读取时逐文件SHA-256；原始BBO恢复与归档实际时间的10,047个共同可靠p完全一致，0个差异。归档有效p另有7个未通过更严格研究过滤。
- 整数秒缓存：10,045个有效p，8,879个有效sigma；保留缺失原因，不插值。sigma按当前新适配器30秒历史、已完成秒桶、至少10区间/20秒有效时长重算。保守排除端点盘口，缺失路径不等于价格不变。
- 主研究：704个标准探针，47个市场簇，T40..110每10秒、双向、5 share；公平价成本与假设bid成本各自计算。前31场450条、后16场254条。不是704个真实NORMAL成交，后16场只是时间切分的信号行为检查。
- 补充：18个较早市场的38个实际开仓价格/本地MATCHED观察时间锚点。开仓策略及数量与当前不同，未当作当前NORMAL策略对照。
- 当前NORMAL：另12场中的13个真实episode，逐腿核对开仓及配对经济数据；没有与47场行情重合，不拼接成同一交易路径。
- `path_probe_results.csv`保存78,084行（主/补充探针 × 6个r × 9个SL候选）。同一路径的不同参数行不构成独立样本。
- `candidate_summary.csv`为完整候选表；`evidence.json`为口径、分位数、市场聚类bootstrap、限制和来源哈希；`validation.json`记录独立检查PASS。
- `research.ipynb`已从头执行：4个代码单元、2张图、无错误输出；两张图已人工查看标题、单位、系列、数值与截断情况。

TP报价穿价机会不作为成交。所有路径SL统计继续假设TP后尚未配平，不把价格信号计为真实退出。缺少真实长挂Maker反事实、完整session payoff及精确撮合时间，因此不报告伪造的收益排名、胜率或正EV结论。

## 关键行为

r=.002与固定DD=.06组合时，公平价成本探针63.21%先TP，42.05%在2秒内TP；假设bid成本分别80.82%、72.87%。这是原已确认TP公式的早触发特征，未偷偷添加冷却期。实际策略可能在1秒网格之前触发。

继续未配平的探针中，89.49%出现路径SL，持有中位数10秒。624条完整的10秒后续路径中28.37%回到触发前参考位；同时76.92%继续恶化至少一tick，两类可重叠，不能作胜负标签或相加。相比DD=.04/.08的持有中位数7/12秒，推荐.06是风险取舍，非EV优化。

原第10→11点逆向.05、h=2.636609、事前sigma=.01875448，z=1.641883；若仍未配平，A分支触发SL，当前正PnL不阻止。这个已知案例不参与独立验证。

本目录含较完整结果，主策略/会计/执行规则和断档处理的完整待确认定义见方案文件。用户确认后再落代码；本轮不改`REPLAY_VALIDATED`、不切换新实盘策略。

# 24 场逐单时间线（北京时间，2026-10-17 13:00–15:00）

范围冻结为 24 个连续已结束市场；未过滤异常结果。POST 时间按 monotonic/墙钟对齐。
成交 match_time 为官方秒级时间；CONFIRMED 时间是本地观察时间。NONE/INVALID/等待日志不构成完整逐帧信号流。

## 13:00 / fbe3d887a4484a46b8e689f9319fe2e9

[btc-updown-5m-1792213200](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:1)；结束 13:05:12.424，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:40)|NORMAL|13:00:24.805|VALID_YES / +0.891|YES 0.79×5.0|223|13:00:25.477 SIGNAL_REVERSED / -0.360|ACK / 13:00:27.091|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:79)|NORMAL|13:00:29.226|VALID_NO / -0.653|NO 0.2×5.0|212|13:00:31.036 SIGNAL_REVERSED / +0.375|ACK / 13:00:33.674|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:129)|NORMAL|13:00:35.750|VALID_NO / -0.587|NO 0.21×5.0|214|13:00:37.752 ORDER_DEADLINE|ACK / 13:00:41.029|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:189)|NORMAL|未发送|VALID_YES / +0.354|YES 0.83×5.0|—|13:00:43.343 PLACEMENT_NOT_ACKNOWLEDGED|ABORTED / 13:00:46.360|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:244)|NORMAL|13:00:51.278|VALID_YES / +0.378|YES 0.88×5.0|305|13:00:52.788 SIGNAL_REVERSED / -0.306|ACK / 13:00:57.024|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:329)|NORMAL|13:01:01.737|VALID_YES / +0.900|YES 0.88×5.0|215|13:01:02.660 SIGNAL_REVERSED / -0.829|ACK / 13:01:08.353|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:440)|NORMAL|13:01:14.836|VALID_YES / +0.509|YES 0.92×5.0|218|13:01:14.846 SIGNAL_REVERSED / -0.405|REJECTED / 13:01:21.248|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:651)|NORMAL|13:01:42.391|VALID_YES / +0.340|YES 0.95×5.0|209|13:01:44.392 ORDER_DEADLINE|ACK / 13:01:51.165|0；|

相关事件：

- [13:00:22.803](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:23) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.4332652058055002199053113600
- [13:00:48.486](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:223) 等待 OBI_NO_SIGNAL；NONE / 0.2497215828940530106169723068
- [13:00:49.884](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:227) 等待 WS_BOOK_STALE；INVALID / None
- [13:00:58.930](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:310) 等待 OBI_NO_SIGNAL；NONE / 0.04616442610586095212742357691
- [13:01:12.594](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:418) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_NO / -0.881226236714745011843245937
- [13:01:27.765](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:518) 等待 WS_BOOK_STALE；INVALID / None
- [13:01:35.226](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:599) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_NO / -0.5124995501169695879071441425
- [13:02:47.362](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:1075) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [13:05:05.802](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fbe3d887a4484a46b8e689f9319fe2e9.jsonl:1896) 网络 PAUSED → PROBE：NETWORK_PROBE_READY

## 13:05 / ea5ef4acef3b455db2a24f4bce68970d

[btc-updown-5m-1792213500](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea5ef4acef3b455db2a24f4bce68970d.jsonl:1)；结束 13:10:08.960，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 -1.050000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea5ef4acef3b455db2a24f4bce68970d.jsonl:28)|NORMAL|13:05:29.204|VALID_NO / -0.476|NO 0.59×5.0|280|13:05:29.485 SIGNAL_REVERSED / +0.369|ACK / 13:05:39.477|5.0；13:05:29.000|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea5ef4acef3b455db2a24f4bce68970d.jsonl:179)|REBALANCE|13:05:49.460|库存补仓|YES 0.51×5.0|481|13:05:50.801 WS_BOOK_STALE|ACK / 13:05:53.649|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea5ef4acef3b455db2a24f4bce68970d.jsonl:232)|REBALANCE|13:05:55.743|库存补仓|YES 0.62×5.0|206|13:05:58.749 ORDER_DEADLINE|ACK / 13:06:11.522|5.0；13:05:56.000|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea5ef4acef3b455db2a24f4bce68970d.jsonl:384)|NORMAL|13:06:13.631|VALID_NO / -0.939|NO 0.51×5.0|219|13:06:15.220 SIGNAL_REVERSED / +0.435|ACK / 13:06:19.594|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea5ef4acef3b455db2a24f4bce68970d.jsonl:454)|NORMAL|13:06:21.634|VALID_YES / +0.509|YES 0.49×5.0|196|13:06:22.713 SIGNAL_REVERSED / -0.983|ACK / 13:06:28.380|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea5ef4acef3b455db2a24f4bce68970d.jsonl:576)|NORMAL|13:06:37.616|VALID_YES / +0.564|YES 0.47×5.0|212|13:06:38.270 SIGNAL_REVERSED / -0.305|ACK / 13:06:44.580|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea5ef4acef3b455db2a24f4bce68970d.jsonl:667)|NORMAL|13:06:46.692|VALID_YES / +0.832|YES 0.4×5.0|220|13:06:47.676 SIGNAL_REVERSED / -0.319|ACK / 13:06:55.031|0；|

相关事件：

- [13:05:39.014](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea5ef4acef3b455db2a24f4bce68970d.jsonl:109) 本地观察 CONFIRMED：NO 5.0 @ 0.59；trade=f0a4e12c-97ed-4295-b851-95a1a9c4bdd0
- [13:05:41.575](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea5ef4acef3b455db2a24f4bce68970d.jsonl:130) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.5195223766992138720074212385
- [13:06:09.213](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea5ef4acef3b455db2a24f4bce68970d.jsonl:336) 本地观察 CONFIRMED：YES 5.0 @ 0.62；trade=e56619bb-af19-476f-bbf0-bf1fde76bb0e
- [13:06:30.338](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea5ef4acef3b455db2a24f4bce68970d.jsonl:532) 等待 OBI_NO_SIGNAL；NONE / -0.1986829457839949281141992351
- [13:06:32.429](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea5ef4acef3b455db2a24f4bce68970d.jsonl:544) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.741308976578512725608421388
- [13:06:34.515](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea5ef4acef3b455db2a24f4bce68970d.jsonl:556) 等待 OBI_NO_SIGNAL；NONE / 0.1537896550750021187039181898
- [13:06:57.055](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea5ef4acef3b455db2a24f4bce68970d.jsonl:763) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.783853286261382341367709898
- [13:09:24.489](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea5ef4acef3b455db2a24f4bce68970d.jsonl:1207) 合并 5 对，状态 CONFIRMED，释放成本 6.05

## 13:10 / 825b26bc5c4b4496bc904c372d16f251

[btc-updown-5m-1792213800](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:1)；结束 13:15:20.399，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 5.0 / NO 0.0；残仓成本 2.750000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:28)|NORMAL|13:10:22.922|VALID_YES / +0.487|YES 0.48×5.0|606|13:10:24.624 SIGNAL_REVERSED / -0.981|ACK / 13:10:26.336|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:85)|NORMAL|13:10:30.227|VALID_NO / -0.716|NO 0.44×5.0|415|13:10:30.426 SIGNAL_REVERSED / +0.485|ACK / 13:10:33.436|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:166)|NORMAL|13:10:40.976|VALID_YES / +0.933|YES 0.49×5.0|210|13:10:42.348 SIGNAL_REVERSED / -0.577|ACK / 13:10:45.840|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:230)|NORMAL|13:10:47.890|VALID_NO / -0.954|NO 0.43×5.0|391|13:10:48.072 SIGNAL_REVERSED / +0.489|ACK / 13:10:52.934|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:301)|NORMAL|13:10:54.989|VALID_NO / -0.836|NO 0.47×5.0|293|13:10:56.301 SIGNAL_REVERSED / +0.895|ACK / 13:11:01.799|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:383)|NORMAL|13:11:03.919|VALID_NO / -0.650|NO 0.54×5.0|260|13:11:05.273 SIGNAL_REVERSED / +0.373|ACK / 13:11:11.391|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:483)|NORMAL|13:11:15.328|VALID_YES / +0.636|YES 0.26×5.0|292|13:11:17.154 SIGNAL_REVERSED / -0.340|ACK / 13:11:23.728|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:580)|NORMAL|13:11:25.762|VALID_NO / -0.707|NO 0.71×5.0|285|13:11:27.361 SIGNAL_REVERSED / +0.673|ACK / 13:11:35.713|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:705)|NORMAL|13:11:39.686|VALID_YES / +0.831|YES 0.29×5.0|217|13:11:41.372 SIGNAL_REVERSED / -0.307|ACK / 13:11:50.645|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:835)|NORMAL|13:11:54.067|VALID_YES / +0.395|YES 0.55×5.0|1094|13:11:55.877 SIGNAL_REVERSED / -0.745|ACK / 13:12:18.068|5.0；13:11:55.000|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:1093)|REBALANCE|13:12:20.090|库存补仓|NO 0.22×5.0|199|13:12:23.095 ORDER_DEADLINE|ACK / 13:12:33.512|0；|
|[w12](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:1239)|REBALANCE|13:12:35.542|库存补仓|NO 0.32×5.0|1186|13:12:35.874 WS_BOOK_STALE|REJECTED / 13:12:48.493|0；|

相关事件：

- [13:10:28.434](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:66) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.948205141057450523378695605
- [13:10:35.524](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:131) 等待 OBI_NO_SIGNAL；NONE / -0.06523179034053275246334851515
- [13:10:37.235](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:143) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [13:10:37.434](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:135) 等待 NETWORK_PAUSED；VALID_NO / -0.634668532862039925510337220
- [13:10:40.433](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:159) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [13:10:45.840](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:211) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [13:11:13.539](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:467) 等待 OBI_NO_SIGNAL；NONE / 0.2668383672832279715913525326
- [13:11:37.693](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:688) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.7534303600877403653713755065
- [13:11:52.281](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:814) 等待 WS_BOOK_STALE；INVALID / None
- [13:12:05.700](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:950) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [13:12:14.541](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:1032) 本地观察 CONFIRMED：YES 5.0 @ 0.55；trade=be7a29b6-5abf-452a-9f57-bc03fe080aeb
- [13:12:19.564](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:1086) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [13:12:33.512](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:1220) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [13:13:38.655](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:1680) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [13:15:09.019](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_825b26bc5c4b4496bc904c372d16f251.jsonl:2222) 网络 PAUSED → PROBE：NETWORK_PROBE_READY

## 13:15 / 4d91b0396f0b4463977321bd1ad1c108

[btc-updown-5m-1792214100](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_4d91b0396f0b4463977321bd1ad1c108.jsonl:1)；结束 13:20:08.799，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +1.550000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_4d91b0396f0b4463977321bd1ad1c108.jsonl:34)|NORMAL|13:15:33.094|VALID_YES / +0.719|YES 0.35×5.0|225|13:15:35.098 ORDER_DEADLINE|ACK / 13:15:36.713|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_4d91b0396f0b4463977321bd1ad1c108.jsonl:75)|NORMAL|13:15:38.836|VALID_YES / +0.817|YES 0.35×5.0|224|13:15:39.397 SIGNAL_REVERSED / -0.448|ACK / 13:15:49.791|5.0；13:15:39.000|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_4d91b0396f0b4463977321bd1ad1c108.jsonl:193)|REBALANCE|13:15:52.276|库存补仓|NO 0.55×5.0|327|13:15:55.282 ORDER_DEADLINE|ACK / 13:16:03.580|5.0；13:15:54.000|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_4d91b0396f0b4463977321bd1ad1c108.jsonl:310)|NORMAL|13:16:07.308|VALID_NO / -0.603|NO 0.56×5.0|194|13:16:09.309 ORDER_DEADLINE|ACK / 13:16:13.937|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_4d91b0396f0b4463977321bd1ad1c108.jsonl:382)|NORMAL|13:16:16.193|VALID_NO / -0.927|NO 0.57×5.0|239|13:16:16.974 SIGNAL_REVERSED / +0.316|ACK / 13:16:22.524|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_4d91b0396f0b4463977321bd1ad1c108.jsonl:463)|NORMAL|13:16:24.590|VALID_NO / -0.837|NO 0.59×5.0|211|13:16:24.815 SIGNAL_REVERSED / +0.450|ACK / 13:16:30.665|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_4d91b0396f0b4463977321bd1ad1c108.jsonl:550)|NORMAL|13:16:32.720|VALID_YES / +0.591|YES 0.42×5.0|296|13:16:33.386 SIGNAL_REVERSED / -0.439|ACK / 13:16:49.556|5.0；13:16:33.000|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_4d91b0396f0b4463977321bd1ad1c108.jsonl:737)|REBALANCE|13:16:51.591|库存补仓|NO 0.37×5.0|299|13:16:53.470 WS_BOOK_STALE|ACK / 13:17:01.680|5.0；13:16:51.000|

相关事件：

- [13:15:49.323](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_4d91b0396f0b4463977321bd1ad1c108.jsonl:167) 本地观察 CONFIRMED：YES 5.0 @ 0.35；trade=cc5560a6-e57c-4dfb-992c-986b1af2658e
- [13:16:02.963](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_4d91b0396f0b4463977321bd1ad1c108.jsonl:275) 本地观察 CONFIRMED：NO 5.0 @ 0.55；trade=1d6b5485-965a-49ed-ae5d-4a4ee423c41b
- [13:16:05.716](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_4d91b0396f0b4463977321bd1ad1c108.jsonl:297) 等待 OBI_NO_SIGNAL；NONE / -0.2665328317773403239324068841
- [13:16:44.443](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_4d91b0396f0b4463977321bd1ad1c108.jsonl:660) 本地观察 CONFIRMED：YES 5.0 @ 0.42；trade=45138022-423d-47a1-9000-13e1035df784
- [13:16:59.973](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_4d91b0396f0b4463977321bd1ad1c108.jsonl:813) 本地观察 CONFIRMED：NO 5.0 @ 0.37；trade=d22c41b3-1038-4c16-945a-7c751a6bda9a
- [13:19:25.633](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_4d91b0396f0b4463977321bd1ad1c108.jsonl:1263) 合并 10 对，状态 CONFIRMED，释放成本 8.45

## 13:20 / 1245ddafee7249f38af900e9a952e1d2

[btc-updown-5m-1792214400](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:1)；结束 13:25:08.877，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:58)|NORMAL|13:20:28.327|VALID_NO / -0.639|NO 0.84×5.0|202|13:20:28.789 SIGNAL_REVERSED / +0.321|ACK / 13:20:29.959|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:93)|NORMAL|13:20:32.082|VALID_NO / -0.685|NO 0.84×5.0|358|13:20:32.711 SIGNAL_REVERSED / +0.367|ACK / 13:20:35.259|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:232)|NORMAL|13:20:53.629|VALID_NO / -0.537|NO 0.82×5.0|219|13:20:54.255 SIGNAL_REVERSED / +0.358|ACK / 13:20:57.735|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:365)|NORMAL|13:21:13.254|VALID_NO / -0.764|NO 0.84×5.0|304|13:21:13.597 SIGNAL_REVERSED / +0.871|ACK / 13:21:18.031|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:447)|NORMAL|13:21:22.194|VALID_NO / -0.869|NO 0.86×5.0|216|13:21:24.197 ORDER_DEADLINE|ACK / 13:21:30.026|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:559)|NORMAL|13:21:37.109|VALID_NO / -0.933|NO 0.86×5.0|226|13:21:39.110 ORDER_DEADLINE|ACK / 13:21:45.428|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:651)|NORMAL|13:21:47.485|VALID_YES / +0.899|YES 0.34×5.0|276|13:21:49.488 ORDER_DEADLINE|ACK / 13:21:57.177|0；|

相关事件：

- [13:20:23.223](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:24) 等待 OBI_NO_SIGNAL；NONE / 0.2536408707103571151909520490
- [13:20:24.825](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:35) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.7672119248676686671269495635
- [13:20:26.374](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:43) 等待 OBI_NO_SIGNAL；NONE / -0.1166680389089497641703619191
- [13:20:37.335](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:137) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.709943001899936668777707410
- [13:20:38.724](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:141) 等待 WS_BOOK_STALE；INVALID / None
- [13:20:41.353](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:158) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.6169261873514702231050260705
- [13:20:43.332](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:169) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.3308289207375818726334421234
- [13:20:49.940](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:204) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.9353236473744322558368084145
- [13:20:51.738](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:215) 等待 OBI_NO_SIGNAL；NONE / 0.2736516298979255844583470530
- [13:20:59.588](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:286) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.766380878885923240847593223
- [13:21:03.028](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:304) 等待 WS_BOOK_STALE；INVALID / None
- [13:21:05.201](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:314) 等待 OBI_NO_SIGNAL；NONE / 0.02864707011711447712781544618
- [13:21:07.254](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:326) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.8761869790955048503893974585
- [13:21:11.475](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:349) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.7901129540049304826781090295
- [13:21:20.106](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:430) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.4518460723118596160640678365
- [13:21:32.008](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:527) 等待 OBI_NO_SIGNAL；NONE / 0.0699477669352552941652832328
- [13:21:33.616](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:538) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.6328960405849946494391819585
- [13:21:59.284](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:750) 等待 OBI_NO_SIGNAL；NONE / 0.1697093844107301706479070944
- [13:22:43.945](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:907) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [13:25:01.790](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1245ddafee7249f38af900e9a952e1d2.jsonl:1389) 网络 PAUSED → PROBE：NETWORK_PROBE_READY

## 13:25 / a544c0e3093c490e9537f4f063b4b32f

[btc-updown-5m-1792214700](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:1)；结束 13:30:10.980，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:37)|NORMAL|13:25:23.455|VALID_YES / +0.434|YES 0.24×5.0|222|13:25:25.458 ORDER_DEADLINE|ACK / 13:25:27.063|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:92)|NORMAL|13:25:32.151|VALID_YES / +0.841|YES 0.25×5.0|265|13:25:32.622 SIGNAL_REVERSED / -0.834|ACK / 13:25:35.474|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:145)|NORMAL|13:25:37.715|VALID_NO / -0.733|NO 0.72×5.0|319|13:25:39.719 ORDER_DEADLINE|ACK / 13:25:43.162|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:225)|NORMAL|13:25:47.234|VALID_NO / -0.588|NO 0.74×5.0|313|13:25:47.327 SIGNAL_REVERSED / +0.343|ACK / 13:25:51.978|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:313)|NORMAL|13:25:58.216|VALID_YES / +0.439|YES 0.2×5.0|217|13:26:00.219 ORDER_DEADLINE|ACK / 13:26:05.558|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:395)|NORMAL|13:26:07.538|VALID_YES / +0.427|YES 0.21×5.0|212|13:26:08.397 SIGNAL_REVERSED / -0.862|ACK / 13:26:14.941|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:497)|NORMAL|13:26:18.735|VALID_NO / -0.662|NO 0.74×5.0|215|13:26:20.738 ORDER_DEADLINE|ACK / 13:26:28.263|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:612)|NORMAL|13:26:32.314|VALID_NO / -0.826|NO 0.74×5.0|212|13:26:34.317 ORDER_DEADLINE|ACK / 13:26:42.650|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:767)|NORMAL|13:26:52.684|VALID_YES / +0.966|YES 0.44×5.0|658|13:26:54.278 SIGNAL_REVERSED / -0.385|ACK / 13:27:03.374|0；|

相关事件：

- [13:25:21.764](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:24) 等待 OBI_NO_SIGNAL；NONE / 0.2015218966766746039962729061
- [13:25:34.542](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:118) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [13:25:37.002](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:138) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [13:25:43.162](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:192) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [13:25:45.125](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:203) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.7928652543368445784547910875
- [13:25:54.019](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:286) 等待 OBI_NO_SIGNAL；NONE / 0.02712870943282649086352296998
- [13:26:16.959](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:481) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.7187217928654977007012727595
- [13:26:47.011](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:730) 等待 OBI_NO_SIGNAL；NONE / -0.2946013328858416838471496380
- [13:26:49.116](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:742) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.6923076923076923076923076925
- [13:26:50.705](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a544c0e3093c490e9537f4f063b4b32f.jsonl:746) 等待 WS_BOOK_STALE；INVALID / None

## 13:30 / 34939312e2e44e8aac15b3610cc6c691

[btc-updown-5m-1792215000](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:1)；结束 13:35:15.991，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 5.0；残仓成本 3.100000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:29)|NORMAL|13:30:23.004|VALID_NO / -0.895|NO 0.62×5.0|278|13:30:23.928 SIGNAL_REVERSED / +0.331|ACK / 13:30:34.751|5.0；13:30:23.000|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:141)|REBALANCE|13:30:36.869|库存补仓|YES 0.37×5.0|226|13:30:38.904 WS_BOOK_STALE|ACK / 13:30:41.801|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:195)|REBALANCE|13:30:43.914|库存补仓|YES 0.35×5.0|216|13:30:44.907 WS_BOOK_STALE|ACK / 13:30:48.456|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:264)|REBALANCE|13:30:52.233|库存补仓|YES 0.3×5.0|319|13:30:53.076 WS_BOOK_STALE|ACK / 13:30:57.488|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:334)|REBALANCE|13:30:59.504|库存补仓|YES 0.3×5.0|442|13:30:59.636 WS_BOOK_STALE|ACK / 13:31:05.598|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:415)|REBALANCE|13:31:07.711|库存补仓|YES 0.32×5.0|211|13:31:08.420 WS_BOOK_STALE|ACK / 13:31:14.446|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:506)|REBALANCE|13:31:16.500|库存补仓|YES 0.29×5.0|424|13:31:16.700 WS_BOOK_STALE|ACK / 13:31:24.814|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:612)|REBALANCE|13:31:26.921|库存补仓|YES 0.38×5.0|218|13:31:27.139 PLACEMENT_NOT_ACKNOWLEDGED|REJECTED / 13:31:36.532|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:721)|REBALANCE|13:31:38.487|库存补仓|YES 0.35×5.0|436|13:31:38.703 WS_BOOK_STALE|ACK / 13:31:47.322|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:874)|REBALANCE|13:31:57.406|库存补仓|YES 0.31×5.0|780|13:31:57.965 WS_BOOK_STALE|ACK / 13:32:07.203|0；|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:1026)|REBALANCE|13:32:15.077|库存补仓|YES 0.26×5.0|225|13:32:15.605 WS_BOOK_STALE|ACK / 13:32:25.755|0；|
|[w12](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:1161)|REBALANCE|13:32:27.831|库存补仓|YES 0.25×5.0|397|13:32:28.028 WS_BOOK_STALE|ACK / 13:32:39.208|0；|
|[w13](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:1305)|REBALANCE|13:32:41.328|库存补仓|YES 0.2×5.0|225|13:32:41.470 WS_BOOK_STALE|REJECTED / 13:32:52.937|0；|
|[w14](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:1448)|REBALANCE|13:32:55.132|库存补仓|YES 0.15×6.67|258|13:32:55.860 WS_BOOK_STALE|ACK / 13:33:07.816|0；|
|[w15](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:1611)|REBALANCE|13:33:11.723|库存补仓|YES 0.17×5.89|348|13:33:11.865 WS_BOOK_STALE|ACK / 13:33:25.135|0；|
|[w16](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:1790)|REBALANCE|13:33:29.370|库存补仓|YES 0.44×5.0|336|13:33:29.490 WS_BOOK_STALE|ACK / 13:33:44.067|0；|
|[w17](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:2015)|REBALANCE|13:33:56.234|库存补仓|YES 0.41×5.0|219|13:33:56.529 WS_BOOK_STALE|ACK / 13:34:11.452|0；|

相关事件：

- [13:30:34.045](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:112) 本地观察 CONFIRMED：NO 5.0 @ 0.62；trade=fb7cf47e-60b9-4f72-9bc1-47a814c755ce
- [13:30:50.106](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:242) 等待 WS_BOOK_STALE；INVALID / None
- [13:31:29.878](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:629) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [13:31:37.982](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:711) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [13:31:47.322](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:816) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [13:31:50.934](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:834) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.8772571698335890475628466895
- [13:31:54.666](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:849) 等待 WS_BOOK_STALE；INVALID / None
- [13:32:09.353](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:990) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.9539430505820446982694101255
- [13:32:12.980](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:1005) 等待 WS_BOOK_STALE；INVALID / None
- [13:33:27.283](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:1768) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.647577092511013215859030837
- [13:33:46.683](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:1957) 等待 WS_BOOK_STALE；INVALID / None
- [13:33:50.101](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:1976) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.800433653206242888731457032
- [13:34:12.793](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:2192) 等待 MINIMUM_ORDER_EXCEEDS_MAXIMUM；VALID_NO / -0.7374555854060143859953202185
- [13:34:29.726](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:2269) 等待 WS_BOOK_STALE；INVALID / None
- [13:34:31.336](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_34939312e2e44e8aac15b3610cc6c691.jsonl:2278) 等待 WS_EMPTY_BOOK；INVALID / None

## 13:35 / 0df72e2518964f29b5a8f623b424373b

[btc-updown-5m-1792215300](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:1)；结束 13:40:08.196，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:84)|NORMAL|13:35:35.172|VALID_NO / -0.301|NO 0.84×5.0|209|13:35:37.176 ORDER_DEADLINE|ACK / 13:35:38.826|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:138)|NORMAL|13:35:42.837|VALID_NO / -0.781|NO 0.9×5.0|220|13:35:44.837 ORDER_DEADLINE|ACK / 13:35:47.557|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:293)|NORMAL|13:36:09.071|VALID_NO / -0.734|NO 0.87×5.0|700|13:36:10.379 SIGNAL_REVERSED / +0.572|ACK / 13:36:13.871|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:366)|NORMAL|13:36:18.040|VALID_NO / -0.832|NO 0.88×5.0|288|13:36:20.042 ORDER_DEADLINE|ACK / 13:36:24.497|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:437)|NORMAL|13:36:26.612|VALID_NO / -0.945|NO 0.89×5.0|215|13:36:28.616 ORDER_DEADLINE|ACK / 13:36:34.082|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:530)|NORMAL|13:36:38.346|VALID_NO / -0.819|NO 0.9×5.0|219|13:36:40.349 ORDER_DEADLINE|ACK / 13:36:46.892|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:657)|NORMAL|13:36:54.159|VALID_NO / -0.993|NO 0.81×5.0|299|13:36:54.238 SIGNAL_REVERSED / +0.357|ACK / 13:37:01.843|0；|

相关事件：

- [13:35:24.747](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:26) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [13:35:24.751](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:15) 等待 NETWORK_PAUSED；VALID_YES / 0.726925177498634625887493173
- [13:35:26.364](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:27) 等待 WS_BOOK_STALE；INVALID / None
- [13:35:27.935](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:42) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [13:35:28.388](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:44) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.562247891455812247891455812
- [13:35:30.040](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:47) 等待 WS_BOOK_STALE；INVALID / None
- [13:35:32.162](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:64) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.741795509120781901672494418
- [13:35:38.826](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:111) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [13:35:40.843](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:122) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.7541815849943889274835675735
- [13:35:49.162](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:176) 等待 WS_BOOK_STALE；INVALID / None
- [13:35:51.120](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:192) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.911174276070349973352282821
- [13:35:53.224](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:204) 等待 OBI_NO_SIGNAL；NONE / -0.2229773348826836625702782962
- [13:35:54.829](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:215) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.521797245352882084831176163
- [13:36:03.306](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:256) 等待 OBI_NO_SIGNAL；NONE / 0.1962585622789881055416800613
- [13:36:04.898](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:267) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.9569619950506294308223845445
- [13:36:06.960](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:276) 等待 OBI_NO_SIGNAL；NONE / -0.2705174647913967103956416053
- [13:36:16.065](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:349) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.811970841384155981029334270
- [13:36:36.251](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:513) 等待 OBI_NO_SIGNAL；NONE / -0.09632012229104632091845671245
- [13:36:50.669](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0df72e2518964f29b5a8f623b424373b.jsonl:628) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.528847332173231048258621112

## 13:40 / 9f3fa7138582470fa0a56c0b5279be66

[btc-updown-5m-1792215600](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:1)；结束 13:45:09.441，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 -0.050000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:29)|NORMAL|13:40:22.270|VALID_YES / +0.350|YES 0.46×5.0|281|13:40:23.013 SIGNAL_REVERSED / -0.394|ACK / 13:40:24.625|5.0；13:40:22.000|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:108)|REBALANCE|13:40:34.888|库存补仓|NO 0.55×5.0|350|13:40:37.892 ORDER_DEADLINE|ACK / 13:40:40.395|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:172)|REBALANCE|13:40:44.432|库存补仓|NO 0.55×5.0|196|13:40:47.435 ORDER_DEADLINE|ACK / 13:40:55.461|5.0；13:40:46.000|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:281)|NORMAL|13:40:57.777|VALID_NO / -0.770|NO 0.58×5.0|198|13:40:59.781 ORDER_DEADLINE|ACK / 13:41:03.768|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:361)|NORMAL|13:41:07.862|VALID_YES / +0.341|YES 0.36×5.0|207|13:41:09.865 ORDER_DEADLINE|ACK / 13:41:15.182|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:463)|NORMAL|13:41:21.115|VALID_YES / +0.342|YES 0.43×5.0|197|13:41:23.115 ORDER_DEADLINE|ACK / 13:41:29.353|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:563)|NORMAL|13:41:33.002|VALID_YES / +0.547|YES 0.4×5.0|210|13:41:35.004 ORDER_DEADLINE|ACK / 13:41:42.451|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:677)|NORMAL|13:41:45.924|VALID_YES / +0.307|YES 0.37×5.0|208|13:41:46.192 SIGNAL_REVERSED / -0.393|ACK / 13:41:53.992|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:786)|NORMAL|13:41:55.960|VALID_NO / -0.520|NO 0.85×5.0|209|13:41:57.963 ORDER_DEADLINE|ACK / 13:42:06.787|0；|

相关事件：

- [13:40:30.700](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:73) 本地观察 CONFIRMED：YES 5.0 @ 0.46；trade=8fed84f0-2e5a-4cb0-a816-1a0e39f9a2d6
- [13:40:33.123](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:92) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.9827385319121391274327881585
- [13:40:54.084](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:244) 本地观察 CONFIRMED：NO 5.0 @ 0.55；trade=3bcb9bcd-3a3b-4877-9322-c2ce1c59ee93
- [13:41:19.176](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:441) 等待 NETWORK_RECOVERING；VALID_YES / 0.661752123997691955669631310
- [13:41:31.413](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:550) 等待 OBI_NO_SIGNAL；NONE / 0.01095101924473826972056971046
- [13:41:35.975](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:578) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [13:41:44.018](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:663) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [13:41:53.992](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:770) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [13:44:25.045](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9f3fa7138582470fa0a56c0b5279be66.jsonl:1298) 合并 5 对，状态 CONFIRMED，释放成本 5.05

## 13:45 / 466e5603c85d4d009cf85ebcc9bd66ae

[btc-updown-5m-1792215900](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:1)；结束 13:50:10.005，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 -0.450000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:29)|NORMAL|13:45:23.190|VALID_YES / +0.763|YES 0.21×5.0|357|13:45:25.192 ORDER_DEADLINE|ACK / 13:45:27.990|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:93)|NORMAL|13:45:33.433|VALID_YES / +0.381|YES 0.22×5.0|201|13:45:35.269 SIGNAL_REVERSED / -0.380|ACK / 13:45:37.626|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:149)|NORMAL|13:45:41.344|VALID_NO / -0.381|NO 0.76×5.0|216|13:45:43.345 ORDER_DEADLINE|ACK / 13:45:47.073|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:211)|NORMAL|13:45:49.195|VALID_NO / -0.504|NO 0.77×5.0|227|13:45:51.194 ORDER_DEADLINE|ACK / 13:45:55.180|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:288)|NORMAL|13:45:58.656|VALID_NO / -0.826|NO 0.79×5.0|200|13:46:00.223 SIGNAL_REVERSED / +0.604|ACK / 13:46:05.641|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:424)|NORMAL|13:46:17.743|VALID_NO / -0.899|NO 0.66×5.0|218|13:46:19.743 ORDER_DEADLINE|ACK / 13:46:26.050|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:516)|NORMAL|13:46:28.014|VALID_NO / -0.996|NO 0.68×5.0|202|13:46:29.259 SIGNAL_REVERSED / +0.376|ACK / 13:46:45.009|5.0；13:46:28.000|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:704)|REBALANCE|13:46:47.059|库存补仓|YES 0.36×5.0|285|13:46:50.064 ORDER_DEADLINE|ACK / 13:46:58.360|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:829)|REBALANCE|13:47:02.434|库存补仓|YES 0.38×5.0|214|13:47:05.439 ORDER_DEADLINE|ACK / 13:47:15.007|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:957)|REBALANCE|13:47:17.045|库存补仓|YES 0.41×5.0|299|13:47:20.048 ORDER_DEADLINE|ACK / 13:47:29.898|5.0；13:47:17.000|

相关事件：

- [13:45:30.082](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:69) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.750957258039411013915263207
- [13:45:31.982](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:80) 等待 OBI_NO_SIGNAL；NONE / 0.2069136935490038621850024856
- [13:45:56.702](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:267) 等待 WS_BOOK_STALE；INVALID / None
- [13:46:07.877](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:365) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.832037874968077373897441606
- [13:46:12.232](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:390) 等待 OBI_NO_SIGNAL；NONE / 0.2367926327548156826417459063
- [13:46:13.658](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:398) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.4246764169898602869024811925
- [13:46:15.615](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:407) 等待 OBI_NO_SIGNAL；NONE / -0.01505815437342764429212090276
- [13:46:44.538](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:678) 本地观察 CONFIRMED：NO 5.0 @ 0.68；trade=f472b255-556f-4e45-8eb9-fcf972ccd12b
- [13:47:00.527](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:812) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.792665901834215655106638838
- [13:47:07.470](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:858) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [13:47:16.637](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:950) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [13:47:26.600](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:1035) 本地观察 CONFIRMED：YES 5.0 @ 0.41；trade=795e19b2-845b-4327-9cb5-5587b506f919
- [13:47:29.898](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:1077) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [13:49:26.295](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_466e5603c85d4d009cf85ebcc9bd66ae.jsonl:1403) 合并 5 对，状态 CONFIRMED，释放成本 5.45

## 13:50 / 0d356257058b4148ab3cc03e9676b769

[btc-updown-5m-1792216200](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0d356257058b4148ab3cc03e9676b769.jsonl:1)；结束 13:55:10.672，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0d356257058b4148ab3cc03e9676b769.jsonl:29)|NORMAL|13:50:22.752|VALID_NO / -0.667|NO 0.57×5.0|216|13:50:24.756 ORDER_DEADLINE|ACK / 13:50:26.378|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0d356257058b4148ab3cc03e9676b769.jsonl:86)|NORMAL|13:50:31.533|VALID_NO / -0.350|NO 0.57×5.0|215|13:50:33.537 ORDER_DEADLINE|ACK / 13:50:36.066|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0d356257058b4148ab3cc03e9676b769.jsonl:137)|NORMAL|13:50:38.179|VALID_NO / -0.637|NO 0.57×5.0|203|13:50:40.178 ORDER_DEADLINE|ACK / 13:50:44.086|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0d356257058b4148ab3cc03e9676b769.jsonl:201)|NORMAL|13:50:46.190|VALID_NO / -0.620|NO 0.62×5.0|209|13:50:48.192 ORDER_DEADLINE|ACK / 13:50:52.619|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0d356257058b4148ab3cc03e9676b769.jsonl:284)|NORMAL|13:50:56.846|VALID_YES / +0.886|YES 0.34×5.0|226|13:50:58.849 ORDER_DEADLINE|ACK / 13:51:04.458|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0d356257058b4148ab3cc03e9676b769.jsonl:366)|NORMAL|13:51:06.592|VALID_YES / +0.896|YES 0.36×5.0|205|13:51:07.417 SIGNAL_REVERSED / -0.349|ACK / 13:51:13.517|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0d356257058b4148ab3cc03e9676b769.jsonl:460)|NORMAL|13:51:15.849|VALID_NO / -0.689|NO 0.36×5.0|312|13:51:15.946 SIGNAL_REVERSED / +0.791|ACK / 13:51:23.566|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0d356257058b4148ab3cc03e9676b769.jsonl:597)|NORMAL|13:51:32.888|VALID_YES / +0.523|YES 0.72×5.0|213|13:51:34.181 SIGNAL_REVERSED / -0.693|ACK / 13:51:42.334|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0d356257058b4148ab3cc03e9676b769.jsonl:709)|NORMAL|13:51:44.380|VALID_YES / +0.779|YES 0.74×5.0|213|13:51:46.380 ORDER_DEADLINE|ACK / 13:51:55.813|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0d356257058b4148ab3cc03e9676b769.jsonl:833)|NORMAL|13:51:58.156|VALID_NO / -0.777|NO 0.22×5.0|224|13:51:58.637 SIGNAL_REVERSED / +0.316|ACK / 13:52:08.664|0；|

相关事件：

- [13:50:28.488](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0d356257058b4148ab3cc03e9676b769.jsonl:65) 等待 SIGNAL_EPISODE_UNAVAILABLE；VALID_NO / -0.4402375372319069272355484178
- [13:50:29.880](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0d356257058b4148ab3cc03e9676b769.jsonl:75) 等待 OBI_NO_SIGNAL；NONE / -0.1764546345941209605073748774
- [13:51:25.182](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0d356257058b4148ab3cc03e9676b769.jsonl:545) 等待 WS_BOOK_STALE；INVALID / None
- [13:51:27.207](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0d356257058b4148ab3cc03e9676b769.jsonl:561) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.5838150289017341040462427745
- [13:51:29.301](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_0d356257058b4148ab3cc03e9676b769.jsonl:573) 等待 OBI_NO_SIGNAL；NONE / -0.2712399205328970433563164661

## 13:55 / c1f3268e04494adda7d59b322de768a3

[btc-updown-5m-1792216500](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:1)；结束 14:00:48.096，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 -0.450000 USDC；期末 YES 0.0 / NO 5.0；残仓成本 2.400000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:28)|NORMAL|13:55:22.545|VALID_NO / -0.991|NO 0.59×5.0|299|13:55:24.321 SIGNAL_REVERSED / +0.507|ACK / 13:55:25.918|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:82)|NORMAL|13:55:30.021|VALID_NO / -0.851|NO 0.58×5.0|209|13:55:30.511 SIGNAL_REVERSED / +0.357|ACK / 13:55:44.528|5.0；13:55:30.000|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:230)|REBALANCE|13:55:46.868|库存补仓|YES 0.41×5.0|223|13:55:49.869 ORDER_DEADLINE|ACK / 13:55:52.900|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:290)|REBALANCE|13:55:54.886|库存补仓|YES 0.45×5.0|204|13:55:55.642 WS_BOOK_STALE|ACK / 13:56:00.764|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:363)|REBALANCE|13:56:02.830|库存补仓|YES 0.61×5.0|212|13:56:05.833 ORDER_DEADLINE|ACK / 13:56:11.605|5.0；13:56:02.000|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:489)|NORMAL|13:56:19.808|VALID_NO / -0.362|NO 0.38×5.0|1348|13:56:21.739 SIGNAL_REVERSED / +0.683|ACK / 13:56:35.545|5.0；13:56:21.000|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:671)|REBALANCE|13:56:39.771|库存补仓|YES 0.37×5.0|210|13:56:39.981 PLACEMENT_NOT_ACKNOWLEDGED|REJECTED / 13:56:46.432|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:761)|REBALANCE|13:56:48.535|库存补仓|YES 0.57×5.0|198|13:56:51.538 ORDER_DEADLINE|ACK / 13:56:58.854|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:867)|REBALANCE|13:57:01.178|库存补仓|YES 0.63×5.0|593|13:57:01.302 WS_BOOK_STALE|REJECTED / 13:57:10.004|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:1025)|REBALANCE|13:57:19.109|库存补仓|YES 0.64×5.0|289|13:57:22.112 ORDER_DEADLINE|ACK / 13:57:30.829|0；|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:1199)|REBALANCE|13:57:40.787|库存补仓|YES 0.71×5.0|220|13:57:41.052 WS_BOOK_STALE|ACK / 13:57:50.531|0；|

相关事件：

- [13:55:27.919](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:64) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.939948794307420894084808814
- [13:55:44.068](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:204) 本地观察 CONFIRMED：NO 5.0 @ 0.58；trade=31778d1d-6bc3-40fb-83f5-309804e15cc6
- [13:56:10.885](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:422) 本地观察 CONFIRMED：YES 5.0 @ 0.61；trade=fe1f7f64-9af5-4316-9a19-91b00e2bf931
- [13:56:13.680](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:446) 等待 OBI_NO_SIGNAL；NONE / 0.2307692307692307692307692308
- [13:56:15.583](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:458) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.845823998458239984582399846
- [13:56:17.402](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:469) 等待 OBI_NO_SIGNAL；NONE / -0.1241796987027008789243720161
- [13:56:31.330](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:589) 本地观察 CONFIRMED：NO 5.0 @ 0.38；trade=160e491b-c1d9-47f4-8e81-4ded8e1ad48f
- [13:56:37.557](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:650) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；NONE / -0.2028014616321559074299634592
- [14:00:39.065](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c1f3268e04494adda7d59b322de768a3.jsonl:2159) 合并 5 对，状态 CONFIRMED，释放成本 5.45

## 14:00 / 40b66d90669d46a1ac6a03c221c1c8d4

[btc-updown-5m-1792216800](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_40b66d90669d46a1ac6a03c221c1c8d4.jsonl:1)；结束 14:05:01.558，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|

相关事件：

- [14:02:00.597](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_40b66d90669d46a1ac6a03c221c1c8d4.jsonl:24) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [14:05:01.542](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_40b66d90669d46a1ac6a03c221c1c8d4.jsonl:653) 网络 PAUSED → PROBE：NETWORK_PROBE_READY

## 14:05 / 9adc60d6cb79401aab9bc1f5255c9e32

[btc-updown-5m-1792217100](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:1)；结束 14:10:12.133，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:31)|NORMAL|14:05:23.230|VALID_NO / -0.556|NO 0.32×5.0|200|14:05:25.233 ORDER_DEADLINE|ACK / 14:05:26.806|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:82)|NORMAL|14:05:30.730|VALID_YES / +0.733|YES 0.61×5.0|211|14:05:32.067 SIGNAL_REVERSED / -0.333|ACK / 14:05:34.355|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:130)|NORMAL|14:05:36.465|VALID_NO / -0.583|NO 0.36×5.0|208|14:05:38.463 ORDER_DEADLINE|ACK / 14:05:41.862|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:192)|NORMAL|14:05:43.981|VALID_NO / -0.697|NO 0.32×5.0|208|14:05:45.983 ORDER_DEADLINE|ACK / 14:05:50.761|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:267)|NORMAL|14:05:52.877|VALID_NO / -0.984|NO 0.32×5.0|799|14:05:54.879 ORDER_DEADLINE|ACK / 14:06:00.197|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:349)|NORMAL|14:06:02.162|VALID_YES / +0.597|YES 0.66×5.0|215|14:06:04.036 SIGNAL_REVERSED / -0.716|ACK / 14:06:10.031|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:438)|NORMAL|14:06:12.133|VALID_YES / +0.515|YES 0.7×5.0|295|14:06:12.736 SIGNAL_REVERSED / -0.431|ACK / 14:06:20.168|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:583)|NORMAL|14:06:30.461|VALID_YES / +0.892|YES 0.86×5.0|209|14:06:30.987 SIGNAL_REVERSED / -0.784|ACK / 14:06:39.126|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:718)|NORMAL|14:06:45.928|VALID_YES / +0.510|YES 0.89×5.0|211|14:06:46.693 SIGNAL_REVERSED / -0.979|ACK / 14:06:55.910|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:849)|NORMAL|14:06:59.744|VALID_YES / +0.865|YES 0.62×5.0|288|14:07:00.344 MARKET_SUBMISSION_CUTOFF|ACK / 14:07:10.804|0；|

相关事件：

- [14:05:28.874](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:66) 等待 OBI_NO_SIGNAL；NONE / 0.04116935542633964353896151212
- [14:06:22.269](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:535) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_NO / -0.5010712798251602626031026325
- [14:06:25.276](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:546) 等待 WS_BOOK_STALE；INVALID / None
- [14:06:26.753](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:554) 等待 WS_RECONNECT_REQUIRED；INVALID / None
- [14:06:29.043](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:570) 等待 OBI_NO_SIGNAL；NONE / -0.05054376758688873678606738155
- [14:06:41.277](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:690) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_NO / -0.3866853884907072374765655740
- [14:06:44.298](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:707) 等待 OBI_NO_SIGNAL；NONE / 0.06480574989851715657011867525
- [14:06:57.955](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:833) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.995370691850103002106335208
- [14:08:02.478](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:1147) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [14:10:02.384](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9adc60d6cb79401aab9bc1f5255c9e32.jsonl:1574) 网络 PAUSED → PROBE：NETWORK_PROBE_READY

## 14:10 / 140333c80b7e4a759569c74b4e1efc89

[btc-updown-5m-1792217400](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:1)；结束 14:15:15.070，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 5.0；残仓成本 3.450000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:28)|NORMAL|14:10:22.637|VALID_NO / -0.923|NO 0.63×5.0|520|14:10:24.639 ORDER_DEADLINE|ACK / 14:10:26.585|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:71)|NORMAL|14:10:28.618|VALID_YES / +0.834|YES 0.39×5.0|278|14:10:29.126 SIGNAL_REVERSED / -0.481|ACK / 14:10:31.903|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:121)|NORMAL|14:10:34.040|VALID_NO / -0.744|NO 0.6×5.0|222|14:10:36.042 ORDER_DEADLINE|ACK / 14:10:39.505|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:182)|NORMAL|14:10:41.501|VALID_NO / -0.758|NO 0.67×5.0|209|14:10:42.352 SIGNAL_REVERSED / +0.878|ACK / 14:10:46.781|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:279)|NORMAL|14:10:54.031|VALID_NO / -0.950|NO 0.65×5.0|217|14:10:54.358 SIGNAL_REVERSED / +0.787|ACK / 14:10:59.852|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:358)|NORMAL|14:11:01.709|VALID_NO / -0.368|NO 0.67×5.0|215|14:11:03.712 ORDER_DEADLINE|ACK / 14:11:10.132|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:491)|NORMAL|14:11:19.640|VALID_NO / -0.795|NO 0.62×5.0|253|14:11:21.643 ORDER_DEADLINE|ACK / 14:11:29.249|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:602)|NORMAL|14:11:31.350|VALID_NO / -0.988|NO 0.67×5.0|369|14:11:31.499 SIGNAL_REVERSED / +0.338|ACK / 14:11:39.840|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:709)|NORMAL|14:11:41.861|VALID_NO / -0.694|NO 0.69×5.0|305|14:11:43.864 ORDER_DEADLINE|ACK / 14:12:03.695|5.0；14:11:43.000|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:980)|REBALANCE|14:12:12.514|库存补仓|YES 0.39×5.0|317|14:12:12.622 WS_BOOK_STALE|ACK / 14:12:23.232|0；|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:1112)|REBALANCE|14:12:25.538|库存补仓|YES 0.35×5.0|381|14:12:25.717 WS_BOOK_STALE|REJECTED / 14:12:36.076|0；|
|[w12](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:1248)|REBALANCE|14:12:38.544|库存补仓|YES 0.36×5.0|322|14:12:38.645 WS_BOOK_STALE|ACK / 14:12:50.025|0；|
|[w13](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:1401)|REBALANCE|14:12:54.030|库存补仓|YES 0.5×5.0|387|14:12:54.218 WS_BOOK_STALE|REJECTED / 14:13:05.809|0；|
|[w14](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:1544)|REBALANCE|14:13:07.875|库存补仓|YES 0.33×5.0|300|14:13:08.443 WS_BOOK_STALE|ACK / 14:13:20.692|0；|
|[w15](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:1702)|REBALANCE|14:13:23.008|库存补仓|YES 0.34×5.0|322|14:13:23.116 WS_BOOK_STALE|ACK / 14:13:36.273|0；|
|[w16](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:1864)|REBALANCE|14:13:38.262|库存补仓|YES 0.35×5.0|225|14:13:39.278 WS_BOOK_STALE|ACK / 14:13:53.617|0；|
|[w17](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:2088)|REBALANCE|14:14:04.457|库存补仓|YES 0.39×5.0|325|14:14:04.560 WS_BOOK_STALE|REJECTED / 14:14:19.060|0；|

相关事件：

- [14:10:48.318](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:239) 等待 WS_BOOK_STALE；INVALID / None
- [14:10:51.922](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:262) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.631098661259000842083555311
- [14:11:12.476](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:447) 等待 OBI_NO_SIGNAL；NONE / 0.2022702244811520542143159678
- [14:11:14.045](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:451) 等待 WS_BOOK_STALE；INVALID / None
- [14:11:16.353](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:467) 等待 OBI_NO_SIGNAL；NONE / 0.1810452239267167050427120401
- [14:11:57.354](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:848) 本地观察 CONFIRMED：NO 5.0 @ 0.69；trade=64fc2f3f-408a-4ac8-b958-a1e3ce81599f
- [14:12:05.297](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:927) 等待 WS_BOOK_STALE；INVALID / None
- [14:12:07.422](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:943) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.3833317410273558166937358683
- [14:12:08.983](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:947) 等待 WS_BOOK_STALE；INVALID / None
- [14:12:52.116](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:1381) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.6055825242718446601941747575
- [14:13:59.413](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:2048) 等待 WS_BOOK_STALE；INVALID / None
- [14:14:21.350](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:2255) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.4641576338800016117983640247
- [14:14:23.265](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:2267) 等待 WS_EMPTY_BOOK；INVALID / None
- [14:15:06.618](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:2496) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [14:15:14.559](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_140333c80b7e4a759569c74b4e1efc89.jsonl:2542) 网络 PAUSED → PROBE：NETWORK_PROBE_READY

## 14:15 / 9df4da340bad496d8e2822fbfdfea4fd

[btc-updown-5m-1792217700](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9df4da340bad496d8e2822fbfdfea4fd.jsonl:1)；结束 14:20:10.120，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9df4da340bad496d8e2822fbfdfea4fd.jsonl:53)|NORMAL|14:15:27.775|VALID_NO / -0.625|NO 0.52×5.0|208|14:15:28.669 SIGNAL_REVERSED / +0.302|ACK / 14:15:30.280|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9df4da340bad496d8e2822fbfdfea4fd.jsonl:104)|NORMAL|14:15:34.393|VALID_YES / +0.945|YES 0.55×5.0|1455|14:15:35.193 SIGNAL_REVERSED / -0.519|REJECTED / 14:15:38.168|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9df4da340bad496d8e2822fbfdfea4fd.jsonl:185)|NORMAL|14:15:44.725|VALID_YES / +0.644|YES 0.6×5.0|300|14:15:46.728 ORDER_DEADLINE|ACK / 14:15:49.690|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9df4da340bad496d8e2822fbfdfea4fd.jsonl:257)|NORMAL|14:15:54.065|VALID_YES / +0.588|YES 0.53×5.0|212|14:15:55.232 SIGNAL_REVERSED / -0.630|ACK / 14:15:58.692|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9df4da340bad496d8e2822fbfdfea4fd.jsonl:376)|NORMAL|14:16:09.109|VALID_YES / +0.860|YES 0.56×5.0|213|14:16:10.489 SIGNAL_REVERSED / -0.368|ACK / 14:16:15.245|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9df4da340bad496d8e2822fbfdfea4fd.jsonl:516)|NORMAL|14:16:27.274|VALID_NO / -0.986|NO 0.42×5.0|214|14:16:28.096 SIGNAL_REVERSED / +0.729|ACK / 14:16:33.807|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9df4da340bad496d8e2822fbfdfea4fd.jsonl:634)|NORMAL|14:16:40.848|VALID_YES / +0.564|YES 0.54×5.0|217|14:16:42.852 ORDER_DEADLINE|ACK / 14:16:49.118|0；|

相关事件：

- [14:15:23.978](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9df4da340bad496d8e2822fbfdfea4fd.jsonl:25) 等待 OBI_NO_SIGNAL；NONE / -0.0910281121576899298105247845
- [14:15:25.866](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9df4da340bad496d8e2822fbfdfea4fd.jsonl:36) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.8228633054731733620922081425
- [14:15:31.739](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9df4da340bad496d8e2822fbfdfea4fd.jsonl:79) 等待 WS_BOOK_STALE；INVALID / None
- [14:15:41.515](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9df4da340bad496d8e2822fbfdfea4fd.jsonl:159) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.8818584719068200131858471905
- [14:16:56.443](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9df4da340bad496d8e2822fbfdfea4fd.jsonl:722) 等待 WS_BOOK_STALE；INVALID / None
- [14:17:09.712](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9df4da340bad496d8e2822fbfdfea4fd.jsonl:845) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [14:20:03.295](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_9df4da340bad496d8e2822fbfdfea4fd.jsonl:1874) 网络 PAUSED → PROBE：NETWORK_PROBE_READY

## 14:20 / ea9bd55b00dd4f58b5523cc8f018b412

[btc-updown-5m-1792218000](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:1)；结束 14:25:11.132，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 -0.600000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:29)|NORMAL|14:20:23.151|VALID_NO / -0.961|NO 0.68×5.0|205|14:20:25.155 ORDER_DEADLINE|ACK / 14:20:35.277|5.0；14:20:25.000|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:137)|REBALANCE|14:20:37.568|库存补仓|YES 0.37×5.0|216|14:20:38.704 WS_BOOK_STALE|ACK / 14:20:48.686|5.0；14:20:38.000;14:20:38.000|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:268)|NORMAL|14:20:52.588|VALID_NO / -0.952|NO 0.67×5.0|305|14:20:54.588 ORDER_DEADLINE|ACK / 14:20:58.065|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:331)|NORMAL|14:21:00.076|VALID_YES / +0.529|YES 0.33×5.0|317|14:21:00.807 SIGNAL_REVERSED / -0.477|ACK / 14:21:05.267|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:413)|NORMAL|14:21:09.192|VALID_YES / +0.836|YES 0.64×5.0|263|14:21:09.610 SIGNAL_REVERSED / -0.507|ACK / 14:21:15.309|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:503)|NORMAL|14:21:18.874|VALID_NO / -0.787|NO 0.36×5.0|199|14:21:19.970 SIGNAL_REVERSED / +0.485|ACK / 14:21:34.040|5.0；14:21:19.000|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:677)|REBALANCE|14:21:36.236|库存补仓|YES 0.68×5.0|336|14:21:36.371 WS_BOOK_STALE|ACK / 14:21:43.537|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:780)|REBALANCE|14:21:45.563|库存补仓|YES 0.67×5.0|344|14:21:45.703 WS_BOOK_STALE|REJECTED / 14:21:53.299|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:895)|REBALANCE|14:21:57.472|库存补仓|YES 0.65×5.0|207|14:21:57.574 WS_BOOK_STALE|ACK / 14:22:05.895|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:1007)|REBALANCE|14:22:07.763|库存补仓|YES 0.67×5.0|284|14:22:07.982 WS_BOOK_STALE|ACK / 14:22:17.285|0；|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:1127)|REBALANCE|14:22:19.323|库存补仓|YES 0.71×5.0|288|14:22:19.867 WS_BOOK_STALE|ACK / 14:22:41.022|5.0；14:22:19.000|

相关事件：

- [14:20:34.785](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:111) 本地观察 CONFIRMED：NO 5.0 @ 0.68；trade=76d07631-fc4a-4ac0-a754-5fd3ce23fd35
- [14:20:44.499](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:197) 本地观察 CONFIRMED：YES 0.01 @ 0.37；trade=ff7dfcde-2339-4323-bd02-163f004f90bc
- [14:20:48.235](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:231) 本地观察 CONFIRMED：YES 4.99 @ 0.37；trade=ec009a82-bf5d-4266-9487-8be6f1b8e5d8
- [14:20:50.708](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:252) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.947256589158968640274868518
- [14:20:57.608](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:308) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [14:20:59.697](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:324) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [14:21:05.267](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:387) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [14:21:06.859](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:390) 等待 WS_BOOK_STALE；INVALID / None
- [14:21:17.401](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:489) 等待 OBI_NO_SIGNAL；NONE / 0.2719154364862402041188263167
- [14:21:28.975](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:599) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [14:21:32.644](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:637) 本地观察 CONFIRMED：NO 5.0 @ 0.36；trade=700f51bd-ee19-4589-8092-781486508fd9
- [14:21:35.626](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:667) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [14:21:43.537](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:761) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [14:21:55.482](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:875) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.718221326310541773688374462
- [14:22:34.998](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:1289) 本地观察 CONFIRMED：YES 5.0 @ 0.71；trade=05f6c002-be0d-4567-a32b-de21878321f8
- [14:23:53.360](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:1613) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [14:24:03.617](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:1674) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [14:24:27.006](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ea9bd55b00dd4f58b5523cc8f018b412.jsonl:1655) 合并 10 对，状态 CONFIRMED，释放成本 10.60

## 14:25 / d4009e85f3384bb4a57c18b074f538a4

[btc-updown-5m-1792218300](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:1)；结束 14:30:10.795，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:41)|NORMAL|14:25:24.809|VALID_NO / -0.661|NO 0.52×5.0|208|14:25:26.811 ORDER_DEADLINE|ACK / 14:25:28.429|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:85)|NORMAL|14:25:30.326|VALID_YES / +0.420|YES 0.46×5.0|518|14:25:30.529 SIGNAL_REVERSED / -0.669|ACK / 14:25:33.641|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:137)|NORMAL|14:25:35.750|VALID_NO / -0.456|NO 0.44×5.0|210|14:25:37.677 SIGNAL_REVERSED / +0.428|ACK / 14:25:40.754|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:199)|NORMAL|14:25:42.891|VALID_NO / -0.583|NO 0.52×5.0|421|14:25:43.102 SIGNAL_REVERSED / +0.492|ACK / 14:25:47.691|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:315)|NORMAL|14:25:58.842|VALID_YES / +0.862|YES 0.44×5.0|219|14:26:00.845 ORDER_DEADLINE|ACK / 14:26:05.977|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:430)|NORMAL|14:26:14.792|VALID_YES / +0.580|YES 0.61×5.0|196|14:26:16.793 ORDER_DEADLINE|ACK / 14:26:22.616|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:577)|NORMAL|14:26:35.194|VALID_YES / +0.919|YES 0.5×5.0|202|14:26:36.963 SIGNAL_REVERSED / -0.730|ACK / 14:26:44.281|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:720)|NORMAL|14:26:53.813|VALID_NO / -0.351|NO 0.2×5.0|699|14:26:55.468 SIGNAL_REVERSED / +0.397|ACK / 14:27:03.952|0；|

相关事件：

- [14:25:22.860](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:25) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.7967768665443084047735210525
- [14:25:49.630](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:254) 等待 NETWORK_RECOVERING；NONE / -0.07359421051095742507805888705
- [14:25:49.630](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:264) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [14:25:51.115](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:265) 等待 NETWORK_PAUSED；NONE / -0.0807098679445032595977043517
- [14:25:52.698](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:280) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [14:25:53.217](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:282) 等待 OBI_NO_SIGNAL；NONE / -0.2519743832744608138766836105
- [14:25:56.727](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:300) 等待 WS_BOOK_STALE；INVALID / None
- [14:26:05.977](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:379) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [14:26:08.094](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:391) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.978388891890431681884488627
- [14:26:10.235](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:403) 等待 OBI_NO_SIGNAL；NONE / 0.06587915818058384249830278345
- [14:26:24.672](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:515) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.915452708472484083972369945
- [14:26:26.561](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:527) 等待 OBI_NO_SIGNAL；NONE / 0.02698606491811560644971161537
- [14:26:28.520](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:539) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.908710106114403202417162500
- [14:26:31.127](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:551) 等待 WS_BOOK_STALE；INVALID / None
- [14:26:46.430](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:674) 等待 OBI_NO_SIGNAL；NONE / 0.2854654950080429667247178315
- [14:26:51.731](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:699) 等待 WS_BOOK_STALE；INVALID / None
- [14:28:49.635](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:1182) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [14:30:02.605](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:1441) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [14:30:09.090](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_d4009e85f3384bb4a57c18b074f538a4.jsonl:1479) 网络 PROBE → PAUSED：NETWORK_REPEATED_SLOW

## 14:30 / b0a5b9b3a86d4752a56ff4a8ff95abc9

[btc-updown-5m-1792218600](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:1)；结束 14:35:09.892，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:30)|NORMAL|14:30:22.580|VALID_NO / -0.913|NO 0.72×5.0|209|14:30:24.582 ORDER_DEADLINE|ACK / 14:30:26.402|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:72)|NORMAL|14:30:28.506|VALID_YES / +0.583|YES 0.27×5.0|211|14:30:30.508 ORDER_DEADLINE|ACK / 14:30:32.971|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:121)|NORMAL|14:30:35.098|VALID_NO / -0.324|NO 0.73×5.0|214|14:30:37.100 ORDER_DEADLINE|ACK / 14:30:40.395|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:179)|NORMAL|14:30:42.259|VALID_NO / -0.358|NO 0.77×5.0|218|14:30:44.261 ORDER_DEADLINE|ACK / 14:30:48.266|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:257)|NORMAL|14:30:52.032|VALID_NO / -0.858|NO 0.77×5.0|304|14:30:54.032 ORDER_DEADLINE|ACK / 14:30:59.658|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:340)|NORMAL|14:31:01.654|VALID_NO / -0.567|NO 0.77×5.0|202|14:31:03.656 ORDER_DEADLINE|ACK / 14:31:09.909|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:466)|NORMAL|14:31:18.186|VALID_NO / -0.564|NO 0.79×5.0|212|14:31:20.189 ORDER_DEADLINE|ACK / 14:31:27.432|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:570)|NORMAL|14:31:29.767|VALID_NO / -0.850|NO 0.78×5.0|213|14:31:31.768 ORDER_DEADLINE|ACK / 14:31:39.968|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:739)|NORMAL|14:31:52.940|VALID_NO / -0.562|NO 0.83×5.0|365|14:31:53.349 SIGNAL_REVERSED / +0.516|ACK / 14:32:02.387|0；|

相关事件：

- [14:30:50.752](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:244) 等待 WS_BOOK_STALE；INVALID / None
- [14:31:12.036](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:427) 等待 OBI_NO_SIGNAL；NONE / -0.06921622413379850328903566005
- [14:31:15.163](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:438) 等待 NETWORK_RECOVERING；NONE / -0.2373167031855722231586044160
- [14:31:15.164](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:447) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [14:31:16.762](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:448) 等待 NETWORK_PAUSED；VALID_NO / -0.8097062262132782766597845825
- [14:31:18.162](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:463) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [14:31:24.191](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:517) 网络 PROBE → PAUSED：NETWORK_REPEATED_SLOW
- [14:31:29.276](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:564) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [14:31:42.082](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:679) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.3613137876738151737597303231
- [14:31:43.797](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:683) 等待 NETWORK_RECOVERING；VALID_YES / 0.4497527163257820109032494719
- [14:31:43.797](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:693) 网络 PROBE → PAUSED：NETWORK_REPEATED_SLOW
- [14:31:45.199](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:691) 等待 NETWORK_PAUSED；VALID_YES / 0.3400393614694948611414826154
- [14:31:52.796](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:736) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [14:32:02.387](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0a5b9b3a86d4752a56ff4a8ff95abc9.jsonl:845) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED

## 14:35 / b0eddca793ab4f0181bda02c431f09e4

[btc-updown-5m-1792218900](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0eddca793ab4f0181bda02c431f09e4.jsonl:1)；结束 14:40:10.293，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.550000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0eddca793ab4f0181bda02c431f09e4.jsonl:38)|NORMAL|14:35:24.679|VALID_YES / +0.984|YES 0.64×5.0|216|14:35:25.658 SIGNAL_REVERSED / -0.301|ACK / 14:35:26.800|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0eddca793ab4f0181bda02c431f09e4.jsonl:75)|NORMAL|14:35:28.920|VALID_YES / +0.803|YES 0.64×5.0|225|14:35:30.923 ORDER_DEADLINE|ACK / 14:35:33.372|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0eddca793ab4f0181bda02c431f09e4.jsonl:124)|NORMAL|14:35:35.508|VALID_NO / -0.676|NO 0.32×5.0|210|14:35:37.508 ORDER_DEADLINE|ACK / 14:35:41.668|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0eddca793ab4f0181bda02c431f09e4.jsonl:230)|NORMAL|14:35:51.801|VALID_NO / -0.313|NO 0.37×5.0|287|14:35:52.685 SIGNAL_REVERSED / +0.484|ACK / 14:35:56.868|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0eddca793ab4f0181bda02c431f09e4.jsonl:298)|NORMAL|14:35:58.877|VALID_NO / -0.994|NO 0.36×5.0|218|14:35:59.283 SIGNAL_REVERSED / +0.301|ACK / 14:36:04.485|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0eddca793ab4f0181bda02c431f09e4.jsonl:376)|NORMAL|14:36:06.814|VALID_YES / +0.754|YES 0.6×5.0|217|14:36:08.816 ORDER_DEADLINE|ACK / 14:36:15.455|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0eddca793ab4f0181bda02c431f09e4.jsonl:469)|NORMAL|14:36:17.828|VALID_YES / +0.568|YES 0.65×5.0|225|14:36:18.486 SIGNAL_REVERSED / -0.331|ACK / 14:36:25.794|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0eddca793ab4f0181bda02c431f09e4.jsonl:580)|NORMAL|14:36:29.729|VALID_NO / -0.885|NO 0.32×5.0|210|14:36:30.184 SIGNAL_REVERSED / +0.345|ACK / 14:36:38.129|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0eddca793ab4f0181bda02c431f09e4.jsonl:688)|NORMAL|14:36:40.454|VALID_YES / +0.652|YES 0.66×5.0|271|14:36:41.280 SIGNAL_REVERSED / -0.307|ACK / 14:37:00.971|5.0；14:36:41.000|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0eddca793ab4f0181bda02c431f09e4.jsonl:918)|REBALANCE|14:37:03.076|库存补仓|NO 0.23×5.0|211|14:37:06.080 ORDER_DEADLINE|ACK / 14:37:27.445|5.0；14:37:04.000|

相关事件：

- [14:35:22.913](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0eddca793ab4f0181bda02c431f09e4.jsonl:25) 等待 OBI_NO_SIGNAL；NONE / -0.03300785506013219744528955806
- [14:35:43.711](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0eddca793ab4f0181bda02c431f09e4.jsonl:182) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.979968349992988922497546123
- [14:36:54.822](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0eddca793ab4f0181bda02c431f09e4.jsonl:830) 本地观察 CONFIRMED：YES 5.0 @ 0.66；trade=c768709c-b342-4cb5-82b5-c1877246bb25
- [14:37:20.428](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0eddca793ab4f0181bda02c431f09e4.jsonl:1070) 本地观察 CONFIRMED：NO 5.0 @ 0.23；trade=b0324c3a-57e5-4c71-b4f8-86787ce33031
- [14:39:29.118](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b0eddca793ab4f0181bda02c431f09e4.jsonl:1486) 合并 5 对，状态 CONFIRMED，释放成本 4.45

## 14:40 / e32fa30f22554e2a93eff9782ce41a62

[btc-updown-5m-1792219200](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_e32fa30f22554e2a93eff9782ce41a62.jsonl:1)；结束 14:45:11.978，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_e32fa30f22554e2a93eff9782ce41a62.jsonl:45)|NORMAL|14:40:25.150|VALID_NO / -0.908|NO 0.31×5.0|222|14:40:27.150 ORDER_DEADLINE|ACK / 14:40:29.006|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_e32fa30f22554e2a93eff9782ce41a62.jsonl:87)|NORMAL|14:40:31.040|VALID_YES / +0.378|YES 0.68×5.0|297|14:40:33.040 ORDER_DEADLINE|ACK / 14:40:35.749|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_e32fa30f22554e2a93eff9782ce41a62.jsonl:138)|NORMAL|14:40:37.835|VALID_NO / -0.622|NO 0.31×5.0|222|14:40:38.932 SIGNAL_REVERSED / +0.301|ACK / 14:40:42.964|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_e32fa30f22554e2a93eff9782ce41a62.jsonl:200)|NORMAL|14:40:45.086|VALID_NO / -0.710|NO 0.36×5.0|225|14:40:46.670 SIGNAL_REVERSED / +0.394|ACK / 14:40:50.814|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_e32fa30f22554e2a93eff9782ce41a62.jsonl:278)|NORMAL|14:40:54.663|VALID_NO / -0.823|NO 0.39×5.0|412|14:40:56.664 ORDER_DEADLINE|ACK / 14:41:01.749|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_e32fa30f22554e2a93eff9782ce41a62.jsonl:358)|NORMAL|14:41:03.852|VALID_YES / +0.814|YES 0.62×5.0|204|14:41:04.663 SIGNAL_REVERSED / -0.667|ACK / 14:41:11.437|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_e32fa30f22554e2a93eff9782ce41a62.jsonl:448)|NORMAL|14:41:13.570|VALID_YES / +0.876|YES 0.65×5.0|217|14:41:13.900 SIGNAL_REVERSED / -0.301|ACK / 14:41:20.812|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_e32fa30f22554e2a93eff9782ce41a62.jsonl:547)|NORMAL|14:41:22.865|VALID_YES / +0.877|YES 0.65×5.0|219|14:41:24.865 ORDER_DEADLINE|ACK / 14:41:32.788|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_e32fa30f22554e2a93eff9782ce41a62.jsonl:656)|NORMAL|14:41:34.865|VALID_YES / +0.620|YES 0.68×5.0|256|14:41:36.305 SIGNAL_REVERSED / -0.644|ACK / 14:41:45.057|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_e32fa30f22554e2a93eff9782ce41a62.jsonl:776)|NORMAL|14:41:47.127|VALID_NO / -0.449|NO 0.3×5.0|281|14:41:47.534 SIGNAL_REVERSED / +0.332|ACK / 14:41:59.068|0；|

相关事件：

- [14:40:21.827](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_e32fa30f22554e2a93eff9782ce41a62.jsonl:24) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.655657062543921293042867182
- [14:40:23.525](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_e32fa30f22554e2a93eff9782ce41a62.jsonl:34) 等待 OBI_NO_SIGNAL；NONE / 0.2228108667344499741353168870
- [14:40:52.360](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_e32fa30f22554e2a93eff9782ce41a62.jsonl:256) 等待 WS_BOOK_STALE；INVALID / None
- [14:41:53.377](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_e32fa30f22554e2a93eff9782ce41a62.jsonl:828) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [14:45:02.053](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_e32fa30f22554e2a93eff9782ce41a62.jsonl:1530) 网络 PAUSED → PROBE：NETWORK_PROBE_READY

## 14:45 / 7b63552a2626494eabdc5b0c04371354

[btc-updown-5m-1792219500](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7b63552a2626494eabdc5b0c04371354.jsonl:1)；结束 14:50:10.436，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7b63552a2626494eabdc5b0c04371354.jsonl:29)|NORMAL|14:45:22.353|VALID_YES / +0.898|YES 0.42×5.0|204|14:45:22.667 SIGNAL_REVERSED / -0.335|ACK / 14:45:24.940|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7b63552a2626494eabdc5b0c04371354.jsonl:70)|NORMAL|14:45:27.038|VALID_NO / -0.873|NO 0.58×5.0|207|14:45:29.039 ORDER_DEADLINE|ACK / 14:45:31.581|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7b63552a2626494eabdc5b0c04371354.jsonl:120)|NORMAL|14:45:33.305|VALID_NO / -0.741|NO 0.6×5.0|283|14:45:35.307 ORDER_DEADLINE|ACK / 14:45:38.990|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7b63552a2626494eabdc5b0c04371354.jsonl:200)|NORMAL|14:45:44.078|VALID_NO / -0.986|NO 0.64×5.0|205|14:45:45.277 SIGNAL_REVERSED / +0.366|ACK / 14:45:49.663|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7b63552a2626494eabdc5b0c04371354.jsonl:272)|NORMAL|14:45:51.623|VALID_NO / -0.691|NO 0.68×5.0|208|14:45:53.626 ORDER_DEADLINE|ACK / 14:45:58.938|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7b63552a2626494eabdc5b0c04371354.jsonl:354)|NORMAL|14:46:01.054|VALID_NO / -0.817|NO 0.7×5.0|206|14:46:02.024 SIGNAL_REVERSED / +0.413|ACK / 14:46:08.008|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7b63552a2626494eabdc5b0c04371354.jsonl:443)|NORMAL|14:46:10.108|VALID_NO / -0.570|NO 0.72×5.0|209|14:46:11.455 SIGNAL_REVERSED / +0.347|ACK / 14:46:18.644|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7b63552a2626494eabdc5b0c04371354.jsonl:559)|NORMAL|14:46:23.759|VALID_NO / -0.467|NO 0.69×5.0|213|14:46:25.762 ORDER_DEADLINE|ACK / 14:46:33.832|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7b63552a2626494eabdc5b0c04371354.jsonl:670)|NORMAL|14:46:35.933|VALID_NO / -0.698|NO 0.69×5.0|205|14:46:37.932 ORDER_DEADLINE|ACK / 14:46:47.048|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7b63552a2626494eabdc5b0c04371354.jsonl:805)|NORMAL|14:46:50.460|VALID_YES / +0.954|YES 0.29×5.0|317|14:46:50.536 SIGNAL_REVERSED / -0.333|ACK / 14:47:00.686|0；|

相关事件：

- [14:45:37.603](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7b63552a2626494eabdc5b0c04371354.jsonl:152) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [14:45:40.571](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7b63552a2626494eabdc5b0c04371354.jsonl:178) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [14:45:41.085](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7b63552a2626494eabdc5b0c04371354.jsonl:180) 等待 OBI_NO_SIGNAL；NONE / -0.1005895331266538497009768798
- [14:45:49.663](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7b63552a2626494eabdc5b0c04371354.jsonl:256) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [14:46:48.691](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7b63552a2626494eabdc5b0c04371354.jsonl:780) 等待 NETWORK_RECOVERING；VALID_NO / -0.4375183399706560469503248795

## 14:50 / 1fa461eb587542f4b1c0b4f6fdc39bd1

[btc-updown-5m-1792219800](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:1)；结束 14:55:10.013，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:30)|NORMAL|14:50:22.572|VALID_YES / +0.831|YES 0.41×5.0|202|14:50:23.253 SIGNAL_REVERSED / -0.835|ACK / 14:50:25.313|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:86)|NORMAL|14:50:30.129|VALID_NO / -0.401|NO 0.56×5.0|266|14:50:32.132 ORDER_DEADLINE|ACK / 14:50:34.661|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:138)|NORMAL|14:50:37.114|VALID_NO / -0.946|NO 0.6×5.0|207|14:50:37.961 SIGNAL_REVERSED / +0.373|ACK / 14:50:41.792|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:198)|NORMAL|14:50:43.898|VALID_NO / -0.592|NO 0.6×5.0|207|14:50:44.701 SIGNAL_REVERSED / +0.328|ACK / 14:50:48.649|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:266)|NORMAL|14:50:50.673|VALID_YES / +0.808|YES 0.4×5.0|301|14:50:51.893 SIGNAL_REVERSED / -0.570|ACK / 14:50:57.144|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:400)|NORMAL|14:51:08.234|VALID_NO / -0.867|NO 0.26×5.0|216|14:51:08.885 SIGNAL_REVERSED / +0.446|ACK / 14:51:15.109|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:490)|NORMAL|14:51:17.113|VALID_YES / +0.756|YES 0.74×5.0|220|14:51:18.229 SIGNAL_REVERSED / -0.452|ACK / 14:51:25.424|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:591)|NORMAL|14:51:27.455|VALID_YES / +0.972|YES 0.83×5.0|315|14:51:29.455 ORDER_DEADLINE|ACK / 14:51:37.647|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:706)|NORMAL|14:51:39.667|VALID_NO / -0.620|NO 0.2×5.0|290|14:51:41.670 ORDER_DEADLINE|ACK / 14:51:50.729|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:848)|NORMAL|14:51:56.165|VALID_YES / +0.702|YES 0.88×5.0|211|14:51:58.164 ORDER_DEADLINE|ACK / 14:52:08.085|0；|

相关事件：

- [14:50:27.309](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:66) 等待 OBI_NO_SIGNAL；NONE / -0.03195200816882232811436351259
- [14:50:59.129](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:341) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.8969815596991861543216235705
- [14:51:00.975](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:352) 等待 OBI_NO_SIGNAL；NONE / -0.1981794538361508452535760728
- [14:51:02.317](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:356) 等待 WS_BOOK_STALE；INVALID / None
- [14:51:04.416](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:373) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.9700082477318737347229511885
- [14:51:37.187](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:685) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [14:51:39.269](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:699) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [14:51:50.729](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:813) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [14:51:52.802](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_1fa461eb587542f4b1c0b4f6fdc39bd1.jsonl:824) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_NO / -0.6033763390628506524138625925

## 14:55 / 30e6a48bfb0a45eb89ab3745b6cb17f2

[btc-updown-5m-1792220100](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:1)；结束 15:00:50.313，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.057705 USDC；期末 YES 0.0 / NO 5.004754；残仓成本 2.952805。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:30)|NORMAL|14:55:23.893|VALID_YES / +0.682|YES 0.39×5.0|290|14:55:25.299 SIGNAL_REVERSED / -0.530|ACK / 14:55:27.316|2.885246；14:55:25.000|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:111)|NORMAL|14:55:37.576|VALID_NO / -0.410|NO 0.62×7.89|222|14:55:38.155 SIGNAL_REVERSED / +0.311|ACK / 14:55:40.959|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:160)|NORMAL|14:55:42.931|VALID_YES / +0.883|YES 0.35×5.0|218|14:55:43.932 SIGNAL_REVERSED / -0.310|ACK / 14:55:47.608|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:239)|NORMAL|14:55:53.470|VALID_NO / -0.339|NO 0.59×7.89|235|14:55:55.474 ORDER_DEADLINE|ACK / 14:56:05.738|7.89；14:55:54.000|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:376)|REBALANCE|14:56:09.667|库存补仓|YES 0.4×5.01|299|14:56:10.399 WS_BOOK_STALE|ACK / 14:56:16.520|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:461)|REBALANCE|14:56:18.646|库存补仓|YES 0.39×5.01|221|14:56:19.862 WS_BOOK_STALE|ACK / 14:56:26.064|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:562)|REBALANCE|14:56:30.117|库存补仓|YES 0.69×5.01|735|14:56:30.220 WS_BOOK_STALE|REJECTED / 14:56:38.056|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:705)|REBALANCE|14:56:46.219|库存补仓|YES 0.67×5.01|305|14:56:49.222 ORDER_DEADLINE|ACK / 14:56:57.066|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:860)|REBALANCE|14:57:06.301|库存补仓|YES 0.65×5.01|207|14:57:08.988 WS_BOOK_STALE|ACK / 14:57:17.746|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:1033)|REBALANCE|14:57:27.721|库存补仓|YES 0.55×5.01|228|14:57:30.723 ORDER_DEADLINE|ACK / 14:57:40.444|0；|

相关事件：

- [14:55:33.679](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:79) 本地观察 CONFIRMED：YES 2.885246 @ 0.3900000207954538；trade=9aea54bd-9dac-4cf7-be8e-29c7762e600d
- [14:55:35.488](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:90) 等待 WS_BOOK_STALE；INVALID / None
- [14:55:51.826](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:226) 等待 OBI_NO_SIGNAL；NONE / -0.2881877645248172373989996152
- [14:56:04.331](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:328) 本地观察 CONFIRMED：NO 7.89 @ 0.59；trade=02714ab3-ed2b-4580-b61f-043844b2c583
- [14:56:07.871](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:360) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.628647741602375065780981601
- [14:58:49.120](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:1547) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [15:00:08.450](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:2005) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [15:00:10.671](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:2018) 网络 PROBE → PAUSED：NETWORK_REPEATED_SLOW
- [15:00:18.183](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:2060) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [15:00:41.519](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_30e6a48bfb0a45eb89ab3745b6cb17f2.jsonl:2074) 合并 2.885246 对，状态 CONFIRMED，释放成本 2.827541139999999894634800000

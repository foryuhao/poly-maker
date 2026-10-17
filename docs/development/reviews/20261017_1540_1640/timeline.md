# 12 场逐单时间线（北京时间，2026-10-17 15:40–16:40）

范围冻结为 12 个连续已结束市场；未过滤异常结果。POST 时间按 monotonic/墙钟对齐。
成交 match_time 为官方秒级时间；CONFIRMED 时间是本地观察时间。NONE/INVALID/等待日志不构成完整逐帧信号流。

## 15:40 / a241a21cb7344e66a92c3e10ec70af68

[btc-updown-5m-1792222800](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:1)；结束 15:45:10.105，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 -0.450000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:31)|NORMAL|15:40:22.600|VALID_YES / +0.959|YES 0.48×5.0|614|15:40:22.885 SIGNAL_REVERSED / -0.340|ACK / 15:40:25.078|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:74)|NORMAL|15:40:27.126|VALID_NO / -0.944|NO 0.51×5.0|287|15:40:29.126 ORDER_DEADLINE|ACK / 15:40:31.831|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:126)|NORMAL|15:40:33.812|VALID_YES / +0.967|YES 0.49×5.0|211|15:40:35.812 ORDER_DEADLINE|ACK / 15:40:38.828|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:183)|NORMAL|15:40:40.720|VALID_YES / +0.959|YES 0.5×5.0|276|15:40:42.720 ORDER_DEADLINE|ACK / 15:40:46.985|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:253)|NORMAL|15:40:48.984|VALID_NO / -0.951|NO 0.44×5.0|218|15:40:50.985 ORDER_DEADLINE|ACK / 15:40:56.108|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:409)|NORMAL|15:41:12.525|VALID_NO / -0.466|NO 0.43×5.0|307|15:41:14.527 ORDER_DEADLINE|ACK / 15:41:21.260|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:534)|NORMAL|15:41:29.165|VALID_NO / -0.900|NO 0.4×5.0|201|15:41:29.618 SIGNAL_REVERSED / +0.343|ACK / 15:41:45.177|5.0；15:41:29.000|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:718)|REBALANCE|15:41:47.149|库存补仓|YES 0.69×5.0|201|15:41:49.294 WS_BOOK_STALE|ACK / 15:41:56.958|5.0；15:41:47.000|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:830)|NORMAL|15:41:58.974|VALID_YES / +0.303|YES 0.66×5.0|216|15:42:00.582 MARKET_SUBMISSION_CUTOFF|ACK / 15:42:10.044|0；|

相关事件：

- [15:40:57.584](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:319) 等待 WS_BOOK_STALE；INVALID / None
- [15:41:02.487](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:347) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.956761646313885119855269109
- [15:41:04.471](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:359) 等待 OBI_NO_SIGNAL；NONE / 0.1752626797620884112373802300
- [15:41:08.657](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:382) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.6804689658486191694595601045
- [15:41:22.839](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:489) 等待 WS_BOOK_STALE；INVALID / None
- [15:41:25.182](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:505) 等待 OBI_NO_SIGNAL；NONE / 0.2711471154980534035943214440
- [15:41:27.077](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:517) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.4272588813630678933617305811
- [15:41:41.020](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:651) 本地观察 CONFIRMED：NO 5.0 @ 0.4；trade=9d709e40-5300-4768-b349-522e7ab06f5d
- [15:41:55.770](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:794) 本地观察 CONFIRMED：YES 5.0 @ 0.69；trade=d5ab88bf-7070-45c7-95e0-ae66827360c4
- [15:42:52.125](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:1084) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [15:44:03.796](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:1351) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [15:44:26.814](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:1333) 合并 5 对，状态 CONFIRMED，释放成本 5.45
- [15:44:56.789](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:1544) 网络 PROBE → PAUSED：NETWORK_REPEATED_SLOW
- [15:45:00.978](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:1569) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [15:45:05.194](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a241a21cb7344e66a92c3e10ec70af68.jsonl:1594) 网络 PROBE → PAUSED：NETWORK_REPEATED_SLOW

## 15:45 / af294965fa6e4b2d9e0508698f2b3cf4

[btc-updown-5m-1792223100](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:1)；结束 15:50:08.712，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 -0.137700 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:29)|NORMAL|15:45:23.408|VALID_NO / -0.508|NO 0.86×5.0|216|15:45:25.404 ORDER_DEADLINE|ACK / 15:45:27.020|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:70)|NORMAL|15:45:29.189|VALID_NO / -0.762|NO 0.87×5.0|206|15:45:31.191 ORDER_DEADLINE|ACK / 15:45:33.555|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:121)|NORMAL|15:45:35.727|VALID_NO / -0.379|NO 0.87×5.0|194|15:45:37.730 ORDER_DEADLINE|ACK / 15:45:45.929|5.0；15:45:36.000|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:249)|REBALANCE|15:45:51.589|库存补仓|YES 0.17×5.89|207|15:45:51.923 WS_BOOK_STALE|ACK / 15:46:02.181|5.89；15:45:51.000|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:374)|NORMAL|15:46:04.429|VALID_NO / -0.428|NO 0.69×5.89|212|15:46:06.242 SIGNAL_REVERSED / +0.960|ACK / 15:46:11.639|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:467)|NORMAL|15:46:16.088|VALID_NO / -0.591|NO 0.76×5.89|309|15:46:16.414 SIGNAL_REVERSED / +0.405|ACK / 15:46:30.148|5.89；15:46:16.000|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:634)|REBALANCE|15:46:32.124|库存补仓|YES 0.24×5.0|211|15:46:33.762 WS_BOOK_STALE|ACK / 15:46:49.453|5.0；15:46:33.000|

相关事件：

- [15:45:45.463](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:204) 本地观察 CONFIRMED：NO 5.0 @ 0.87；trade=a4d972b5-628b-4089-926a-072e2765aecb
- [15:45:48.100](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:224) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；NONE / -0.1849134687735139202407825433
- [15:45:49.599](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:228) 等待 WS_BOOK_STALE；INVALID / None
- [15:45:59.584](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:324) 本地观察 CONFIRMED：YES 5.89 @ 0.17；trade=ee26c169-edb6-4c1e-b530-56c8c9d9855f
- [15:46:28.769](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:598) 本地观察 CONFIRMED：NO 5.89 @ 0.76；trade=0923a385-0b12-465a-92ae-b0b02998c350
- [15:46:45.020](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:753) 本地观察 CONFIRMED：YES 5.0 @ 0.24；trade=f31d55d4-c3e3-4324-a54c-816ec9426089
- [15:46:51.725](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:818) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.9924782621776935343141679455
- [15:46:53.113](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:822) 等待 WS_BOOK_STALE；INVALID / None
- [15:46:55.131](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:837) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_YES / 0.627143922445935868754660701
- [15:49:21.355](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_af294965fa6e4b2d9e0508698f2b3cf4.jsonl:1288) 合并 10.89 对，状态 CONFIRMED，释放成本 11.0277

## 15:50 / 64d50379820a42fe882f8737db4b23a6

[btc-updown-5m-1792223400](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_64d50379820a42fe882f8737db4b23a6.jsonl:1)；结束 15:55:08.021，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 -0.100000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_64d50379820a42fe882f8737db4b23a6.jsonl:75)|NORMAL|15:50:31.611|VALID_YES / +0.816|YES 0.65×5.0|209|15:50:33.611 ORDER_DEADLINE|ACK / 15:50:34.967|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_64d50379820a42fe882f8737db4b23a6.jsonl:128)|NORMAL|15:50:40.041|VALID_YES / +0.550|YES 0.61×5.0|207|15:50:41.522 SIGNAL_REVERSED / -0.641|ACK / 15:50:51.663|5.0；15:50:41.000|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_64d50379820a42fe882f8737db4b23a6.jsonl:244)|REBALANCE|15:50:53.775|库存补仓|NO 0.43×5.0|221|15:50:53.995 PLACEMENT_NOT_ACKNOWLEDGED|REJECTED / 15:50:56.863|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_64d50379820a42fe882f8737db4b23a6.jsonl:292)|REBALANCE|15:50:58.964|库存补仓|NO 0.41×5.0|205|15:51:01.966 ORDER_DEADLINE|ACK / 15:51:05.777|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_64d50379820a42fe882f8737db4b23a6.jsonl:357)|REBALANCE|15:51:07.884|库存补仓|NO 0.41×5.0|207|15:51:10.887 ORDER_DEADLINE|ACK / 15:51:26.651|5.0；15:51:09.000|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_64d50379820a42fe882f8737db4b23a6.jsonl:539)|NORMAL|15:51:28.750|VALID_NO / -0.768|NO 0.4×5.0|206|15:51:30.754 ORDER_DEADLINE|ACK / 15:51:35.956|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_64d50379820a42fe882f8737db4b23a6.jsonl:648)|NORMAL|15:51:44.015|VALID_YES / +0.647|YES 0.58×5.0|309|15:51:44.961 SIGNAL_REVERSED / -0.427|ACK / 15:51:50.866|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_64d50379820a42fe882f8737db4b23a6.jsonl:736)|NORMAL|15:51:52.874|VALID_NO / -0.680|NO 0.65×5.0|315|15:51:54.875 ORDER_DEADLINE|ACK / 15:52:02.165|0；|

相关事件：

- [15:50:22.572](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_64d50379820a42fe882f8737db4b23a6.jsonl:24) 等待 OBI_NO_SIGNAL；NONE / -0.2549998660630575125231041226
- [15:50:50.264](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_64d50379820a42fe882f8737db4b23a6.jsonl:204) 本地观察 CONFIRMED：YES 5.0 @ 0.61；trade=4296c42d-73c0-4a2b-9dc0-b674cb2b2411
- [15:51:23.331](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_64d50379820a42fe882f8737db4b23a6.jsonl:483) 本地观察 CONFIRMED：NO 5.0 @ 0.41；trade=ed005c5d-af0f-477e-a77e-6fe341345031
- [15:51:41.338](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_64d50379820a42fe882f8737db4b23a6.jsonl:631) 等待 WS_BOOK_STALE；INVALID / None
- [15:53:35.876](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_64d50379820a42fe882f8737db4b23a6.jsonl:1149) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [15:54:03.742](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_64d50379820a42fe882f8737db4b23a6.jsonl:1264) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [15:54:22.159](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_64d50379820a42fe882f8737db4b23a6.jsonl:1247) 合并 5 对，状态 CONFIRMED，释放成本 5.10

## 15:55 / 60388fdc55094efe9ca55f2f49fee91a

[btc-updown-5m-1792223700](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:1)；结束 16:00:10.256，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 -0.400000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:42)|NORMAL|15:55:24.673|VALID_NO / -0.770|NO 0.38×5.0|232|15:55:26.665 SIGNAL_REVERSED / +0.914|ACK / 15:55:38.046|5.0；15:55:26.000|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:156)|REBALANCE|15:55:40.062|库存补仓|YES 0.7×5.0|287|15:55:43.063 ORDER_DEADLINE|ACK / 15:55:49.843|5.0；15:55:40.000|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:259)|NORMAL|15:55:54.052|VALID_NO / -0.814|NO 0.26×5.0|291|15:55:56.056 ORDER_DEADLINE|ACK / 15:55:59.430|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:323)|NORMAL|15:56:01.582|VALID_NO / -0.324|NO 0.28×5.0|443|15:56:01.801 SIGNAL_REVERSED / +0.989|ACK / 15:56:06.542|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:392)|NORMAL|15:56:08.688|VALID_NO / -0.366|NO 0.26×5.0|221|15:56:09.421 SIGNAL_REVERSED / +0.377|ACK / 15:56:14.642|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:470)|NORMAL|15:56:16.746|VALID_YES / +0.786|YES 0.71×5.0|220|15:56:18.746 ORDER_DEADLINE|ACK / 15:56:25.471|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:572)|NORMAL|15:56:29.734|VALID_NO / -0.543|NO 0.3×5.0|221|15:56:31.736 ORDER_DEADLINE|ACK / 15:56:39.569|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:679)|NORMAL|15:56:41.714|VALID_NO / -0.369|NO 0.27×5.0|221|15:56:42.593 SIGNAL_REVERSED / +0.336|ACK / 15:56:50.523|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:832)|NORMAL|15:57:00.477|VALID_NO / -0.805|NO 0.39×5.0|391|15:57:00.645 SIGNAL_REVERSED / +0.765|ACK / 15:57:10.452|0；|

相关事件：

- [15:55:22.698](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:26) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.8559488548051262500366579665
- [15:55:37.210](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:130) 本地观察 CONFIRMED：NO 5.0 @ 0.38；trade=18ca82ae-e1b8-480c-8c87-fac6a0ae8cf3
- [15:55:47.948](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:207) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [15:55:48.432](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:211) 本地观察 CONFIRMED：YES 5.0 @ 0.7；trade=b5660c34-36a3-4c0e-b5ba-2df020f7b8b9
- [15:55:51.514](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:242) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [15:55:59.430](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:304) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [15:56:34.385](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:606) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [15:56:41.183](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:672) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [15:56:50.523](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:773) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [15:56:52.143](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:776) 等待 WS_RECONNECT_REQUIRED；INVALID / None
- [15:56:54.879](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:793) 等待 OBI_NO_SIGNAL；NONE / 0.1834403997144896502498215560
- [15:56:56.437](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:797) 等待 WS_BOOK_STALE；INVALID / None
- [15:56:58.434](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:813) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.959077330205022575672836896
- [15:59:08.815](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:1374) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [15:59:28.181](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:1327) 合并 5 对，状态 CONFIRMED，释放成本 5.40
- [16:00:01.310](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_60388fdc55094efe9ca55f2f49fee91a.jsonl:1563) 网络 PAUSED → PROBE：NETWORK_PROBE_READY

## 16:00 / bd317b53cd69414b8b0e8e47d2155c68

[btc-updown-5m-1792224000](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:1)；结束 16:05:09.909，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.400000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:47)|NORMAL|16:00:26.491|VALID_NO / -0.774|NO 0.48×5.0|231|16:00:28.491 ORDER_DEADLINE|ACK / 16:00:29.913|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:98)|NORMAL|16:00:34.210|VALID_NO / -0.503|NO 0.45×5.0|229|16:00:35.256 SIGNAL_REVERSED / +0.541|ACK / 16:00:37.977|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:156)|NORMAL|16:00:41.539|VALID_YES / +0.891|YES 0.56×5.0|225|16:00:42.878 SIGNAL_REVERSED / -0.800|ACK / 16:00:46.431|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:215)|NORMAL|16:00:48.397|VALID_NO / -0.743|NO 0.43×5.0|230|16:00:48.987 SIGNAL_REVERSED / +0.387|ACK / 16:00:53.190|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:286)|NORMAL|16:00:55.393|VALID_NO / -0.401|NO 0.34×5.0|310|16:00:55.626 SIGNAL_REVERSED / +0.620|ACK / 16:01:08.317|5.0；16:00:55.000|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:432)|REBALANCE|16:01:10.401|库存补仓|YES 0.55×5.0|230|16:01:13.403 ORDER_DEADLINE|ACK / 16:01:19.532|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:524)|REBALANCE|16:01:21.842|库存补仓|YES 0.58×5.0|227|16:01:23.255 WS_BOOK_STALE|ACK / 16:01:41.080|5.0；16:01:23.000|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:718)|NORMAL|16:01:43.208|VALID_YES / +0.365|YES 0.36×5.0|219|16:01:44.295 SIGNAL_REVERSED / -0.472|ACK / 16:01:52.402|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:847)|NORMAL|16:01:58.588|VALID_NO / -0.713|NO 0.69×5.0|268|16:02:00.591 ORDER_DEADLINE|ACK / 16:02:09.979|0；|

相关事件：

- [16:00:23.110](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:24) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.4694096775768623557559284517
- [16:00:39.474](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:134) 等待 WS_BOOK_STALE；INVALID / None
- [16:01:05.690](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:385) 本地观察 CONFIRMED：NO 5.0 @ 0.34；trade=9645b32f-0d1f-4e8f-a447-020b0b0df145
- [16:01:26.580](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:550) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [16:01:38.430](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:666) 本地观察 CONFIRMED：YES 5.0 @ 0.58；trade=2310fc36-bc0d-4c49-8789-b01106d555ae
- [16:01:42.696](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:711) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [16:01:52.402](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:812) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [16:04:25.156](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd317b53cd69414b8b0e8e47d2155c68.jsonl:1344) 合并 5 对，状态 CONFIRMED，释放成本 4.60

## 16:05 / 829b6140697048bf8c03463dceca81f1

[btc-updown-5m-1792224300](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:1)；结束 16:10:12.989，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 5.0；残仓成本 3.750000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:79)|NORMAL|16:05:32.945|VALID_NO / -0.688|NO 0.75×5.0|212|16:05:33.692 SIGNAL_REVERSED / +0.491|ACK / 16:05:35.275|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:124)|NORMAL|16:05:37.195|VALID_YES / +0.659|YES 0.21×5.0|309|16:05:37.284 SIGNAL_REVERSED / -0.449|ACK / 16:05:40.550|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:250)|NORMAL|16:05:56.347|VALID_YES / +0.941|YES 0.24×5.0|296|16:05:58.200 SIGNAL_REVERSED / -0.868|ACK / 16:06:01.421|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:309)|NORMAL|16:06:03.994|VALID_YES / +0.686|YES 0.32×5.0|221|16:06:04.705 SIGNAL_REVERSED / -0.640|ACK / 16:06:09.142|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:380)|NORMAL|16:06:11.265|VALID_NO / -0.407|NO 0.55×5.0|218|16:06:13.265 ORDER_DEADLINE|ACK / 16:06:18.343|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:459)|NORMAL|16:06:20.432|VALID_NO / -0.689|NO 0.74×5.0|256|16:06:22.429 ORDER_DEADLINE|ACK / 16:06:28.657|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:550)|NORMAL|16:06:30.718|VALID_YES / +0.470|YES 0.3×5.0|259|16:06:32.719 ORDER_DEADLINE|ACK / 16:06:40.032|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:676)|NORMAL|16:06:45.880|VALID_NO / -0.668|NO 0.75×5.0|239|16:06:47.613 SIGNAL_REVERSED / +0.330|ACK / 16:06:55.502|5.0；16:06:47.000|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:789)|REBALANCE|16:06:57.556|库存补仓|YES 0.2×5.0|225|16:07:00.559 ORDER_DEADLINE|ACK / 16:07:09.617|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:913)|REBALANCE|16:07:11.726|库存补仓|YES 0.21×5.0|366|16:07:11.856 WS_BOOK_STALE|ACK / 16:07:22.939|0；|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:1057)|REBALANCE|16:07:27.138|库存补仓|YES 0.2×5.0|218|16:07:30.142 ORDER_DEADLINE|ACK / 16:07:41.082|0；|
|[w12](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:1197)|REBALANCE|16:07:43.148|库存补仓|YES 0.17×5.89|263|16:07:46.153 ORDER_DEADLINE|ACK / 16:07:57.880|0；|

相关事件：

- [16:05:23.731](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:26) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [16:05:23.776](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:15) 等待 NETWORK_PAUSED；VALID_NO / -0.741392789017792926608086557
- [16:05:25.376](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:27) 等待 WS_BOOK_STALE；INVALID / None
- [16:05:27.015](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:42) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [16:05:27.543](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:44) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.740042225780397602482535814
- [16:05:35.275](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:103) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [16:05:42.609](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:168) 等待 OBI_NO_SIGNAL；NONE / 0.0987644329340671198877738211
- [16:05:48.745](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:200) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.802632577770355413181107383
- [16:05:50.555](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:212) 等待 OBI_NO_SIGNAL；NONE / -0.1793089347781639746473113883
- [16:05:52.450](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:223) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.9939163868204604078454275565
- [16:06:42.160](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:648) 等待 OBI_NO_SIGNAL；NONE / -0.1052334433970334126020085200
- [16:06:43.971](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:659) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.740986324077911313717364277
- [16:06:55.222](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:762) 本地观察 CONFIRMED：NO 5.0 @ 0.75；trade=fbfdd546-ec97-42a9-83f5-b6b3c45452b3
- [16:07:59.322](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:1345) 等待 MINIMUM_ORDER_EXCEEDS_MAXIMUM；VALID_NO / -0.3450890354272899591531622928
- [16:08:24.172](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:1441) 网络 NORMAL → PAUSED：NETWORK_TRANSPORT_ERROR
- [16:08:37.792](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:1527) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [16:08:43.625](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:1549) 等待 WS_BOOK_STALE；INVALID / None
- [16:08:45.521](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:1564) 等待 MINIMUM_ORDER_EXCEEDS_MAXIMUM；VALID_YES / 0.4338534314592216209322345862
- [16:08:54.257](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_829b6140697048bf8c03463dceca81f1.jsonl:1601) 等待 WS_EMPTY_BOOK；INVALID / None

## 16:10 / eecf5ee3966e475a99dfe8fd168f0366

[btc-updown-5m-1792224600](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:1)；结束 16:15:13.016，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.550000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:29)|NORMAL|16:10:26.456|VALID_NO / -0.947|NO 0.28×5.0|469|16:10:28.459 ORDER_DEADLINE|ACK / 16:10:29.823|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:73)|NORMAL|16:10:31.967|VALID_NO / -0.747|NO 0.26×5.0|321|16:10:32.073 SIGNAL_REVERSED / +0.341|REJECTED / 16:10:34.015|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:122)|NORMAL|16:10:38.002|VALID_NO / -0.703|NO 0.24×5.0|205|16:10:39.113 SIGNAL_REVERSED / +0.859|ACK / 16:10:41.739|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:173)|NORMAL|16:10:44.049|VALID_NO / -0.999|NO 0.23×5.0|417|16:10:45.327 SIGNAL_REVERSED / +0.402|ACK / 16:10:48.865|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:233)|NORMAL|16:10:50.997|VALID_NO / -0.693|NO 0.24×5.0|201|16:10:52.998 ORDER_DEADLINE|ACK / 16:10:57.426|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:305)|NORMAL|16:10:59.551|VALID_NO / -0.720|NO 0.26×5.0|213|16:11:00.926 SIGNAL_REVERSED / +0.438|ACK / 16:11:06.457|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:387)|NORMAL|16:11:08.579|VALID_YES / +0.960|YES 0.74×5.0|219|16:11:10.578 ORDER_DEADLINE|ACK / 16:11:16.563|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:476)|NORMAL|16:11:18.676|VALID_NO / -0.658|NO 0.24×5.0|217|16:11:20.677 ORDER_DEADLINE|ACK / 16:11:27.884|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:579)|NORMAL|16:11:29.964|VALID_NO / -0.874|NO 0.39×5.0|208|16:11:31.967 ORDER_DEADLINE|ACK / 16:11:39.797|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:690)|NORMAL|16:11:42.153|VALID_YES / +0.621|YES 0.6×5.0|214|16:11:43.438 SIGNAL_REVERSED / -0.328|ACK / 16:12:02.785|5.0；16:11:43.000|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:918)|REBALANCE|16:12:04.877|库存补仓|NO 0.31×5.0|209|16:12:05.130 WS_BOOK_STALE|ACK / 16:12:15.213|0；|
|[w12](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:1049)|REBALANCE|16:12:17.277|库存补仓|NO 0.29×5.0|216|16:12:20.283 ORDER_DEADLINE|ACK / 16:12:43.711|5.0；16:12:17.000|

相关事件：

- [16:10:35.915](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:105) 等待 OBI_NO_SIGNAL；NONE / -0.2227512091631202091252352027
- [16:11:55.752](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:820) 本地观察 CONFIRMED：YES 5.0 @ 0.6；trade=89c23e15-a743-4c3e-8e7d-e7d70f8feda5
- [16:12:36.769](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:1222) 本地观察 CONFIRMED：NO 5.0 @ 0.29；trade=edb1cbf9-1686-4f51-b48b-512e17763fe9
- [16:14:02.967](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:1603) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [16:14:28.685](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:1585) 合并 5 对，状态 CONFIRMED，释放成本 4.45
- [16:15:01.984](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:1837) 网络 PAUSED → PROBE：NETWORK_PROBE_READY

## 16:15 / 95f209364352435e95f226c6f54775be

[btc-updown-5m-1792224900](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_95f209364352435e95f226c6f54775be.jsonl:1)；结束 16:20:04.789，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_95f209364352435e95f226c6f54775be.jsonl:70)|NORMAL|16:16:07.847|VALID_YES / +0.929|YES 0.72×5.0|461|16:16:09.849 ORDER_DEADLINE|ACK / 16:16:11.674|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_95f209364352435e95f226c6f54775be.jsonl:111)|NORMAL|16:16:14.151|VALID_YES / +0.621|YES 0.78×5.0|299|16:16:15.197 SIGNAL_REVERSED / -0.649|ACK / 16:16:17.935|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_95f209364352435e95f226c6f54775be.jsonl:238)|NORMAL|16:16:39.608|VALID_YES / +0.987|YES 0.84×5.0|203|16:16:40.501 SIGNAL_REVERSED / -0.348|ACK / 16:16:44.514|0；|

相关事件：

- [16:15:47.888](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_95f209364352435e95f226c6f54775be.jsonl:20) 等待 WS_BOOK_STALE；INVALID / None
- [16:16:27.321](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_95f209364352435e95f226c6f54775be.jsonl:167) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_NO / -0.5517875355628321434322829995
- [16:16:37.519](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_95f209364352435e95f226c6f54775be.jsonl:221) 等待 OBI_NO_SIGNAL；NONE / -0.2226311235287371899792537609
- [16:16:46.637](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_95f209364352435e95f226c6f54775be.jsonl:296) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_NO / -0.6404977858990134172309369955
- [16:16:48.034](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_95f209364352435e95f226c6f54775be.jsonl:307) 等待 OBI_NO_SIGNAL；NONE / -0.2906496375769865169559493364
- [16:16:49.939](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_95f209364352435e95f226c6f54775be.jsonl:316) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_NO / -0.5567538339066043828949937665
- [16:16:57.428](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_95f209364352435e95f226c6f54775be.jsonl:353) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.6517469244900269025500831455
- [16:16:59.332](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_95f209364352435e95f226c6f54775be.jsonl:366) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_NO / -0.818173193985485477572058754

## 16:20 / a47a5c23d92e46a1b7bc5bb77b97d207

[btc-updown-5m-1792225200](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:1)；结束 16:25:11.392，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:28)|NORMAL|16:20:21.581|VALID_YES / +0.556|YES 0.5×5.0|298|16:20:22.776 SIGNAL_REVERSED / -0.340|ACK / 16:20:24.642|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:83)|NORMAL|16:20:28.642|VALID_NO / -0.670|NO 0.51×5.0|215|16:20:30.161 SIGNAL_REVERSED / +0.323|ACK / 16:20:32.664|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:149)|NORMAL|16:20:38.091|VALID_YES / +0.589|YES 0.48×5.0|216|16:20:39.086 SIGNAL_REVERSED / -0.663|ACK / 16:20:42.574|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:210)|NORMAL|16:20:44.598|VALID_NO / -0.811|NO 0.6×5.0|207|16:20:45.294 SIGNAL_REVERSED / +0.371|ACK / 16:20:50.138|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:282)|NORMAL|16:20:52.128|VALID_NO / -0.578|NO 0.62×5.0|308|16:20:53.050 SIGNAL_REVERSED / +0.612|ACK / 16:20:58.305|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:375)|NORMAL|16:21:02.514|VALID_NO / -0.673|NO 0.52×5.0|206|16:21:03.699 SIGNAL_REVERSED / +0.819|ACK / 16:21:09.530|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:473)|NORMAL|16:21:13.332|VALID_NO / -0.497|NO 0.54×5.0|207|16:21:14.431 SIGNAL_REVERSED / +0.347|ACK / 16:21:21.819|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:603)|NORMAL|16:21:29.532|VALID_YES / +0.584|YES 0.5×5.0|212|16:21:31.533 ORDER_DEADLINE|ACK / 16:21:39.573|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:716)|NORMAL|16:21:41.531|VALID_NO / -0.631|NO 0.47×5.0|193|16:21:43.535 ORDER_DEADLINE|ACK / 16:21:52.555|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:850)|NORMAL|16:21:56.331|VALID_YES / +0.906|YES 0.5×5.0|200|16:21:57.410 SIGNAL_REVERSED / -0.326|ACK / 16:22:06.917|0；|

相关事件：

- [16:20:26.577](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:65) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.9725869678445132816140793335
- [16:20:34.297](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:119) 等待 WS_BOOK_STALE；INVALID / None
- [16:20:36.511](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:136) 等待 OBI_NO_SIGNAL；NONE / -0.09668210550846223309738122285
- [16:21:00.343](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:358) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.878761345075521578796706350
- [16:21:11.200](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:450) 等待 NETWORK_RECOVERING；NONE / -0.2877638890958120409409871724
- [16:21:23.841](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:570) 等待 OBI_NO_SIGNAL；NONE / 0.09262857883145485166472340975
- [16:21:54.535](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a47a5c23d92e46a1b7bc5bb77b97d207.jsonl:834) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.7140941714646265478350027605

## 16:25 / 54663396127a43c3adaaae7639287b57

[btc-updown-5m-1792225500](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_54663396127a43c3adaaae7639287b57.jsonl:1)；结束 16:30:10.075，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_54663396127a43c3adaaae7639287b57.jsonl:46)|NORMAL|16:25:28.692|VALID_NO / -0.790|NO 0.65×5.0|228|16:25:29.172 SIGNAL_REVERSED / +0.608|ACK / 16:25:31.127|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_54663396127a43c3adaaae7639287b57.jsonl:98)|NORMAL|16:25:35.260|VALID_YES / +0.468|YES 0.31×5.0|225|16:25:35.962 SIGNAL_REVERSED / -0.310|ACK / 16:25:38.575|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_54663396127a43c3adaaae7639287b57.jsonl:158)|NORMAL|16:25:42.616|VALID_YES / +0.583|YES 0.32×5.0|233|16:25:43.823 SIGNAL_REVERSED / -0.360|ACK / 16:25:47.794|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_54663396127a43c3adaaae7639287b57.jsonl:219)|NORMAL|16:25:49.921|VALID_NO / -0.962|NO 0.52×5.0|211|16:25:51.924 ORDER_DEADLINE|ACK / 16:25:56.632|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_54663396127a43c3adaaae7639287b57.jsonl:290)|NORMAL|16:25:58.713|VALID_NO / -0.563|NO 0.56×5.0|284|16:26:00.715 ORDER_DEADLINE|ACK / 16:26:06.431|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_54663396127a43c3adaaae7639287b57.jsonl:387)|NORMAL|16:26:10.693|VALID_NO / -0.697|NO 0.57×5.0|250|16:26:10.719 SIGNAL_REVERSED / +0.544|REJECTED / 16:26:16.650|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_54663396127a43c3adaaae7639287b57.jsonl:481)|NORMAL|16:26:20.541|VALID_YES / +0.674|YES 0.39×5.0|307|16:26:20.822 SIGNAL_REVERSED / -0.347|ACK / 16:26:26.844|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_54663396127a43c3adaaae7639287b57.jsonl:610)|NORMAL|16:26:36.661|VALID_NO / -0.456|NO 0.69×5.0|226|16:26:37.393 SIGNAL_REVERSED / +0.378|ACK / 16:26:44.527|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_54663396127a43c3adaaae7639287b57.jsonl:709)|NORMAL|16:26:46.667|VALID_YES / +0.625|YES 0.25×5.0|236|16:26:47.568 SIGNAL_REVERSED / -0.765|ACK / 16:26:56.158|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_54663396127a43c3adaaae7639287b57.jsonl:819)|NORMAL|16:26:58.334|VALID_YES / +0.675|YES 0.27×5.0|249|16:27:00.335 ORDER_DEADLINE|ACK / 16:27:09.779|0；|

相关事件：

- [16:25:26.543](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_54663396127a43c3adaaae7639287b57.jsonl:29) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.9070651163751264947027116335
- [16:26:24.228](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_54663396127a43c3adaaae7639287b57.jsonl:518) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [16:26:36.135](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_54663396127a43c3adaaae7639287b57.jsonl:603) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [16:26:44.527](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_54663396127a43c3adaaae7639287b57.jsonl:692) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED

## 16:30 / 172d12bbc990476baa05c1257dbbff78

[btc-updown-5m-1792225800](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:1)；结束 16:35:07.580，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:29)|NORMAL|16:30:23.907|VALID_YES / +0.788|YES 0.27×5.0|221|16:30:25.907 ORDER_DEADLINE|ACK / 16:30:27.626|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:91)|NORMAL|16:30:34.126|VALID_NO / -0.550|NO 0.73×5.0|253|16:30:34.977 SIGNAL_REVERSED / +0.616|ACK / 16:30:37.652|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:147)|NORMAL|16:30:41.347|VALID_YES / +0.849|YES 0.25×5.0|234|16:30:43.350 ORDER_DEADLINE|ACK / 16:30:46.946|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:207)|NORMAL|16:30:49.091|VALID_NO / -0.370|NO 0.77×5.0|242|16:30:49.432 SIGNAL_REVERSED / +0.577|ACK / 16:30:54.251|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:284)|NORMAL|16:30:57.838|VALID_NO / -0.327|NO 0.84×5.0|226|16:30:59.158 SIGNAL_REVERSED / +0.869|ACK / 16:31:04.658|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:424)|NORMAL|16:31:19.562|VALID_NO / -0.676|NO 0.89×5.0|230|16:31:20.102 SIGNAL_REVERSED / +0.843|ACK / 16:31:27.626|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:545)|NORMAL|16:31:35.467|VALID_NO / -0.775|NO 0.9×5.0|234|16:31:37.471 ORDER_DEADLINE|ACK / 16:31:45.333|0；|

相关事件：

- [16:30:29.835](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:65) 等待 OBI_NO_SIGNAL；NONE / -0.0944484079029745351833787109
- [16:30:31.481](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:69) 等待 WS_BOOK_STALE；INVALID / None
- [16:30:39.692](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:134) 等待 OBI_NO_SIGNAL；NONE / -0.000987052197642687104453347268
- [16:30:56.316](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:271) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.662062583541830894197484320
- [16:31:09.932](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:377) 等待 OBI_NO_SIGNAL；NONE / 0.1677622323963475458475074814
- [16:31:13.063](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:392) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.7437706422591138110370920715
- [16:31:47.490](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:644) 等待 OBI_NO_SIGNAL；NONE / 0.2326980942828485456369107322
- [16:31:51.254](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:666) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.4155584309022627575977530217
- [16:31:54.278](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:674) 等待 WS_BOOK_STALE；INVALID / None
- [16:31:56.257](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_172d12bbc990476baa05c1257dbbff78.jsonl:690) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.6650409150956981236030327885

## 16:35 / 7be5d0b9990f48c2a628f3fe4fd421d8

[btc-updown-5m-1792226100](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7be5d0b9990f48c2a628f3fe4fd421d8.jsonl:1)；结束 16:40:10.188，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7be5d0b9990f48c2a628f3fe4fd421d8.jsonl:28)|NORMAL|16:35:23.141|VALID_NO / -0.568|NO 0.31×5.0|260|16:35:25.143 ORDER_DEADLINE|ACK / 16:35:27.168|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7be5d0b9990f48c2a628f3fe4fd421d8.jsonl:93)|NORMAL|16:35:33.298|VALID_NO / -0.493|NO 0.38×5.0|326|16:35:35.302 ORDER_DEADLINE|ACK / 16:35:37.837|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7be5d0b9990f48c2a628f3fe4fd421d8.jsonl:145)|NORMAL|16:35:39.959|VALID_YES / +0.614|YES 0.62×5.0|219|16:35:41.960 ORDER_DEADLINE|ACK / 16:35:45.963|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7be5d0b9990f48c2a628f3fe4fd421d8.jsonl:209)|NORMAL|16:35:48.007|VALID_YES / +0.546|YES 0.62×5.0|309|16:35:50.009 ORDER_DEADLINE|ACK / 16:35:54.350|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7be5d0b9990f48c2a628f3fe4fd421d8.jsonl:298)|NORMAL|16:36:00.756|VALID_NO / -0.566|NO 0.39×5.0|862|16:36:02.757 ORDER_DEADLINE|ACK / 16:36:08.462|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7be5d0b9990f48c2a628f3fe4fd421d8.jsonl:386)|NORMAL|16:36:10.566|VALID_YES / +0.696|YES 0.59×5.0|223|16:36:12.565 ORDER_DEADLINE|ACK / 16:36:18.841|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7be5d0b9990f48c2a628f3fe4fd421d8.jsonl:479)|NORMAL|16:36:20.888|VALID_YES / +0.380|YES 0.62×5.0|305|16:36:22.889 ORDER_DEADLINE|ACK / 16:36:30.361|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7be5d0b9990f48c2a628f3fe4fd421d8.jsonl:583)|NORMAL|16:36:32.518|VALID_NO / -0.323|NO 0.42×5.0|225|16:36:34.520 ORDER_DEADLINE|ACK / 16:36:42.439|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7be5d0b9990f48c2a628f3fe4fd421d8.jsonl:693)|NORMAL|16:36:44.480|VALID_YES / +0.636|YES 0.56×5.0|306|16:36:46.483 ORDER_DEADLINE|ACK / 16:36:55.694|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7be5d0b9990f48c2a628f3fe4fd421d8.jsonl:817)|NORMAL|16:36:57.808|VALID_YES / +0.885|YES 0.54×5.0|692|16:36:58.663 SIGNAL_REVERSED / -0.846|ACK / 16:37:08.715|0；|

相关事件：

- [16:35:29.237](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7be5d0b9990f48c2a628f3fe4fd421d8.jsonl:65) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.9844948018823310514850103495
- [16:35:31.233](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7be5d0b9990f48c2a628f3fe4fd421d8.jsonl:77) 等待 OBI_NO_SIGNAL；NONE / -0.1300597957646002661151990253
- [16:36:04.255](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7be5d0b9990f48c2a628f3fe4fd421d8.jsonl:321) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [16:36:10.070](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7be5d0b9990f48c2a628f3fe4fd421d8.jsonl:379) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [16:36:18.841](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7be5d0b9990f48c2a628f3fe4fd421d8.jsonl:463) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED

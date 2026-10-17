# 12 场逐单时间线（北京时间，2026-10-17 22:45–23:45）

范围冻结为 12 个连续已结束市场；未过滤异常结果。POST 时间按 monotonic/墙钟对齐。
本批全部 33 条成交缺少可用官方撮合时间（缓存 timestamp=0）；成交列使用本地首次 MATCHED 观察时间，相关事件使用 CONFIRMED 观察时间，均不能称为官方撮合时刻。NONE/INVALID/等待日志不构成完整逐帧信号流。

## 22:45 / bd475b352b0d43f39a230fd7afd45282

[btc-updown-5m-1792248300](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:1)；结束 22:50:00.909，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.600000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:86)|NORMAL|22:45:22.445|VALID_NO / -0.458|NO 0.56×5.0|214|22:45:24.446 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:45:26.912|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:140)|NORMAL|22:45:28.264|VALID_YES / +0.958|YES 0.43×5.0|862|22:45:29.850 SIGNAL_REVERSED / -0.899|ACK / SAFE_TO_CONTINUE  / 22:45:31.842|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:282)|NORMAL|22:45:33.589|VALID_YES / +0.305|YES 0.49×5.0|217|22:45:35.590 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:45:37.844|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:337)|NORMAL|22:45:39.493|VALID_NO / -0.835|NO 0.43×5.0|218|22:45:41.492 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:45:43.643|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:387)|NORMAL|22:45:45.185|VALID_NO / -0.887|NO 0.46×5.0|220|22:45:47.096 SIGNAL_REVERSED / +0.735|ACK / SAFE_TO_CONTINUE  / 22:45:49.844|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:462)|NORMAL|22:45:51.980|VALID_NO / -0.655|NO 0.52×5.0|594|22:45:52.342 SIGNAL_REVERSED / +0.667|REJECTED / BLOCKED SYNC_CACHE_NOT_READY / 22:46:02.924|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:577)|NORMAL|22:46:05.379|VALID_NO / -0.753|NO 0.66×5.0|213|22:46:06.271 SIGNAL_REVERSED / +0.548|ACK / SAFE_TO_CONTINUE  / 22:46:11.872|5.0；本地MATCHED 22:46:06.768|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:698)|REBALANCE|22:46:13.446|库存补仓|YES 0.4×5.0|241|22:46:13.687 PLACEMENT_NOT_ACKNOWLEDGED|REJECTED / BLOCKED SYNC_CACHE_NOT_READY / 22:46:24.073|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:810)|REBALANCE|22:46:26.552|库存补仓|YES 0.39×5.0|465|22:46:26.683 WS_BOOK_STALE|REJECTED / SAFE_TO_CONTINUE  / 22:46:29.941|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:862)|REBALANCE|22:46:31.230|库存补仓|YES 0.41×5.0|247|22:46:32.312 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 22:46:33.893|0；|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:922)|REBALANCE|22:46:36.236|库存补仓|YES 0.45×5.0|462|22:46:36.360 WS_BOOK_STALE|REJECTED / SAFE_TO_CONTINUE  / 22:46:47.947|0；|
|[w12](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:1051)|REBALANCE|22:46:50.188|库存补仓|YES 0.2×5.0|367|22:46:50.290 WS_BOOK_STALE|REJECTED / SAFE_TO_CONTINUE  / 22:46:53.944|0；|
|[w13](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:1121)|REBALANCE|22:46:56.366|库存补仓|YES 0.22×5.0|267|22:46:57.344 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 22:47:04.920|5.0；本地MATCHED 22:46:57.955;本地MATCHED 22:46:57.956;本地MATCHED 22:46:57.956|

相关事件：

- [22:45:21.257](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:15) 等待 NETWORK_RECOVERING；VALID_NO / -0.8589648016440674551205850945
- [22:45:22.029](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:71) 等待 OBI_NO_SIGNAL；NONE / -0.06038802518309135294873442115
- [22:45:27.836](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:126) 等待 WS_BOOK_STALE；INVALID / None
- [22:45:33.069](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:179) 等待 OBI_NO_SIGNAL；NONE / 0.2179286018366478136593079122
- [22:45:39.062](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:326) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.9556338732253549290141971605
- [22:45:51.109](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:438) 等待 OBI_NO_SIGNAL；NONE / -0.0003334444814938312770923641214
- [22:45:51.553](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:447) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.655172413793103448275862069
- [22:46:11.858](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:646) 本地观察 CONFIRMED：NO 5.0 @ 0.66；trade=05c2d968-91f3-4c1c-97fa-202f1527b6c8
- [22:46:34.668](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:898) 等待 WS_BOOK_STALE；INVALID / None
- [22:46:49.267](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:1031) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.879547097085039749457961937
- [22:46:49.719](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:1036) 等待 WS_BOOK_STALE；INVALID / None
- [22:46:55.392](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:1105) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.7103515886101332382692393385
- [22:47:04.898](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:1236) 本地观察 CONFIRMED：YES 4.98 @ 0.22；trade=e8381363-7977-437b-ab38-4cec84880afc
- [22:47:04.899](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:1237) 本地观察 CONFIRMED：YES 0.01 @ 0.22；trade=558f4e26-3ecf-400a-8ccc-f6b24058a4b2
- [22:47:04.899](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:1238) 本地观察 CONFIRMED：YES 0.01 @ 0.22；trade=51aa609a-5fad-485e-9db0-6dc02d725811
- [22:49:15.857](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_bd475b352b0d43f39a230fd7afd45282.jsonl:1824) 合并 5 对，状态 CONFIRMED，释放成本 4.40

## 22:50 / b5d177b153e74e3392b0a9f300650f91

[btc-updown-5m-1792248600](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:1)；结束 22:55:00.418，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:140)|NORMAL|22:50:21.120|VALID_YES / +0.600|YES 0.49×5.0|223|22:50:23.123 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:50:24.897|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:184)|NORMAL|22:50:26.120|VALID_NO / -0.702|NO 0.5×5.0|222|22:50:28.121 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:50:29.884|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:248)|NORMAL|22:50:30.405|VALID_YES / +0.311|YES 0.49×5.0|212|22:50:32.405 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:50:34.893|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:724)|NORMAL|22:50:38.718|VALID_YES / +0.382|YES 0.49×5.0|213|22:50:40.719 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:50:42.913|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:773)|NORMAL|22:50:44.119|VALID_YES / +0.326|YES 0.64×5.0|216|22:50:44.907 SIGNAL_REVERSED / -0.330|ACK / SAFE_TO_CONTINUE  / 22:50:46.932|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:919)|NORMAL|22:50:50.767|VALID_YES / +0.328|YES 0.6×5.0|221|22:50:52.771 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:50:54.935|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:993)|NORMAL|22:50:58.746|VALID_YES / +0.860|YES 0.63×5.0|433|22:51:00.749 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:51:02.962|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:1045)|NORMAL|22:51:04.118|VALID_YES / +0.595|YES 0.63×5.0|202|22:51:06.119 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:51:07.933|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:1092)|NORMAL|22:51:08.368|VALID_NO / -0.875|NO 0.36×5.0|219|22:51:10.371 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:51:11.959|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:1150)|NORMAL|22:51:14.115|VALID_NO / -0.600|NO 0.41×5.0|227|22:51:15.295 SIGNAL_REVERSED / +0.317|ACK / SAFE_TO_CONTINUE  / 22:51:18.954|0；|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:1213)|NORMAL|22:51:20.134|VALID_YES / +0.407|YES 0.6×5.0|218|22:51:22.135 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:51:25.955|0；|
|[w12](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:1279)|NORMAL|22:51:26.402|VALID_YES / +0.322|YES 0.63×5.0|214|22:51:27.911 SIGNAL_REVERSED / -0.467|ACK / SAFE_TO_CONTINUE  / 22:51:34.366|0；|
|[w13](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:1372)|NORMAL|22:51:34.855|VALID_YES / +0.491|YES 0.59×5.0|240|22:51:36.853 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:51:38.706|0；|
|[w14](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:1729)|NORMAL|22:51:42.153|VALID_NO / -0.311|NO 0.31×5.0|218|22:51:44.155 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:51:46.186|0；|
|[w15](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:1784)|NORMAL|22:51:46.612|VALID_NO / -0.959|NO 0.31×5.0|216|22:51:48.074 SIGNAL_REVERSED / +0.936|ACK / SAFE_TO_CONTINUE  / 22:51:50.398|0；|
|[w16](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:1839)|NORMAL|22:51:50.818|VALID_NO / -0.961|NO 0.33×5.0|221|22:51:51.852 SIGNAL_REVERSED / +0.307|ACK / SAFE_TO_CONTINUE  / 22:51:54.394|0；|
|[w17](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:1918)|NORMAL|22:51:54.919|VALID_NO / -0.642|NO 0.38×5.0|226|22:51:55.916 SIGNAL_REVERSED / +0.640|ACK / SAFE_TO_CONTINUE  / 22:51:58.418|0；|
|[w18](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:1977)|NORMAL|22:51:58.850|VALID_YES / +0.611|YES 0.59×5.0|205|22:52:00.184 MARKET_SUBMISSION_CUTOFF|ACK / SAFE_TO_CONTINUE  / 22:52:02.435|0；|

相关事件：

- [22:50:19.986](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:15) 等待 NETWORK_RECOVERING；VALID_NO / -0.7982869910625620655412115195
- [22:50:30.305](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:220) 等待 OBI_NO_SIGNAL；NONE / 0.0820778712580708276266875367
- [22:50:36.311](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:300) 等待 NETWORK_RECOVERING；NONE / 0.2474468851278357940223262514
- [22:50:48.255](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:814) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.9508644953759549658222758345
- [22:50:50.377](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:842) 等待 OBI_NO_SIGNAL；NONE / 0.04454419889502762430939226519
- [22:50:56.132](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:965) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.992923560298342697821871860
- [22:50:57.106](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:972) 等待 WS_BOOK_STALE；INVALID / None
- [22:51:34.817](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:1357) 等待 OBI_NO_SIGNAL；NONE / -0.0002510447893438870593794763501
- [22:51:39.714](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:1419) 等待 NETWORK_RECOVERING；VALID_YES / 0.9784285493906515143723967705
- [22:51:41.630](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b5d177b153e74e3392b0a9f300650f91.jsonl:1695) 等待 OBI_NO_SIGNAL；NONE / -0.03557259094882657521350279067

## 22:55 / 3d2096e512234f5aa2ef9f14ccafe5c6

[btc-updown-5m-1792248900](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:1)；结束 23:00:00.572，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.000000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:68)|NORMAL|22:55:20.599|VALID_NO / -0.980|NO 0.32×5.0|245|22:55:22.602 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:55:24.368|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:264)|NORMAL|22:55:26.615|VALID_YES / +0.309|YES 0.68×5.0|227|22:55:28.617 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:55:32.366|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:325)|NORMAL|22:55:33.738|VALID_NO / -0.915|NO 0.29×5.0|399|22:55:34.768 SIGNAL_REVERSED / +0.333|ACK / SAFE_TO_CONTINUE  / 22:55:37.370|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:370)|NORMAL|22:55:37.800|VALID_NO / -0.775|NO 0.36×5.0|285|22:55:38.164 SIGNAL_REVERSED / +0.545|ACK / SAFE_TO_CONTINUE  / 22:55:42.380|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:425)|NORMAL|22:55:43.619|VALID_NO / -0.521|NO 0.47×5.0|224|22:55:44.565 SIGNAL_REVERSED / +0.310|ACK / SAFE_TO_CONTINUE  / 22:55:46.393|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:478)|NORMAL|22:55:47.837|VALID_YES / +0.303|YES 0.58×5.0|216|22:55:49.060 SIGNAL_REVERSED / -0.475|ACK / SAFE_TO_CONTINUE  / 22:55:51.388|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:525)|NORMAL|22:55:51.815|VALID_NO / -0.723|NO 0.41×5.0|343|22:55:53.691 SIGNAL_REVERSED / +0.423|ACK / SAFE_TO_CONTINUE  / 22:55:56.714|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:587)|NORMAL|22:55:58.105|VALID_NO / -0.572|NO 0.41×5.0|218|22:56:00.105 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:56:02.564|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:645)|NORMAL|22:56:04.499|VALID_YES / +0.997|YES 0.65×5.0|284|22:56:06.501 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:56:08.435|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:699)|NORMAL|22:56:09.661|VALID_YES / +0.446|YES 0.6×5.0|532|22:56:10.321 SIGNAL_REVERSED / -0.308|ACK / SAFE_TO_CONTINUE  / 22:56:12.236|0；|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:947)|NORMAL|22:56:15.181|VALID_YES / +0.989|YES 0.64×5.0|213|22:56:17.185 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:56:18.637|0；|
|[w12](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:1009)|NORMAL|22:56:19.656|VALID_YES / +0.747|YES 0.69×5.0|229|22:56:21.656 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:56:24.840|0；|
|[w13](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:1379)|NORMAL|22:56:27.733|VALID_NO / -0.474|NO 0.3×5.0|217|22:56:29.735 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:56:31.555|0；|
|[w14](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:1479)|NORMAL|22:56:32.218|VALID_YES / +0.500|YES 0.69×5.0|296|22:56:32.244 SIGNAL_REVERSED / -0.559|REJECTED / SAFE_TO_CONTINUE  / 22:56:35.554|0；|
|[w15](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:1544)|NORMAL|22:56:37.644|VALID_YES / +0.773|YES 0.92×5.0|225|22:56:39.311 SIGNAL_REVERSED / -0.521|ACK / SAFE_TO_CONTINUE  / 22:56:41.557|0；|
|[w16](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:1599)|NORMAL|22:56:41.961|VALID_YES / +0.508|YES 0.92×5.0|225|22:56:43.961 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:56:45.562|0；|
|[w17](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:1653)|NORMAL|22:56:45.992|VALID_YES / +0.824|YES 0.93×5.0|234|22:56:47.991 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 22:56:50.579|0；|
|[w18](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:1727)|NORMAL|22:56:52.108|VALID_YES / +0.367|YES 0.94×5.0|348|22:56:52.213 SIGNAL_REVERSED / -0.589|REJECTED / BLOCKED SYNC_CACHE_NOT_READY / 22:57:02.766|0；|

相关事件：

- [22:55:19.943](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:15) 等待 NETWORK_RECOVERING；VALID_NO / -0.987047726312825566661973814
- [22:55:25.634](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:110) 等待 OBI_NO_SIGNAL；NONE / -0.2099372399302360712470988917
- [22:56:03.667](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:631) 等待 WS_BOOK_STALE；INVALID / None
- [22:56:10.859](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:710) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [22:56:13.018](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:747) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [22:56:13.705](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:752) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.802885046142818743840157692
- [22:56:13.708](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:758) 等待 NETWORK_RECOVERING；VALID_YES / 0.802885046142818743840157692
- [22:56:18.637](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:996) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [22:56:31.980](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:1427) 等待 OBI_NO_SIGNAL；NONE / 0.0866879813980526086324662113
- [22:56:51.675](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_3d2096e512234f5aa2ef9f14ccafe5c6.jsonl:1712) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.3805878960282315219410148880

## 23:00 / 86efea18a69b4f89b080bc8209a1370f

[btc-updown-5m-1792249200](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1)；结束 23:05:00.473，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 -0.950000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:56)|NORMAL|23:00:23.702|VALID_YES / +0.527|YES 0.32×5.0|236|23:00:25.701 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:00:27.471|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:96)|NORMAL|23:00:27.927|VALID_NO / -0.880|NO 0.68×5.0|247|23:00:29.926 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:00:32.472|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:248)|NORMAL|23:00:34.852|VALID_YES / +0.313|YES 0.32×5.0|256|23:00:36.852 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:00:39.639|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:326)|NORMAL|23:00:40.914|VALID_NO / -0.937|NO 0.66×5.0|501|23:00:41.906 SIGNAL_REVERSED / +0.381|ACK / SAFE_TO_CONTINUE  / 23:00:44.486|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:373)|NORMAL|23:00:45.817|VALID_NO / -0.675|NO 0.66×5.0|228|23:00:47.818 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:00:50.501|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:825)|NORMAL|23:00:55.862|VALID_NO / -0.956|NO 0.67×5.0|243|23:00:56.154 SIGNAL_REVERSED / +0.433|ACK / SAFE_TO_CONTINUE  / 23:00:58.513|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:879)|NORMAL|23:00:59.849|VALID_NO / -0.966|NO 0.68×5.0|392|23:01:00.002 SIGNAL_REVERSED / +0.970|ACK / SAFE_TO_CONTINUE  / 23:01:02.518|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:921)|NORMAL|23:01:03.779|VALID_YES / +0.693|YES 0.36×5.0|248|23:01:05.263 SIGNAL_REVERSED / -0.311|ACK / SAFE_TO_CONTINUE  / 23:01:07.517|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:972)|NORMAL|23:01:08.020|VALID_YES / +0.790|YES 0.42×5.0|288|23:01:10.019 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:01:12.501|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1026)|NORMAL|23:01:13.782|VALID_YES / +0.302|YES 0.47×5.0|252|23:01:14.281 SIGNAL_REVERSED / -0.405|ACK / SAFE_TO_CONTINUE  / 23:01:16.534|0；|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1216)|NORMAL|未发送|VALID_YES / +0.338|YES 0.41×5.0|—|23:01:18.748 PLACEMENT_NOT_ACKNOWLEDGED|ABORTED / SAFE_TO_CONTINUE SIGNAL_STALE_OR_EPISODE_CHANGED / 23:01:19.320|0；|
|[w12](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1242)|NORMAL|23:01:19.816|VALID_NO / -0.549|NO 0.58×5.0|228|23:01:21.818 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:01:23.532|0；|
|[w13](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1309)|NORMAL|23:01:24.962|VALID_YES / +0.894|YES 0.32×5.0|435|23:01:26.362 SIGNAL_REVERSED / -0.801|ACK / SAFE_TO_CONTINUE  / 23:01:28.531|0；|
|[w14](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1364)|NORMAL|23:01:29.949|VALID_YES / +0.828|YES 0.34×5.0|625|23:01:30.158 SIGNAL_REVERSED / -0.309|ACK / SAFE_TO_CONTINUE  / 23:01:32.542|0；|
|[w15](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1427)|NORMAL|23:01:34.680|VALID_YES / +0.725|YES 0.28×5.0|932|23:01:36.567 SIGNAL_REVERSED / -0.333|ACK / SAFE_TO_CONTINUE  / 23:01:38.554|0；|
|[w16](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1481)|NORMAL|23:01:39.819|VALID_YES / +0.674|YES 0.24×5.0|236|23:01:41.149 SIGNAL_REVERSED / -0.466|ACK / SAFE_TO_CONTINUE  / 23:01:51.395|5.0；本地MATCHED 23:01:42.711|
|[w17](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1766)|REBALANCE|23:01:51.893|库存补仓|NO 0.8×5.0|506|23:01:53.459 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:01:56.375|0；|
|[w18](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1830)|REBALANCE|23:01:57.788|库存补仓|NO 0.8×5.0|250|23:01:59.297 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:02:01.587|0；|
|[w19](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1887)|REBALANCE|23:02:01.992|库存补仓|NO 0.78×5.0|227|23:02:04.996 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:02:06.582|0；|
|[w20](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1952)|REBALANCE|23:02:07.789|库存补仓|NO 0.84×5.0|194|23:02:10.790 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:02:12.591|0；|
|[w21](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:2018)|REBALANCE|23:02:14.002|库存补仓|NO 0.9×5.0|249|23:02:17.004 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:02:18.595|0；|
|[w22](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:2085)|REBALANCE|23:02:19.714|库存补仓|NO 0.89×5.0|206|23:02:20.546 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:02:22.598|0；|
|[w23](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:2145)|REBALANCE|23:02:23.783|库存补仓|NO 0.91×5.0|238|23:02:25.454 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:02:27.595|0；|
|[w24](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:2208)|REBALANCE|23:02:28.028|库存补仓|NO 0.92×5.0|209|23:02:31.033 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:02:32.826|0；|
|[w25](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:2281)|REBALANCE|23:02:33.794|库存补仓|NO 0.91×5.0|319|23:02:33.902 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:02:36.623|0；|
|[w26](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:2345)|REBALANCE|23:02:37.728|库存补仓|NO 0.9×5.0|198|23:02:40.729 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:02:42.638|0；|
|[w27](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:2417)|REBALANCE|23:02:43.753|库存补仓|NO 0.9×5.0|217|23:02:45.021 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:02:46.794|0；|
|[w28](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:2481)|REBALANCE|23:02:47.815|库存补仓|NO 0.94×5.0|364|23:02:50.819 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:02:52.494|0；|
|[w29](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:2548)|REBALANCE|23:02:53.832|库存补仓|NO 0.95×5.0|218|23:02:56.837 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:03:03.389|5.0；本地MATCHED 23:02:56.838|

相关事件：

- [23:00:22.880](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:15) 等待 NETWORK_RECOVERING；VALID_YES / 0.507590011117762764046865646
- [23:00:33.694](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:142) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.4287181662889233757968894794
- [23:00:34.121](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:149) 等待 OBI_NO_SIGNAL；NONE / 0.1639738741835682365073908560
- [23:00:39.654](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:294) 等待 NETWORK_RECOVERING；VALID_NO / -0.6269426849992275606364900355
- [23:00:51.824](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:425) 等待 SIGNAL_EPISODE_UNAVAILABLE；VALID_NO / -0.593240699955952428622912746
- [23:00:55.409](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:812) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.8072358310651928382306652595
- [23:01:17.492](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1069) 等待 NETWORK_RECOVERING；NONE / -0.1034041993270550198311998346
- [23:01:24.025](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1288) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.916913123844731977818853974
- [23:01:24.506](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1293) 等待 OBI_NO_SIGNAL；NONE / -0.004523664843697252042400918236
- [23:01:33.347](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1408) 等待 WS_BOOK_STALE；INVALID / None
- [23:01:51.367](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:1695) 本地观察 CONFIRMED：YES 5.0 @ 0.24；trade=e6d986bc-6626-4419-b8dd-70c10b041fbc
- [23:03:03.330](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:2867) 本地观察 CONFIRMED：NO 5.0 @ 0.95；trade=a0fbc863-c705-40e9-b5e2-154682edffed
- [23:04:15.613](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_86efea18a69b4f89b080bc8209a1370f.jsonl:3228) 合并 5 对，状态 CONFIRMED，释放成本 5.95

## 23:05 / ae7561e3e0154cfe96444e2dc08f6f23

[btc-updown-5m-1792249500](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1)；结束 23:10:00.717，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 -0.847610 USDC；期末 YES 0.0 / NO 0.02；残仓成本 0.012590。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:35)|NORMAL|23:05:22.158|VALID_NO / -0.528|NO 0.39×5.0|250|23:05:22.731 SIGNAL_REVERSED / +0.367|ACK / SAFE_TO_CONTINUE  / 23:05:24.409|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:70)|NORMAL|23:05:25.581|VALID_NO / -0.909|NO 0.39×5.0|215|23:05:27.585 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:05:29.392|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:160)|NORMAL|23:05:30.213|VALID_NO / -0.335|NO 0.39×5.0|195|23:05:30.579 SIGNAL_REVERSED / +0.301|ACK / SAFE_TO_CONTINUE  / 23:05:32.465|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:201)|NORMAL|23:05:33.782|VALID_NO / -0.862|NO 0.43×5.0|1134|23:05:35.742 SIGNAL_REVERSED / +0.320|ACK / SAFE_TO_CONTINUE  / 23:05:37.418|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:241)|NORMAL|23:05:37.871|VALID_YES / +0.646|YES 0.56×5.0|216|23:05:39.509 SIGNAL_REVERSED / -0.810|ACK / SAFE_TO_CONTINUE  / 23:05:41.448|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:285)|NORMAL|23:05:41.867|VALID_YES / +0.675|YES 0.58×5.0|209|23:05:42.445 SIGNAL_REVERSED / -0.528|ACK / SAFE_TO_CONTINUE  / 23:05:44.234|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:372)|NORMAL|23:05:46.326|VALID_NO / -0.303|NO 0.43×5.0|950|23:05:47.638 SIGNAL_REVERSED / +0.469|ACK / SAFE_TO_CONTINUE  / 23:05:49.431|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:589)|NORMAL|23:05:51.031|VALID_YES / +0.343|YES 0.52×5.0|207|23:05:53.032 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:05:55.438|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:646)|NORMAL|23:05:56.546|VALID_NO / -0.562|NO 0.47×5.0|208|23:05:58.548 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:06:00.437|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:697)|NORMAL|23:06:01.617|VALID_NO / -0.430|NO 0.47×5.0|211|23:06:02.492 SIGNAL_REVERSED / +0.355|ACK / SAFE_TO_CONTINUE  / 23:06:04.503|0；|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:747)|NORMAL|23:06:05.652|VALID_YES / +0.848|YES 0.54×5.0|222|23:06:06.786 SIGNAL_REVERSED / -0.648|ACK / SAFE_TO_CONTINUE  / 23:06:13.565|5.0；本地MATCHED 23:06:08.436|
|[w12](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:928)|REBALANCE|23:06:14.272|库存补仓|NO 0.47×5.0|390|23:06:14.396 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:06:16.459|0；|
|[w13](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:977)|REBALANCE|23:06:17.809|库存补仓|NO 0.49×5.0|237|23:06:19.524 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:06:21.785|0；|
|[w14](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1055)|REBALANCE|23:06:24.664|库存补仓|NO 0.51×5.0|250|23:06:26.403 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:06:33.513|0.02；本地MATCHED 23:06:26.635;本地MATCHED 23:06:26.635|
|[w15](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1396)|REBALANCE|23:06:34.073|库存补仓|NO 0.66×5.0|400|23:06:34.567 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:06:37.001|0；|
|[w16](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1451)|REBALANCE|23:06:37.697|库存补仓|NO 0.77×5.0|276|23:06:37.974 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:06:40.025|0；|
|[w17](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1534)|REBALANCE|23:06:43.739|库存补仓|NO 0.65×5.0|450|23:06:45.024 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:06:47.029|0；|
|[w18](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1607)|REBALANCE|23:06:48.931|库存补仓|NO 0.61×5.0|332|23:06:49.051 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:06:51.550|0；|
|[w19](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1719)|REBALANCE|23:06:52.377|库存补仓|NO 0.63×5.0|308|23:06:53.404 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:06:54.522|0；|
|[w20](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1784)|REBALANCE|23:06:55.709|库存补仓|NO 0.63×5.0|214|23:06:56.341 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:06:58.061|0；|
|[w21](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1842)|REBALANCE|23:06:59.810|库存补仓|NO 0.63×5.0|202|23:07:00.657 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:07:08.059|5.0；本地MATCHED 23:07:02.030|

相关事件：

- [23:05:22.110](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:22) 等待 OBI_NO_SIGNAL；NONE / 0.0904320087808057075237098904
- [23:05:49.448](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:407) 等待 NETWORK_RECOVERING；VALID_YES / 0.6811886416552651160557835865
- [23:05:50.627](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:576) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.4648652472235976515526632242
- [23:05:55.456](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:635) 等待 WS_BOOK_STALE；INVALID / None
- [23:06:13.540](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:872) 本地观察 CONFIRMED：YES 5.0 @ 0.54；trade=05a808ca-a24a-478a-aa5e-9b1d4c3d4bb5
- [23:06:20.765](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:996) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [23:06:23.053](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1034) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [23:06:32.496](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1265) 本地观察 CONFIRMED：NO 0.01 @ 0.51；trade=3a3ba49b-2ac5-440a-96d3-f4d7123ad721
- [23:06:33.481](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1314) 本地观察 CONFIRMED：NO 0.01 @ 0.51；trade=b9f17cab-2867-43d2-8e16-a41df7162fa7
- [23:06:33.513](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1384) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [23:06:41.748](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1499) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；NONE / 0.01345291479820627802690582960
- [23:06:47.548](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1581) 等待 WS_BOOK_STALE；INVALID / None
- [23:06:48.040](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1585) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.923739710033548615934853613
- [23:06:50.002](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1620) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [23:06:51.569](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1658) 等待 NETWORK_PAUSED；VALID_NO / -0.5723009529887875139612132115
- [23:06:52.002](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1710) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [23:06:54.522](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:1768) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [23:07:08.012](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:2048) 本地观察 CONFIRMED：NO 5.0 @ 0.63；trade=99704fd4-872b-4ed2-9901-7ae7c7c41408
- [23:09:14.662](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_ae7561e3e0154cfe96444e2dc08f6f23.jsonl:2635) 合并 5 对，状态 CONFIRMED，释放成本 5.847609561752988047808764940

## 23:10 / 7d354af8b75e4dfca27357f2b358e9eb

[btc-updown-5m-1792249800](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:1)；结束 23:15:00.762，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.050000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:25)|NORMAL|23:10:20.963|VALID_YES / +0.930|YES 0.42×5.0|344|23:10:22.965 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:10:29.525|5.0；本地MATCHED 23:10:22.966|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:117)|REBALANCE|23:10:30.023|库存补仓|NO 0.54×5.0|550|23:10:33.028 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:10:35.678|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:168)|REBALANCE|23:10:36.114|库存补仓|NO 0.57×5.0|231|23:10:39.115 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:10:46.682|5.0；本地MATCHED 23:10:39.116|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:303)|NORMAL|23:10:48.190|VALID_NO / -0.660|NO 0.57×5.0|223|23:10:48.804 SIGNAL_REVERSED / +0.344|ACK / SAFE_TO_CONTINUE  / 23:10:50.687|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:344)|NORMAL|23:10:51.931|VALID_YES / +0.754|YES 0.42×5.0|210|23:10:53.932 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:10:55.689|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:388)|NORMAL|23:10:56.112|VALID_YES / +0.531|YES 0.42×5.0|209|23:10:56.695 SIGNAL_REVERSED / -0.308|ACK / SAFE_TO_CONTINUE  / 23:10:58.700|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:450)|NORMAL|23:11:00.490|VALID_YES / +0.392|YES 0.42×5.0|237|23:11:01.337 SIGNAL_REVERSED / -0.516|ACK / SAFE_TO_CONTINUE  / 23:11:03.710|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:494)|NORMAL|23:11:04.127|VALID_NO / -0.649|NO 0.57×5.0|222|23:11:06.129 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:11:07.717|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:590)|NORMAL|23:11:09.256|VALID_YES / +0.313|YES 0.37×5.0|518|23:11:09.396 SIGNAL_REVERSED / -0.429|ACK / SAFE_TO_CONTINUE  / 23:11:11.726|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:636)|NORMAL|23:11:12.155|VALID_YES / +0.936|YES 0.37×5.0|208|23:11:13.837 SIGNAL_REVERSED / -0.556|ACK / SAFE_TO_CONTINUE  / 23:11:15.721|0；|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:688)|NORMAL|23:11:16.375|VALID_YES / +0.842|YES 0.43×5.0|233|23:11:17.732 WS_RECONNECT_REQUIRED|ACK / SAFE_TO_CONTINUE  / 23:11:22.735|0；|
|[w12](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:780)|NORMAL|23:11:25.956|VALID_NO / -0.943|NO 0.52×5.0|572|23:11:27.957 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:11:29.926|0；|
|[w13](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:831)|NORMAL|23:11:30.348|VALID_YES / +0.717|YES 0.51×5.0|219|23:11:30.925 SIGNAL_REVERSED / -0.763|ACK / SAFE_TO_CONTINUE  / 23:11:32.739|0；|
|[w14](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:879)|NORMAL|23:11:34.000|VALID_NO / -0.608|NO 0.41×5.0|213|23:11:36.004 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:11:37.747|0；|
|[w15](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:991)|NORMAL|23:11:38.477|VALID_NO / -0.717|NO 0.43×5.0|217|23:11:39.102 SIGNAL_REVERSED / +0.636|ACK / SAFE_TO_CONTINUE  / 23:11:40.760|0；|
|[w16](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:1040)|NORMAL|23:11:42.068|VALID_YES / +0.732|YES 0.51×5.0|221|23:11:43.664 SIGNAL_REVERSED / -0.914|ACK / SAFE_TO_CONTINUE  / 23:11:45.861|0；|
|[w17](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:1111)|NORMAL|23:11:47.562|VALID_NO / -0.989|NO 0.47×5.0|691|23:11:47.949 SIGNAL_REVERSED / +0.333|ACK / SAFE_TO_CONTINUE  / 23:11:50.770|0；|
|[w18](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:1172)|NORMAL|23:11:51.982|VALID_YES / +0.988|YES 0.49×5.0|392|23:11:52.152 SIGNAL_REVERSED / -0.573|ACK / SAFE_TO_CONTINUE  / 23:11:54.833|0；|
|[w19](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:1245)|NORMAL|23:11:56.775|VALID_NO / -0.983|NO 0.42×5.0|279|23:11:56.840 SIGNAL_REVERSED / +0.363|ACK / SAFE_TO_CONTINUE  / 23:11:58.593|0；|
|[w20](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:1296)|NORMAL|23:12:00.077|VALID_NO / -0.953|NO 0.43×5.0|221|23:12:00.551 MARKET_SUBMISSION_CUTOFF|ACK / SAFE_TO_CONTINUE  / 23:12:02.795|0；|

相关事件：

- [23:10:29.518](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:69) 本地观察 CONFIRMED：YES 5.0 @ 0.42；trade=93d348d5-f5db-459a-a0ec-b16b5ed7010a
- [23:10:46.668](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:247) 本地观察 CONFIRMED：NO 5.0 @ 0.57；trade=95fd7f41-70ce-45a2-bbc6-99fc9c0f71ff
- [23:11:00.028](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:428) 等待 OBI_NO_SIGNAL；NONE / -0.1463958259460007091839319183
- [23:11:08.140](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:535) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.983718487394957983193277311
- [23:11:08.980](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:547) 等待 OBI_NO_SIGNAL；NONE / -0.07986695362942672666797104285
- [23:11:24.065](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:754) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.979328592690590375392756739
- [23:11:24.966](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:759) 等待 WS_BOOK_STALE；INVALID / None
- [23:11:38.170](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:927) 等待 OBI_NO_SIGNAL；NONE / 0.008979048885932823412038576655
- [23:11:46.799](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:1093) 等待 WS_BOOK_STALE；INVALID / None
- [23:11:55.954](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:1226) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.3239243301306916512502237871
- [23:14:15.552](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_7d354af8b75e4dfca27357f2b358e9eb.jsonl:1903) 合并 5 对，状态 CONFIRMED，释放成本 4.95

## 23:15 / b20decaba25a4eb4ab0e70c121cc0e79

[btc-updown-5m-1792250100](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:1)；结束 23:20:00.923，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +1.050000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:134)|NORMAL|23:15:29.157|VALID_NO / -0.310|NO 0.51×5.0|217|23:15:29.985 SIGNAL_REVERSED / +0.401|ACK / SAFE_TO_CONTINUE  / 23:15:36.834|5.0；本地MATCHED 23:15:30.853|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:221)|REBALANCE|23:15:37.334|库存补仓|YES 0.44×5.0|228|23:15:37.562 PLACEMENT_NOT_ACKNOWLEDGED|REJECTED / SAFE_TO_CONTINUE  / 23:15:44.896|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:320)|REBALANCE|23:15:47.300|库存补仓|YES 0.24×5.0|777|23:15:49.009 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:15:50.847|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:361)|REBALANCE|23:15:52.077|库存补仓|YES 0.27×5.0|383|23:15:53.651 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:15:55.830|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:405)|REBALANCE|23:15:56.252|库存补仓|YES 0.28×5.0|215|23:15:57.301 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:16:03.686|5.0；本地MATCHED 23:15:57.663|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:527)|NORMAL|23:16:07.241|VALID_NO / -0.780|NO 0.76×5.0|559|23:16:09.245 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:16:11.846|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:576)|NORMAL|23:16:12.401|VALID_YES / +0.715|YES 0.24×5.0|234|23:16:14.403 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:16:16.914|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:633)|NORMAL|未发送|VALID_YES / +0.977|YES 0.24×5.0|—|23:16:18.142 PLACEMENT_NOT_ACKNOWLEDGED|ABORTED / SAFE_TO_CONTINUE NETWORK_RECOVERING / 23:16:18.660|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:804)|NORMAL|23:16:21.130|VALID_NO / -0.812|NO 0.72×5.0|293|23:16:23.132 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:16:24.895|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:867)|NORMAL|23:16:26.216|VALID_NO / -0.482|NO 0.72×5.0|265|23:16:28.216 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:16:32.885|0；|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:964)|NORMAL|23:16:34.705|VALID_NO / -0.305|NO 0.73×5.0|1266|23:16:36.057 SIGNAL_REVERSED / +0.517|ACK / SAFE_TO_CONTINUE  / 23:16:37.945|0；|
|[w12](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:1101)|NORMAL|23:16:39.774|VALID_YES / +0.626|YES 0.23×5.0|230|23:16:41.775 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:16:43.891|0；|
|[w13](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:1153)|NORMAL|23:16:44.325|VALID_YES / +0.606|YES 0.23×5.0|234|23:16:45.717 SIGNAL_REVERSED / -0.301|ACK / SAFE_TO_CONTINUE  / 23:16:47.905|0；|
|[w14](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:1206)|NORMAL|23:16:48.340|VALID_NO / -0.992|NO 0.77×5.0|739|23:16:50.343 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:16:52.021|0；|
|[w15](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:1256)|NORMAL|23:16:52.468|VALID_NO / -0.711|NO 0.79×5.0|261|23:16:54.470 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:16:56.969|0；|
|[w16](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:1316)|NORMAL|23:16:58.071|VALID_YES / +0.710|YES 0.2×5.0|296|23:16:58.671 SIGNAL_REVERSED / -0.753|ACK / SAFE_TO_CONTINUE  / 23:17:00.951|0；|

相关事件：

- [23:15:27.991](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:25) 等待 NETWORK_RECOVERING；VALID_NO / -0.6455764460581658697918539025
- [23:15:29.122](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:127) 等待 OBI_NO_SIGNAL；NONE / -0.2926633041433301358418170003
- [23:15:36.823](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:176) 本地观察 CONFIRMED：NO 5.0 @ 0.51；trade=640091b4-0909-43a7-a71a-ee34431197a8
- [23:15:46.057](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:295) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.8668776339211002753731229745
- [23:16:03.672](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:467) 本地观察 CONFIRMED：YES 5.0 @ 0.28；trade=bfa5431a-e0a4-4fcd-bc59-86eed15ffd39
- [23:16:19.621](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:649) 等待 NETWORK_RECOVERING；VALID_YES / 0.762573471928656223760025480
- [23:16:26.105](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:848) 等待 OBI_NO_SIGNAL；NONE / -0.2093675010071640013312080713
- [23:16:37.961](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:1001) 等待 NETWORK_RECOVERING；VALID_NO / -0.6081462441356748692322901875
- [23:16:39.337](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:1088) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.561909604740763920126733293
- [23:19:21.636](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b20decaba25a4eb4ab0e70c121cc0e79.jsonl:1919) 合并 5 对，状态 CONFIRMED，释放成本 3.95

## 23:20 / c3a74171a4604ccd8eadb35f1f306079

[btc-updown-5m-1792250400](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1)；结束 23:25:00.701，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 -1.400000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:364)|NORMAL|23:20:23.904|VALID_NO / -0.326|NO 0.9×5.0|585|23:20:24.237 SIGNAL_REVERSED / +0.353|ACK / SAFE_TO_CONTINUE  / 23:20:26.760|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:408)|NORMAL|23:20:28.022|VALID_NO / -0.367|NO 0.91×5.0|400|23:20:28.507 SIGNAL_REVERSED / +0.304|ACK / SAFE_TO_CONTINUE  / 23:20:30.574|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:870)|NORMAL|23:20:34.633|VALID_NO / -0.327|NO 0.93×5.0|239|23:20:36.636 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:20:38.023|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:946)|NORMAL|23:20:40.073|VALID_NO / -0.339|NO 0.93×5.0|247|23:20:40.387 SIGNAL_REVERSED / +0.377|ACK / SAFE_TO_CONTINUE  / 23:20:42.561|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1059)|NORMAL|23:20:48.024|VALID_NO / -0.457|NO 0.88×5.0|788|23:20:49.218 SIGNAL_REVERSED / +0.497|ACK / SAFE_TO_CONTINUE  / 23:20:51.543|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1192)|NORMAL|23:20:53.539|VALID_NO / -0.883|NO 0.89×5.0|260|23:20:54.749 SIGNAL_REVERSED / +0.311|ACK / SAFE_TO_CONTINUE  / 23:20:57.555|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1343)|NORMAL|23:20:58.825|VALID_NO / -0.760|NO 0.9×5.0|254|23:21:00.484 SIGNAL_REVERSED / +0.394|ACK / SAFE_TO_CONTINUE  / 23:21:02.739|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1399)|NORMAL|23:21:04.278|VALID_NO / -0.474|NO 0.91×5.0|239|23:21:06.280 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:21:16.592|5.0；本地MATCHED 23:21:09.584|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1637)|REBALANCE|23:21:20.943|库存补仓|YES 0.36×5.0|227|23:21:21.875 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:21:28.597|5.0；本地MATCHED 23:21:22.587|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1805)|NORMAL|23:21:31.112|VALID_NO / -0.983|NO 0.71×5.0|481|23:21:31.298 SIGNAL_REVERSED / +0.776|REJECTED / SAFE_TO_CONTINUE  / 23:21:40.642|0；|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1912)|NORMAL|23:21:42.110|VALID_YES / +0.318|YES 0.24×5.0|258|23:21:42.461 SIGNAL_REVERSED / -0.375|ACK / SAFE_TO_CONTINUE  / 23:21:44.621|0；|
|[w12](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1964)|NORMAL|23:21:46.000|VALID_NO / -0.447|NO 0.76×5.0|410|23:21:46.210 SIGNAL_REVERSED / +0.415|ACK / SAFE_TO_CONTINUE  / 23:21:48.630|0；|
|[w13](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:2018)|NORMAL|23:21:49.987|VALID_YES / +0.683|YES 0.32×5.0|466|23:21:50.932 SIGNAL_REVERSED / -0.921|ACK / SAFE_TO_CONTINUE  / 23:21:58.651|5.0；本地MATCHED 23:21:51.593|
|[w14](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:2222)|REBALANCE|23:22:00.151|库存补仓|NO 0.69×5.0|277|23:22:03.154 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:22:08.918|5.0；本地MATCHED 23:22:03.155|

相关事件：

- [23:20:20.815](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:15) 等待 NETWORK_RECOVERING；VALID_NO / -0.7447723759806351891277085945
- [23:20:22.001](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:170) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.928952042628774422735346359
- [23:20:22.857](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:180) 等待 OBI_NO_SIGNAL；NONE / 0.03254608799173655000962902012
- [23:20:23.328](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:203) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.4725836325836325836325836326
- [23:20:23.522](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:240) 等待 OBI_NO_SIGNAL；NONE / 0.2947672263121656691912504634
- [23:20:23.539](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:246) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.3013506379800945060321629521
- [23:20:23.807](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:330) 等待 OBI_NO_SIGNAL；NONE / 0.2789397563553429381988095900
- [23:20:29.341](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:417) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [23:20:31.373](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:445) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [23:20:31.993](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:449) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.4629692832764505119453924915
- [23:20:33.544](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:707) 等待 OBI_NO_SIGNAL；NONE / 0.2749714350199602206203890480
- [23:20:33.940](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:764) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.3065052717780734977991606101
- [23:20:34.617](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:864) 等待 OBI_NO_SIGNAL；NONE / -0.1058999486572761568855057258
- [23:20:38.023](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:913) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [23:20:40.011](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:925) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.4103285022402002575776814381
- [23:20:40.062](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:940) 等待 OBI_NO_SIGNAL；NONE / 0.1732484268662572243432072311
- [23:20:44.129](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:984) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.8551690177562784230802653305
- [23:20:46.057](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1019) 等待 WS_BOOK_STALE；INVALID / None
- [23:20:46.568](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1032) 等待 OBI_NO_SIGNAL；NONE / 0.1166436507438327788588286988
- [23:20:47.072](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1034) 等待 WS_BOOK_STALE；INVALID / None
- [23:20:47.697](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1042) 等待 OBI_NO_SIGNAL；NONE / 0.1200956638634445243380123175
- [23:20:52.010](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1098) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.910397195111141751282783616
- [23:20:52.782](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1174) 等待 OBI_NO_SIGNAL；NONE / -0.2978221855750109133239559587
- [23:20:57.992](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1237) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.6093609770986802243208644825
- [23:21:03.824](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1385) 等待 WS_BOOK_STALE；INVALID / None
- [23:21:16.575](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1535) 本地观察 CONFIRMED：NO 5.0 @ 0.91；trade=a3ed4dc0-6c9b-4811-a99e-a5e409a55154
- [23:21:17.077](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1582) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；NONE / -0.2651583710407239819004524887
- [23:21:28.578](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:1745) 本地观察 CONFIRMED：YES 5.0 @ 0.36；trade=05c28733-2237-410e-8e06-d873e242a241
- [23:21:58.619](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:2161) 本地观察 CONFIRMED：YES 5.0 @ 0.32；trade=3fbeeb6a-fe63-4e08-aa70-e2cd93b88c02
- [23:22:08.882](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:2374) 本地观察 CONFIRMED：NO 5.0 @ 0.69；trade=5b852bd2-5a7a-42df-a32e-af625ae48c57
- [23:24:14.831](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_c3a74171a4604ccd8eadb35f1f306079.jsonl:2943) 合并 10 对，状态 CONFIRMED，释放成本 11.40

## 23:25 / cf35a9ebd90f41c1b93cc26ed8418d30

[btc-updown-5m-1792250700](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1)；结束 23:30:00.800，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 -4.200000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:167)|NORMAL|23:25:22.864|VALID_YES / +0.733|YES 0.52×5.0|248|23:25:23.679 SIGNAL_REVERSED / -0.390|ACK / SAFE_TO_CONTINUE  / 23:25:30.723|5.0；本地MATCHED 23:25:25.521|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:263)|REBALANCE|23:25:32.466|库存补仓|NO 0.63×5.0|442|23:25:35.466 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:25:41.797|5.0；本地MATCHED 23:25:35.727|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:388)|NORMAL|23:25:42.404|VALID_NO / -0.826|NO 0.53×5.0|401|23:25:43.621 SIGNAL_REVERSED / +0.537|ACK / SAFE_TO_CONTINUE  / 23:25:49.597|5.0；本地MATCHED 23:25:44.729|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:482)|REBALANCE|23:25:50.516|库存补仓|YES 0.57×5.0|245|23:25:51.411 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:25:57.561|5.0；本地MATCHED 23:25:52.006|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:600)|NORMAL|23:26:01.041|VALID_YES / +0.972|YES 0.6×5.0|631|23:26:01.244 SIGNAL_REVERSED / -0.339|REJECTED / SAFE_TO_CONTINUE  / 23:26:07.773|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:708)|NORMAL|23:26:11.539|VALID_YES / +0.915|YES 0.71×5.0|1535|23:26:13.445 SIGNAL_REVERSED / -0.600|ACK / SAFE_TO_CONTINUE  / 23:26:15.757|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:785)|NORMAL|23:26:18.192|VALID_YES / +0.759|YES 0.69×5.0|547|23:26:18.905 SIGNAL_REVERSED / -0.598|ACK / SAFE_TO_CONTINUE  / 23:26:26.770|5.0；本地MATCHED 23:26:19.608|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:2998)|REBALANCE|23:29:16.975|库存补仓|NO 0.9×5.0|230|23:29:19.980 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:29:23.951|5.0；本地MATCHED 23:29:19.981|

相关事件：

- [23:25:20.885](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:15) 等待 NETWORK_RECOVERING；VALID_YES / 0.6379027114778810660379181725
- [23:25:21.996](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:151) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.4642122112965927281042762406
- [23:25:30.716](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:217) 本地观察 CONFIRMED：YES 5.0 @ 0.52；trade=94d7946c-dc4b-4bc0-8248-1e511b901a3f
- [23:25:31.689](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:248) 等待 WS_BOOK_STALE；INVALID / None
- [23:25:41.785](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:315) 本地观察 CONFIRMED：NO 5.0 @ 0.63；trade=4812768d-7565-4700-9cac-7f8520f373f0
- [23:25:42.286](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:352) 等待 OBI_NO_SIGNAL；NONE / 0.1883686030579177450575262620
- [23:25:49.587](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:436) 本地观察 CONFIRMED：NO 5.0 @ 0.53；trade=dc49932d-f4cc-4a74-97cd-c22f162e3757
- [23:25:50.079](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:469) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.5415881424132837569398461775
- [23:25:57.548](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:541) 本地观察 CONFIRMED：YES 5.0 @ 0.57；trade=348bdf4e-93df-4f37-aed2-4999bb4e775e
- [23:26:09.554](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:670) 等待 WS_BOOK_STALE；INVALID / None
- [23:26:10.019](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:680) 等待 OBI_NO_SIGNAL；NONE / -0.2261935820506130967910253065
- [23:26:10.416](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:685) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.6762504711139826630054380015
- [23:26:16.172](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:747) 等待 OBI_NO_SIGNAL；NONE / 0.2399394994958291319094325786
- [23:26:16.565](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:752) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.734955463827938301107973061
- [23:26:16.996](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:760) 等待 OBI_NO_SIGNAL；NONE / 0.1538086730550592821179145688
- [23:26:17.406](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:765) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.9149233033579772164606392665
- [23:26:26.755](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:875) 本地观察 CONFIRMED：YES 5.0 @ 0.69；trade=88690711-2beb-4053-b904-d2bb75e61229
- [23:26:27.260](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:919) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_NO / -0.4352764804243787118722251052
- [23:26:28.477](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:936) 等待 WS_BOOK_STALE；INVALID / None
- [23:26:29.859](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:942) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_YES / 0.8385351912550659583743723055
- [23:26:29.864](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:950) 等待 WS_BOOK_STALE；INVALID / None
- [23:26:30.417](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:952) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_YES / 0.981894226068693306295377596
- [23:26:31.651](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:972) 等待 WS_BOOK_STALE；INVALID / None
- [23:26:32.492](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:974) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_YES / 0.775280898876404494382022472
- [23:26:49.117](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1161) 等待 WS_BOOK_STALE；INVALID / None
- [23:26:50.013](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1168) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_YES / 0.4166174999358694815688890029
- [23:26:51.625](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1191) 等待 WS_BOOK_STALE；INVALID / None
- [23:26:52.425](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1193) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_YES / 0.896945183608302288451303885
- [23:27:22.136](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1521) 等待 WS_BOOK_STALE；INVALID / None
- [23:27:22.588](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1523) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；NONE / -0.1157655668412087565453283704
- [23:27:38.757](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1703) 等待 WS_BOOK_STALE；INVALID / None
- [23:27:39.179](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1705) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_NO / -0.5587114651887772774506408035
- [23:27:55.936](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1892) 等待 WS_BOOK_STALE；INVALID / None
- [23:27:56.747](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1898) 等待 WS_RECONNECT_REQUIRED；INVALID / None
- [23:27:58.615](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1907) 等待 WS_REBUILD_REQUIRED；INVALID / None
- [23:27:59.271](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1912) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_NO / -0.9734777054565362856621448995
- [23:27:59.275](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1916) 等待 WS_BOOK_STALE；INVALID / None
- [23:28:00.928](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1924) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_NO / -0.9734777054565362856621448995
- [23:28:01.354](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1934) 等待 WS_BOOK_STALE；INVALID / None
- [23:28:01.871](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1936) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_YES / 0.631858407079646017699115044
- [23:28:02.270](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1947) 等待 WS_BOOK_STALE；INVALID / None
- [23:28:03.642](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:1953) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_NO / -0.6183079763193074033993252275
- [23:28:18.117](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:2119) 等待 NETWORK_RECOVERING；NONE / 0.2722589315712845783402396685
- [23:28:21.089](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:2462) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；NONE / -0.0004916689429117727396886096695
- [23:28:30.794](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:2573) 等待 WS_BOOK_STALE；INVALID / None
- [23:28:31.267](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:2575) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_NO / -0.753117412239186797810609490
- [23:28:35.250](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:2623) 等待 WS_BOOK_STALE；INVALID / None
- [23:28:35.777](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:2625) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_NO / -0.3147714750485385947420895209
- [23:28:54.031](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:2831) 等待 WS_BOOK_STALE；INVALID / None
- [23:28:55.675](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:2838) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_NO / -0.7368421052631578947368421055
- [23:28:55.679](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:2843) 等待 WS_BOOK_STALE；INVALID / None
- [23:28:56.158](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:2848) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_NO / -0.7368421052631578947368421055
- [23:28:59.754](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:2893) 等待 WS_BOOK_STALE；INVALID / None
- [23:29:00.213](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:2895) 等待 OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT；VALID_NO / -0.3103448275862068965517241379
- [23:29:15.389](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:2918) 合并 10 对，状态 CONFIRMED，释放成本 11.73333333333333333333333333
- [23:29:16.178](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:2982) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.843205364788749381867303494
- [23:29:23.932](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:3072) 本地观察 CONFIRMED：NO 5.0 @ 0.9；trade=3e2d7bb6-818f-47a7-a5a0-a71c7b7e6231
- [23:29:37.754](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_cf35a9ebd90f41c1b93cc26ed8418d30.jsonl:3115) 合并 5 对，状态 CONFIRMED，释放成本 7.466666666666666666666666668

## 23:30 / b9cd6ef04c354e8a8ec4b2366f5c407f

[btc-updown-5m-1792251000](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:1)；结束 23:35:00.676，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 -1.050000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:76)|NORMAL|23:30:22.034|VALID_NO / -0.361|NO 0.29×5.0|377|23:30:22.899 SIGNAL_REVERSED / +0.385|ACK / SAFE_TO_CONTINUE  / 23:30:29.529|5.0；本地MATCHED 23:30:24.707|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:169)|REBALANCE|23:30:30.844|库存补仓|YES 0.74×5.0|209|23:30:32.185 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:30:35.710|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:228)|REBALANCE|23:30:36.211|库存补仓|YES 0.77×5.0|1176|23:30:36.672 WS_BOOK_STALE|UNKNOWN / BLOCKED SYNC_CACHE_NOT_READY / 23:30:47.912|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:397)|REBALANCE|23:30:51.865|库存补仓|YES 0.78×5.0|208|23:30:54.868 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:30:57.121|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:474)|REBALANCE|23:30:58.622|库存补仓|YES 0.81×5.0|304|23:30:58.723 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:31:00.436|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:510)|REBALANCE|23:31:01.883|库存补仓|YES 0.84×5.0|463|23:31:04.884 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:31:06.812|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:568)|REBALANCE|23:31:08.324|库存补仓|YES 0.82×5.0|208|23:31:11.325 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:31:13.698|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:627)|REBALANCE|23:31:14.505|库存补仓|YES 0.83×5.0|212|23:31:17.506 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:31:19.656|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:680)|REBALANCE|23:31:20.068|库存补仓|YES 0.82×5.0|210|23:31:23.068 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:31:24.677|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:737)|REBALANCE|23:31:26.012|库存补仓|YES 0.87×5.0|431|23:31:29.017 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:31:31.710|0；|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:795)|REBALANCE|23:31:32.131|库存补仓|YES 0.91×5.0|218|23:31:34.522 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:31:36.660|0；|
|[w12](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:854)|REBALANCE|23:31:37.961|库存补仓|YES 0.92×5.0|206|23:31:40.965 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:31:42.667|0；|
|[w13](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:912)|REBALANCE|23:31:43.954|库存补仓|YES 0.94×5.0|206|23:31:46.957 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:31:49.674|0；|
|[w14](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:975)|REBALANCE|23:31:50.109|库存补仓|YES 0.92×5.0|203|23:31:51.202 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:31:58.693|5.0；本地MATCHED 23:31:52.669|
|[w15](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:1180)|NORMAL|23:32:00.174|VALID_YES / +0.672|YES 0.93×5.0|207|23:32:00.483 MARKET_SUBMISSION_CUTOFF|ACK / SAFE_TO_CONTINUE  / 23:32:02.720|0；|

相关事件：

- [23:30:21.442](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:15) 等待 NETWORK_RECOVERING；VALID_NO / -0.572068698177422110090537575
- [23:30:21.892](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:33) 等待 OBI_NO_SIGNAL；NONE / 0.02776312815490092669328712424
- [23:30:29.523](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:119) 本地观察 CONFIRMED：NO 5.0 @ 0.29；trade=80747ddf-1a5f-4e6d-ab44-73ca1e24f7a2
- [23:30:29.564](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:149) 等待 WS_BOOK_STALE；INVALID / None
- [23:30:30.068](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:152) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.9577544596012591815320041975
- [23:30:35.725](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:215) 等待 WS_BOOK_STALE；INVALID / None
- [23:30:37.387](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:231) 网络 NORMAL → PAUSED：NETWORK_REPEATED_SLOW
- [23:30:50.811](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:381) 网络 PAUSED → PROBE：NETWORK_PROBE_READY
- [23:30:57.121](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:450) 网络 PROBE → NORMAL：NETWORK_WRITE_RECOVERED
- [23:30:57.863](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:459) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；NONE / 0.2590127565169162506932889628
- [23:31:58.666](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:1123) 本地观察 CONFIRMED：YES 5.0 @ 0.92；trade=4514ccde-4555-401a-be6e-7dc9a709d96f
- [23:34:16.309](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_b9cd6ef04c354e8a8ec4b2366f5c407f.jsonl:1775) 合并 5 对，状态 CONFIRMED，释放成本 6.05

## 23:35 / a303f5bdd5e748218cf8c0cc0563628f

[btc-updown-5m-1792251300](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:1)；结束 23:40:00.632，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 +0.050000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:70)|NORMAL|23:35:25.794|VALID_NO / -0.586|NO 0.74×5.0|201|23:35:26.592 SIGNAL_REVERSED / +0.311|ACK / SAFE_TO_CONTINUE  / 23:35:34.566|5.0；本地MATCHED 23:35:27.853|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:170)|REBALANCE|23:35:36.044|库存补仓|YES 0.28×5.0|208|23:35:37.789 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:35:39.590|0；|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:214)|REBALANCE|23:35:40.297|库存补仓|YES 0.25×5.0|328|23:35:40.688 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:35:48.595|5.0；本地MATCHED 23:35:41.406;本地MATCHED 23:35:41.407;本地MATCHED 23:35:41.406|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:388)|NORMAL|23:35:53.668|VALID_YES / +0.404|YES 0.2×5.0|217|23:35:55.669 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:35:57.392|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:445)|NORMAL|23:35:58.735|VALID_NO / -0.906|NO 0.83×5.0|645|23:35:59.094 SIGNAL_REVERSED / +0.880|ACK / SAFE_TO_CONTINUE  / 23:36:01.582|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:628)|NORMAL|23:36:03.240|VALID_NO / -0.927|NO 0.87×5.0|220|23:36:05.210 ORDER_DEADLINE|ACK / SAFE_TO_CONTINUE  / 23:36:07.388|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:674)|NORMAL|23:36:07.815|VALID_NO / -0.526|NO 0.88×5.0|278|23:36:08.097 SIGNAL_REVERSED / +0.300|ACK / SAFE_TO_CONTINUE  / 23:36:10.595|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:733)|NORMAL|23:36:13.023|VALID_NO / -0.978|NO 0.86×5.0|243|23:36:13.027 SIGNAL_REVERSED / +0.322|UNKNOWN / BLOCKED SYNC_CACHE_NOT_READY / 23:36:24.198|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:902)|NORMAL|23:36:30.564|VALID_YES / +0.832|YES 0.24×5.0|259|23:36:30.579 SIGNAL_REVERSED / -0.400|REJECTED / SAFE_TO_CONTINUE  / 23:36:33.633|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:958)|NORMAL|23:36:35.892|VALID_NO / -0.967|NO 0.72×5.0|436|23:36:37.583 SIGNAL_REVERSED / +0.444|ACK / SAFE_TO_CONTINUE  / 23:36:40.637|0；|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:1073)|NORMAL|23:36:45.811|VALID_NO / -0.618|NO 0.83×5.0|973|23:36:46.400 SIGNAL_REVERSED / +0.562|ACK / SAFE_TO_CONTINUE  / 23:36:48.648|0；|
|[w12](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:1151)|NORMAL|未发送|VALID_NO / -0.482|NO 0.87×5.0|—|23:36:52.035 PLACEMENT_NOT_ACKNOWLEDGED|ABORTED / SAFE_TO_CONTINUE NETWORK_RECOVERING / 23:36:52.450|0；|
|[w13](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:1260)|NORMAL|23:36:56.783|VALID_NO / -0.919|NO 0.91×5.0|749|23:36:57.207 SIGNAL_REVERSED / +0.893|ACK / SAFE_TO_CONTINUE  / 23:36:59.656|0；|

相关事件：

- [23:35:24.576](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:20) 等待 NETWORK_RECOVERING；VALID_NO / -0.942493041658040622915372760
- [23:35:34.556](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:121) 本地观察 CONFIRMED：NO 5.0 @ 0.74；trade=a708f0a1-d4ab-4af0-9308-0264cd001b14
- [23:35:46.592](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:265) 本地观察 CONFIRMED：YES 2.0 @ 0.25；trade=638376ff-7e0c-4029-912a-6aa3c9055d6e
- [23:35:47.398](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:271) 本地观察 CONFIRMED：YES 2.99 @ 0.25；trade=325c488c-6eb8-448a-96fa-d0b28af07446
- [23:35:48.583](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:282) 本地观察 CONFIRMED：YES 0.01 @ 0.25；trade=b6001070-0e06-488b-9327-45c95f98408d
- [23:35:48.646](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:323) 等待 WS_BOOK_STALE；INVALID / None
- [23:35:50.425](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:332) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.3828571428571428571428571429
- [23:35:50.997](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:340) 等待 OBI_NO_SIGNAL；NONE / 0.0891089108910891089108910891
- [23:35:51.588](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:345) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.5079637442719583341744554575
- [23:35:53.257](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:375) 等待 OBI_NO_SIGNAL；NONE / 0.2638095096714813770170568413
- [23:35:57.877](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:426) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.6292423865239399219981744255
- [23:36:01.599](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:485) 等待 NETWORK_RECOVERING；VALID_NO / -0.3272711375360671504765235639
- [23:36:02.784](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:603) 等待 OBI_NO_SIGNAL；NONE / -0.1217553244483955764352444380
- [23:36:03.027](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:614) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.3174782809429702323643509056
- [23:36:03.207](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:616) 等待 OBI_NO_SIGNAL；NONE / -0.1539667660843630166169578185
- [23:36:11.444](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:714) 等待 WS_BOOK_STALE；INVALID / None
- [23:36:27.926](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:859) 等待 OBI_NO_SIGNAL；NONE / -0.1939145699239321240491515506
- [23:36:28.432](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:864) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.6301247045398973021436139865
- [23:36:41.883](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:1012) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.6741508035504456134642153405
- [23:36:43.739](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:1032) 等待 OBI_NO_SIGNAL；NONE / -0.0620386122079503066814414808
- [23:36:44.145](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:1041) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.3059794715003275824415811313
- [23:36:45.378](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:1057) 等待 OBI_NO_SIGNAL；NONE / 0.2383956484557906099732234232
- [23:36:49.824](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:1114) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.7905095502744132411975341825
- [23:36:51.218](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:1133) 等待 OBI_NO_SIGNAL；NONE / 0.1885703412406444247114042877
- [23:36:53.457](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:1170) 等待 NETWORK_RECOVERING；VALID_NO / -0.982317237598857693548886207
- [23:36:55.345](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:1238) 等待 WS_BOOK_STALE；INVALID / None
- [23:37:00.067](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:1303) 等待 NO_LEGAL_SIGNAL_CANDIDATE；VALID_YES / 0.939222811340473063020738454
- [23:39:17.655](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_a303f5bdd5e748218cf8c0cc0563628f.jsonl:1869) 合并 5 对，状态 CONFIRMED，释放成本 4.95

## 23:40 / fd04bce31a6248cea39a541571f41f37

[btc-updown-5m-1792251600](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1)；结束 23:45:00.213，COMPLETED / SAFE_TO_CONTINUE。
合并毛收益 -0.500000 USDC；期末 YES 0.0 / NO 0.0；残仓成本 0.000000。

|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|
|---|---|---|---|---|---:|---|---|---|
|[w1](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:28)|NORMAL|23:40:21.429|VALID_NO / -0.337|NO 0.46×5.0|230|23:40:22.334 SIGNAL_REVERSED / +0.918|ACK / SAFE_TO_CONTINUE  / 23:40:25.029|0；|
|[w2](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:89)|NORMAL|23:40:26.625|VALID_NO / -0.969|NO 0.42×5.0|250|23:40:27.170 SIGNAL_REVERSED / +0.396|ACK / SAFE_TO_CONTINUE  / 23:40:34.044|5.0；本地MATCHED 23:40:28.017|
|[w3](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:199)|REBALANCE|23:40:36.926|库存补仓|YES 0.51×5.0|716|23:40:37.406 WS_BOOK_STALE|REJECTED / BLOCKED SYNC_CACHE_NOT_READY / 23:40:48.151|0；|
|[w4](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:316)|REBALANCE|23:40:50.610|库存补仓|YES 0.56×5.0|381|23:40:51.278 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:40:53.053|0；|
|[w5](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:498)|REBALANCE|23:40:55.299|库存补仓|YES 0.54×5.0|229|23:40:55.892 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:40:58.073|0；|
|[w6](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:538)|REBALANCE|23:40:58.471|库存补仓|YES 0.57×5.0|229|23:40:58.751 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:41:01.064|0；|
|[w7](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:587)|REBALANCE|23:41:01.690|库存补仓|YES 0.59×5.0|220|23:41:01.911 PLACEMENT_NOT_ACKNOWLEDGED|REJECTED / SAFE_TO_CONTINUE  / 23:41:03.105|0；|
|[w8](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:625)|REBALANCE|23:41:05.756|库存补仓|YES 0.59×5.0|218|23:41:06.339 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:41:08.107|0；|
|[w9](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:672)|REBALANCE|23:41:08.922|库存补仓|YES 0.58×5.0|344|23:41:09.055 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:41:11.090|0；|
|[w10](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:723)|REBALANCE|未发送|库存补仓|YES 0.57×5.0|—|23:41:12.561 PLACEMENT_NOT_ACKNOWLEDGED|ABORTED / SAFE_TO_CONTINUE NETWORK_RECOVERING / 23:41:12.910|0；|
|[w11](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:762)|REBALANCE|23:41:15.346|库存补仓|YES 0.57×5.0|659|23:41:15.596 WS_BOOK_STALE|UNKNOWN / BLOCKED SYNC_CACHE_NOT_READY / 23:41:26.631|0；|
|[w12](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:894)|REBALANCE|23:41:30.426|库存补仓|YES 0.45×5.0|224|23:41:30.650 PLACEMENT_NOT_ACKNOWLEDGED|REJECTED / SAFE_TO_CONTINUE  / 23:41:40.138|0；|
|[w13](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1002)|REBALANCE|23:41:41.920|库存补仓|YES 0.45×5.0|542|23:41:42.022 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:41:43.939|0；|
|[w14](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1057)|REBALANCE|23:41:45.266|库存补仓|YES 0.59×5.0|311|23:41:45.368 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:41:48.149|0；|
|[w15](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1109)|REBALANCE|23:41:48.987|库存补仓|YES 0.55×5.0|212|23:41:49.223 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:41:51.076|0；|
|[w16](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1152)|REBALANCE|23:41:51.717|库存补仓|YES 0.55×5.0|226|23:41:51.852 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:41:54.156|0；|
|[w17](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1205)|REBALANCE|23:41:55.101|库存补仓|YES 0.42×5.0|213|23:41:55.403 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:41:57.194|0；|
|[w18](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1249)|REBALANCE|23:41:57.750|库存补仓|YES 0.45×5.0|220|23:41:57.978 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:41:58.959|0；|
|[w19](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1347)|REBALANCE|23:42:01.383|库存补仓|YES 0.46×5.0|1224|23:42:01.491 WS_BOOK_STALE|UNKNOWN / BLOCKED SYNC_CACHE_NOT_READY / 23:42:13.254|0；|
|[w20](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1495)|REBALANCE|23:42:18.310|库存补仓|YES 0.64×5.0|211|23:42:18.624 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:42:20.205|0；|
|[w21](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1569)|REBALANCE|23:42:23.783|库存补仓|YES 0.55×5.0|210|23:42:24.233 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:42:26.228|0；|
|[w22](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1638)|REBALANCE|23:42:29.093|库存补仓|YES 0.61×5.0|215|23:42:29.545 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:42:31.230|0；|
|[w23](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1688)|REBALANCE|23:42:31.741|库存补仓|YES 0.61×5.0|333|23:42:31.856 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:42:34.209|0；|
|[w24](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1747)|REBALANCE|23:42:35.405|库存补仓|YES 0.61×5.0|208|23:42:35.750 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:42:38.225|0；|
|[w25](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1811)|REBALANCE|23:42:39.841|库存补仓|YES 0.64×5.0|218|23:42:40.059 PLACEMENT_NOT_ACKNOWLEDGED|REJECTED / SAFE_TO_CONTINUE  / 23:42:41.221|0；|
|[w26](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1860)|REBALANCE|23:42:43.739|库存补仓|YES 0.6×5.0|215|23:42:44.891 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:42:47.238|0；|
|[w27](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1930)|REBALANCE|23:42:48.191|库存补仓|YES 0.74×5.0|1087|23:42:48.749 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:42:51.230|0；|
|[w28](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1991)|REBALANCE|23:42:51.745|库存补仓|YES 0.68×5.0|293|23:42:52.259 WS_BOOK_STALE|ACK / SAFE_TO_CONTINUE  / 23:42:59.251|5.0；本地MATCHED 23:42:53.200;本地MATCHED 23:42:53.200|

相关事件：

- [23:40:21.422](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:20) 等待 OBI_NO_SIGNAL；NONE / -0.2468628639414550923743461962
- [23:40:25.292](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:66) 等待 WS_BOOK_STALE；INVALID / None
- [23:40:25.766](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:70) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.970560385641731944564001033
- [23:40:34.034](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:138) 本地观察 CONFIRMED：NO 5.0 @ 0.42；trade=7c89591c-4509-4f99-ae64-5424b9e3f8a5
- [23:40:50.179](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:305) 等待 WS_BOOK_STALE；INVALID / None
- [23:40:53.390](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:346) 等待 NETWORK_RECOVERING；VALID_NO / -0.795501022494887525562372188
- [23:41:08.515](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:657) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.8586006174439704946621733085
- [23:41:13.303](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:740) 等待 WS_BOOK_STALE；INVALID / None
- [23:41:14.709](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:748) 等待 NETWORK_RECOVERING；NONE / -0.1582761250953470633104500381
- [23:41:43.954](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1034) 等待 WS_BOOK_STALE；INVALID / None
- [23:41:44.414](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1037) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.746692733789507983056370153
- [23:41:59.399](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1282) 等待 NETWORK_RECOVERING；VALID_YES / 0.962158067993124945378273662
- [23:42:17.796](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1483) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_NO / -0.980069155755856870896305218
- [23:42:34.224](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1734) 等待 WS_BOOK_STALE；INVALID / None
- [23:42:47.802](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:1915) 等待 FINAL_QUOTES_OR_DIRECTION_CHANGED；VALID_YES / 0.707040786432733309462582598
- [23:42:59.208](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:2170) 本地观察 CONFIRMED：YES 0.01 @ 0.68；trade=5a6ac764-fbc9-4a35-9813-cb9b63763d58
- [23:42:59.208](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:2171) 本地观察 CONFIRMED：YES 4.99 @ 0.68；trade=4303d1ff-e532-4b23-9501-99b85608d268
- [23:44:16.024](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_fd04bce31a6248cea39a541571f41f37.jsonl:2532) 合并 5 对，状态 CONFIRMED，释放成本 5.50

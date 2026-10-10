# ITER-0030 — Worker Evidence Archive

Orchestrator created lossless gzip copies of all currently delivered Worker raw .log files. Originals remain untouched in worker_evidence/ and /var/tmp; Git archival will include these compressed copies and this manifest. Decompressed bytes equal originals. Failed executions are retained; this archive does not convert any failed, incomplete or missing attempt into PASS. Worker02 discloses missing attempts1/2, attempt07 and exact early command limitations. No source/test/report change by this archive.

| Raw file | Bytes | Raw SHA256 | Gzip file | Gzip SHA256 |
|---|---:|---|---|---|
| attempt-03-cross-mode-failed.log | 134296 | 79fda32dacc567d1ad729b05641991e1119172cb457b4723290cdf7bcf6150de | attempt-03-cross-mode-failed.log.gz | b15be6bb1ff79227335b0952e2e5ba3f2eb65701b88051fbd3c9dcd7eecc5837 |
| attempt-04-cross-mode.log | 126 | e6c8b941b3b71d53fd9883e1d1212456d0877601bc4eb567f49a300f5f943681 | attempt-04-cross-mode.log.gz | e21fccbff393ec68d954d6c60e11ba7db81a9f825fabbb3e36560a4a6ff38381 |
| attempt-05-regressions.log | 146085 | c1b976cca5518804c6418566bb40b1f800628d90900f01fb7de7a4de5958c5bb | attempt-05-regressions.log.gz | 6c30d7b3b2732ece11450062975ece8b5c48b1dd1344e9496c354153feca4937 |
| attempt-06-isolated.log | 24895 | ecbbeb3fcf31ae0cdb21ccb2855900a3da22ed4b6468413697a77c09c0d68bc5 | attempt-06-isolated.log.gz | 443872eb7307077bea15bfc582b092718614aceac4d3563522136918299847a5 |
| attempt-08-runtime-first-current.log | 217 | 6dffef0af5ecec3130927fb2d88c6abc0b94316612a72b092365de8316f2b820 | attempt-08-runtime-first-current.log.gz | d767aae25e48df1e83b2bdbb20b7f305ce98f8dcbd40a76e249f343a740f56ec |
| attempt-09-negative-oracle.log | 21384 | 688c112786a821d48323c83a901c0c90eeac6aeba5b1f99b09cb3351582735bf | attempt-09-negative-oracle.log.gz | b6411425c4f170295b76505b47072b471460fb9260bc6b451872c143b0526e89 |
| attempt-10-negative-oracle.log | 126 | 9cef4e2926f17d1eded449a72a53822b06ad8cc8b8f88060049d4f6c869a18f2 | attempt-10-negative-oracle.log.gz | a57ab9e9ab1e441a12c80461e43e0de199a469ca0c70557d192d014869e0e560 |
| attempt-11-monitor-finalguard.log | 45 | d5fc4556bcd9138a80d56c28419f6c3758f29e88db2ec00a0a06d1b8335d06ea | attempt-11-monitor-finalguard.log.gz | 651713500f37bf0c9caf65c628de9808c65315961914a671d623ca3b27eaae8f |
| attempt-12-monitor-finalguard.log | 223 | 562babc33a143174e2e4b177db5af6ad9eb058ccf6316fdc333219a926da49cb | attempt-12-monitor-finalguard.log.gz | d266741ef4d3ede4d70274165fec77109c48089cac2a0f7503ded398481be972 |
| attempt-13-monitor-finalguard.log | 11872174 | 98eab9522d9286535f454267615cedf5bbf0fa7fad0afa329cf7bd1f71b4655b | attempt-13-monitor-finalguard.log.gz | 3e51e31f45513a607edf5aebf01fab0be5ba89ae174a49049b8bbdf98e304e9c |
| attempt-14-monitor-finalguard.log | 11816905 | e460d965ed98485539622b07af1cb1a64413ea4ede4b580b27f57db570143584 | attempt-14-monitor-finalguard.log.gz | aaca2ce1690374d94aacdd4fb319a98f4baaf3d097ee9495e90ec1183694a86e |
| attempt-15-finalguard.log | 11881138 | f21f5d89decbb2117eeb20ed2276fdf91580d8b6d7b41bccf5132bd322ee81dc | attempt-15-finalguard.log.gz | d12636ec2746a6717cc946635e2a18be5fe097c6c685fce40c72f8c0733f421d |
| attempt-16-finalguard.log | 19188 | 898b0be2904683162dcad9f7465ab52349bc9ba6acb2d5f27c3baed260aaff8e | attempt-16-finalguard.log.gz | 0c3f24ad19510815e6fb5b3958bfb5c6bac13ab6cc8294c31372497e532e035c |
| attempt-17-finalguard.log | 141 | 63b644cc89881d943497978eb6cb1cf122c99860e75b8bb87cf7e9e228d94f11 | attempt-17-finalguard.log.gz | 1f6b4c99346ac05944928d5c29ff60414cd970754e47dd97576a33e80843340d |
| attempt-18-positive-oracle.log | 143315 | eb5ad42ffc956cfb67968de6e9786c18ee36b6da702afff896d6884b22bd7285 | attempt-18-positive-oracle.log.gz | d9ecb95799559005009b824e1af550b76ae8bac2ae422df09ec0b0cf71848617 |
| attempt-19-positive-reprice.log | 142 | dac485a1f55a40154db2c8971644ec269dd248961f75a382f8d647ea900a3e5e | attempt-19-positive-reprice.log.gz | c9142bd2504b3f344392c19e1d0fcd59286e67a8f6dead65347eae6edaee4151 |
| attempt-20-capital-matrix.log | 13681149 | 4225d2751e496bfbecc8caedb2f30409e2f0e39ca85853a232a8c0891d596cf9 | attempt-20-capital-matrix.log.gz | 8c14eb4d1bf34608210cde79900fb9719520c12c7e69bc12ba802a55dfbd4dd2 |
| attempt-21-capital-positive.log | 16585466 | 7b4a44afd70367ede703c556440d4a660257f68841f48f0c09400f3a28f10a60 | attempt-21-capital-positive.log.gz | 1c60638fc521c6633d66473aaf561075fb71eafe501fe6f812dcc3c4f881a381 |
| attempt-22-model-exact.log | 142 | 043bb96d911f25d638e3f2c8f28006ebebfc0b1169c74595048ebadc715c797c | attempt-22-model-exact.log.gz | 4e4c60e62d82774fbe1e1399e46514d5dd08589414304d1d45144c9e84846186 |
| attempt-23-late-proof.log | 141 | ae2045aeb901234a787b075dd9f5ab5fc026945b52f1460c6f3c4ff9423ccdbc | attempt-23-late-proof.log.gz | ca31a9a59af06417f90b8134b7e94e4d2be6029812b64933f9bd7b7586ca70af |
| attempt-24-affected-runtime-first.log | 1019 | d327f0f265e6f07a492746cdcd3456d4af314849ade9d8932ad6ec8c689d4eb1 | attempt-24-affected-runtime-first.log.gz | c7db9fbe55c93904f6c6e63b31888453356f044922bafcfd1fd61f9ad6ea7137 |
| attempt-25-full-suite.log | 2071 | b69725a3666bfa4e0f5cae080edabb16a32cded175ebe1ec58f7f88e3e3a8a01 | attempt-25-full-suite.log.gz | 2631f5114d0003378769cb0401e66ffa1ca7da4f461cee5e16a57f5bf16b1f0d |

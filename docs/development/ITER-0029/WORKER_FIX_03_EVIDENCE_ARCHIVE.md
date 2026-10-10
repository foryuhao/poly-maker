# ITER-0029 — Fix03 large-log archival

Two original raw logs exceed the GitHub file limit. Original scratch and copied local files remain unchanged and preserved; existing *.log ignore rules apply. Only their lossless gzip copies will enter Git, with uncompressed hash equality verified below. This is storage-format archival, not a specialist report correction. All smaller logs and the original failure/success history remain intact.

| Original | Archived copy | Original bytes | SHA256 of original and decompression |
|---|---|---|---|
| affected_rdp_runtime_placement_capital_capturefix_01.log | affected_rdp_runtime_placement_capital_capturefix_01.log.gz | 150939081 | 7ea574ac38344334db08f016af5d12e0efc4ed8990a90299d4d11c93b222eca5 |
| affected_rdp_runtime_placement_capital_s_01.log | affected_rdp_runtime_placement_capital_s_01.log.gz | 151812831 | 69cad057025af070c7428dc153e8b016b54e5f8c7cf1dbc128120f996a056da9 |

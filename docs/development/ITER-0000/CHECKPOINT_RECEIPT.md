# ITER-0000 Final Checkpoint Receipt

Status: READY_FOR_RDP_DEVELOPMENT
Date: 2026-10-07

## Canonical refs verified on origin
FL root: 97ab0f98168f989b1878e4058022044c8372dcd3.
FL reconstructed baseline: 76fe93ffeb8cc1f8ca440587ab901ea402221228, fl-reconstructed-baseline, reconstruct/fl-canonical-20261007.
RDP starting baseline: 6a5ed93c84d66960eaa73b27a0fe34acfa0bf6c9, rdp-phase0-baseline, rdp-phase0-dev.
IM paused snapshot: a03b59fd0b3c42955029193b30893a395e03dfa4, im-paused-20261007, im-paused (unchanged).

Exact git ls-remote output:
```text
a03b59fd0b3c42955029193b30893a395e03dfa4	refs/heads/im-paused
6a5ed93c84d66960eaa73b27a0fe34acfa0bf6c9	refs/heads/rdp-phase0-dev
76fe93ffeb8cc1f8ca440587ab901ea402221228	refs/heads/reconstruct/fl-canonical-20261007
e882a37d49d105d4b61aa4464c546b27590171b8	refs/tags/fl-reconstructed-baseline
76fe93ffeb8cc1f8ca440587ab901ea402221228	refs/tags/fl-reconstructed-baseline^{}
0ebd13afa26a3874252360e610845e1acdb9e1a1	refs/tags/im-paused-20261007
a03b59fd0b3c42955029193b30893a395e03dfa4	refs/tags/im-paused-20261007^{}
e9f10440b1ddfea8c364972f48db58cb4755be95	refs/tags/rdp-phase0-baseline
6a5ed93c84d66960eaa73b27a0fe34acfa0bf6c9	refs/tags/rdp-phase0-baseline^{}
```

## Topology verification
97ab0f9 is ancestor of FL; FL is ancestor of RDP (exit 0).
19c322a and im-paused are NOT ancestors of RDP (exit 1).
RDP-to-FL source/test/config/dependency/AGENTS diff is empty. Entire RDP delta is documentation/test evidence only:
```text
M	docs/CURRENT_STATUS.md
M	docs/development/BASELINE.md
M	docs/development/ITER-0000/01_READINESS_AUDIT.md
A	docs/development/ITER-0000/FL_TO_IM_CLASSIFICATION.md
M	docs/development/ITER-0000/RESULT.md
A	docs/development/ITER-0000/preservation/fl-full-regression.log
A	docs/development/ITER-0000/preservation/fl-regression-excluding-spender.log
A	docs/development/ITER-0000/preservation/rdp-full-regression.log
```

FL changes vs root outside documentation: livecfg/strategy.toml, src/polymaker/config.py, src/polymaker/favorite_live.py, tests/test_favorite_live.py only. Detailed hunk/classification provenance and retained prior blocked audit history reside on RDP branch under docs/development/ITER-0000/.

## Validation and preservation
FL full regression 656 passed/2 skipped; RDP full regression 656 passed/2 skipped. Exact historical spender test passed alone currently; historical earlier bounded hang retained, no waiver/no exclusions in final full gate. All three canonical worktrees clean, no untracked reviewed files. Original HEAD and 18 material fingerprints unchanged. Capture manifest fingerprints/readability match; raw data and research outputs untouched/local.

## Runtime limits
Legacy FL RPC/merge code is retained as historical FL functionality, not authorized RDP runtime authority. Future RDP Architect must restrict runtime to official Polymarket HTTP/WebSocket; this readiness task creates no RDP runtime or trading capability. Shared-core included NONE; identity/readiness/signing/order-read candidates deferred. No live order/cancel or authenticated trading action; no new runtime dependency.

## Next authorized action
Archive historical AGENTS.md, install RDP Phase 0 AGENTS.md and custom Architect/Worker/Reviewer configuration, then begin ITER-0001. These actions were NOT performed here. No main merge/PR/history rewrite/tag movement.

This receipt is kept on the separate pre-rdp-controls-20261007 archival branch so final baseline tags remain unchanged. RDP baseline Git tree itself contains all required BASELINE/audit/RESULT files and test/provenance evidence.

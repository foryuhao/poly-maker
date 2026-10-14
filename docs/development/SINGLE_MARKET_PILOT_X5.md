# Single Market Pilot x5

Release date: 2026-10-14 (Asia/Shanghai).
Branch: `single-market-pilot`. Release tag: `single-market-pilot-x5`.
Predecessor: `single-market-pilot` (`8ba5a25`).

This release archives the current RDP-SM implementation after the five-market
pilot sequence on 2026-10-13, 22:50–23:15 Asia/Shanghai. It includes the subsequent
runtime recovery and dynamic tick fixes present in the workspace at publication.
The entry point remains `uv run polymaker rdp-sm-run`, with one pinned BTC
five-minute market per invocation.

## Included changes

- Use the official balance and allowance with a **20 USDC capital upper limit**.
- Round REBALANCE BUY quantities up to the 0.01-share SDK grid while satisfying
  the actual inventory gap, at least 5 shares, the official market minimum and
  the existing 1 USDC minimum notional. Actual fills retain their full precision.
- From T=240, merge confirmed paired holdings through the existing settlement
  backend. Credit inventory reduction and recovered funds only after verified
  transaction, token-burn and collateral proofs; retain append-only history.
- Apply inclusive 0.01 numeric tolerance to RDP fill economics, order
  reconciliation and indexed inventory comparisons. Identity, ownership,
  lifecycle and dynamic tick consistency remain strictly validated; shared
  processors retain their default tolerance outside RDP.
- Pause and recheck transient read failures, delayed cancellation/confirmation,
  inventory discrepancies and dynamic tick mismatches within the same market.
  Restore fresh admission and quote evidence before continuing. Preserve
  unresolved placement/Merge risk through final shutdown.
- Show inventory differences, Merge outcomes and PAUSED/RESUMED recovery
  diagnostics in human logs.

The trading schedule is unchanged: wait until T=15; NORMAL and REBALANCE until
T=180; only REBALANCE until T=300; stop new BUY at T=300 and complete closing and
reconciliation. Orders remain BUY-only, maker-only and post-only with a
3-second fill window.

Details and evidence are retained in
[20 USDC and Merge](RDP_SM_CAPITAL20_MERGE.md) and
[runtime recovery](RDP_SM_RUNTIME_RECOVERY.md).

## Publication scope

The release contains source, regression tests and development documentation.
Local credentials, account state, trading Journals, settlement logs and generated
research outputs retain their existing exclusion from version control.
Earlier release and development reports remain historical records of their
respective snapshots.

## Verification

The publication checks use the existing offline test runner, with network/DNS
blocked and workspace credentials denied:

- RDP-SM, FillTruth, official position parsing and settlement regression:
  **771 passed**.
- Shared execution, state/tracker, user-stream parsing and Favorite Live
  regression: **153 passed**.
- Total: **924 passed, 0 failed**.
- Ruff on all changed Python files and Git whitespace checks: passed.
- Both runs: **0 network/DNS attempts**; workspace credentials denied.

Reproduce from the repository root:

```bash
.venv/bin/python tests/rdp_sm_m0_offline.py tests/test_rdp_sm*.py tests/test_filltruth.py tests/test_gateway_positions_v2.py tests/test_settlement_sweep.py -q
.venv/bin/python tests/rdp_sm_m0_offline.py tests/test_execution.py tests/test_state.py tests/test_userstream_parse.py tests/test_favorite_live.py -q
```

No real orders or settlement transactions were executed while preparing this
release. These results verify the current offline implementation; historical
trade logs remain separate evidence of earlier live sessions.

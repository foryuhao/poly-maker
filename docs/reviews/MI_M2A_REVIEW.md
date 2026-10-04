# MI M2A Live Wiring / NO-SEND Review

Verdict: PASS — M2A CLOSED after M2A.1 Re-review and final NO-SEND market observation.
Reviewed predecessor baseline: MI M1 checkpoint `5befd9ee6eb27293bb6f6d885fbe458ea44cabe4`.
Reviewed commit / working tree: uncommitted M2A source, tests and documents on `m4-dynamic-ev`, based on `5befd9e`; the reviewed checkpoint is the commit containing this document.
Architecture reference: `docs/architecture/MI_ARCHITECTURE.md`, M2A NO-SEND supplement.
Implementation report reference: `docs/reports/MI_M2A_IMPLEMENTATION_REPORT.md`.

## Scope reviewed

MI read-only account adapter, exact BTC5m discovery and Up/Down identity, official market WS readiness, MI Snapshot mapping, frozen M1 `evaluate()`, NO-SEND decision sink, persisted decision chain, CLI gate, and related tests. The reviewed tree includes untracked `src/polymaker/mi_live.py`, `src/polymaker/mi_readonly_account.py`, `tests/test_mi_live.py`, and the implementation report; ordinary `git diff` alone does not show them. M1 policy and tests have no diff from `5befd9e`.

## Initial Review — NEEDS FIX

### P0
- None. MI M2A has no reachable order-submit or order-cancel path; only official Gamma/CLOB/Data HTTP and official market WS are reachable for market and account state.

### P1
- The runner took decision time before an awaited account read. A read crossing T−15 could give M1 stale elapsed time and produce a desired PLACE after the cutoff.

### P2 / TEST GAP
- The existing end-of-market `NO_VALID_BOOK` terminal state lacked a deterministic runner-level test.

## M2A.1 Re-review — PASS

The runner now obtains one `evaluation_now` after the account snapshot and clean-state check and passes it to both Snapshot construction and event persistence. The same value controls the one-shot final-window branch. A fake-clock runner test advances from elapsed 284.9s before the account read to 285.2s after it and records WAIT with the post-read timestamp. The `NO_VALID_BOOK` runner test advances a market with invalid books to its end and verifies zero valid evaluations, `INCOMPLETE`, `MIReadinessError: NO_VALID_BOOK`, only WAIT/DATA_INVALID events and zero execution/cancel calls. The P1 and TEST GAP are CLOSED. No new timeout or M1 semantic change was introduced.

## Final third NO-SEND market — PASS

The final code observed exactly one additional market, `btc-updown-5m-1791118200`, using `uv run polymaker inventory-live --once --research --no-send`. Artifact: `livecfg/logs/runs/20261004T125000999631Z_mi_1791118200/` (generated; exclude from Git). Official Gamma lookup matched slug, condition, token IDs and Up/Down labels. The run completed with 265 evaluations, 262 valid and 3 invalid books. Independent recomputation of all valid rows found zero probability/target/delta, action, price/quantity, identity, cash-cover or potential-inventory mismatches. Desired PLACE after T−15: zero. Desired BUY YES 59, BUY NO 104, WAIT 102, CANCEL 0. The run reported zero submit/cancel/fill calls; source inspection confirms the runner has no execution gateway or order mutator, and the observed process produced no order ID or ACK. No natural WS disconnect occurred in this market. Details and reconstructable examples are in the implementation report.

## Architecture compliance

PASS for the M2A NO-SEND supplement: official data → validated Snapshot → frozen M1 controller → MI-specific recording sink. The runtime is single-market and cannot send or cancel. Broader historical trial, Merge, settlement and send-enabled design are outside this accepted slice.

## Safety / regression assessment

M1 strategy and tests remain byte-identical to the M1 checkpoint. Existing readiness behavior is delegated to the same predicate in the generic market-data service. MI discovery requests `cache=False` and strict official metadata; other callers retain defaults. No FavoriteFirst strategy state or order role is reused. The last real-market cutoff event was WAIT/DATA_INVALID at elapsed 285.403s because its book was one-sided; the valid-book cutoff crossing case is covered by the deterministic M2A.1 test.

## Tests reviewed / executed

- M1 targeted: 57 passed.
- M2A targeted: 19 passed.
- Related MI/marketdata/engine/BTC5m/config: 68 passed.
- Full pytest: 765 passed, 2 skipped.
- Ruff on M2A touched scope: passed.
- Mypy on MI integration modules: passed.

## Untracked artifact audit

Include untracked M2A source, test and implementation report in the checkpoint. Exclude pre-existing `livecfg/config.toml` and `research_outputs/`, plus generated `livecfg/logs/runs/` and other runtime/research artifacts. No credential values belong in this checkpoint.

## Required fixes

- None for M2A. The initial P1 and test gap were repaired and independently re-reviewed.

## Deferred issues

- M2B needs its own architecture, safety review and explicit authorization before any MI order submission or cancellation. M2A observation does not establish execution or profitability.
- The final market had no natural WS disconnect; existing earlier observations and tests support reconnect behavior. Do not manufacture a disconnect merely for this checkpoint.

## Final verdict

PASS — M2A CLOSED; ready for a complete reviewed Git checkpoint. M2B is NOT STARTED.

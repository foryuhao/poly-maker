# M4A-A1 First Live Startup Blocker Implementation Report

Status: **IMPLEMENTED — AWAITING REVIEW**
Commit / working tree base: `m4a-a1-pass` (`97ab0f9`), initially clean; this report, `livecfg/strategy.toml`, `tests/test_favorite_live.py`, and `docs/CURRENT_STATUS.md` form the unreviewed working tree.
Reviewed predecessor baseline: `m4a-a1-pass` (`97ab0f9`).
Architecture reference: `docs/architecture/M4A_ARCHITECTURE.md` (AE-1 and one-market controlled-live plan).

## Root cause and static evidence

Root cause: **CONFIRMED**. `favorite_live.py` imports `PROFILE` from `btc5m.py`, where it is `btc5m-survival`, and creates the committed BTC5m `MarketEntry` with that name. `Config.load("livecfg")` reads only `livecfg/strategy.toml`, which previously exposed only `live-tiny`. Engine `_resolve_markets()` therefore raised `KeyError("unknown strategy profile: 'btc5m-survival'")` after gateway connection and before trading.

The authoritative BTC5m definition exists in `config/strategy.toml`, originally documented as the M1 BTC5m paper survival baseline. `livecfg/markets.toml` uses `live-tiny` for older political markets; its long lifecycle and volatility horizons differ materially from the BTC5m baseline. The wrapper's use of `btc5m-survival` follows its shared BTC5m selector constant. No separate historical statement proving the author's intent was found. The concrete fault is a **livecfg config omission**, not evidence that the code constant should change to `live-tiny`.

The source profile parameters are: `micro_levels=3`, `flow_ewma_halflife_s=8`, `gamma=0.6`, `delta_min_ticks=2`, `c_vol=1.2`, `c_tox=2.0`, `vol_short_halflife_s=3`, `vol_long_halflife_s=30`, `base_size_usdc=5`, `q_max_usdc=20`, `q_soft_frac=0.5`, `layers=1`, `layer_step_ticks=2`, `reward_size_mult=1.0`, `reprice_ticks=2`, `resize_frac=0.5`, `min_edge_ticks=1`, `event_cooloff_s=5`, `event_jump_ticks=8`, `event_sweep_levels=3`, `event_sweep_mult=4`, `event_sweep_frac=0.8`, `trend_flow_z=2.0`, `trend_vol_ratio=3.0`, `end_date_taper_days=0.00347`, `reduce_only_hours=0.01667`, `halt_before_hours=0.00333`, `exit_urgency_s=20`, `merge_min_size=20`.

The existing `live-tiny` parameters are: `micro_levels=3`, `flow_ewma_halflife_s=90`, `gamma=0.8`, `delta_min_ticks=2`, `c_vol=1.5`, `c_tox=3.0`, `vol_short_halflife_s=10`, `vol_long_halflife_s=600`, `base_size_usdc=5.0`, `q_max_usdc=12.0`, `q_soft_frac=0.5`, `layers=1`, `layer_step_ticks=2`, `reprice_ticks=2`, `resize_frac=0.2`, `min_edge_ticks=1`, `event_cooloff_s=90`, `event_jump_ticks=6`, `event_sweep_levels=2`, `trend_flow_z=1.2`, `end_date_taper_days=7`, `reduce_only_hours=48`, `halt_before_hours=6`, `exit_urgency_s=600`, `merge_min_size=20.0`. Unlisted fields use `StrategyProfile` defaults.

## Scope implemented

Added the existing authoritative `btc5m-survival` definition verbatim to `livecfg/strategy.toml`. Kept `live-tiny`, `btc5m.PROFILE`, FavoriteFirst behavior, A1 config identity, and Research schema unchanged.

## Files added

- This implementation report.

## Files modified

- `livecfg/strategy.toml`: added the BTC5m profile.
- `tests/test_favorite_live.py`: added a no-network startup regression.
- `docs/CURRENT_STATUS.md`: recorded the new review gate and handoff.

## Important implementation decisions

The regression compares the loaded live profile to the authoritative `config/strategy.toml` profile, so a future parameter drift fails. It constructs `MarketEntry(profile=favorite_live.PROFILE)`, then drives the real `Engine.start(arm_favorite=False)` through `_resolve_markets()` using a cached fake market and mocked gateway/Gamma. An intentional exception at the next startup step proves resolution completed. No order path runs.

## Deviations from architecture

- NONE. This is an additive correction to the existing one-market controlled-live configuration; no A1 identity or AE-1 architecture changes.

## Tests added

- `test_livecfg_favorite_profile_survives_engine_market_resolution`.

## Tests executed

- `.venv/bin/pytest tests/test_favorite_live.py -q -k livecfg_favorite_profile_survives_engine_market_resolution`: 1 passed.
- `.venv/bin/pytest tests/test_favorite_live.py tests/test_btc5m.py tests/test_config_aliases.py -q -k 'not test_required_spender_follows_client_order_version_and_market_risk'`: 72 passed, 1 deselected.
- `.venv/bin/ruff check tests/test_favorite_live.py`: passed.
- `git diff --check`: passed.

## Test results

`PROFILE exists = True`; `profile_for = PASS`; startup passes `_resolve_markets()` and reaches metadata refresh. Regression: **PASS as a test result**, pending independent review. The deselected spender test is the previously documented unrelated hang; no full-suite PASS is claimed.

## Known limitations / unresolved issues

No real market or order was run. This test establishes the specific profile lookup repair, not real exchange readiness or M4A acceptance. Actual M4A readiness: **NOT READY**.

## Safety / behavior impact

FavoriteFirst trading semantics changed: **NO**. A1 identity semantics changed: **NO**. The intended BTC5m profile is now loadable at startup; no strategy, guard, quantity, quote, reprice, gateway, Fill Truth, Research auditor, or DP logic changed.

## Git diff summary

Two implementation files modified, this report added, and the short current-status handoff updated. No commit or PASS tag created.

## Handoff to Reviewer

Independently inspect the full tracked and untracked working tree against `m4a-a1-pass`, including the source and live profile equality, fake startup isolation, and unchanged A1 identity/strategy paths. Review this startup fix before retrying exactly one manually supervised FL BTC5m market with Research enabled. Do not infer broader M4A readiness from the startup regression.

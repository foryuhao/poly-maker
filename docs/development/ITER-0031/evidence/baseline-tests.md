# Coding-before baseline receipt

HEAD: 68deb7a1c2d277a94e915fd6a60e97df9804b2d7 (single-market-pilot).
Worktree was clean. Recorded from tool output before production edits; this is a summary, not a reconstructed raw console transcript.

Command:
`.venv/bin/python tests/rdp_sm_m0_offline.py tests/ -q`

Result: **13 failed, 1371 passed, 2 skipped in 108.23s**.
M0 offline guard: **0 network/DNS attempts; workspace credentials denied**.

Existing failed node IDs:

- test_config_aliases.py::test_cli_conflict_stops_before_settlement_preflight
- test_rdp_sm_cli_logging.py::test_l01_l04_l08_l09_l11_normal_rebalance_confirmed_summary
- test_rdp_sm_cli_logging.py::test_l04_pending_never_displayed_as_confirmed[MATCHED]
- test_rdp_sm_cli_logging.py::test_l04_pending_never_displayed_as_confirmed[MINED]
- test_rdp_sm_cli_logging.py::test_l09_secrets_are_redacted_and_raw_responses_not_rendered
- test_rdp_sm_cli_logging.py::test_http_diagnostics_and_json_only_switch[--json-only]
- test_rdp_sm_cli_logging.py::test_http_diagnostics_and_json_only_switch[--http-info]
- test_rdp_sm_entry.py::test_r11_r12_full_cli_single_market_and_exit
- test_rdp_sm_inventory_wait.py::test_persistent_gap_blocks_until_300_closes_orders_and_logs_real_difference
- test_rdp_sm_tick_recovery.py::test_cli_shows_tick_values_and_preserves_json_and_journal[decision]
- test_rdp_sm_tick_recovery.py::test_cli_shows_tick_values_and_preserves_json_and_journal[admission]
- test_risk_shadow.py::test_baseline_01_and_protected_sources_identical
- test_risk_shadow_cli.py::test_execution_strategy_and_entry_control_flow_preserved

First failure: isolated runner correctly denies the old test's attempted workspace .env access.
Ten CLI tests: preexisting reconciliation logging contaminates JSON stdout.
Last two: historical frozen HEAD/source-shape audits already fail on the supplied HEAD.
These were not caused by RDP-SIZE-001, and none was deleted or skipped in the complete final run.

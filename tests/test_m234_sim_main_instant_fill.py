from m234_sim_main_instant_fill import InstantFill

from test_m1234_sim_instant_fill import append, candidate, fixture, resolution, submit


def test_primary_assumed_fills_use_main_submits_and_preserve_baseline_comparison(tmp_path):
    run, path, _, out = fixture(tmp_path)
    append(path, [("ACCOUNT_OPEN", {"profile": s, "scenario": "main/trades-only",
                                    "initial_cash": "20", "at": 1})
                  for s in ("M2", "M3", "M4")])
    append(path, [candidate(), candidate(), submit(), submit("main/trades-only", price=".3"),
                  submit("main/cancel-half", price=".3"),
                  ("MODELLED_FILL", {"scenario": "main/cancel-half", "order": "order", "quantity": "2"}),
                  ("UNKNOWN_TAIL", {"scenario": "main/cancel-half", "order": "order"}), resolution()])
    ledger = InstantFill(run, out)
    try:
        result = ledger.refresh()
        assert result["status"] == "OK"
        assert result["source_scenario"] == "main/trades-only"
        assert result["strategies"]["M4"]["signals"] == 1
        assert result["strategies"]["M4"]["assumed_filled_orders"] == 1
        assert float(result["strategies"]["M4"]["settled_gross_pnl_usdc"]) == 7
        comparisons = result["execution_comparison"]["M4"]
        assert comparisons["main/cancel-half"]["filled_orders"] == 1
        assert comparisons["main/cancel-half"]["unresolved_orders"] == 1
        assert comparisons["main/cancel-half"]["settled_gross_pnl_usdc"] is None
        assert comparisons["trades-only"]["filled_orders"] == 0
    finally:
        ledger.close()

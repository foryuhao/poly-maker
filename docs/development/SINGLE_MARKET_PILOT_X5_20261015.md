# Single Market Pilot x5 — 2026-10-15

Branch: `single-market-pilot`. Tag: `single-market-pilot-x5`.
This publication moves the existing tag to the new commit at the user's explicit
request. Its parent is `a728c4db3d01dab2fddb2b9ac162c9c5bf4bb3a1`.

## Included behavior

- Optional `rdp-sm-run --shadow`: independent read-only Risk Shadow process,
  causal decision comparisons, standard risk metrics and deterministic replay.
- Inventory-state classification uses whole-share half-up rounding before the
  absolute gap is compared with 5. Actual inventory and order sizing retain
  their original precision.
- MATCHED-order remaining-share tolerance is separately configurable through
  `[rdp_sm].matched_remaining_tolerance_shares`, default 0.1 share. Fill evidence,
  price and inventory checks retain their separate tolerances.
- `NORMAL_END_S = 120`: NORMAL/REBALANCE new orders at T15≤T<T120;
  REBALANCE-only new orders at T120≤T<T300. Pre-submit checks and human CLI
  messages use the same cutoff. Existing close/confirmation/Merge handling
  continues under its existing rules.

The historical RDP-RISK-002 reports and evidence retain their original task scope
and results. Shadow's declared baseline commit identifies that historical
development baseline, not the actual commit of every subsequent execution.
Raw Journal, observations, research outputs and credentials remain excluded by
the repository's existing ignore rules.

## Publication validation

```bash
.venv/bin/python tests/rdp_sm_m0_offline.py \
  tests/test_rdp_sm*.py tests/test_risk_shadow*.py -q \
  -k 'not test_baseline_01_and_protected_sources_identical'
```

Result: **728 passed, 1 deselected**, 96.08 seconds. The offline guard reported
**0 network/DNS attempts** and denied access to workspace credentials.
Ruff passed for all changed source and test files. Staged whitespace checks pass
outside `docs/development/RDP-RISK-002/evidence/`. Those historical console
receipts are preserved byte-for-byte, including their original trailing spaces.

The one deselected test asserts HEAD and protected source bytes equal the old
RDP-RISK-002 baseline. It is preserved unchanged as a historical task audit;
the authorized trading cutoff, inventory classification, tolerance and Shadow
integration changes intentionally exceed that original frozen scope. This is
not a full-repository test result and does not claim that audit passed.

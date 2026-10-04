# MI M1 Review

Verdict: **PASS — M1.1 FINAL RE-REVIEW** (initial NEEDS FIX preserved below)
Review date: 2026-10-04 (Asia/Shanghai)
Reviewed predecessor baseline: `19c322a` is the pre-MI archival Git base, not a Review PASS; `m4a-a1-pass` (`97ab0f9`) is the most recent reviewed live baseline.
Reviewed commit / working tree: `m4-dynamic-ev` at `19c322a`, plus the untracked MI M1 source, tests, implementation report and reconstructed architecture, and the tracked `docs/CURRENT_STATUS.md` modification. No MI commit or tag exists. Exact source SHA-256: `adbaa132794166e9e9fe0d9604b22e50831a2e291d109802ced3db78569135fd`; test SHA-256: `1d1555d84b85c681f19a6b83a620031434b49598abe4f3efecee4d8d6b98d2f8`.
Architecture reference: `docs/architecture/MI_ARCHITECTURE.md` (reconstructed draft, not yet accepted; SHA-256 `18ccbd4661b25468801055e069db3080d277ef21e4af2fbde971b1360aef4cba`).
Implementation report reference: `docs/reports/MI_M1_IMPLEMENTATION_REPORT.md` (SHA-256 `6715c8b281747b921513b86c47a14b32062175087060a1bc4812c0b19d7fe080`).

## Scope reviewed

Independently read the M1 source and all 50 parameterized test cases, the implementation report, current status, reconstructed MI architecture, relevant clauses of the attached BTC5m Maker Inventory SOP v0.2, and the Engine/CLI/gateway integration references. Inspected tracked and untracked Git state. Evaluated only the pure M1 policy and caller contract; M2 trial risk, live order handling, Merge, settlement and replay are not M1 deliverables.

## Findings

### P0

- None found. The MI module is not wired to an authenticated or live execution path.

### P1

1. **Pure reduction is misclassified as ordinary target tracking.** `src/polymaker/strategy/maker_inventory.py:276-284` sets `reducing_old_exposure` only when the *target crosses zero* and the full flip fails an M1 size/cash/precision check. This is not the meaning of a pure reduction: an order that only moves an existing nonzero net position toward zero must qualify regardless of the target's sign. Reproduction: with valid p=0.50 books, `qY=20`, `qN=0`, $100 available and 0.01 share step, `evaluate` emits BUY NO 20 and potential net `[0,20]`, yet reports `reducing_old_exposure=False`, `reason=TRACK_TARGET`. At p=0.60 it similarly emits BUY NO 15 ending at +5 with the flag false. This public decision field would incorrectly deny a legal reduction if the M2 stop gate relied on it, and it prevents correct reduction-path attribution. Repair the classification from actual net and candidate fill interval; keep risk permission itself in M2. Add both directional and zero-target regression cases. `tests/test_maker_inventory.py:116-144` tests only constrained flips, not these ordinary pure reductions.
2. **A quantity-step mismatch prematurely discards a feasible near-full flip.** `src/polymaker/strategy/maker_inventory.py:279-287` treats `_floor_step(desired, step) != desired` as a failed full flip and caps `desired` at the old exposure before trying the legal floored size. Reproduction: p=0.10, `qY=20`, `qN=0`, $100 cash, step 0.3: target is -20 and requested BUY NO is 40. The valid downward-quantized 39.9-share order fits cash and the ±25 net bound, ending at -19.9; M1 instead emits only 19.8 shares, ending at +0.2. This changes normal inventory tracking solely because the desired quantity is not an exact step multiple, contrary to the SOP's downward-sizing rule and the architecture's normal policy boundary. `tests/test_maker_inventory.py:161-167` currently asserts this behavior, so the passing suite does not resolve it. Separate quantity quantization from a true full-flip admission failure; let the later M2 risk gate decide whether a direction-increasing candidate is admissible. Add a test for the legal quantized near-full flip and for an actually risk-limited pure reduction.

### P2

1. **The implementation report predates the canonical architecture.** Its architecture reference and “Deviations from architecture: NONE” describe an earlier user M1 specification, while the canonical reconstructed draft now identifies M1-to-v0.2 gaps. Preserve the historical fact that implementation began before the architecture existed, but on repair update the same report to cite this Review and record the actual decisions/deviations against the accepted architecture. Do not rewrite history as if the draft was approved before M1 was written.
2. **A zero-remaining nonterminal order can be kept indefinitely.** `src/polymaker/strategy/maker_inventory.py:170-181,249-266` accepts `remaining_qty=0` with `WORKING` and can return KEEP if the target still points in the same direction. The caller is expected to provide authoritative lifecycle state, so this is not a demonstrated live safety fault, but the boundary should reject an inconsistent nonterminal snapshot or explicitly specify that terminal reconciliation removes it. Add a focused case if the M1 caller contract is tightened.

## Architecture compliance

The source is a pure single-market decision function and imports only the generic `Side` enum. It computes the stated probability, continuous target, confirmed net/pair/residual, 5-share threshold, best-bid BUY intent, potential inventory interval and final-15-second gate. The five modeled nonterminal order lifecycle states all block a second PLACE. Pending cancellation retains potential remaining inventory, and invalid books or time expiry request cancellation when an order is present. Static inspection found no MI import or call in Engine, CLI, gateway, or the strategy package initializer.

The two P1 findings prevent M1 PASS against the reconstructed design. The architecture itself remains a draft; this Review does not accept it on behalf of the Architect or retroactively certify M1. M1 intentionally lacks `W_market`, `W_trial`, trial stop, Merge and settlement accounting. Those are later-slice requirements, not independent M1 failures; `REDUCE_OLD_EXPOSURE` must not be presented as the SOP v0.2 risk rule.

## Safety / regression assessment

No existing live trading semantics changed through MI M1 because the MI module is not wired into live execution. The present defects affect the M1 decision contract and would propagate to shadow/replay or later admission if reused unchanged. The separate dirty `livecfg/config.toml` signature-type change is outside the reviewed M1 scope and was not attributed to MI.

## Tests reviewed / executed

- `uv run pytest tests/test_maker_inventory.py -q`: **50 passed**.
- `uv run ruff check src/polymaker/strategy/maker_inventory.py tests/test_maker_inventory.py`: **passed**.
- `uv run mypy src/polymaker/strategy/maker_inventory.py`: **passed**.
- `git diff --check`: **passed for tracked changes**; this command does not inspect untracked source or tests.
- Ran read-only independent probes for p=0.50 and 0.60 reductions, p=0.40 full flip, and p=0.10 with 0.3 step / restricted cash. Their exact outputs are described in the P1 findings. The prior report's 739-pass full-suite result was historical evidence and was not rerun because these concrete defects already block M1 PASS.

## Untracked artifact audit

`git ls-files --others --exclude-standard` found four MI artifacts in scope: `docs/architecture/MI_ARCHITECTURE.md`, `docs/reports/MI_M1_IMPLEMENTATION_REPORT.md`, `src/polymaker/strategy/maker_inventory.py`, and `tests/test_maker_inventory.py`. All four were inspected; the two code/test artifacts and the architecture/report have hashes above. The 748 local files under `research_outputs/` were inventoried but are generated, out of scope, and not reviewed for MI M1. No other untracked path appeared before writing this Review. `docs/CURRENT_STATUS.md` is tracked and modified. The tracked `livecfg/config.toml` signature-type change is pre-existing and outside this Review.

## Required fixes

1. Repair and test `reducing_old_exposure` so it accurately identifies any legal opposite-side BUY whose entire fill interval only decreases the absolute current net exposure, including targets at zero or on the original side. Keep the separate M2 risk admission gate explicit.
2. Repair and test quantity-step flooring of a feasible near-full flip. Do not cap it at the old exposure merely because the exact target delta is not step-aligned; retain a tested pure-reduction fallback for an actual M1 cash/inventory constraint. Leave trial-risk admission to M2.
3. Update the canonical M1 Implementation Report with the repair results and the post-implementation architecture reference. Re-run focused checks and request independent re-review of the repaired working tree. Do not checkpoint or tag before PASS.

## Deferred issues

- P2 zero-remaining nonterminal snapshot and report-history clarification can be resolved during M1 repair or explicitly carried with a documented caller contract.
- Trial-wide risk, stop, actual cash/fee reservation, Merge/settlement, identity validation and authenticated order-state recovery belong to later authorized slices. Their absence here does not authorize bypassing them in a live adapter.
- Formal acceptance or revision of the reconstructed MI architecture remains an independent decision. If acceptance changes the M1 contract materially, re-review against the revised canonical document.

## Initial Review verdict (historical)

**NEEDS FIX — two P1 findings.** M1 is isolated from live execution and its 50 existing tests pass, but its reduction classification and step-limited flip sizing do not satisfy the current policy contract. No Review PASS, MI checkpoint, tag, push, live trial, or M2 implementation follows from this Review. Next action: Worker repairs the bounded M1 issues and report, then a separate re-review inspects the exact repaired tree.

## M1.1 Final Re-review — 2026-10-04

**Verdict: PASS. M1 CLOSED — READY FOR M2. M1.1 RE-REVIEW PASS.** This verdict applies to the repaired, uncommitted M1 working tree on branch `m4-dynamic-ev` at predecessor commit `19c322a`. The original NEEDS FIX above remains the historical verdict for the earlier source and test hashes; it has not been erased.

Reviewed source SHA-256: `19e732e4bb515ce0bbbe11fc676f4c2f73b33db8e0b2eccd5880461e8987f8f1`.
Reviewed test SHA-256: `1b057b52cbadb7a165dd8dab94c76e12bd10a02b2d0d7acdd2375a30d64f0608`.
Architecture reference: `docs/architecture/MI_ARCHITECTURE.md` (reconstructed draft; SHA-256 `18ccbd4661b25468801055e069db3080d277ef21e4af2fbde971b1360aef4cba`).
Implementation report reference: `docs/reports/MI_M1_IMPLEMENTATION_REPORT.md` (M1.1 repair section; SHA-256 `961129abcd24a2fe1d4e2b4bb99b913bf15d58d520498122574011ce1199eb64`).

### Previous findings disposition

| Finding | Final status | Evidence |
|---|---|---|
| P1 pure-reduction classification | CLOSED | `reducing_old_exposure` uses confirmed net inventory, candidate BUY direction and full-fill endpoint. Reductions to zero or to a smaller same-side net position are true in both directions; full flips and flat-start BUY are false. |
| P1 quantity-step flip sizing | CLOSED | Full desired quantity is floored before cash and inventory admission. With `N_actual=+20`, `N_target=-20`, step `0.3` and sufficient cash, output is BUY `39.9` NO with reduction flag false. Cash-constrained fallback remains capped at old exposure. |
| Test gap: subminimum working remainder | CLOSED | An explicit partial-fill test with remaining `4`, new-order minimum `5`, unchanged best bid and valid constraints returns KEEP_EXISTING_ORDER. |

### Adversarial cases and regressions

A–K all PASS on independent read-only inputs: `+20→0` BUY 20 NO/true; `+20→+5` BUY 15 NO/true; `−20→0` BUY 20 YES/true; `−20→−5` BUY 15 YES/true; sufficient-cash `+20→−20` BUY 40 NO/false; flat→`+10` BUY 10 YES/false; step-0.3 full flips BUY 39.9 NO and BUY 39.9 YES/false; cash for only 25 NO yields BUY 20 NO/true; step-0.3 cash-constrained fallback yields BUY 19.8 NO/true; old exposure 3 below the minimum yields WAIT. A separate subminimum remainder probe returned KEEP. Existing probability, threshold, potential-inventory, order-lifecycle, direction-change, data-validity, T−15 and BUY-only regressions remained passing. No new material finding was identified.

### Tests and scope

- `uv run pytest tests/test_maker_inventory.py -q`: **57 passed**.
- `uv run pytest tests/test_maker_inventory.py tests/test_favorite_first.py tests/test_quoting.py tests/test_config_aliases.py -q`: **131 passed**.
- `uv run pytest -q`: **746 passed, 2 skipped**.
- Ruff check and format check: **passed**. Mypy on MI source: **passed**.
- MI has no live, network, Merge, Settlement, Trial/W_trial, Gamma/Delta, SELL or FavoriteFirst state dependency. No M2 implementation was reviewed or authorized by this verdict.

### Reviewed artifact and checkpoint handoff

The reviewed M1 scope consists of `src/polymaker/strategy/maker_inventory.py`, `tests/test_maker_inventory.py`, `docs/reports/MI_M1_IMPLEMENTATION_REPORT.md`, this canonical Review, the draft MI architecture for context, and the MI entry in `docs/CURRENT_STATUS.md`. All were present in the working tree; source, test, implementation report and architecture hashes are recorded above. The separate `livecfg/config.toml` modification and generated `research_outputs/` are outside scope. No source/test issue remains for M1. Before declaring a Git checkpoint, stage and inspect every reviewed artifact, exclude those dirty/runtime paths, then commit and verify the pushed tree. The architecture draft remains subject to a separate M2 acceptance decision; M1 PASS does not grant live trading authority.

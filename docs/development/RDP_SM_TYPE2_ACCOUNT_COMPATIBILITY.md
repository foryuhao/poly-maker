# RDP-SM — Type 2 Account Compatibility

## Result

**TYPE2_BLOCKED**

The configured signer/funder mapping, existing official SDK Type2 support and current official account reads were verified. Current Safe control authority was **NOT_VERIFIED**. The user's explicit condition requires NOT_VERIFIED and no real submission when signer/funder control cannot be proved. Therefore the SM execution guard was retained; no production compatibility change or M3 development was started. MicroLive remains unauthorized.

## Baseline and scope

- Date: 2026-10-12, Asia/Shanghai.
- Branch: `fl-restored-97ab0f9`.
- HEAD: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`.
- SOP v1.8 FROZEN; MicroLive First; M2 Architect ACCEPTED per user instruction.
- Existing CLI diff and untracked SM sources/tests/reports preserved. No historical RDP branches inspected, reset, cleanup or SOP modification.
- Scope: current selected `config`, actual FL SDK/account semantics, minimal Type2 feasibility, existing official HTTP reads and focused offline regressions. No shared signing/Gateway change, RPC, new wallet system, new external interface, independent review, real order/cancel or funds operation.

## 1. Actual account and FL reuse

Historical account evidence redacted for publication. Original private working report is preserved locally. Refer to Readiness v2 for the current verified policy and readiness decision.

## 2. Current official read-only verification

Historical account evidence redacted for publication. Original private working report is preserved locally. Refer to Readiness v2 for the current verified policy and readiness decision.

## 3. Why production adaptation remains blocked

The existing repository authority verifier (`settlement_sweep.py:270-277`) reads deployed code, `getOwners()` and `getThreshold()` through blockchain RPC. Its policy requires threshold1 with the locally derived signer among owners. Only its source was inspected; no RPC/settlement initialization or call occurred.

No inspected existing approved HTTP account read supplies equivalent current authority information. Adding an endpoint, invoking RPC, treating historical fills as current authority, or simply allowing arbitrary Type2 funders would exceed this task's safety condition.

The remaining prerequisite is **trusted current evidence that the configured Safe's authorization policy permits this locally derived signer to sign alone** (matching funder, current owner and threshold information). No real submission is an acceptable verification method.

Once that condition is established, the identified minimal code scope is the two SM-side identity checks at startup/per-submit: retain all configured-key/SDK signer/funder/type consistency checks, retain Type0 signer=funder, admit verified Type2 signer/funder separation, and reject unsupported modes or mismatches. Reuse the existing SDK signing and Gateway funder account reads. No shared Gateway signing change or general Proxy framework is indicated.

This report does not add a boolean authority bypass or implement an unverified Type2 execution branch.

## 4. Offline tests and regression

Created `tests/test_rdp_sm_type2_account.py`, 4 cases using real SM/FL readers/SDK and existing Fake HTTP fixtures with a public deterministic dummy key:

- Actual Type2 SDK initializes with key-derived signer, SDK-derived distinct Safe funder and mode2. Balance request sends signature_type2; Data API positions target that funder. M1 read-only preflight remains usable and caps the reported budget at $30. No order/cancel occurs.
- Correct deterministic Safe but unverified authority remains execution-blocked before a journal/session or order is created.
- Unrelated configured Type2 funder also remains execution-blocked, with no POST/cancel.
- Type0 original path still executes two actual fake-boundary Maker placements through production Gateway and closes safely with zero fill/cost.

These tests prove existing read capability and the retained authority boundary, **not completed Type2 executor compatibility**. Initial test authoring expected RuntimeError instead of the actual `_Issue(ValueError)`; corrected the new assertion to check `_Issue.status == BLOCKED`. Production logic was not changed to make tests pass.

Exact commands:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_type2_account.py -q --tb=short

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_type2_account.py tests/test_rdp_sm_m2_h01_pagination.py tests/test_rdp_sm_m2_h01_commitment.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_execution.py -q --tb=short

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight' --tb=short

.venv/bin/ruff check --no-cache tests/test_rdp_sm_type2_account.py
git diff --check
git diff --quiet -- src/polymaker/execution src/polymaker/state src/polymaker/journal.py src/polymaker/config.py src/polymaker/engine.py src/polymaker/favorite_live.py
```

Final results:

```text
Focused: 4 passed in 0.65s
Account/M2: 96 passed in 15.61s
M1/FL: 346 passed, 1 deselected in 6.56s
All offline runs: 0 network/DNS attempts; workspace credentials denied
Ruff/shared-core/whitespace checks: PASS
```

Distinct total **442 executed / 442 passed / 0 failed / 0 skipped; 1 deselected**. Focused repeat is not added again. The prior credential-protection exclusion remains unchanged and justified; no real credentials were enabled in tests.

M2-H01 cross-snapshot/pagination capital protection and M2-H02 deadline safety: **PASS**. M0-H01/H02/H03 remain **CONTAINED**. $30/UNKNOWN/partial-fill/cancel regressions pass. Selected FL behavior unchanged. No independent Reviewer was requested or run for this task.

## 5. Change manifest and next action

Created:

- `tests/test_rdp_sm_type2_account.py`.
- `docs/development/RDP_SM_TYPE2_ACCOUNT_COMPATIBILITY.md`.

Production changes: **NONE**. Shared signing/Gateway/Tracker/FillTruth/configuration/SOP and all prior reports/tests preserved. No Type2 execution adaptation or M3 code developed.

Remaining account blocker: **current Safe signing authority NOT_VERIFIED; current SM Type2 execution remains blocked**. Successful authenticated reads, matching deterministic mapping and historical activity are recorded without treating them as current authority proof.

Next minimal action: obtain trusted current Safe authorization evidence for this signer/funder pair. Then complete the identified SM-only compatibility check and its actual Type2 execution tests. Until then: **TYPE2_BLOCKED; no real order; no M3 start; no MicroLive authorization**.

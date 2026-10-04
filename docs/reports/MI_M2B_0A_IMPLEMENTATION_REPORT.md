# MI M2B-0A Generic Live Authorization Gate Implementation Report

Status: IMPLEMENTED — AWAITING REVIEW; no MI execution authority
Commit / working tree base: `f25a5a539f711f2dcfe492105c7d92bbfbe812f7` on `m4-dynamic-ev`, plus the pre-existing uncommitted M2B blocker/status documents and local `livecfg/config.toml` / `research_outputs/`.
Reviewed predecessor baseline: `mi-m2a-reviewed-baseline` (`f25a5a539f711f2dcfe492105c7d92bbfbe812f7`).
Architecture reference: `docs/architecture/MI_ARCHITECTURE.md`, M2B-0A supplement; explicit user M2B-0A scope.

## Scope implemented

- Added a pure, strategy-independent `LiveAuthorizationRequest`, `LiveAuthorizationResult`, `validate_live_identity()` and `authorize_live_write()`. Denial is the default when explicit mode, arm intent, live environment, credentials, signer, funder, authentication or signature mode is missing or inconsistent.
- FavoriteFirst `_wallet_identity()` now delegates its existing signer/funder checks to the generic identity check. Its final live arm path invokes the generic authorization check after its own final preflight and before `Engine.arm_favorite_first()`.
- The existing FavoriteFirst $12 balance/allowance threshold, market readiness, flat-position, order, user-stream and Fill Truth checks remain in FavoriteFirst. The generic module has no strategy imports, dollar checks, network calls, execution adapter or mutable global state.

## Files added

- `src/polymaker/live_authorization.py`
- `tests/test_live_authorization.py`
- This implementation report.

## Files modified

- `src/polymaker/favorite_live.py`: delegate wallet identity and check explicit generic authorization at the existing final arm boundary.
- `tests/test_favorite_live.py`: use a valid synthetic signer in runner fakes; add final authorization denial regressions.
- `docs/architecture/MI_ARCHITECTURE.md`: bounded M2B-0A architecture supplement.
- `docs/CURRENT_STATUS.md`: handoff status.

## Important implementation decisions

- The request carries credential data only in memory; its private key field is excluded from dataclass `repr`. Neither the gate nor its result logs credential values.
- `live_environment` is accepted only when the connected gateway explicitly reports non-paper mode; absence of that evidence denies authorization.
- The generic gate validates write authority, not cash sufficiency or strategy readiness. FavoriteFirst retains the $12 rule. No MI dollar threshold is active; a possible $25 minimum is pending M2B-0B cash semantics.
- The FavoriteFirst runner still starts Engine disarmed and arms only after both preflights and committed-market checks. The new authorization call is immediately before the existing arm call. Its final check duplicates identity validation from the preflight and cannot authorize a path previously denied by the preflight.

## Deviations from architecture

- NONE for this bounded supplement. The older reconstructed MI architecture remains a draft for wider execution work.

## Tests added

- Generic allow and fail-closed cases: missing mode, arm, live environment, credentials, authentication, configured account, connected signer/funder, signature mode, and a valid proxy signer/funder split.
- FavoriteFirst final-arm denial when connected credentials, signer, funder or live environment fail after a mocked preflight. Existing $12 and preflight-only tests remain.

## Tests executed

- `uv run pytest tests/test_live_authorization.py tests/test_favorite_live.py -q`: 66 passed.
- `uv run pytest tests/test_maker_inventory.py -q`: 57 passed.
- `uv run pytest tests/test_mi_live.py -q`: 19 passed.
- `uv run pytest tests/test_engine.py tests/test_livetest.py tests/test_config_aliases.py -q`: 35 passed.
- `uv run pytest -q`: 781 passed, 2 skipped.
- `uv run ruff check src/polymaker/live_authorization.py src/polymaker/favorite_live.py tests/test_live_authorization.py tests/test_favorite_live.py`: passed.
- `uv run mypy src/polymaker/live_authorization.py --follow-imports=silent`: passed. The same command including `favorite_live.py` reports its pre-existing `collateral_readiness()` optional-value `min()` type error at line 200; no new authorization code error was reported.

## Test results

All executed tests and touched-file Ruff passed. New generic module Mypy passed. FavoriteFirst's pre-existing Mypy type error remains; its cash code and behavior were not changed.

## Known limitations / unresolved issues

- M2B-0B cash/reservation contract is still unresolved. No strategy capital gate or MI execution path is enabled.
- The generic gate is a pure authorization decision; a future MI execution adapter must enforce it at its write boundary in addition to MI readiness and final send guards.
- FavoriteFirst's pre-existing Mypy debt in `collateral_readiness()` remains.

## Safety / behavior impact

No live trading semantics or FavoriteFirst strategy policy changed. The new final gate can only deny an arm that has lost identity or authentication after preflight. No real market, order, cancel, or authenticated trading command was run. M1 and M2A NO-SEND source/tests are unchanged from the checkpoint.

## Git diff summary

One pure module and its tests; a narrow FavoriteFirst authorization call and fake-fixture tests; architecture/report/status documentation. Pre-existing local config and research files were preserved. No commit, tag, or push.

## Handoff to Reviewer

Independently compare FavoriteFirst preflight/arm behavior with the M2A checkpoint; verify the gate has no strategy imports or dollar thresholds and that explicit arm/mode, identity and authentication fail closed. Review the exact working tree including untracked gate/tests/reports. M2B-0B cash semantics remains a separate blocker; no MI live execution should begin from this implementation alone.

## Review blocker repair — 2026-10-04

Initial Architect Review returned NEEDS FIX: direct non-paper FavoriteFirst
`Engine.start(arm_favorite=True)` / `run_forever()` could arm without the generic
gate, `Engine.arm_favorite_first()` accepted no authorization evidence, and the
final FavoriteFirst submit guard checked only the armed flag. This section
preserves that finding; the repair awaits independent Re-review.

`Engine.start()` now rejects non-paper FavoriteFirst auto-arm before connecting
or starting tasks. The controlled wrapper still starts disarmed and passes its
generic authorization result and final FavoriteFirst preflight to the sole
`Engine.arm_favorite_first()` boundary. Engine validates both, then independently
rechecks the current generic identity evidence and retains the authorized
result. Disarm clears it. The final FavoriteFirst submit guard requires that
stored authorization and rechecks current identity/credentials before the
gateway POST. Directly setting `_favorite_armed=True` without authorization
does not permit a POST. Paper mode and non-FavoriteFirst Engine paths are not
given new authorization requirements.

Repair files modified: `src/polymaker/engine.py`, `src/polymaker/favorite_live.py`,
`src/polymaker/live_authorization.py`, `tests/test_favorite_live.py`, this
report, `docs/architecture/MI_ARCHITECTURE.md`, and `docs/CURRENT_STATUS.md`.
No MI strategy, M1/M2A source, cash formula, credential, or live config changed.

Repair tests cover non-paper `start()` and `run_forever()` denial before any
gateway connection; direct arm without/with denied authorization; authorized
gate plus denied FavoriteFirst $12 readiness; authorized gate plus accepted
readiness; forged armed state; and credential loss after arm with zero POST
calls. Synthetic credentials and a fake order client were used throughout.

Repair verification: FavoriteFirst 57 passed; Gate + FavoriteFirst + Engine 82
passed before the final additional credential-loss assertion; M1 57 passed;
M2A 19 passed; related live/config 22 passed. After the final assertion,
FavoriteFirst targeted 57 passed and full suite 784 passed, 2 skipped.
Ruff passed. Mypy reported only the pre-existing FavoriteFirst
`collateral_readiness()` error and four pre-existing Engine errors; no error
in the new/modified authorization code. No real market, order, cancel, commit,
or push occurred. Status remains IMPLEMENTED — AWAITING INDEPENDENT RE-REVIEW.

## Authorization identity-binding repair — 2026-10-04

The independent Re-review found one remaining blocker: the stored result had
only an authorized boolean, so a coherent change of configured and connected
execution identity from A to B could pass a fresh Gate(B) check while retaining
the arm capability issued for A. This repair does not reopen the earlier
start, direct-arm, or forged-armed fixes.

The generic result now includes an immutable `LiveExecutionIdentity` of the
validated signer, funder, and signature mode. These are the same canonical
values already derived and compared by the Gate; no secret or client object is
stored in the result. Denied results have no identity. Engine compares the
caller's authorization identity with its freshly validated identity at arm,
stores that validated result, and requires every submit-time result to be both
authorized and equal to the stored arm identity. A new account must complete a
new authorization, FavoriteFirst readiness, and arm cycle.
The Engine also rejects a mismatched FavoriteFirst placement before calling
`gateway.place()`. The existing gateway submit guard remains the final check
before the official POST if identity changes after that precheck.

Tests added: authorized result carries its validated identity; denied results
carry none; a valid Gate(B) result cannot arm using the old A authorization;
stable A permits the existing fake-client POST path; config-only, gateway-only,
and coherent config-plus-gateway A→B changes each cause zero fake POST calls.
The coherent case explicitly verifies that Gate(B) would otherwise authorize.
An Engine recompute regression also confirms stable A reaches fake
`gateway.place()` and coherent A→B does not call `gateway.place()` at all.

Verification: Gate and FavoriteFirst 75 passed; Gate/FavoriteFirst/Engine 88
passed; M1/M2A 76 passed; related strategy/gateway 83 passed; full suite 790
passed, 2 skipped. Ruff touched scope passed. Mypy passed for the new generic
module; its combined Engine/FavoriteFirst invocation still reports the same
four pre-existing Engine errors and one pre-existing FavoriteFirst collateral
type error, with no new error in the changed authorization code. No real
market, order, cancel, commit, or push occurred. M1/M2A core files remain
unchanged. M2B-0B cash/reservation remains unresolved. Status:
IMPLEMENTED — AWAITING INDEPENDENT RE-REVIEW.

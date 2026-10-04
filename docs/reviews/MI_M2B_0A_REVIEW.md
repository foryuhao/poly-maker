# MI M2B-0A Generic Live Authorization Gate Review

Verdict: PASS — final independent Re-review on 2026-10-04
Reviewed predecessor baseline: `mi-m2a-reviewed-baseline` / `f25a5a539f711f2dcfe492105c7d92bbfbe812f7`
Reviewed commit / working tree: `f25a5a5` plus the uncommitted M2B-0A source, tests, and documents identified below; no checkpoint has been created.
Architecture reference: `docs/architecture/MI_ARCHITECTURE.md`, M2B-0A supplement.
Implementation report reference: `docs/reports/MI_M2B_0A_IMPLEMENTATION_REPORT.md`.

## Review history

- Initial Review: NEEDS FIX. Non-paper FavoriteFirst `Engine.start()`, `run_forever()`, direct arm, and submit paths could bypass the Generic Gate.
- First Re-review: NEEDS FIX. The Engine bypass was repaired, but an authorization result contained only a boolean and reason. A coherent config and gateway identity change from A to B could reuse arm(A) when Gate(B) passed.
- Final Re-review: PASS for the exact working tree below. The arm authorization is now bound to a validated execution identity, and a different identity cannot inherit it.

## Scope reviewed

`src/polymaker/live_authorization.py`, `src/polymaker/engine.py`, `src/polymaker/favorite_live.py`, `src/polymaker/execution/gateway.py`, `tests/test_live_authorization.py`, `tests/test_favorite_live.py`, and the relevant baseline diff. The two new Gate files are untracked and were read directly; ordinary `git diff` does not show them.

Reviewed source/test fingerprints (SHA-256):

```text
424ae88747127052191f409ac0aeac3a61a058b0da2b832e2d79b7bd8bb552bb  src/polymaker/live_authorization.py
b92db3511c078df91751afa2d80207fb7d69736669561245e59b5fcb6b23febe  src/polymaker/engine.py
72340efe2b31b6c08d7551b8713ae65bfb6679b79cbad39fa5e4da86d8488f71  src/polymaker/favorite_live.py
109d3cc0bbd80e45e66cee1c0612daf7503b9e9cf3171422f7117412f8075eae  tests/test_live_authorization.py
47b60f50cd12cb2ab618e7b773fddb2f5e27db7080b075b4e1f600787d98b851  tests/test_favorite_live.py
```

## Findings

### P0

- None in the reviewed identity binding and FavoriteFirst live-write boundary.

### P1

- None in the reviewed scope.

### P2

- None requiring a fix before the M2B-0A checkpoint.

## Architecture compliance

`LiveExecutionIdentity` is a frozen signer, funder, and signature-mode snapshot. The Gate derives a canonical signer from the configured private key, canonicalizes the configured funder, and compares the connected signer/funder before issuing that snapshot. Denied results carry no identity. Engine rejects authorized results lacking identity, compares the result identity to a fresh Gate result at arm, and stores that fresh result. Before FavoriteFirst placement and again through the gateway submit guard, it requires the fresh Gate identity to equal the stored arm identity.

The reviewed offline matrix passed: A→A permits the fake-client placement path; config-only A→B and gateway-only A→B deny; coherent config-plus-gateway A→B denies despite Gate(B) passing. The coherent case did not call Engine `gateway.place()`; all changed-identity cases had zero fake `post_orders()` calls. No automatic A→B re-arm path was found.

Non-paper FavoriteFirst `start(arm_favorite=True)` rejects before gateway connection; `run_forever()` uses that `start()` path; direct arm requires authorization and FavoriteFirst readiness; a forged armed flag without a stored authorization cannot pass the submit guard. The $12 collateral/allowance rule remains in FavoriteFirst preflight. The generic Gate has no strategy import, dollar rule, execution method, or network request.

## Safety / regression assessment

M1 strategy/tests and M2A core/tests have no diff from the reviewed predecessor. No MI execution or cash-reservation contract was added. This Review ran only offline tests with fake/mock clients. No real market, order, cancel, or authenticated trade write was run.

## Tests reviewed / executed

- Gate + FavoriteFirst + Engine: 88 passed.
- M1 + M2A: 76 passed.
- Related strategy/gateway: 83 passed.
- Full suite: 790 passed, 2 skipped.
- Ruff on touched source/tests: passed.
- `git diff --check`: passed.
- Mypy on Gate/Engine/FavoriteFirst: five errors in pre-existing Engine book/type and FavoriteFirst collateral code, none in the new authorization logic. This same set was reported before the identity-binding repair.

## Untracked artifact audit

The reviewed `src/polymaker/live_authorization.py`, `tests/test_live_authorization.py`, and M2B implementation reports are untracked and must be included in a later complete checkpoint. Local `research_outputs/` and the pre-existing `livecfg/config.toml` modification are outside this Review and must not be included merely to clean the tree.

## Required fixes

- None for M2B-0A. Independent checkpoint verification must include all reviewed source, tests, and documents; this Review did not commit or tag them.

## Deferred issues

- M2B-0B official spendable cash and account-wide order-reservation contract remains unresolved. M2B execution and real trading remain unauthorized.

## Final verdict

M2B-0A CLOSED — final independent Re-review PASS for the exact working tree fingerprint above. Next step is a separate Git checkpoint of that reviewed tree before beginning M2B-0B.

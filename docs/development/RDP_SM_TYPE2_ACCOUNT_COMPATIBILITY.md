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

| Item | Verified evidence / source | Result |
|---|---|---|
| Configuration | User-selected `config`; `Config.load('config')`, `config/config.toml` | Signature Type **2** |
| Signer | Canonical private-key configuration; local `eth_account.Account.from_key` only | `0x72EE...49d1` |
| Funder | Canonical wallet-address configuration passed to SDK `funder` | `0x2d73...9D5D`; different from signer |
| FL SDK initialization | `execution/gateway.py:134-153`: key, configured signature type and funder passed separately; signer from SDK; funder used for funds/positions | Existing Type2 capability confirmed |
| Local expected Safe | Existing installed official relayer SDK `builder.derive.derive` and chain contract config, no client/network initialized | Expected Safe **exactly matches** configured funder; not a current ownership-state proof |
| Balance / allowance | SDK `get_balance_allowance` at `client.py:687` sends authenticated GET with configured signature_type=2 | Current read succeeds; returned balance and allowance map present |
| Positions | Production `Gateway.positions:653` queries official Data API with `user=gateway.funder` | Current read succeeds; explicit funder target |
| Open orders | Production `Gateway.open_orders:595` uses actual SDK L2 authenticated account read | Current read succeeds; no explicit funder parameter in this interface |
| SM limit | `rdp_sm_execution.py:425-427` startup and `:549-553` before submission require mode0/signer=funder | Type2 remains execution-blocked |

No private key, API secret/passphrase or full account identity was printed, recorded or added to tests. The local expected-Safe calculation reused an already installed project dependency (`py-builder-relayer-client`), without creating a wallet/client or issuing a relayer request.

Historical current-workspace FL evidence was read locally with sockets/DNS denied; only aggregates were emitted:

- 8 non-paper Gateway connection records with signer/funder prefixes matching the local identities.
- 118 historical positions requests include the exact configured funder.
- 11 HTTP200 balance/allowance records specify signature_type=2.
- 8 HTTP200 order POST records, 6 `fill` and 6 `owned_maker_fill_applied` records.

This corroborates historical FL Type2 activity consistent with the configured relationship. Truncated connection identities and historical trading do not establish the Safe's current owners/threshold or authorize a new live request.

## 2. Current official read-only verification

Used the existing SM `_Reads` composition and production Gateway account readers with `Config.load('config')`. Did **not** call `Gateway.connect` (which can create API credentials); the existing read-only adapter only derives an existing API key using official GET, then initializes the same SDK signer/type/funder.

The temporary diagnostic enforced GET-only, exact existing official hosts/paths, no redirects and bounded HTTP timeouts. Raw SDK output/logging was captured rather than emitted; only sanitized results were printed. No diagnostic code or credentials were saved to project files.

Actual requests/results:

| Existing official path | Result |
|---|---|
| CLOB `/auth/derive-api-key` | PASS; SDK signer and configured Type2/funder agree with local configuration |
| CLOB `/balance-allowance` | PASS; official balance **36.064169 USDC**, 4 allowance entries |
| CLOB `/data/orders` | PASS; 0 open orders |
| Data API `/v2/positions?user=<configured funder>` | PASS; 0 positive positions |

**HTTP writes: 0. Real orders/cancels: 0. RPC/third-party/new-interface calls: 0.** Official authenticated read requests were authorized by this task; offline tests below remain completely network-free. The allowance map read succeeded, but no quote-specific live capital eligibility or full live SM session is claimed.

These responses establish signer authentication and the configured signature-mode reading capability. They do not return the Safe's current owner set/threshold or independently attest current signer-to-Safe control. Public funder positions alone are not authority evidence. The expected-Safe address proves the deterministic mapping, not that ownership was never changed after deployment.

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

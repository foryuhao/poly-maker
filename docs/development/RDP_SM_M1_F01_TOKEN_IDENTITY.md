# RDP-SM M1-F01 — Token Identity Safety Fix

## 1. Original Reviewer Finding

Date: 2026-10-11, Asia/Shanghai. Branch: `fl-restored-97ab0f9`. HEAD: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`, unchanged from original FL baseline. RDP-SM SOP v1.8 remains frozen; architecture remains FL-first / Thin SM Orchestrator.

M1 Independent Reviewer concluded FAIL for the sole current blocker **M1-H01 — Token Identity Validation Insufficient**. Three Fake HTTP counterexamples executed real production readers and returned false SESSION_READY:

| Original counterexample | Incorrect observed result |
|---|---|
| D1: canonical YES `"1"`, positive position under `"1 "` | YES inventory reported zero |
| D2: YES `"1"`, NO `"01"` | Two strings representing one underlying uint256 token accepted as distinct legs |
| D3: market YES `"01"`, canonical position/order under `"1"` | Positive inventory and existing order missed; target inventory/orders reported zero |

Inputs: `RDP_SM_M1_SAFE_SESSION.md`, `RDP_SM_M1_INDEPENDENT_REVIEW.md`, actual M1 adapter and tests. Original reports, including the Reviewer FAIL decision, are preserved. This is a Worker fix report, not independent acceptance. No M0 re-audit or rework performed.

## 2. Root Cause

M1 previously accepted any decimal-looking positive uint256 string and checked leg uniqueness by string equality. `"01"` and `"1"` passed as distinct. Account cleanliness then used exact string keys/membership.

The real FL Gateway retains positive-position token keys as supplied, including whitespace; its order parser strips asset ID whitespace. Consequently validating only market IDs could not prove account cleanliness, and validating only post-parsed orders would miss raw malformed spellings.

Existing FL `favorite_live.validate_meta` checks nonempty/distinct strings, not canonical uint256 identity. No suitable existing canonical Token ID validator was found in the inspected execution/market path. Floating-point numeric parsers are unsuitable for uint256 identity. The fix therefore adds one small SM-local exact-string/integer validator, without changing shared code or introducing a Token framework.

## 3. Minimal Code Change

Only `src/polymaker/rdp_sm.py` changes existing implementation:

- `_token_id` at :84 requires a string matching ASCII `[1-9][0-9]{0,77}` with integer value less than `2**256`. Zero, leading zeros, whitespace, Unicode digits, signs, non-string inputs and overflow fail with TOKEN_IDENTITY_INVALID / SESSION_BLOCKED. The maximum positive uint256 remains valid.
- Market mapping at :280 validates both actual parsed token IDs, then requires them to differ. Because accepted representation is unique, string distinctness now implies numeric distinctness.
- Book identity at :295 validates the raw asset ID before comparing with its canonical market token.
- SDK open-order response at :147 validates each raw `asset_id` before the unchanged Gateway parser can strip whitespace. Invalid aliases cannot disappear or be treated as unrelated orders.
- Account composition at :337 validates all positive-position keys returned by the real Gateway before any absent target key can mean zero. At :340 it also validates parsed open-order IDs before target membership checks.

Policy: **reject aliases; do not normalize and silently merge/discard account evidence**. Accepted market, book, position and order identities therefore share one representation. Malformed returned account identity blocks the session even if it appears to belong to another market, because cleanliness cannot be reliably established from that evidence.

Gateway still performs its existing official position pagination, positive-position filtering, size validation and order parsing. The SM validator checks returned position keys; it does not replace the shared parser or claim validation of rows discarded by that parser. Known nonzero canonical target inventory/orders continue to reject through unchanged M1 checks. No new network path, state store, runtime, identity registry or execution capability is added.

## 4. Regression Tests

New file: `tests/test_rdp_sm_m1_f01_token_identity.py`, **18 parametrized cases**. It imports the existing M1 Fake HTTP fixtures; real SM, Gamma parser, SDK, Gateway account reads and book components execute. No FakeGateway or mock internal preflight logic is used.

| Requirement | Test / assertions | Result |
|---|---|---|
| T01 YES=1 / NO=01 | `test_f01_t01_t02_duplicate_or_aliased_market_tokens_block` :28; consistent fake alias books supplied; BLOCKED at market, no account/read authorization | PASS |
| T02 YES=1 / NO=1-space | Same test; BLOCKED at market; identical canonical legs also block | PASS |
| T03 Account position alias | `test_f01_t03_positive_position_alias_cannot_be_hidden` :38, `01` and `1 ` with size2; actual positions GET reached, market check PASS, BLOCKED at account, no clean account dictionary | PASS |
| T04 Open-order alias | `test_f01_t04_open_order_alias_cannot_be_hidden_or_stripped` :50, `01` and `1 `; actual authenticated orders GET reached, market PASS, BLOCKED at account | PASS |
| Reviewer D3 | `test_f01_reviewer_d3_market_alias_with_canonical_inventory_and_order` :60; fake market/book YES=01 plus canonical position/order=1; BLOCKED at market | PASS |
| T05 Legitimate READY | `test_f01_t05_distinct_canonical_tokens_keep_ready_path` :71; normal YES=1 and maximum uint256 YES, distinct NO=2; actual account zero inventory/orders, every check PASS, READY/read_only/no authorization | PASS |
| T06 Invalid identity | `test_f01_t06_invalid_identity_blocks_at_actual_read_boundary` :93; market/position/order sources, overflow/Unicode/invalid spelling; BLOCKED at corresponding actual stage | PASS |
| Conflicting account aliases | `test_f01_conflicting_account_position_aliases_block` :105; positive canonical and leading-zero records together cannot be merged into a clean result | PASS |
| Book alias | `test_f01_book_identity_alias_blocks` :115; raw asset=01 with canonical market=1 blocks at books | PASS |

D1 is directly reproduced by the size2/`1 ` account test, D2 by the consistent duplicate-numeric market fixture, and D3 by its exact mixed market/account test. Account-path tests use a valid canonical market so they cannot pass merely because market formatting validation prevents all account reads. Original M1 canonical positive inventory/order tests still independently prove REJECTED rather than false READY.

## 5. Actual Test Results

Environment: repository `.venv`, Python 3.12.14, existing pytest/pytest-asyncio and pinned official SDK. No install or live credential access.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_rdp_sm_m1.py -q
```

```text
64 passed in 0.96s
M0 offline guard: 0 network/DNS attempts; workspace credentials denied
```

Breakdown: 18 new F01 + all 46 original M1 cases. Original M1 tests are unchanged.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_rdp_sm_m1.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight'
```

```text
182 passed, 1 deselected in 1.68s
M0 offline guard: 0 network/DNS attempts; workspace credentials denied
```

Final distinct executed cases: **182 passed / 0 failed / 0 skipped** = 18 F01 + 46 M1 + 118 existing FL regressions. One pre-existing settlement CLI credential-isolation case remains explicitly deselected, following the independent review's JUSTIFIED disposition. Its documented failure is default dotenv discovery attempting protected workspace credentials, unrelated to M1/F01. It is not counted as passed; protection is not weakened, and no deferred environment/test fix is attempted.

```bash
.venv/bin/ruff check --no-cache src/polymaker/rdp_sm.py tests/test_rdp_sm_m1_f01_token_identity.py
git diff --check
git diff --quiet -- src/polymaker/execution src/polymaker/state src/polymaker/engine.py src/polymaker/config.py src/polymaker/favorite_live.py
```

All passed. The unchanged offline runner clears inherited environment, uses temporary CWD, denies real sockets/DNS and workspace credentials, disables pytest cache; bytecode is disabled. Fake HTTP permits only official host GETs and existing tests forbid trading/RPC/runtime calls. No orders, cancellations, live API smoke or external requests were executed.

Worker self-check only. Formal post-fix independent review is pending.

## 6. FL Regression Impact

**PASS** for the selected 118 existing regressions; shared execution/state/runtime/configuration code remains byte-for-byte baseline through unchanged Git diff. No shared Gateway, Tracker or FillTruth behavior is altered. Existing CLI addition from M1 is untouched in F01. The new restrictions apply only to the independent SM preflight path.

Valid distinct canonical token IDs still produce READY, including the uint256 maximum boundary. Original time-window, market/tick, account-failure, balance/allowance, capital and read-only CLI tests pass unchanged. No new external interface is introduced.

## 7. MicroLive Impact

M1-H01's three known false READY counterexamples are contained by executable BLOCKED evidence. Account identity comparisons cannot silently treat these tested aliases as clean or unrelated; this is the sole blocker addressed by F01.

This result does not authorize trading or assert that all MicroLive prerequisites are complete. READY remains a current read-only snapshot; the original $30 limit, frozen time boundaries and M2 obligations are unchanged. Invalid spellings now conservatively block and require trustworthy official data rather than a blind retry or invented identity resolution mechanism.

## 8. Remaining Deferred Issues

No approved deferred issue was repaired:

- Configured signer/funder authoritative cross-binding and actual live service semantics remain NOT_VERIFIED, as recorded by M1 review.
- Broader SDK RFQ write-boundary claims and execution price-grid validation remain deferred. Current M1 call graph does not invoke RFQ or execute quotes.
- Live smoke remains NOT_VERIFIED; no network activity added.
- M2 actual execution integrity, real-time capital/time checks and Architect-assigned M0-H01/H02/H03 remain under their existing disposition. No M0 code or evidence revisited.
- The credential-guard-incompatible settlement regression remains excluded as previously justified.

## 9. Change Manifest

F01 work only:

1. Modified `src/polymaker/rdp_sm.py` — one canonical validator and market/book/account/order validation call sites.
2. Created `tests/test_rdp_sm_m1_f01_token_identity.py` — 18 focused offline regressions using existing external fixtures.
3. Created this report: `docs/development/RDP_SM_M1_F01_TOKEN_IDENTITY.md`.

No other implementation/test/report edits. The pre-existing CLI diff and untracked M0/M1 files are preserved. Historical report and untouched entry/test hashes:

```text
9230957f5572fea6438d071e92cc80d9c3f5dc6136e06a76d80fe0cb56e3eb03  docs/development/RDP_SM_M1_SAFE_SESSION.md
13f52c69c7f5e77aaf8aa3532212bfa3bd8900f2d2a4541ba56697623a83e436  docs/development/RDP_SM_M1_INDEPENDENT_REVIEW.md
00133cf7024d7b2d7bedb5f4d5a717015844146b73d987f3cfb6c209f07468e8  src/polymaker/cli.py
7713ddcd71b356617cb595f24cc1417706068bdd27edc815af050cb563867356  tests/test_rdp_sm_m1.py
```

Final `git status --short --untracked-files=all`:

```text
 M src/polymaker/cli.py
?? docs/development/RDP_SM_M0_EXECUTION_FEASIBILITY.md
?? docs/development/RDP_SM_M0_INDEPENDENT_REVIEW.md
?? docs/development/RDP_SM_M1_F01_TOKEN_IDENTITY.md
?? docs/development/RDP_SM_M1_INDEPENDENT_REVIEW.md
?? docs/development/RDP_SM_M1_SAFE_SESSION.md
?? src/polymaker/rdp_sm.py
?? tests/rdp_sm_m0_offline.py
?? tests/test_rdp_sm_m0_execution.py
?? tests/test_rdp_sm_m1.py
?? tests/test_rdp_sm_m1_f01_token_identity.py
```

The modified adapter was already untracked from M1, so Git still shows it as untracked rather than `M`. Staged diff remains empty, branch/HEAD unchanged, no commit made. Shared core changes: **NO**. SOP and deferred findings unchanged.

## 10. Final Worker Result

**CODE_READY**

Three Reviewer counterexamples no longer return READY. Market, book and real account-read paths enforce consistent canonical Token IDs; positive aliased inventory/orders block before clean status can be produced. All 64 M1/F01 tests and 118 selected FL regressions pass with zero network/DNS attempts and no shared-core modification.

Next: **Independent Reviewer** for M1-F01. No M2 work or MicroLive action started.

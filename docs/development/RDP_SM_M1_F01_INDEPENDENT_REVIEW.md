# RDP-SM M1-F01 — Independent Review

## 1. Baseline

- Date: 2026-10-11, Asia/Shanghai.
- Project: `/home/ubuntu/poly-maker`.
- Branch: `fl-restored-97ab0f9`.
- HEAD: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`, identical to original FL baseline.
- Frozen strategy: RDP-SM SOP v1.8. Architecture: FL-first / Thin SM Orchestrator.
- Separate independent reviewer: `/root/m0_fresh_review`, who inspected source, independently ran 64 tests and supplementary diagnostics, and concluded PASS without edits. Root authored F01 previously, assembled this report and separately reran the targeted and FL regression suites; root self-check is not claimed as the independent reviewer provenance.
- Review-entry Git status: pre-existing CLI addition plus untracked M0/M1/F01 source, tests and reports. All included as appropriate inputs and preserved. Staged diff empty. Final status in section 11.

## 2. Review Scope

Single question: does M1-F01 close M1-H01 so aliases, duplicate numeric token identities and malformed identities cannot hide an existing positive target holding/open order or produce the original false READY results?

Read the original M1 report, M1 Independent Review FAIL report, F01 fix report, actual adapter, new tests and directly relevant Gamma/Gateway/SDK parsing and original M1 tests. Inspected Git diff and untracked files; independently executed offline tests. No production/test edits, live credentials, external HTTP/WS/RPC, actual orders, M2 or M0 re-audit.

Signer/funder authority, RFQ boundary, price grid and M0-H01/H02/H03 are explicitly outside this fix review. Their approved dispositions are unchanged. This is neither a full M1 architecture re-audit nor MicroLive authorization.

## 3. Original Finding

M1-H01 arose because `"1"`, `"01"` and `"1 "` can represent the same intended numeric token while exact string membership treats them differently. The original independent review demonstrated:

- D1: positive position under `"1 "` was missed by canonical YES `"1"`; READY with zero target inventory.
- D2: YES `"1"` and NO `"01"` were accepted as different legs despite equal numeric identity.
- D3: market YES `"01"` hid canonical `"1"` inventory and an open order; READY with target inventory/order count zero.

Those historical reports remain intact. This review verifies their correction through real current readers and external Fake HTTP evidence; it does not rewrite the original FAIL conclusion.

## 4. Root Cause Verification

`src/polymaker/rdp_sm.py:84-92` now defines `_token_id`: string only, full ASCII `[1-9][0-9]{0,77}` match, exact integer below `2**256`. This admits a unique representation for each accepted positive uint256 and rejects leading zeros, whitespace, non-ASCII digits, signs, non-string inputs, zero and overflow. No floating-point conversion, sample-specific special cases or alias merging is used.

At :280-284 both parsed market tokens must pass that validator and differ. Accepted canonical decimal strings have a one-to-one relation to numeric identities, so distinct strings cannot represent the same uint256. This addresses the cause rather than just blocking `01` specifically.

Shared Gateway preserves positive-position keys (`execution/gateway.py:686-697`) and strips open-order asset whitespace (:635). F01 validates positive position keys before lookup and validates raw SDK order IDs before that stripping. Thus it does not merely tighten metadata and leave account comparison vulnerable.

Validation exceptions are fail-closed: `_Issue` reaches BLOCKED at SM :490-494; SDK/Gateway-wrapped read errors reach BLOCKED at :495-497. Neither handler treats invalid identity as an empty account or returns READY.

## 5. Token Identity Path Audit

| Path | Actual source and behavior | Verdict |
|---|---|---|
| Market metadata | Gamma still pairs official outcomes/token IDs; SM :267-284 validates shape, Up/Down labels, both canonical token strings and distinctness | PASS |
| YES/NO mapping | `meta.tokens=(yes,no)` only after canonical validation; identity is from official paired outcomes, not title | PASS |
| Book | SM :293-298 validates raw `asset_id` with the same helper, then requires exact canonical market identity/condition match | PASS |
| Positions | Real `Gateway.positions` performs existing pagination/value checks; SM :333-337 validates every returned positive-position key before :380-381 missing-key defaults | PASS for positive holding evidence |
| Raw open orders | SM SDK `_get` :128-147 validates every row's raw `asset_id`, on every returned page, before Gateway whitespace stripping | PASS |
| Parsed open orders | SM :338-340 validates parsed token IDs again before target membership :382-383 | PASS |
| Canonical positive target state | Original M1 inventory/open-order tests still return REJECTED; no change to cleanliness rules | PASS |

Exact string comparison remains in account lookup; this is safe for accepted inputs because both sides must be canonical. Invalid equivalent spellings are BLOCKED instead of normalized into an ambiguous merged account. Market-only early blocking is complemented by actual account-path tests with a valid canonical market, so account safety is independently exercised.

**Boundary limitation:** the shared Gateway returns positive positions only. It discards known zero-share rows before SM sees their keys. F01 does not validate every raw zero-share Data API row; its report explicitly disclaims that. Section 8 assesses the independently tested limitation without expanding the scope or claiming universal raw-row validation.

## 6. Counterexample Verification

All test references below are in `tests/test_rdp_sm_m1_f01_token_identity.py`. Assertions run actual preflight/FL readers with existing Fake HTTP transport. No execution or internal preflight replacement is used.

| Scenario | Test / actual assertion | Observed result | Verdict |
|---|---|---|---|
| C01 YES=1 / NO=01 | :28; matching alias fake books supplied, BLOCKED at market, account None, no authorization, only metadata GET | BLOCKED; not READY | PASS |
| C02 YES=1 / NO=1-space | :28; same safety assertions; identical canonical YES/NO also tested | BLOCKED; not READY | PASS |
| C03 Positive position uses alias | :38; size2 under 01/1-space with valid canonical market; market PASS, actual positions GET reached, TOKEN_IDENTITY_INVALID at account, no account dictionary | BLOCKED; positive alias not ignored | PASS |
| C04 Open order uses alias | :50; raw asset 01/1-space; market PASS, actual authenticated orders GET reached, BLOCKED at account, account None | BLOCKED before clean result | PASS |
| Original D3 combined mismatch | :60; market YES=01 plus canonical position/order=1 and matching fake alias book; BLOCKED at market, TOKEN_IDENTITY_INVALID | BLOCKED; not READY | PASS |
| C05 Legal distinct/clean | :71; YES=1 or maximum uint256, NO=2; READY, actual zero target inventory/orders, all checks PASS, read_only true, authorization false | READY | PASS |
| C06 Invalid active identity | :93; market, positive position and order paths with overflow, Unicode digits or invalid spelling; BLOCKED at correct stage, account None | BLOCKED | PASS |
| Conflicting position aliases | :105; canonical and aliased positive records together; TOKEN_IDENTITY_INVALID, no account result | BLOCKED | PASS |
| Book alias | :115; canonical market, raw book asset01; BLOCKED at books, no market result | BLOCKED | PASS |

The three original false READY results are closed by direct executable assertions. Existing canonical positive YES/NO position and target-order tests (`test_rdp_sm_m1.py:260`, :276) remain REJECTED, including target order count1. Noncanonical positive positions/orders cannot reach membership lookup; canonical ones cannot be ignored merely because of formatting differences.

Additional independent filtered-row observations are in section 8. C06 above concerns identities participating in market/book/open-order/positive-position validation, not a claim that all raw zero-share records are examined.

## 7. Regression Results

Environment: repository `.venv`, Python 3.12.14, existing pytest/pytest-asyncio, pinned SDK. No installs. Preserved offline runner clears inherited environment before imports, uses temporary CWD, denies real socket/DNS and workspace credential access, disables pytest cache. `-B` prevents bytecode files. M1 fixtures additionally restrict fake HTTP methods/hosts and forbid trading/RPC/runtime paths.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_rdp_sm_m1.py -q
```

Root independently reran: **64 passed in 2.26s** = 18 F01 + 46 unchanged M1 tests. Separate independent reviewer: **64 passed in 1.91s**. Both runs reported zero network/DNS attempts and workspace credentials denied.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight'
```

Root independently reran: **118 passed, 1 deselected in 2.18s**; zero network/DNS attempts, workspace credentials denied.

Primary selected review total: **182 executed / 182 passed / 0 failed / 0 skipped**, plus one explicit deselection. The separate reviewer's duplicate 64-case execution and additional diagnostics are reported separately rather than added to that distinct-case total. Worker counts are independently substantiated.

Previously justified exclusion remains `test_cli_conflict_stops_before_settlement_preflight`. Existing `Config.load(load_env=True)` dotenv discovery reaches protected workspace `.env` before the expected alias conflict. F01 did not change CLI/config/test/guard files; original independent review already reproduced the cause. This review checked those sources/hashes and preserved the exclusion without enabling credentials or enlarging that audit. The excluded case is not counted as passed or skipped.

```bash
.venv/bin/ruff check --no-cache src/polymaker/rdp_sm.py tests/test_rdp_sm_m1_f01_token_identity.py
git diff --check
git diff --quiet -- src/polymaker/execution src/polymaker/state src/polymaker/engine.py src/polymaker/config.py src/polymaker/favorite_live.py
```

All passed. Shared-core sources still match FL baseline. Original CLI/M1-test hashes match prior review, so F01 changes no existing FL Token handling or command behavior. F01 adds pure validation calls; no external interface, runtime or trading action is introduced.

## 8. MicroLive Impact

| Issue | Impact | FIX NOW / DEFER / DROP |
|---|---|---|
| Original M1-H01 hidden positive holding/order or duplicate leg | HIGH originally; tested false READY paths now reliably BLOCKED. No remaining current blocker demonstrated | FIX NOW requirement satisfied by F01 |
| Zero-share position rows filtered before SM validation | NONE for demonstrated positive-holding/order safety; LOW contract-coverage limitation. Malformed raw zero-share IDs can still yield READY, but cannot hide a positive record in the tested combinations | DROP additional raw-row validation work for this fix's current safety objective; disclose limitation |
| Existing approved deferred issues | Disposition frozen; not re-investigated | DEFER unchanged, section 9 |

Separate reviewer independently ran this read-only diagnostic using real Gateway/SM and the existing external fixtures; it changed no files. It specifically tested whether filtering could still conceal a positive target position:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B - <<'PY'
import os, sys, socket, tempfile, runpy, asyncio
from pathlib import Path
root=Path.cwd(); retained={k:os.environ[k] for k in ('PATH','LANG') if k in os.environ}
os.environ.clear(); os.environ.update(retained)
network=[]
def deny(*a,**kw):
    network.append(True); raise AssertionError('offline review network forbidden')
def protect(event,args):
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
        p=Path(os.fsdecode(args[0])).absolute()
        if p==root/'.env' or root/'.aws' in p.parents:
            raise AssertionError('workspace credentials forbidden')
sys.addaudithook(protect)
for name in ('connect','connect_ex','sendto','sendmsg'):
    if hasattr(socket.socket,name): setattr(socket.socket,name,deny)
for name in ('create_connection','getaddrinfo','gethostbyname','gethostbyname_ex'):
    setattr(socket,name,deny)
sys.path[:0]=[str(root),str(root/'src')]
import pytest
with tempfile.TemporaryDirectory(prefix='rdp-m1-f01-review-') as tmp:
    os.chdir(tmp); ns=runpy.run_path(str(root/'tests/test_rdp_sm_m1.py'))
    async def diag(name,rows):
        with pytest.MonkeyPatch.context() as mp:
            fixture=ns['venue'].__wrapped__(mp,Path(tmp)); venue=next(fixture)
            try:
                venue.positions=rows
                r=await ns['run_preflight'](ns['cfg'].__wrapped__(),ns['SLUG'])
                print(name,r.status.value,'account=',r.account,'last=',r.checks[-1].detail)
            finally:
                try:next(fixture)
                except StopIteration:pass
    async def main():
        for token in ('1 ','01',str(2**256),'invalid'):
            await diag('zero '+repr(token),[{'token_id':token,'current_size':0,'avg_price':.5}])
        await diag('positive canonical plus zero alias',[
            {'token_id':'1','current_size':2,'avg_price':.5},
            {'token_id':'01','current_size':0,'avg_price':.5}])
        await diag('positive alias plus zero canonical',[
            {'token_id':'01','current_size':2,'avg_price':.5},
            {'token_id':'1','current_size':0,'avg_price':.5}])
    asyncio.run(main())
print('network/DNS attempts:',len(network))
PY
```

Observed output summarized from the account dictionaries:

| Input | Actual result |
|---|---|
| Size0 token `1 `, `01`, `2**256` decimal or `invalid` | Each READY, target inventory0; shared Gateway discarded known zero-size records |
| Positive canonical token1 size2 plus zero alias01 | REJECTED, YES inventory2 |
| Positive alias01 size2 plus zero canonical1 | BLOCKED, account None, TOKEN_IDENTITY_INVALID |

Network/DNS attempts: **0**. This is an explicit limitation of validation after the existing positive-position filter, not universal malformed-raw-row blocking. Under MicroLive First's actual-impact gate, no hidden positive holding/order or erroneous clean economic state was demonstrated by these zero-share cases. Do not claim the adapter validates every raw Data API record; do not expand shared parsing merely to eliminate a no-impact zero-row observation.

PROVEN: tested aliases/duplicates/active invalid identities block; canonical positive state rejects; valid clean state remains READY; real-reader tests and current sources unchanged by review.

NOT_VERIFIED: live endpoint behavior or all possible malformed payload combinations. No live smoke is necessary for this scoped fix acceptance, and none was performed.

## 9. Deferred Issues

Existing dispositions preserved, without re-audit or fixes:

- Signer/funder authoritative association.
- SDK RFQ boundary.
- Execution price-grid validation.
- M0-H01/H02/H03 under M2 Execution Integrity.

MicroLive remains unauthorized. This PASS does not establish actual execution integrity, live readiness or completion of deferred work.

## 10. Final Reviewer Decision

**PASS**

M1-H01's original three false READY counterexamples are reliably closed. Unique accepted numeric representation is enforced consistently across market, book, positive-position and open-order comparison paths; alias errors fail closed before zero-inventory/order status can be produced. The account fix is exercised through actual official-read adapters, not only unit checks of strings. Legal READY and existing FL behavior remain available in independently passing suites.

No remaining actual blocker for this scoped F01 acceptance was demonstrated. The raw zero-share filtering limitation is disclosed and judged nonblocking under the frozen MicroLive First principle. No approved deferred disposition changed.

Next action: **Architect acceptance of M1-F01**. No M2 implementation or trading started.

## 11. Change Manifest

Review-created file only:

- `docs/development/RDP_SM_M1_F01_INDEPENDENT_REVIEW.md`.

Neither root nor separate reviewer edited production source, tests, Worker reports, earlier review reports, shared core, config or frozen SOP. Before/after input SHA-256 hashes match:

```text
97acb95c0f5c0f5048e46499bfa49efcb90f3453cc19c6c616c1ff159cbab50f  src/polymaker/rdp_sm.py
07d5b0f5f0233d437f08b63a9c8041105857fd50912d221c8b749f6acd8a381e  tests/test_rdp_sm_m1_f01_token_identity.py
00133cf7024d7b2d7bedb5f4d5a717015844146b73d987f3cfb6c209f07468e8  src/polymaker/cli.py
7713ddcd71b356617cb595f24cc1417706068bdd27edc815af050cb563867356  tests/test_rdp_sm_m1.py
9230957f5572fea6438d071e92cc80d9c3f5dc6136e06a76d80fe0cb56e3eb03  docs/development/RDP_SM_M1_SAFE_SESSION.md
13f52c69c7f5e77aaf8aa3532212bfa3bd8900f2d2a4541ba56697623a83e436  docs/development/RDP_SM_M1_INDEPENDENT_REVIEW.md
f18d1c25ed96574bc9ab8e363fbd1543408aa5e1de5f1b99d06c1a33f3decbf8  docs/development/RDP_SM_M1_F01_TOKEN_IDENTITY.md
```

Final Git status (`--short --untracked-files=all`):

```text
 M src/polymaker/cli.py
?? docs/development/RDP_SM_M0_EXECUTION_FEASIBILITY.md
?? docs/development/RDP_SM_M0_INDEPENDENT_REVIEW.md
?? docs/development/RDP_SM_M1_F01_INDEPENDENT_REVIEW.md
?? docs/development/RDP_SM_M1_F01_TOKEN_IDENTITY.md
?? docs/development/RDP_SM_M1_INDEPENDENT_REVIEW.md
?? docs/development/RDP_SM_M1_SAFE_SESSION.md
?? src/polymaker/rdp_sm.py
?? tests/rdp_sm_m0_offline.py
?? tests/test_rdp_sm_m0_execution.py
?? tests/test_rdp_sm_m1.py
?? tests/test_rdp_sm_m1_f01_token_identity.py
```

Branch/HEAD unchanged; staged diff empty; tracked CLI diff unchanged from review entry. No commit, fix or trading action performed. Shared core changes: **NO**.

# ITER-0009 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- Active `AGENTS.md`, Frozen SOP and approved `01_ARCHITECT.md`.
- Worker artifact `02_WORKER.md`.
- Actual CLI diff, new bootstrap/tests, existing Gamma client/parser, domain mapping and logging.
- Relevant BTC5m, catalog and predecessor tests.

## Repository / Diff Reviewed

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `f8ce0378e1028e3796640ba53318f754882b0fb2`.
- Implementation scope: CLI registration plus untracked `rdp_live.py` and `test_rdp_live.py`.
- Other changes are Orchestrator-owned audit/index records. Preserved `research_outputs/` remains untouched.
- `git diff --check`: passed.

Reviewed SHA256:

```text
091de1dc85634ced2d5e6b21e7520f6498ce8bfc6511b42e4eecc3c43566bf2b  src/polymaker/cli.py
e2571497e575d8058f502b4e78b9bb001d80ac4e44669e6e15616e4b0637a85e  src/polymaker/rdp_live.py
48344419c6dec45e33b73342fc462715561eb4008bd9877142367fdc145137bd  tests/test_rdp_live.py
```

## Acceptance Criteria Review

| AC | Assessment |
|---|---|
| 1 | PASS — help accurately describes read-only scope. |
| 2 | Incomplete — initial origins reject correctly, but later configuration mutation bypasses validation. |
| 3 | PASS for valid fixtures — exact lookup and two public book GETs. |
| 4 | Incomplete — numeric conversion can alter venue metadata. |
| 5 | PASS — missing CLOB metadata cannot use Gamma defaults. |
| 6 | Incomplete — reversed outcome order is accepted. |
| 7 | Incomplete — high-precision numeric metadata can silently round into invalid or weakened values. |
| 8 | PASS — expiration is rechecked after reads without fallback selection. |
| 9 | PASS — tested success/failure paths produce durable evidence and appropriate exit codes. |
| 10 | PASS — no Engine, execution or authentication startup. |
| 11 | PASS — affected and full regressions pass. |

## Existing Codebase Compatibility

The bootstrap reuses CLI conventions, exact Gamma lookup, parser, domain types and logging. Its small public metadata reader fills fields omitted by existing helpers without replacing the book service or gateway. FL and IM behavior are unchanged.

## Findings

### BLOCKER

NONE.

### MUST_FIX

**MF-01 — Reversed outcomes certify the wrong YES/NO mapping.**

`_validate_market()` checks the outcome set rather than its order. Raw `outcomes='["Down","Up"]'` passes and produces:

```text
market.yes.outcome = Down
market.no.outcome = Up
```

Existing `MarketMeta.yes/no` resolve positions zero/one. The approved contract explicitly requires expected BTC5m mapping and an outcome-order mismatch test.

Reject reversed ordering before book reads, preserving the expected Up/Down positional mapping. Add the missing regression.

**MF-02 — Numeric metadata silently changes during float conversion.**

`_positive_number()` validates Decimal values, then returns floats without checking exact preservation. Reproductions:

```text
tick_size "0.999999999999999999" → 1.0
min_order_size "5.0000000000000001" → 5.0
```

The first violates the returned tick’s `(0,1)` requirement. The second rounds a venue minimum above five down to five, contrary to unchanged metadata retention and potentially weakening later quantity checks.

Preserve exact supported values or reject unrepresentable precision explicitly. Do not round or add tolerances. Add both boundary regressions and retain ordinary numeric-string support.

**MF-03 — Mutable configuration bypasses official-origin validation.**

`bootstrap_rdp_market()` validates configured origins before its first await, but later reads `cfg.wallet.clob_host` again when constructing the CLOB client.

A mocked Gamma response changing that field to `https://unapproved.example` causes two GETs to the unapproved origin and successful bootstrap.

Snapshot the validated endpoint strings before awaiting and use those snapshots throughout, or revalidate immediately before each use. Add a configuration-mutation regression proving no unapproved request occurs.

All three repairs fit the approved source/test scope and require no architecture decision.

### NOTE

The bootstrap remains an intentionally bounded public preflight; production WS/account/lifecycle composition is deferred.

## Frozen SOP Compliance

No strategy prices, sizes, filters or state transitions are changed. Outcome identity and venue metadata must be repaired before this context is suitable for later RDP composition.

## External Interface Review

Normal fixtures use official Gamma/CLOB GETs only. MF-03 currently permits an unapproved runtime origin after configuration mutation. No POST, DELETE, RPC or authenticated account path is introduced.

## Trading Safety Review

The CLI requires `--preflight-only`; omitted mode fails before requests. No Engine or execution gateway is constructed or connected. No credentials are derived, orders submitted or cancellations performed. Development validation used HTTP mocks only.

## Inventory / Capital Accounting Review

No inventory or accounting mutations occur. MF-02 must be fixed so bootstrap metadata cannot understate the venue minimum used by later gates.

## State Machine Review

No trading state machine is started. Categorized bootstrap failures are distinct from strategy STOP. Market expiry is checked before discovery, after discovery and after metadata reads.

## Test Review

Independent current-tree results:

```text
uv run pytest -q tests/test_rdp_live.py
47 passed

uv run pytest -q tests/test_rdp_live.py tests/test_btc5m.py tests/test_catalog.py tests/test_config_aliases.py tests/test_favorite_live.py tests/test_engine.py
137 passed

uv run pytest -q
1002 passed, 2 skipped

uv run ruff check src/polymaker/rdp_live.py src/polymaker/cli.py tests/test_rdp_live.py
All checks passed!

uv run polymaker rdp-live --help
Passed
```

Passing tests omit the three reproduced defects. Complete mocked CLI success/failure paths were exercised without trading authority.

## Scope Review

Only the authorized CLI registration and bootstrap/tests were added. No unrelated subsystem change or new dependency is present.

## Residual Risks

Public metadata can change after bootstrap. WS readiness, authoritative account state, metadata refresh and lifecycle integration remain deferred. Preflight success does not establish trading readiness.

## Final Decision

RETURN_TO_WORKER.

Repair MF-01 through MF-03, add the specified regressions and return for independent review. Real-money authorization remains NOT_GRANTED.

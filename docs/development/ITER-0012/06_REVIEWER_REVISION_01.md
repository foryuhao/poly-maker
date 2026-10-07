# ITER-0012 — Reviewer Report

## Verdict

PASS

## Reviewed Inputs

- `AGENTS.md`, Frozen SOP, baseline and iteration index.
- Original ITER-0012 contract, Worker report and Reviewer findings.
- `04_ARCHITECT_REVISION_01.md` and `05_WORKER_REVISION_01.md`.
- Actual eight-file source/test diff, existing gateway reader and unchanged capital projection/admission implementation.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `03d5e376fba19c1cd48f15fb42fe717080dbfeec`

Reviewed implementation/test changes and separate index/audit changes. Preserved research assets were untouched. No Reviewer writes, Git mutations or live actions occurred.

SHA-256:

| File | Hash |
|---|---|
| `src/polymaker/execution/gateway.py` | `9a8186f04488fc09e0430cb73c3c49382cb36d550d931f5e3adfc3d21f3beb2e` |
| `src/polymaker/cli.py` | `d75ffa0122cbc0ade759deafc86068481748594160332a85472f192787619548` |
| `src/polymaker/rdp_account.py` | `5fd3a0fd58b23873b945603f75693cc33a15b3208206b08531e8852102703b12` |
| `src/polymaker/rdp_live.py` | `e5e3b0001c7f14f789d4d8e1498744a8db8d9dbec348bd78ce2dddebd27235a9` |
| `src/polymaker/risk/rdp_capital.py` | `c084653825fc0f0230c22e75b05e6a751d778a981a8a3fd2f48fbfc2d30d07ad` |
| `tests/test_rdp_account.py` | `1bf42bf62532f647e83beb01d935912efc5fd9d9bed36c1199d0095f0fb66dd5` |
| `tests/test_rdp_capital.py` | `df924dd6d1512910606e842bcb038c78d3254ee15c161595d55357a5b90dd785` |
| `tests/test_rdp_live.py` | `1b966df263c42cd071d8e6ce2bee82f9c9998588e625bdb3ad2ec0845af844dc` |

## Acceptance Criteria Review

| Revised AC | Result |
|---|---|
| 1 — Actual producer yields exact partial-fill backing | Satisfied: remaining `0.9`, commitment `$0.45`, backing `$30`, eligible. |
| 2 — Genuine independent shortfalls remain ineligible | Satisfied through the actual producer path. |
| 3 — Long quantities survive without context rounding | Satisfied by implementation, tests and independent low-precision probe. |
| 4 — Invalid exact-source fields/cumulative quantities fail | Satisfied; unsupported floats, booleans, nonfinite and missing fields reject. |
| 5 — Legacy float `OpenOrder` behavior preserved | Satisfied. |
| 6 — Exact mode requires strict validation | Satisfied. |
| 7 — Fractional freshness boundary | Satisfied; exact boundary passes and immediately older evidence rejects. |
| 8 — Valid required-spender identities | Satisfied; missing, malformed and normalized duplicates reject. |
| 9 — Original backing requirements retained | Satisfied: collection/count/subset, scope, empty selected state, independent backing, fixed capital, freshness, immutability and CLI separation. |
| 10 — Existing capital/predecessor regressions | Satisfied. |
| 11 — No live or prohibited actions | Satisfied. |

## Existing Codebase Compatibility

The implementation reuses the gateway request, response handling, executor, account capture and capital module. No client or subsystem was duplicated or replaced.

The approved default-off exact path returns a dedicated immutable record. Legacy calls retain float `OpenOrder` results and their prior arithmetic. Existing projection/admission code remains unchanged.

## Findings

### BLOCKER

None.

### MUST_FIX

None.

### NOTE

All previous findings are closed:

- **MF-01:** authoritative decimal order quantities are parsed before subtraction; account capture requests exact mode and preserves the resulting economics.
- **MF-02:** freshness comparisons use exact decimal timestamp representations without a tolerance.
- **MF-03:** required-spender identities receive local address validation and normalized uniqueness checks.

An additional controlled integration probe used two fractional partial fills, source quantities exceeding 70 decimal digits and local Decimal precision of six. Both remaining quantities were exactly `0.9`; total BUY commitment was exactly `$0.9`; backing was exactly `$30`; eligibility passed. The local context remained unchanged.

## Frozen SOP Compliance

Starting capital remains exactly `$30`, regardless of higher wallet balances. The subsequent `$5` NORMAL reserve remains inside that budget.

Startup backing does not redefine Frozen FreeCash, import inventory, adopt orders or change NORMAL/REBALANCE semantics. No strategy-semantic deviation was found.

## External Interface Review

No new runtime interface or dependency was introduced. Exact reads reuse the existing official CLOB request. Account positions retain the existing official Data API path.

The synchronous evaluator performs no network access. Local address validation invokes no blockchain RPC.

## Trading Safety Review

No signing, order submission, cancellation, allowance update, credential mutation or account cleanup was added. Startup eligibility grants no send authority.

Malformed exact fields fail closed. Selected orders and nonzero selected inventory prevent empty-run certification. Errors remain sanitized.

No live account action or real-money trading occurred.

## Inventory / Capital Accounting Review

All observed BUY orders contribute exactly once using remaining quantity; SELL orders do not reserve collateral cash in this startup calculation.

Cash and allowance backing are calculated independently. Complete order counts, unique IDs and selected-subset consistency are checked. Selected positions must match bootstrap token/outcome scope and be zero.

Exact coefficient/exponent arithmetic avoids process-context rounding. No persistent reservation ledger or authoritative inventory mutation appears.

## State Machine Review

No trading controller starts. Account-read success remains separately observable from backing eligibility; ineligible backing does not masquerade as a failed account read.

Timestamp order, future reads, age limits and market expiry prevent certification when evidence is unreliable.

## Test Review

Independent current-tree checks:

- `uv run pytest -q tests/test_rdp_capital.py tests/test_rdp_account.py tests/test_rdp_live.py`  
  **181 passed in 1.90s**
- `uv run pytest -q`  
  **1127 passed, 2 skipped in 12.88s**
- Ruff across all eight changed source/test files: **passed**
- `uv run polymaker rdp-live --help`: **passed**
- `git diff --check`: **passed**

Producer integration tests traverse the actual parser, account capture and evaluator. They cover the original partial-fill reproduction, independent micro-dollar shortfalls and long decimal source quantities. Legacy return behavior, strict-mode enforcement, freshness boundaries and spender failures are covered.

The independent low-context integration probe additionally verified context-independent subtraction and multiple BUY commitments.

## Scope Review

The gateway extension is expressly authorized by Architect Revision 01. Its implementation remains opt-in and preserves predecessor behavior.

No unrelated subsystem, strategy feature, runtime interface or trading authority was introduced.

## Residual Risks

Controlled responses establish implementation correctness, not live SDK response compatibility or spendability. Exact mode deliberately rejects float fields whose original precision is unavailable.

Account evidence remains sequential and requires later trading-time revalidation. Position-reader precision, continuous account reliability and controller integration remain deferred.

This iteration PASS does not establish project-level `MICRO_LIVE_READY`.

## Final Decision

**PASS**

BLOCKER = 0  
MUST_FIX = 0

All revised acceptance criteria and previous findings are closed for the exact reviewed tree. The Orchestrator may archive ITER-0012 and continue to the next minimal prerequisite.

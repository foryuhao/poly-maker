# ITER-0012 — Reviewer Report

## Verdict

RETURN_TO_ARCHITECT

## Reviewed Inputs

- `AGENTS.md`, Frozen SOP, baseline and iteration index.
- ITER-0012 `01_ARCHITECT.md` and `02_WORKER.md`.
- Actual seven-file source/test diff.
- Existing account reader, gateway quantity parser and RDP capital projection/admission code.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `b5245503067ec7114d514e3f7f87ab7590795383`

Reviewed implementation/test changes and separate index/audit changes. Preserved `research_outputs/` was untouched. No Reviewer writes, Git mutations or live actions occurred.

SHA-256:

| File | Hash |
|---|---|
| `src/polymaker/cli.py` | `d75ffa0122cbc0ade759deafc86068481748594160332a85472f192787619548` |
| `src/polymaker/rdp_account.py` | `ed1009aa623a5256460e2123664ca00cb0ddede23bbe8c2c615b2c17d91c18a3` |
| `src/polymaker/rdp_live.py` | `e5e3b0001c7f14f789d4d8e1498744a8db8d9dbec348bd78ce2dddebd27235a9` |
| `src/polymaker/risk/rdp_capital.py` | `5e760ae177b6a9c5eea3bf5442c09ea0a66e5c003ee9d14d74e149ac4364c25a` |
| `tests/test_rdp_account.py` | `ee8c4efa908ed89dd3ffe7307a7f9e876b96ec019de4c4917c05f95f9325fb0a` |
| `tests/test_rdp_capital.py` | `1d8dd0862099fde698e228241f25b9ff15e6d41e475f0c6a3f4300cfd98c4587` |
| `tests/test_rdp_live.py` | `1b966df263c42cd071d8e6ce2bee82f9c9998588e625bdb3ad2ec0845af844dc` |

## Acceptance Criteria Review

| AC | Assessment |
|---|---|
| 1 — Fresh flat state with exactly $30 | Satisfied for tested empty-order evidence. |
| 2 — Independent cash/allowance shortfalls | Satisfied. |
| 3 — Higher cash preserves $30 budget | Satisfied. |
| 4 — All BUY commitments; SELL exclusion | Formula satisfied, authoritative partial-fill precision fails, MF-01. |
| 5 — Selected positions/orders prevent certification | Satisfied. |
| 6 — Collection/subset/duplicate/economics validation | Mostly satisfied; malformed spender evidence accepted, MF-03. |
| 7 — Scope/freshness/expiry validation | Scope comparisons and expiry checks work; exact fractional freshness boundary fails, MF-02. |
| 8 — Immutable independent backing result | Satisfied. |
| 9 — Separate account-read/backing reporting | Satisfied. |
| 10 — Existing capital gates unchanged | Satisfied. |
| 11 — No writes/import/reservation ledger | Satisfied. |

## Existing Codebase Compatibility

Existing account readers, capital module and preflight are reused. No subsystem was duplicated or replaced.

Old capital projection/admission implementation remains unchanged. The gateway is unchanged as required by the current contract, but its float quantity subtraction prevents exact backing evaluation from authoritative partial-fill evidence.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — Exact arithmetic starts after authoritative remaining quantity has lost precision.**

Locations: existing `ExecutionGateway.open_orders()` quantity subtraction; `rdp_account.py` order-evidence conversion; `rdp_capital.py:179–195`.

A controlled integration probe traversed the actual gateway parser, account snapshot and new evaluator with:

```text
Foreign BUY price: 0.5
Original quantity: 1.1
Matched quantity: 0.2
Balance and allowance: $30.45
```

Authoritative remaining quantity is `0.9`; commitment is `$0.45`; both backing values equal `$30`.

Actual result:

```text
ACTUAL_REMAINING 0.9000000000000001
COMMITMENT 0.45000000000000005
CASH_BACKING 29.99999999999999995
ELIGIBLE False
REASON INSUFFICIENT_CASH_BACKING
```

Exact Decimal arithmetic faithfully retains the preceding float-subtraction error. The partial-fill test constructs final Decimal remaining quantity directly and misses this producer defect.

**Required resolution:** preserve authoritative decimal remaining quantity through the account-read producer, then evaluate exact commitment. Do not introduce monetary tolerances or arbitrary rounding.

**Architecture routing:** the contract explicitly keeps the gateway unchanged and limits shared-core changes to CLI reporting. Authorizing a minimal RDP-only producer correction requires an Architect revision. Preserve legacy gateway behavior; no new subsystem or external interface is needed.

**Required test:** the actual parser → account capture → evaluator path above must certify exact `$30` backing. Also cover a genuine shortfall.

**MF-02 — Exact fractional freshness boundary is rejected.**

Location: `rdp_capital.py:154–162`.

Controlled input:

```text
now = 1000.0
receipt times = (999.9, 999.95, 1000.0)
max_age_s = 0.1
```

The earliest read is exactly at the specified decimal boundary. Float subtraction produces `0.10000000000002274`, causing `STALE_ACCOUNT_EVIDENCE`.

Repair the comparison so supported ordinary timestamps honor the exact boundary without a broad time tolerance. Add fractional-boundary and immediately-over-boundary tests.

This is an ordinary local implementation repair.

**MF-03 — Malformed required-spender identity can certify backing.**

Location: `rdp_capital.py:127–134`.

The evaluator checks only nonempty, unique spender strings. A otherwise valid fixture with:

```text
required_spenders = ("not-an-address",)
```

returns `eligible=True`.

The account reader rejects this identity, but the evaluator independently validates supplied evidence and accepts malformed spender evidence.

Require valid spender identities consistent with the account reader’s validation. Add malformed, missing and duplicate-normalized spender cases alongside valid evidence.

This is an ordinary local implementation repair.

### NOTE

Sequential account observations remain correctly distinguished from atomic account state and trading authorization.

## Frozen SOP Compliance

The fixed `$30` capital constant and subsequent `$5` NORMAL reserve remain unchanged. Selected inventory is not imported, and foreign commitments are deducted only in separate startup evidence.

No strategy-semantic deviation was introduced. Exact backing evidence is incomplete under MF-01.

## External Interface Review

No new runtime interface or dependency was added. Existing official account readers remain the source. The evaluator is synchronous and performs no network access.

## Trading Safety Review

No order creation/signing, cancellation, allowance update, account cleanup, credential mutation or send authorization was added.

Ineligible backing remains separate from account-read success. Existing selected orders and positions are preserved. No real-money trading or live account action occurred during review.

## Inventory / Capital Accounting Review

BUY commitments use remaining quantity; SELL commitments are excluded. Cash and allowance are compared independently against fixed capital.

Coefficient/exponent arithmetic avoids Decimal-context rounding, but cannot recover precision already lost by the gateway producer. No persistent reservation ledger or authoritative inventory mutation appears.

## State Machine Review

No trading state machine or admission policy changed. Selected nonzero inventory, including balanced inventory, prevents empty-run certification.

Freshness and expiry checks fail closed generally; MF-02 corrects a valid boundary rejection.

## Test Review

Independent current-tree checks:

- `uv run pytest -q tests/test_rdp_capital.py tests/test_rdp_account.py tests/test_rdp_live.py`  
  **161 passed in 1.94s**
- `uv run pytest -q`  
  **1107 passed, 2 skipped in 12.77s**
- Ruff across all seven changed source/test files: **passed**
- `uv run polymaker rdp-live --help`: **passed**
- `git diff --check`: **passed**

Independent controlled probes reproduced all three findings. Existing tests omit actual partial-fill producer interoperability, fractional freshness boundaries and malformed spender identities.

## Scope Review

The submitted implementation remains within its contract. Resolving MF-01 requires revising its explicit gateway/shared-core restriction.

The smallest revision should authorize exact RDP account-read evidence while preserving predecessor behavior. MF-02 and MF-03 need no strategy, interface or human decision.

## Residual Risks

No live spendability or authenticated account read was validated. Startup eligibility remains sequential evidence requiring later trading-time revalidation.

Continuous cash/order reliability and controller integration remain deferred.

## Final Decision

**RETURN_TO_ARCHITECT**

BLOCKER = 0  
MUST_FIX = 3

Revise the minimum producer scope needed for MF-01, then repair all three findings and return the actual tree for independent review. No human architecture or strategy decision is required.

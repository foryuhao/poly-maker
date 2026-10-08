# ITER-0022 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`, Frozen SOP, baseline and iteration index.
- `ITER-0022/01_ARCHITECT.md`, `02_WORKER.md`, `03_REVIEWER.md`, `04_WORKER_FIX_01.md`.
- Actual repaired runtime/test diff.
- Relevant existing gateway, ownership, processor, FillWindow, capital, accounting, filtering and quote-building components.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `ae8d7168f55793cc7257bc807e346aaa2400acbb`

| File | Reviewed SHA-256 |
|---|---|
| `src/polymaker/rdp_runtime.py` | `e39b6f6bd936dc147d5e84472630c753bd68cb2d38f7572dc0d6dc7863c7fcc5` |
| `tests/test_rdp_placement.py` | `0e0ea8515f76410c78eeb21ac65d8bb00f3e09f40c7849bcfa52c3b03e0c9e3a` |

Implementation diff: 1,921 insertions, 2 deletions. Orchestrator index/audit changes and preserved `research_outputs/` were accounted for.

Reviewer performed no source, test, audit or Git modifications.

## Acceptance Criteria Review

| Criteria | Assessment |
|---|---|
| AC1–3 | Healthy pair, reused reads, pricing/filter rejection and dynamic ticks verified. Positive terminal freshness remains defective as described below. |
| AC4 | Existing fail-closed checks preserved; terminal capture can expose stale evidence while retaining REST. |
| AC5 | Original duplicate-ID defect repaired and independently reproduced closed. |
| AC6–7 | Added healthy Gamma confirmation/deadline and quarantine/FAILED tests close previous coverage gaps. |
| AC8 | Original delayed-failure reproduction repaired; terminal age consistency and positive-decision eligibility remain defective. |
| AC9 | Added direct registered-economics mutation coverage. |
| AC10 | Expanded actual HTTP cancellation, actual SDK teardown and coordinator-overlap coverage verified. |
| AC11–12 | No execution authority, actions or replacement release; existing APIs remain compatible. |
| AC13 | Independent current targeted and affected/predecessor checks pass. |

## Existing Codebase Compatibility

The repair retains existing Gamma and gateway reads, placement ownership, processor, FillWindow and strategy/accounting helpers. No duplicated transport, ledger or execution subsystem was introduced.

Changes remain RDP-local. Shared-core and FL/IM behavior were not modified.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — Terminal order age remains an earlier value when supplied by the caller.**

The original delayed read-failure case now correctly returns:

```text
CLOSE / ORDER_STATE_READ_FAILED:RuntimeError
market_status.age_s=11
captured_at=current time
order_receipt_ts=None
order_age_s=None
```

However, `_prefill_decision` recalculates order age only under `elif order_age_s is None`. Successful and many rejection paths supply an earlier `order_age_s`, which is retained despite recapturing `captured_at`.

Independent boundary reproduction returned an order receipt ten seconds before terminal capture with `order_age_s=0`.

**Repair:** recompute every available order age against the same terminal capture used by the result and log. Use a consistent conservative freshness origin. Continue leaving receipt/age unavailable when no successful read exists. Preserve existing primary failure reasons.

Add an assertion that advances the clock between earlier validation and terminal capture while the caller supplies an earlier age.

**MF-04 — Terminal freshness recapture can retain a positive action with stale evidence.**

Location: `_prefill_decision`, particularly the ignored `_freshness_failure`.

The repair introduces another clock capture after the successful path’s final freshness checks. It refreshes Gamma age but does not use the resulting freshness failure to invalidate REST.

Deterministic reproduction used the accepted pair, valid exact order rows and a clock that remains at `t` through validation, then reaches `t + 10` at terminal decision capture with the status limit set to ten seconds.

Observed:

```text
action=REST
reason=ACKNOWLEDGED_PAIR_STILL_VALID
captured_at=t+10
market_status.age_s=10
market_status.max_age_s=10
order_age_s=0
order_receipt_ts=t
```

Equality is stale under the existing strict freshness rule. The result therefore certifies REST while its own current evidence is stale, contrary to AC4 and AC8.

**Repair:** make positive action eligibility and terminal evidence use one consistent final capture. A newly discovered freshness/system failure must prevent REST or healthy read-based handoff. Already rejected decisions must retain their primary failure category.

Add exact terminal-boundary tests for status and order freshness, including equality and a value immediately below the limit. Assert returned action, categorical reason and structured evidence agree.

### NOTE

- **MF-02 CLOSED:** global duplicate detection now precedes token filtering. The original cross-token acknowledged-ID collision returns `CLOSE / DUPLICATE_OPEN_ORDER_ID`.
- **MF-03 CLOSED:** the expanded tests cover the previously identified mandatory gaps, including active actual mocked Gamma HTTP cancellation and blocked actual SDK executor teardown.
- This iteration remains a read-only prerequisite, not project readiness.

## Frozen SOP Compliance

Strategy formulas and states remain unchanged. Existing confirmed-fill authority and original FillWindow deadlines are preserved.

A positive REST decision with stale terminal evidence conflicts with Frozen pre-fill reliability requirements and requires correction.

## External Interface Review

No new runtime interface or dependency was introduced. Existing official Polymarket paths are reused.

Every independent test/probe globally blocked DNS/socket access and recorded zero attempts.

## Trading Safety Review

Execution authorization remains fixed false. Evaluation does not create, cancel, reconcile, adopt or replace orders.

The expanded ownership tests verify repeated cancellation waits for actual mocked HTTP work, and teardown waits for actual SDK executor work before completion. No real-money activity occurred.

## Inventory / Capital Accounting Review

Existing processor and capital projection remain authoritative. Exact order reductions stay Decimal-based. No new reservation, second capital admission or inventory adoption was added.

Accounting formulas remain unchanged.

## State Machine Review

REST, FILL_WINDOW and CLOSE remain recommendations within the retained attempt. The repair does not add trading states or release placement blocking.

Read failures still override confirmation. Healthy confirmed handoff preserves its original deadline. Terminal freshness must now be made consistent with the recommended action.

## Test Review

Independent pytest commands used `uv run python` with this guard before `pytest.main(...)`:

```python
import socket, pytest
attempts = []
def blocked(*args, **kwargs):
    attempts.append("blocked")
    raise AssertionError("NETWORK_BLOCKED")
socket.getaddrinfo = blocked
socket.create_connection = blocked
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
```

Exact pytest arguments and results:

```text
["-q", "tests/test_rdp_placement.py", "-k", "prefill"]
49 passed, 168 deselected in 0.74s
NETWORK_ATTEMPTS 0
```

```text
["-q", "tests/test_rdp_placement.py",
 "tests/test_rdp_runtime.py", "tests/test_rdp_lifecycle.py",
 "tests/test_rdp_accounting.py", "tests/test_rdp_capital.py",
 "tests/test_rdp_quotes.py", "tests/test_rdp_book_filter.py",
 "tests/test_favorite_first.py", "tests/test_favorite_first_review.py",
 "tests/test_favorite_live.py"]
627 passed in 65.98s
NETWORK_ATTEMPTS 0
```

Additional checks:

```text
uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_placement.py
git diff --check
sha256sum src/polymaker/rdp_runtime.py tests/test_rdp_placement.py
```

Ruff and diff checks passed. Hashes match the Worker artifact.

Independent fixture probes reproduced original MF-01 and MF-02 closure and the terminal-boundary defect; network attempts were zero.

Worker reports 1,491 passed, 2 skipped on this tree. Reviewer did not repeat the full suite in this repair round; the independent current affected/predecessor run is recorded above.

## Scope Review

Both remaining corrections fit the approved runtime/test scope. No architecture revision, shared-core change, strategy decision or human authorization is needed.

## Residual Risks

Until repaired, terminal evidence can misstate order freshness and recommend REST with stale status. Passing current tests does not resolve these deterministic boundary defects.

Other delivery prerequisites remain deferred under the contract.

## Final Decision

RETURN_TO_WORKER

Complete MF-01 and repair MF-04, preserving the closed MF-02/MF-03 findings, immutable audit history and real-money gates.

# ITER-0022 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`
- `docs/strategy/RDP_PHASE0_FROZEN.md`
- `docs/development/BASELINE.md`
- `docs/development/ITERATION_INDEX.md`
- `docs/development/ITER-0022/01_ARCHITECT.md`
- `docs/development/ITER-0022/02_WORKER.md`
- Actual runtime/test diff and relevant placement ownership, gateway, processor, FillWindow, accounting, capital, Book Filter and quote-builder implementations.

## Repository / Diff Reviewed

Canonical root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `ae8d7168f55793cc7257bc807e346aaa2400acbb`

Implementation files:

| File | Reviewed SHA-256 |
|---|---|
| `src/polymaker/rdp_runtime.py` | `b0fb7c7b12da40cfd6cecb2c900a1c6b559f729b32c71f41a071568312a38c8c` |
| `tests/test_rdp_placement.py` | `03ff4740947da925443be2f0f7a544800924f2209a4cdc70d82d728325b180e3` |

Actual implementation diff: 1,339 insertions, 2 deletions. The index modification and untracked Worker artifact are Orchestrator audit changes. Existing untracked `research_outputs/` remains preserved.

No source, test, audit or Git modifications were performed by Reviewer.

## Acceptance Criteria Review

| AC | Assessment |
|---|---|
| 1 | Healthy unchanged pair returns REST with component evidence. |
| 2 | Existing captured Gamma path and authenticated strict gateway are reused without credential derivation or reconnection. |
| 3 | Implemented pricing/filter invalidation; required boundary, individual filter and dynamic-tick coverage remains incomplete: MF-03. |
| 4 | Health, generation, expiry, backlog and stale-input rejection implemented; additional required adverse coverage remains: MF-03. |
| 5 | Missing orders and selected-order contradictions reject, but duplicate IDs can escape token filtering: MF-02. |
| 6 | Existing confirmation and order-read confirmation handoff tested. Healthy delayed Gamma confirmation coverage missing: MF-03. |
| 7 | MATCHED-only and unadmitted CONFIRMED reject; required quarantine/FAILED coverage missing: MF-03. |
| 8 | Successful reads receive freshness checks; terminal rejection evidence can retain obsolete ages: MF-01. |
| 9 | Attempt mutation rejection tested; direct registry-economics mutation coverage missing: MF-03. |
| 10 | Ownership and repeated cancellation implemented. Required active HTTP, real executor teardown and coordinator-overlap coverage incomplete: MF-03. |
| 11 | No create/cancel calls, execution authorization or replacement release introduced. |
| 12 | Existing interfaces and predecessor behavior pass current regression suite. |
| 13 | Independent targeted and full current-tree checks pass. |

All acceptance criteria are not yet satisfied.

## Existing Codebase Compatibility

The implementation reuses the existing Gamma client, exact gateway reads, placement registry, fill processor, FillWindow, capital projection, Book Filter and NORMAL builder. No working subsystem was replaced or new transport/ledger created.

The substantial runtime addition principally consists of local validation, decision construction and repeated evidence-bearing return paths. It remains within the approved prerequisite. No shared-core or FL/IM behavior changes were found.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — Terminal rejection evidence does not consistently recapture current freshness.**

Location: `_run_initial_prefill_evaluation`, particularly the order-read exception branch around lines 1074–1081 and system-failure returns preceding freshness evaluation.

Deterministic reproduction:

1. Compose the accepted initial pair.
2. Return matching Gamma evidence at clock `t`.
3. During `open_orders`, advance the session clock to `t + 11`.
4. Raise a sanitized test `RuntimeError`.

Observed:

```text
action=CLOSE
reason=ORDER_STATE_READ_FAILED:RuntimeError
captured_at=None
market_status.age_s=0.0
order_age_s=None
```

The available Gamma evidence is eleven seconds old, but the returned result and structured decision event report its earlier zero age. AC8 explicitly requires current terminal ages while preserving the primary failure category.

**Required repair:** recapture terminal evaluation time and freshness of available read evidence on all relevant rejection paths, including read exceptions and post-await health/scope failures. Preserve the original failure reason. Do not fabricate a successful order receipt when the read failed.

Add deterministic delayed read-failure and delayed post-await system-failure assertions covering both immutable results and structured evidence.

**MF-02 — Duplicate acknowledged IDs can escape validation through token filtering.**

Location: selected-order filtering and duplicate detection around lines 1143–1154.

Deterministic reproduction:

```python
rows = _prefill_rows(attempt)
rows.append(replace(rows[0], token_id="foreign-token"))
```

The response now contains the acknowledged `yes-order` twice, with contradictory token identities. The evaluator discards the foreign-token row before checking duplicate IDs.

Observed:

```text
action=REST
reason=ACKNOWLEDGED_PAIR_STILL_VALID
```

This violates AC5 and the requirement that duplicate or contradictory order identities cannot certify resting.

**Required repair:** validate duplicate IDs and contradictory occurrences of acknowledged IDs before selected-token filtering. Preserve handling of unrelated, uniquely identified foreign-market orders without adoption or cancellation.

Add this exact cross-token collision test alongside the existing selected-token duplicate test.

**MF-03 — Mandatory acceptance test matrix is incomplete.**

The new tests establish 28 cases, but several explicit contract scenarios are absent or represented by a weaker fixture:

- PriceGate equality boundary and each Book Filter rejection category; the current rejection test injects only `INACTIVE_YES`.
- Dynamic tick change on either token; current movement cases change bids.
- Malformed order evidence reaching the evaluator.
- Quarantined and FAILED fill evidence.
- Accepted confirmation during a **healthy delayed Gamma read**, retaining the original deadline despite ordinary book movement.
- Direct registered-economics mutation during reads; the current test replaces the attempt.
- Evaluator overlap with the FillWindow coordinator.
- First/repeated cancellation while actual mocked Gamma HTTP remains active.
- Teardown while an actual gateway executor read remains blocked.

`test_initial_prefill_cancellation_drains_real_gamma_and_gateway_executor_reads` exercises cancellation during SDK work after Gamma has already completed and closed. `test_initial_prefill_session_teardown_waits_for_owned_order_read` uses an async fake gateway, so it does not establish real executor teardown.

**Required repair:** add the missing focused cases using existing components and fixtures. Parameterize related boundaries/categories where practical. Assert retained ownership until actual work drains, resource closure, preserved receipt deadline, zero create/cancel calls and zero network attempts.

### NOTE

- A confirmed fill combined with an older full-quantity order response returns CLOSE because the response contradicts current confirmed inventory. Conservative rejection of contradictory order evidence is consistent with the contract’s system-state failure rule; no separate strategy change is requested.
- This iteration remains a read-only prerequisite and does not establish `MICRO_LIVE_READY`.

## Frozen SOP Compliance

The intended pre-fill flow matches Frozen §§19–21: unchanged valid quotes may rest; invalid or unreliable evidence requires scoped closure; accepted confirmed fills use the existing FillWindow.

No strategy formulas, trading states, stop rules or FillWindow duration semantics were changed. MF-02 weakens required order-state reliability and must be corrected before PASS.

## External Interface Review

Only existing official Polymarket HTTP/WebSocket infrastructure is used. No RPC, unofficial service, external runtime dependency or new interface was introduced.

All independent tests and probes blocked and counted DNS/socket connections: zero attempts.

## Trading Safety Review

The new API recommends actions without submitting, canceling, reconciling or replacing orders. Execution authorization remains `init=False`, fixed false. Replacement blocking remains retained.

Read/system failures override confirmation. Overlap checks and shielded task draining are present. Required additional cleanup evidence is identified in MF-03.

No real-money activity occurred.

## Inventory / Capital Accounting Review

The existing processor remains fill authority. Exact gateway quantities remain Decimal through validation, including the tested `1e-25` reduction. No fill is adopted from an order-size change.

Capital projection checks existing commitments without performing a second new-order admission or reservation. Accounting formulas and fixed capital assumptions remain unchanged.

## State Machine Review

REST, FILL_WINDOW and CLOSE are internal recommendations, not additional trading states. Existing NORMAL/REBALANCE transitions and close proof remain unchanged.

The API neither releases the placement owner nor permits another cycle before reconciliation.

## Test Review

All independent pytest executions used this global blocker before invoking pytest:

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
rc = pytest.main(PYTEST_ARGUMENTS)
print("NETWORK_ATTEMPTS", len(attempts))
assert not attempts
raise SystemExit(rc)
```

Executed through `uv run python`:

| Exact pytest arguments | Result |
|---|---|
| `["-q", "tests/test_rdp_placement.py", "-k", "prefill"]` | 28 passed, 168 deselected in 0.60s; network attempts 0 |
| `["-q"]` | 1,470 passed, 2 skipped in 77.44s; network attempts 0 |

Additional exact commands:

```text
uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_placement.py
git diff --check
sha256sum src/polymaker/rdp_runtime.py tests/test_rdp_placement.py
```

Ruff and diff checks passed; hashes are recorded above.

Independent fixture-based probes reproduced MF-01 and MF-02 and inspected confirmation/REST contradiction handling. Their global DNS/socket guard recorded zero attempts.

Passing existing tests does not close the identified defects or mandatory coverage gaps.

## Scope Review

Repairs belong within the approved RDP runtime/test scope. No shared-core changes, architecture revision, strategy interpretation or human decision is required.

## Residual Risks

Until repair, duplicate acknowledged IDs can incorrectly certify REST, and terminal evidence can misrepresent freshness. Several required cleanup and fill-race scenarios lack direct evidence.

Production submission, automatic action coordination, subsequent NORMAL/REBALANCE execution and complete delivery metrics remain deferred as specified by the contract.

## Final Decision

RETURN_TO_WORKER

Repair MF-01 and MF-02, complete MF-03, and return the actual resulting tree for independent re-review. Preserve all existing audit artifacts and real-money gates.

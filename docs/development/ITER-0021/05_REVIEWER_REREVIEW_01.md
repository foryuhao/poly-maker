# ITER-0021 — Reviewer Report

## Verdict

RETURN_TO_ARCHITECT

## Reviewed Inputs

- `AGENTS.md` and `docs/strategy/RDP_PHASE0_FROZEN.md`.
- ITER-0021 `01_ARCHITECT.md`, `02_WORKER.md`, `03_REVIEWER.md` and recovered `04_WORKER_FIX_01.md`.
- Actual five-file implementation/test tree.
- Existing Gamma client/parser, admission rejection paths, freshness checks and read ownership.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `9e250350188abb34c69a566e142ea8e4de178fed`

Implementation remains within five approved files. Orchestrator audit/index changes and preserved `research_outputs/` were distinguished from implementation and left untouched.

| File | Reviewed SHA-256 |
|---|---|
| `src/polymaker/rdp_live.py` | `0d4978151cee8a2a9545c24a240b492f5dd40ee5d52ff50b2442519af20c444b` |
| `src/polymaker/rdp_runtime.py` | `d5e7b8948c275905145bb28b5b772c3876565e9b5da731d2d8cd9539da27f91c` |
| `tests/test_rdp_live.py` | `cadfa3a56203088ddb2e324aa8549ddccba01964e7074b15823007f428388327` |
| `tests/test_rdp_runtime.py` | `7d04de533142c8117ad020f0ae96def3e5dcedccc06eaa6a25562295b8132a44` |
| `tests/test_rdp_placement.py` | `ab8a6c776a2b20d08f85aeac2ff550377d2277b823f48906293b1577a8e996c8` |

Reviewer made no source, test, audit or Git edits.

## Acceptance Criteria Review

| Criteria | Result |
|---|---|
| AC1–5: fresh exact-market lookup and strict validation | Implemented; producer and identity regressions pass. Upstream failure diagnostics still require MF-03. |
| AC6–7: exact and joint freshness | MF-01 closed. Eligible captures reject exact-limit status correctly. Rejection evidence after a failed account read remains stale, MF-02. |
| AC8–10: health, configuration, exclusion and cleanup | Existing guarded regressions pass; no new defect identified. |
| AC11: accurate gate evidence | Partially repaired; delayed failed account reads can still clear the status gate, MF-02. |
| AC12: no trading authority/actions | Satisfied. |
| AC13: regression | Independent current affected suite passes; acceptance defects remain. |

## Existing Codebase Compatibility

The RDP-local numeric precheck uses the shared parser’s existing conversions/defaults and then delegates to the existing validator. It preserves accepted numeric semantics and does not replace market parsing or discovery.

Shared Gamma behavior remains unchanged. Dynamic WebSocket ticks, CLOB minima, FL behavior and existing execution gates remain intact.

The remaining upstream diagnostic issue needs a narrowly approved solution in the existing Gamma path.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-02 remains — Failed account-read returns reuse an earlier status-freshness certification.**

Locations: account-read exception returns in `initial_normal_admission()` and rejection capture.

`market_status_verified` becomes true before the account await. It is recomputed after successful account reads, but exception returns retain that earlier value.

Independent reproduction:

1. Obtain valid status at100s, with maximum age10s.
2. Begin account reads.
3. Advance the clock to111s and raise from the positions read.
4. Inspect the rejected admission.

Observed:

```text
reason=ACCOUNT_READ_FAILED
actual_status_age=11.0
recorded_status_age=0.0
CURRENT_MARKET_TRADABILITY_present=False
```

The admission rejects safely, but its evidence still claims the stale status gate is satisfied.

Required repair: evaluate status freshness for every terminal rejection after an await, including failed account reads and precondition failures. Preserve the primary rejection category while recording current status age/limit and retaining the unresolved gate when status is stale or invalid.

Add a delayed account-read failure test asserting age11, unresolved status gate and unchanged execution authorization. Keep the fresh-status/other-prerequisite rejection case.

**MF-03 remains — Upstream HTTP failure diagnostics can log raw response text.**

Location: `GammaClient.market_by_slug()` failure logging used by `read_rdp_market_status()`.

The original malformed numeric-field probe is fixed. However, upstream diagnostics execute before the RDP numeric guard or helper error handling.

Independent reproduction used an actual fake HTTP response:

```python
httpx.Response(
    503,
    extensions={"reason_phrase": b"private-gamma-payload-sentinel"},
)
```

Observed:

```text
category=MARKET_STATUS_LOOKUP_FAILED
RAW_PAYLOAD_IN_LOG=True
event=market_by_slug_failed
```

The shared client logs `str(HTTPStatusError)`, which contains the untrusted reason phrase. The contract prohibits raw HTTP payload logging.

The smallest recommended compatible repair is a default-off sanitized-diagnostics option in the existing Gamma path, enabled only for RDP status reads. This requires an Architect scope revision because the current contract explicitly prohibits shared Gamma changes.

The revision must preserve existing lookup/parser behavior, default FL diagnostics, official-origin restrictions and client cleanup. Add actual fake-HTTP sentinel coverage for upstream failure diagnostics under default and configured logging. Do not introduce a replacement client or global logger mutation.

### NOTE

**MF-01 closed:** Both original exact-limit reproductions now reject with `MARKET_STATUS_STALE`, including realistic epoch timestamps. Below-limit producer evidence still passes.

The original stale-gate and malformed-numeric-payload probes also pass. Their broader failure-path gaps are described above.

No additional exception-context finding is requested.

## Frozen SOP Compliance

Exact freshness now follows the approved strict boundary without tolerance. No strategy quantities, pricing, capital policy or runtime states changed.

The remaining gate-evidence and diagnostic defects require implementation repair, not a Frozen SOP revision.

## External Interface Review

Official Gamma exact-slug GET and redirect restrictions remain unchanged. No new runtime dependency or external interface was introduced.

Independent verification recorded zero DNS/socket attempts.

## Trading Safety Review

Rejected admission remains ineligible and execution authorization remains fixed false. No order-create, cancel or coordinator activation was added.

MF-02 inaccurately clears readiness evidence; MF-03 exposes untrusted response content through diagnostics. Neither reproduction performed real actions.

## Inventory / Capital Accounting Review

Frozen accounting, fixed-$30 backing and approved NORMAL reserve remain unchanged. Eligible decisions still require final account/status freshness checks.

MF-02 concerns rejected evidence after account failure, not inventory adoption or capital arithmetic.

## State Machine Review

Admission serialization and retained read-task ownership remain intact. Cancellation and teardown regressions pass.

Status evidence must be refreshed at rejection capture as well as successful capture.

## Test Review

Independent current-tree execution used an inline `uv run python` DNS/socket guard, then:

```python
pytest.main([
    "-q",
    "tests/test_rdp_live.py",
    "tests/test_rdp_runtime.py",
    "tests/test_rdp_placement.py",
    "tests/test_rdp_account.py",
    "tests/test_rdp_accounting.py",
    "tests/test_rdp_capital.py",
    "tests/test_rdp_lifecycle.py",
    "tests/test_execution.py",
    "tests/test_favorite_first.py",
    "tests/test_favorite_first_review.py",
    "tests/test_favorite_live.py",
])
```

Result: **703 passed in 67.42 seconds; NETWORK_ATTEMPTS=0**.

Independent guarded repetitions verified the original three repairs and reproduced the two remaining gaps: **NETWORK_ATTEMPTS=0**.

Scoped Ruff and `git diff --check`: **passed**.

Recovered Worker current full-suite verification: **1,434 passed, 2 skipped; zero network attempts**. Reviewer did not repeat the full suite during this repair review.

Current tests miss delayed failed-account rejection freshness and upstream HTTP-response diagnostic payloads.

## Scope Review

MF-02 is an ordinary runtime/test repair.

MF-03’s recommended minimal repair changes shared Gamma diagnostics, which the approved contract excludes. Architect must authorize that narrow extension and required default-compatibility tests before implementation.

No human strategy or external-interface decision is required.

## Residual Risks

Fresh Gamma evidence applies to one capture; final submission revalidation remains required.

The repository remains short of project-level MICRO_LIVE_READY. Mocked verification establishes no live trading or economic evidence.

## Final Decision

RETURN_TO_ARCHITECT for a narrow diagnostic-scope revision addressing MF-03. Include MF-02’s ordinary runtime repair in the subsequent Worker work, then independently re-review the actual resulting tree. No human intervention is required.

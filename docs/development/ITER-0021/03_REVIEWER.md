# ITER-0021 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md` and `docs/strategy/RDP_PHASE0_FROZEN.md`.
- ITER-0021 `01_ARCHITECT.md` and `02_WORKER.md`.
- Actual five-file implementation/test diff.
- Existing Gamma exact-slug client/parser, admission serialization, account freshness, retained input health and cleanup ownership.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `9e250350188abb34c69a566e142ea8e4de178fed`

The earlier contract planning HEAD is distinguished from the actual implementation baseline. Implementation changes remain within five approved files. Orchestrator audit/index changes and preserved `research_outputs/` were left untouched.

| File | Reviewed SHA-256 |
|---|---|
| `src/polymaker/rdp_live.py` | `f1d50ef0e4445fccc2be81be4676046029d3f343285debae41f1e6665471bcf7` |
| `src/polymaker/rdp_runtime.py` | `f750e5ebd7a0e600d15fea7b9e4c983e9849683fdefd6dc5b42e63ab004df63a` |
| `tests/test_rdp_live.py` | `966ddad5d495713314d93b9c6094ab6fd30ccc9435f7c030384e0b1a955fc25c` |
| `tests/test_rdp_runtime.py` | `7d04de533142c8117ad020f0ae96def3e5dcedccc06eaa6a25562295b8132a44` |
| `tests/test_rdp_placement.py` | `e76dead3b0bac5aa3fb76f0bf3dddd628c0cc88edf5a5ea5dddd93057dda6761` |

Reviewer made no source, test, audit or Git edits.

## Acceptance Criteria Review

| Criteria | Result |
|---|---|
| AC1–2: unchanged admission and fresh exact-market read | Implemented; actual Gamma/respx admission and repeated reads pass. |
| AC3–5: strict status, identity and failed/malformed lookup | Implemented; invalid inputs reject without changing bootstrap identity. Diagnostics require MF-03. |
| AC6–7: exact freshness and joint evidence | Integer-limit cases pass, but fractional exact-limit evidence can admit, MF-01. |
| AC8: health, generation, expiry and closure | Existing post-await and final synchronous checks remain active; regressions pass. |
| AC9: captured configuration and official origin | Implemented; pre-await snapshot and redirect rejection are covered. |
| AC10: serialization, cancellation and HTTP cleanup | Current tests pass, including actual client cleanup after repeated cancellation. |
| AC11: accurate status-gate evidence | Defective for stale/invalid status, MF-02. |
| AC12: no execution authority or autonomous actions | Satisfied. |
| AC13: regression | Full current suite passes; acceptance defects remain despite passing tests. |

## Existing Codebase Compatibility

The change reuses the existing Gamma client and RDP validation. No discovery, execution, accounting or reconciliation subsystem was duplicated.

Dynamic WebSocket ticks and CLOB minimum-size authority remain unchanged. Shared Gamma defaults and FL behavior were not modified. No IM dependency was introduced.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — Binary-float subtraction admits status at the exact age limit.**

Location: `_evaluate_market_status_freshness()`.

Independent full-admission reproduction:

```text
receipt_ts=100.0
evaluation_ts=100.1
maximum_age=0.1
computed_age=0.09999999999999432
eligible=True
reason=None
```

The contract requires `0 <= age < limit`; equality must reject.

A second reproduction used evidence produced by the actual Gamma helper over fake HTTP:

```text
receipt_ts=2000000102.0
evaluation_ts=2000000102.1
maximum_age=0.1
computed_age=0.09999990463256836
rejection_reason=None
```

Required repair: compare validated timestamps and limits using exact decimal arithmetic, consistent with existing account-freshness handling. Do not introduce an epsilon or widen the boundary.

Add fractional-limit tests immediately below, exactly at and above the boundary, including realistic epoch values and actual producer evidence.

**MF-02 — Stale or invalid status incorrectly clears the unresolved status gate.**

Location: `_admission_rejected()`.

The implementation removes `CURRENT_MARKET_TRADABILITY` whenever `market_status` is non-None, regardless of whether that evidence passed freshness validation.

Independent reproduction:

```text
receipt_ts=100.0
evaluation_ts=100.2
maximum_age=0.1
eligible=False
reason=MARKET_STATUS_STALE
CURRENT_MARKET_TRADABILITY_present=False
```

The existing age-matrix test explicitly asserts this incorrect gate removal.

Required repair: retain rejected evidence for diagnostics, but clear the status gate only when matching, explicitly tradable evidence is valid and fresh for the capture. Stale, future, invalid or unavailable status must leave it unresolved.

Update the age-matrix assertions. Also verify that a failure in another admission prerequisite may retain successfully validated fresh status without clearing unrelated gates.

**MF-03 — The new status path emits raw malformed HTTP payload content through the reused parser logger.**

Location: `read_rdp_market_status()` calling `_validate_market()` / `parse_market()`.

Independent fake Gamma response supplied a valid selected identity/status with:

```text
orderPriceMinTickSize="private-gamma-payload-sentinel"
```

Observed:

```text
category=INVALID_GAMMA_MARKET
RAW_PAYLOAD_IN_LOG=True
event=parse_market_failed
```

The parser’s logged conversion exception includes the payload value verbatim. This violates the contract’s categorical diagnostics and no-raw-HTTP-payload-logging requirement.

Required repair: provide compatible sanitization for the RDP status-read path while preserving existing shared defaults and reusing the existing client/parser. Add a malformed-payload sentinel test inspecting actual emitted diagnostics, not only the returned exception.

Do not duplicate Gamma lookup/parsing or silently modify shared behavior. If the minimal compatible repair requires a shared-core change outside this contract, return to Architect for a scoped revision.

### NOTE

A transport-error probe confirmed that the helper’s message is sanitized and `from None` suppresses raw context in formatted tracebacks. Its `__context__` still retains the original exception. No separate surfaced-context defect was established; no additional repair is requested for that observation.

The Worker’s earlier interrupted unguarded run is disclosed. All independent verification in this review used DNS/socket blocking.

## Frozen SOP Compliance

Fresh status supports the existing MarketTradable prerequisite without adding strategy filters or changing quantities.

MF-01 weakens the approved freshness boundary. MF-02 misstates readiness evidence. Neither requires a strategy-semantic decision.

## External Interface Review

The new read uses the approved official Gamma origin and existing exact-slug GET. Redirects do not reach alternate services.

No new runtime interface or dependency was introduced. Independent verification recorded zero network attempts.

## Trading Safety Review

Eligibility grants no send authority. No create/cancel/coordinator activation was added, and selected account state remains subject to existing rejection rules.

Freshness must fail closed at the exact configured boundary. Rejected evidence must not claim an unresolved gate is satisfied.

## Inventory / Capital Accounting Review

Frozen quantities, accounting, fixed-$30 backing and approved NORMAL reserve are unchanged.

Account and market-status freshness are rechecked in the final synchronous capture. The new status arithmetic requires MF-01; existing account arithmetic remains intact.

## State Machine Review

The change adds an admission prerequisite, not a new state or trading controller. Placement and close coordination remain inactive during reads.

HTTP read ownership persists through cancellation and client cleanup. Existing admission exclusion remains in force.

## Test Review

Independent current-tree full suite used an inline `uv run python` guard blocking/counting:

- `socket.getaddrinfo`
- `socket.create_connection`
- `socket.socket.connect`
- `socket.socket.connect_ex`

It executed:

```python
pytest.main(["-q"])
```

Result: **1,422 passed, 2 skipped in 76.47 seconds; NETWORK_ATTEMPTS=0**.

Independent guarded probes reproduced MF-01, MF-02 and MF-03, including actual Gamma/respx producer evidence: **NETWORK_ATTEMPTS=0**.

Scoped Ruff across all five files and `git diff --check`: **passed**.

An initial standalone probe harness failed before exercising production behavior because the fixture now requires a `monkeypatch` argument. The harness was corrected and the reproductions completed.

Current tests cover many valid failure paths but miss fractional exact-limit arithmetic and raw parser diagnostics, and codify the incorrect stale-status gate assertion.

## Scope Review

Implementation remains within the approved five-file scope. No shared-core replacement or external-interface expansion occurred.

MF-01 and MF-02 are local implementation repairs. MF-03 should use a compatible RDP-local solution; any necessary shared-core extension requires Architect authorization.

## Residual Risks

Status can change immediately after capture. Final submission revalidation remains deferred and mandatory.

Passing mocked tests establish no live order acceptance or strategy-economic evidence.

## Final Decision

RETURN_TO_WORKER. Repair MF-01 through MF-03 and add the specified meaningful tests, then independently re-review the actual resulting tree. No human decision is currently required.

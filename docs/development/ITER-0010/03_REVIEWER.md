# ITER-0010 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- Active `AGENTS.md`, Frozen SOP, `01_ARCHITECT.md` and `02_WORKER.md`.
- Actual adapter, composed preflight, CLI changes and tests, including untracked files.
- Existing market-data transport, consumer, generation handling, parsers and books.

## Repository / Diff Reviewed

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `7c567154b3dc4fc461aae49aa44505ba7b78fdc7`.
- Changes remain within the five authorized implementation/test files plus Orchestrator-owned audit/index records.
- Preserved `research_outputs/` remains untouched.
- `git diff --check`: passed.

Reviewed SHA256:

```text
c525376766c177f82b20d7f3d6e1c34d3bb3e29942aceb24c7346a40ff84d2d0  src/polymaker/rdp_marketdata.py
44c8e87e68222676a183d4d56ea14f24c7bc9820f31555f3cbf83191a6961dfb  src/polymaker/rdp_live.py
1f73f815b28c1ef2820aa7da5853ee9a02840044da27a198c7df091e44408100  src/polymaker/cli.py
a356505f26d996c1558c3db157051414f8c32e038e541f98fbdea15aeb598d38  tests/test_rdp_marketdata.py
1c3b21c4a39ae8a8d33fb2f3a1fc4da05ce571efb1ec7f899f7a012a0e61576b  tests/test_rdp_live.py
```

## Acceptance Criteria Review

| AC | Assessment |
|---|---|
| 1 | PASS — exact selected-token subscription. |
| 2 | PASS — connection-only, incomplete, empty, crossed and obsolete books cannot normally pass. |
| 3 | Incomplete — capture can succeed after the owned service has failed. |
| 4 | PASS — reconnect requires both current-generation snapshots. |
| 5 | PASS — disconnected state rejects readiness. |
| 6 | Incomplete — JSON numeric precision is lost before validation. |
| 7 | Incomplete — explicit null snapshot ticks silently use seeded metadata. |
| 8 | PASS — tick changes update provenance and wake observation. |
| 9 | Incomplete — failed tasks and expired startup deadlines can still certify success. |
| 10 | PASS — HTTP and WS evidence are distinguishable. |
| 11 | PASS — shared service/default predecessor behavior is unchanged. |

## Existing Codebase Compatibility

The adapter reuses shared transport, subscriptions, consumer, parsers and books. It does not introduce a second WebSocket implementation or replace execution infrastructure. No shared service/parser modification, IM dependency or FL behavior change occurred.

## Findings

### BLOCKER

NONE.

### MUST_FIX

**MF-01 — JSON numbers lose precision before representability validation.**

`_process_raw()` uses ordinary `json.loads`. This selected snapshot is accepted:

```text
price: 0.4000000000000000001
size: 3.0000000000000000001
```

The book receives `.4` and `3.0`. `_number()` cannot detect information already lost during JSON decoding.

Preserve numeric literals exactly during validation, then delegate safely to existing mutation. Add raw numeric-literal regressions proving unsupported precision fails before any selected mutation, including an invalid later entry in a multi-message/frame case. Ordinary supported strings and numeric literals must remain valid.

**MF-02 — Explicit null snapshot tick bypasses validation.**

`_validate_book()` skips `tick_size=None`; the frame then mutates the book and retains `REST_BOOTSTRAP` provenance. The contract allows an absent field but requires explicit snapshot ticks to be finite and positive.

Reject an explicitly null tick before mutation. Add a regression distinguishing absent tick acceptance from null tick rejection.

**MF-03 — Failed owned service can certify readiness.**

`await_rdp_feed()` checks `_ready()` before `task.done()`. An owned service that populates both books and then raises `RuntimeError` returns successful readiness; cleanup subsequently suppresses its exception through `gather(return_exceptions=True)`.

Check owned-task termination/failure before certification and detect termination while waiting. Add tests for failure after populating books and failure without a readiness notification. Both must fail explicitly and await cleanup rather than return success.

**MF-04 — Startup deadline is checked after readiness.**

After waking, `_ready()` is tested before the monotonic deadline. A controlled service that blocks processing and publishes books late returned success at approximately `.041` seconds with a `.005`-second startup timeout.

Enforce the startup deadline before accepting/capturing readiness, including after the final wait. Add a late-publication regression and verify cleanup. This remains a connection-preflight deadline; no strategy timer change is required.

All repairs fit the approved local scope.

### NOTE

Immutable capture, token-local tick provenance and normal reconnect cleanup are otherwise correctly implemented.

## Frozen SOP Compliance

No trading strategy semantics, filters, quantities or NORMAL/REBALANCE transitions are changed. The reproduced defects concern public evidence validation and connection-preflight safety.

## External Interface Review

The composed command uses the existing official market WebSocket alongside reviewed official HTTP bootstrap. No account, RPC, order or cancellation interface is introduced.

## Trading Safety Review

The CLI remains preflight-only. No Engine, gateway connection, credentials or trading authority is introduced. Validation used mocked HTTP and fake WebSockets only. Feed success must be repaired so stopped or overdue services cannot certify readiness.

## Inventory / Capital Accounting Review

No inventory or capital mutation. Correct numeric frame validation is required before later Book Filter/accounting integration consumes these books.

## State Machine Review

No trading state machine is introduced. Connection generations and selected snapshot generations normally prevent old/new book pairing. Service termination and startup-deadline checks need the repairs above.

## Test Review

Independent current-tree results:

```text
uv run pytest -q tests/test_rdp_marketdata.py tests/test_rdp_live.py
77 passed

uv run pytest -q tests/test_marketdata.py tests/test_orderbook.py tests/test_catalog.py tests/test_btc5m.py tests/test_live_marketdata.py
34 passed, 2 skipped

uv run pytest -q
1032 passed, 2 skipped

uv run ruff check src/polymaker/rdp_marketdata.py src/polymaker/rdp_live.py src/polymaker/cli.py tests/test_rdp_marketdata.py tests/test_rdp_live.py
All checks passed!

uv run polymaker rdp-live --help
Passed
```

Existing tests cover ordinary readiness, malformed string values, atomic delta validation, reconnects, cancellation and cleanup. They omit the reproduced numeric-literal, null-tick, task-failure and late-readiness cases.

## Scope Review

The implementation remains within approved adapter/composition scope. No architecture revision or human decision is needed.

## Residual Risks

Feed capture is momentary. Continuous reliability, trading-time tick certification, account readiness and lifecycle composition remain deferred.

## Final Decision

RETURN_TO_WORKER.

Repair MF-01 through MF-04, add the specified regressions and return for independent review. Real-money authorization remains NOT_GRANTED.

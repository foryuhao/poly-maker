# ITER-0018 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented `docs/development/ITER-0018/01_ARCHITECT.md` only. The change composes the existing close barrier with the retained RDP placement owner and capital projection. No strategy semantics or external interfaces changed.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Base HEAD, unchanged: `977b5739938cfeccd14ef6bfdeaf8ab3901950b7`
- Working tree initially contained only the preserved, untracked `research_outputs/` tree.
- No Git history operations performed.

## Existing Components Reused

- `RDPPlacementOwner` and its acknowledged-order registry
- Retained processor, store, and gateway
- `close_rdp_cycle` and `RDPCloseResult`
- Strict authenticated account-order reads
- Existing reconciliation, confirmed-fill accounting, and `project_rdp_capital`
- Existing `RDPMode` classification and session cleanup

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `src/polymaker/execution/rdp_placement.py`
- `tests/test_rdp_placement.py`

The untracked `research_outputs/` tree was preserved unchanged.

## Implementation Summary

Added an explicit, deadline-bounded close operation for the current resolved placement attempt. It passes only safely acknowledged IDs, verified against the existing registry, to the existing close barrier. Its thin gateway adapter requests strict exact account-order evidence and delegates cancellation and reconciliation reads to the retained gateway.

Closure evidence is recorded additively. A complete, anomaly-free initial pair can release its block only when the barrier and capital proof agree with fresh inventory, no owned local order remains open, and no owned fill evidence is unresolved. Partial or unknown attempts remain blocked even when their known subset closes. The result exposes barrier, capital, mode, and block evidence while keeping `execution_authorized=False`.

Active close work is owned by the session and drained during cancellation or teardown. No CLI or ordinary cleanup path initiates cancellation.

## Behavior Before

The retained placement owner could register acknowledgments and keep unresolved attempts blocked, but the session did not compose explicit cancellation, reconciliation, and post-close capital proof for owned orders.

## Behavior After

The explicit close operation uses the existing barrier and capital projection. It cancels only safely owned open IDs, accounts for confirmed fills and races through existing reconciliation, and retains the replacement block whenever closure or current-state proof is incomplete.

## Tests Added / Changed

Extended `tests/test_rdp_placement.py` with retained-session composition using a fake SDK behind the real strict gateway and existing barrier. Coverage includes:

- Complete initial pair, zero fills, partial confirmed fills, and fills during cancellation
- NORMAL/REBALANCE classification below, at, and above the five-share boundary, for both inventory signs
- Strict account-order read failure and malformed cumulative fields
- Cancellation uncertainty, remaining orders, pending confirmation, quarantined fills, and terminal checksum changes
- Partial/unknown known-subset closure, unknown zero-ID attempts, and observed-only or foreign orders
- Wrong attempt identity, expired deadline, stale proof, and processor changes during the final read
- Lost input generation, overlapping close attempts, repeated cancellation, and session teardown

## Tests Executed

- `uv run pytest -q tests/test_rdp_placement.py -k 'matched_only_fill_remains or contradictory_confirmed_fill' --tb=short`
- `uv run pytest -q tests/test_rdp_placement.py --tb=short`
- `uv run pytest -q tests/test_rdp_lifecycle.py tests/test_rdp_runtime.py tests/test_rdp_capital.py tests/test_filltruth.py tests/test_rdp_accounting.py --tb=short`
- `PYTHONPATH=/tmp uv run pytest -q -p rdp_network_block`
- `uv run ruff check src/polymaker/rdp_runtime.py src/polymaker/execution/rdp_placement.py tests/test_rdp_placement.py`
- `git diff --check`

The full-suite guard was written to `/tmp/rdp_network_block.py` with this content:

```python
"""Pytest guard for RDP development: fail and count all socket/DNS calls."""
import socket

_ATTEMPTS = []


def _blocked(name):
    def deny(*_args, **_kwargs):
        _ATTEMPTS.append(name)
        raise AssertionError(f"network operation blocked: {name}")
    return deny


def pytest_configure(config):
    socket.socket.connect = _blocked("socket.connect")
    socket.socket.connect_ex = _blocked("socket.connect_ex")
    socket.create_connection = _blocked("socket.create_connection")
    socket.getaddrinfo = _blocked("socket.getaddrinfo")


def pytest_sessionfinish(session, exitstatus):
    print(f"RDP_NETWORK_GUARD_ATTEMPTS={len(_ATTEMPTS)} names={_ATTEMPTS!r}")
    if _ATTEMPTS:
        session.exitstatus = 1
```

## Test Results

- Focused added-case tests: 2 passed.
- Final placement module: 52 passed.
- Final network-guarded full suite: 1,293 passed, 2 skipped in 74.90s.
- Network guard: `RDP_NETWORK_GUARD_ATTEMPTS=0 names=[]`.
- Ruff: passed.
- `git diff --check`: passed.

During development, initial focused runs exposed fixture/assertion issues: the fake SDK lacked a `Decimal` import; expected close-ID order did not match sorted barrier output; zero-fill inventory formatting needed numeric comparison; and several assertions did not reflect existing gateway/capital behavior. These were corrected. The final tree passed the checks above.

## Regression Results

The affected lifecycle, runtime, capital, fill-truth, and accounting regressions passed: 256 passed in 62.13s. The full suite also passed under the network guard.

## Git Diff Summary

- Three scoped tracked files changed: 1,057 insertions and 5 deletions.
- HEAD remains `977b5739938cfeccd14ef6bfdeaf8ab3901950b7`.
- Final SHA-256:
  - `rdp_runtime.py`: `46183b8c432343a46431c2c357ed1f28a73acf12a303787fb933cfd1dd16bbe4`
  - `rdp_placement.py`: `4d28ec394d5ff38b781bcec7871865e6c4166d58164c2bd449991a0138cc59ee`
  - `test_rdp_placement.py`: `2a2eec5037f2bff0ee34726b1fa2d2126eb170aaac849d20c12487ed225f876c`
- No files staged, committed, or pushed.

## Safety Checks

- All cancellation and gateway scenarios used fakes.
- Full suite socket and DNS guard recorded zero attempts.
- No real order, cancellation, signing, or account action was performed.
- Execution authorization remains false.
- Existing research assets were preserved.

## Known Limitations

This adds an explicit close operation only. It does not schedule automatic closure, recover unknown placement outcomes, or compose subsequent NORMAL/REBALANCE actions.

## Deferred Findings

FillWindow scheduling, gated submission, later-cycle orchestration, unknown-placement recovery, continuous account reliability, and complete runtime metrics remain outside this contract.

## Reviewer Attention

Please verify that closure release remains proof-bound to current processor/store evidence, that partial or unknown attempts never unblock after known-subset closure, and that repeated cancellation or teardown cannot detach active close work.

## Human Live Trading Performed

NO

# MI M2B-0B Cash / Reservation Contract Review

Verdict: PASS — final independent Re-review on 2026-10-05; initial Review NEEDS FIX history preserved below
Reviewed predecessor baseline: M2B-0A final Re-review PASS for its uncheckpointed working tree; Git HEAD `f25a5a539f711f2dcfe492105c7d92bbfbe812f7` is the M2A baseline
Reviewed commit / working tree: `f25a5a5` plus uncommitted M2B-0A and M2B-0B files on `m4-dynamic-ev`, 2026-10-05
Architecture reference: `docs/architecture/MI_ARCHITECTURE.md`, M2B-0B supplement
Implementation report reference: `docs/reports/MI_M2B_0B_IMPLEMENTATION_REPORT.md`

Reviewed untracked source/test fingerprints (SHA-256):

```text
aacf685acbbbe7043ae3e98bf4a8370727156320d9803991b030ae9de13b1799  src/polymaker/mi_cash_reservation.py
78eedccaf7b8799bbc826e7937b9908791b9df9d594858163c89396322d3016b  tests/test_mi_cash_reservation.py
```

## Scope reviewed

The untracked M2B-0B module, tests and implementation report were read
directly, along with the M2B-0B architecture/status changes, the existing
`ExecutionGateway` collateral/open-order read methods, installed official
`py_clob_client_v2` pagination, M2B-0A Gate identity, and M1/M2A baseline
diff. No source or test file was edited during Review.

## Findings

### P0

- None found in the official read path or a single reservation book.

### P1 — BLOCKER: separate books double-reserve one funder's cash

`MICashReservationBook.__init__()` creates a fresh lock, `_records`, and
`_active` for every instance (`mi_cash_reservation.py:111-114`). The
check-and-create lock in `reserve()` only serializes calls to that *one*
instance (`:147-172`). No process-level owner, shared registry, or MI
composition root exists. A read-only offline probe created two books in the
same process, supplied the same confirmed `$25` evidence for one identity,
reserved two different markets, and received two ACTIVE `$25` records:

```text
separate_books_same_25: ACTIVE ACTIVE total_reserved=50000000
```

Thus the stated “same capital cannot back two ACTIVE MI reservations inside
the current process” is false for ordinary callers that instantiate the
public class twice. Documentation's instruction that a *future* runtime
should share one book is caller responsibility, not an enforced contract.
This is a direct failure of the M2B-0B closure invariant. The minimal repair
must establish an explicit, testable single process reservation authority
for each collateral pool or otherwise make separate ordinary book instances
share the same in-process accounting. No distributed lock or database is
required.

### P2

- `ConfirmedSpendableCollateral` is a public constructible evidence object.
  Future MI wiring must use the official reader and revalidate cash at its
  actual write boundary. This is documented and is not a separate current
  blocker while no MI execution path exists.

## Architecture compliance

The new module reads `ExecutionGateway.balance_allowance()` which calls the
official CLOB SDK `get_balance_allowance(COLLATERAL)`. Its `open_orders()`
delegates without a market/token filter to `get_open_orders()`; the installed
SDK default `only_first_page=False` traverses cursors until its end marker.
The reader denies nonempty, failed, malformed, or unavailable order/collateral
responses. [Polymarket's order lifecycle documentation](https://docs.polymarket.com/concepts/order-lifecycle)
states available order size is balance less existing open-order commitments.
Requiring *zero account-wide open orders* therefore makes use of the reported
balance conservative. Allowance remains separate from collateral. Cash truth
is VALID under this explicit zero-order condition; no new external source is
introduced.

Amounts remain integer micro-units. `$24.999999` is denied and `$25.000000`
is admitted with no binary-float conversion. Within one book, ACTIVE amounts
are aggregated by canonical funder, while record ownership uses full
`LiveExecutionIdentity` plus normalized `condition_id`. Same-funder signers
cannot allocate another `$25` from a `$40` pool after the first reservation.
The record ID and identity/market checks prevent another identity or market
from releasing a reservation. Duplicate release and stale old IDs do not
credit or release a newer record. The local reservation sum, eligibility check,
and record creation within one book are under one `RLock`; official account
reads occur before that boundary.

## Safety / regression assessment

M2B-0A Gate, Engine, FavoriteFirst, and their tests match the exact hashes
recorded by the M2B-0A final Review. FavoriteFirst's `$12` rule remains in
its own preflight. M1 and M2A source/tests have no diff from `f25a5a5`.
M2B-0B is not imported by the MI runner or CLI and adds no MI execution,
order post/cancel, persistence, RPC, or third-party data path. No real market,
authenticated trade write, order, or cancel was run.

## Tests reviewed / executed

- M2B-0B: 21 passed.
- Generic Gate + FavoriteFirst: 75 passed.
- M1 + M2A: 76 passed.
- Related Engine/gateway: 39 passed.
- Full suite: 811 passed, 2 skipped.
- Ruff on M2B-0B source/test: passed. Mypy on new source: passed.
- `git diff --check`: passed.
- Additional offline probe: two separate books each reserved the same `$25`
  as ACTIVE; one shared book correctly denied a second same-funder `$25`
  reservation from `$40` and denied cross-identity release.

Passing tests do not close the untested multi-instance process boundary.

## Untracked artifact audit

`src/polymaker/mi_cash_reservation.py`, `tests/test_mi_cash_reservation.py`,
and both M2B reports are untracked, so ordinary `git diff` omits them.
Pre-existing `livecfg/config.toml` and `research_outputs/` are unrelated and
were not altered. The M2B-0A checkpoint remains pending.

## Required fixes

1. Enforce one in-process collateral reservation authority across ordinary MI
   callers for the same funder; prove two separately initiated market flows
   cannot both reserve the same `$25`.
2. Add a regression covering two independent book acquisitions/owners in one
   process, as well as existing same-book atomicity.

## Deferred issues

Reservation persistence, cross-process coordination, external-actor cash
changes, order lifecycle release, and MI execution remain outside M2B-0B.
The future execution path must require a current matching ACTIVE record and
perform its own final cash/write checks.

## Final verdict

M2B-0B REMAINS OPEN. Independent Review: NEEDS FIX. The process-local
double-allocation claim cannot be certified from the current implementation.

## Final Re-review — 2026-10-05

Verdict: PASS for the exact uncommitted working tree identified below. This
section supersedes the initial verdict for the repaired source/test tree;
the initial finding above remains historical evidence.

Reviewed predecessor: M2B-0A final Re-review PASS for its uncheckpointed
working tree; Git HEAD remains M2A checkpoint `f25a5a5`. No M2B-0A or M2B-0B
Git checkpoint was created by this Review.

Repaired source/test fingerprints (SHA-256):

```text
8cc43023a86efbf3ff66492ac6459c76fb6bf5621e1b30f87120d9ba32809d9d  src/polymaker/mi_cash_reservation.py
e3ef62b34b2a5e28a5a7d1e7379c27ca1966ea754b948270d42b264893f1e5bc  tests/test_mi_cash_reservation.py
```

### Blocker closure

`_PROCESS_AUTHORITY` is instantiated once at module scope. It owns the
`RLock`, reservation records, ACTIVE index, and reservation ID namespace.
`MICashReservationBook` has no instance state (`__slots__ = ()`) and no
constructor parameter for another backend. Every normal method takes the
module authority and uses its shared lock for the ACTIVE funder sum,
eligibility check, and record creation or release. The private authority is
replaced only by a test fixture; there is no public production reset or
backend-injection API.

Independent offline A–H probes, run without the test fixture, all passed:

- A: two books, same $25/funder, different markets: second denied, remaining 0.
- B: same identity and market across books: one reservation ID, remaining 0.
- C: different signers/same funder, $40: second $25 denied, remaining $15.
- D: different funders each with $25: both ACTIVE independently.
- E: a second book released the first book's record with matching ID/owner/market;
  capacity restored once.
- F: wrong owner with the same funder could not release; record stayed ACTIVE.
- G: releasing old R1 after creating R2 did not release R2.
- H: concurrent different-market reserve through two books admitted exactly
  one $25 record and left zero effective capacity.

The cash reader still uses official CLOB collateral balance and requires an
empty account-wide open-order result. Integer micro-unit boundaries, fixed
25,000,000 micro-unit amount, identity/market ownership, and fail-closed read
behavior are unchanged. Gateway open-order reads remain unfiltered and the
installed official SDK traverses all pages. The process guarantee is limited
to one Python process; no persistence, restart or cross-process claim is made.

### Safety and regression

The M2B-0A Gate, Engine, FavoriteFirst, and corresponding test hashes still
match the M2B-0A final Review. FavoriteFirst's $12 rule remains in its own
preflight. M1 and M2A source/tests have no diff from `f25a5a5`. No MI
execution path, order post/cancel, dynamic capital model, or new external
interface was added. No real market, order, cancel, or authenticated trade
write was run.

### Tests reviewed / executed

- M2B-0B: 27 passed.
- Generic Gate + FavoriteFirst: 75 passed.
- M1 + M2A: 76 passed.
- Related Engine/gateway: 39 passed.
- Full suite: 817 passed, 2 skipped.
- Ruff on touched M2B-0B source/test: passed.
- Mypy on touched M2B-0B source: passed.
- `git diff --check`: passed.
- Independent offline A–H probe: all eight expected outcomes observed.

### Untracked artifact audit and handoff

The repaired M2B-0B source, tests, implementation report, and this Review are
untracked. Ordinary `git diff` does not include them. The pre-existing
`livecfg/config.toml` change and `research_outputs/` remain excluded. A
checkpoint must include the exact reviewed source/test/document tree and
verify the pending M2B-0A predecessor; this Review makes no commit or tag.

### Final verdict

M2B-0B CLOSED — final independent Re-review PASS for the exact working tree
fingerprinted above. The initial process-local double-allocation blocker is
closed. MI micro-live execution remains a separate, unauthorized next slice.

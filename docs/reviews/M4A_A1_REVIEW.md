# M4A-A1 Independent Review

Current verdict: **PASS — independent re-review after P1 repair**.
Initial verdict: **NEEDS FIX** (preserved below).
Reviewed predecessor baseline: `m4a5-pass` / `4d75a19bf4f4e0a967d711cb57f651453f2fdd30`.
Reviewed commit / working tree: branch `m4-dynamic-ev` at `4d75a19`, with the tracked A1 source/test/status changes and the untracked files listed below; no A1 commit or tag.
Architecture reference: `docs/architecture/M4A_ARCHITECTURE.md`, AE-1 section A.
Implementation report reference: `docs/reports/M4A_A1_IMPLEMENTATION_REPORT.md`.

## Scope and verdicts

Root cause independently confirmed: **YES**. On the actual `CONTROLLED_CONFIG`, the old wrapper expression hashes projected compact JSON to `5709c9932ff81a3286828d7daae1b7314118a5b0289c10390b47d5956bef2ff7`; the old Engine expression hashes raw dataclass JSON with default spacing to `9aa0693fbe98e3da7a38a8a0426b89743b680f26bf6987e9cf837ee96776878c`.

Single canonical config identity: **FAIL** for the supplied-context Engine path (P1 below); **PASS** for the normal live wrapper path.
Manifest uses session config_id: **PASS**.
Episode uses same session config_id: **PASS** for a valid context.
Auditor strictness preserved: **PASS**.
Real integration regression: **PASS** to Engine attachment, capture, Episode lifecycle and real writer; the accepted FIRST event is injected into capture, so gateway/UserEventProcessor are outside this test.
Research audit identity gate: **PASS** on the normal FIRST-fill fixture; a separately mutated Episode ID returns integrity `FAIL` with gate `identity`.
Live trading semantics unchanged: **PASS**; no entry, PairStop, Runner, pricing, quantity, reprice, risk or order/cancel logic changed.
Actual M4A readiness: **NOT READY**.

## Findings

### P0

- None.

### P1

1. **A supplied Research identity can misrepresent the actual strategy config and still audit PASS.** `ResearchConfigIdentity` is publicly constructible from `strategy_config`, `normalized_items` and `config_id`. `Engine.attach_favorite_first` checks that `identity.strategy_config is config` and that dataset/session IDs match, but it does not verify that the normalized values and digest were derived from that config. `research_manifest` likewise trusts both fields. I constructed a context pointing to the actual `CONTROLLED_CONFIG` (`max_pair_loss_usd=0.50`) while supplying valid canonical items/digest generated from a different config (`max_pair_loss_usd=0.75`). Through the actual Engine attachment, capture, Episode lifecycle, writer and `audit_session`, the persisted Manifest claimed `0.75` and the audit returned `integrity=PASS`. No repository file was changed for this probe. This violates AE-1's rule that a direct Research-enabled Engine caller may not supply an arbitrary claimed digest, and the A1 requirement that identity reflect the effective runtime strategy config. The real live wrapper itself uses the safe factory; the finding is confined to the direct Engine Research entrypoint.

### P2

- The new regression bypasses wrapper orchestration and injects an accepted FIRST `FILL_REVISION` directly into capture. It verifies Engine attachment and downstream persistence/audit, but does not independently exercise gateway or Fill Truth acceptance. The A1 target does not require a real order path; this is test-depth context, not an additional blocker.
- The implementation does not add a persistent negative test for an Episode/Manifest config-ID mismatch. The unchanged auditor check and an independent temporary-artifact mutation show that mismatch still fails.

## Architecture and safety assessment

The versioned normalizer uses explicit active FavoriteFirst fields, rejects unexpected/nonfinite values, normalizes float types and negative zero, and serializes sorted compact JSON. Dict insertion order, `repr`, timestamps and session IDs do not influence its hash; changing a strategy-relevant field changes the digest. The live wrapper computes its context once and passes it to Engine. Neither Manifest nor Episode computes another hash. The auditor's direct Episode-versus-Manifest comparison in `research/audit.py` is unchanged, as are strategy, execution and risk files. The P1 is a missing validation boundary for caller-supplied contexts, not a relaxation of the auditor.

## Tests reviewed and executed

- `.venv/bin/pytest tests/test_research_audit.py -q`: **52 passed**.
- Unfiltered `.venv/bin/pytest tests/test_research_*.py -q` and `.venv/bin/pytest -q` were attempted; both timed out in unchanged tests. `test_startup_disk_work_runs_on_writer_thread` and `test_required_spender_follows_client_order_version_and_market_risk` each timed out when run alone for eight seconds. Their tests and production dependencies are outside the A1 diff; this is not a full-suite PASS.
- Research tests excluding the writer hang: **112 passed, 1 deselected**.
- Full tests excluding both hangs: **648 passed, 2 skipped, 2 deselected**.
- Relevant Ruff, `mypy src/polymaker/research`, and `git diff --check`: clean.
- Independent in-memory old-hash reproduction; temporary artifact mutation of an Episode config ID returned `FAIL ['identity']`; temporary forged-context runtime fixture returned actual loss `0.50`, persisted loss `0.75`, audit `PASS`.

## Untracked artifact audit

At review start: `src/polymaker/research/identity.py`, `docs/reports/M4A_A1_IMPLEMENTATION_REPORT.md`, and pre-existing `docs/reports/M4A_ACCEPTANCE_PREFLIGHT.md` were untracked. This review adds `docs/reviews/M4A_A1_REVIEW.md`. All must be accounted for at a later checkpoint. The tracked Architecture revision was already present when A1 implementation began and was preserved. No PASS tag was created.

## Required fix and deferred work

Validate any supplied session identity against the actual config at the Engine/Manifest boundary, including its normalized values and digest, without generating a second independent Episode ID. A forged or stale context must degrade Research and must not produce an audit-clean false canonical identity. Add a regression for this direct Engine path, then rerun relevant tests and request re-review of the corrected working tree. Trusted LIVE provenance, timing, loop lag, isolation, acceptance evidence and M4B remain outside A1.

Final verdict: **NEEDS FIX** because P1 remains. Next authorized action: Worker M4A-A1 minimal identity-context validation repair, then independent re-review. No live trading is authorized by this review.

## Independent Re-review After P1 Repair

Verdict: **PASS** for the reviewed M4A-A1 identity implementation.
Reviewed predecessor baseline: `m4a5-pass` / `4d75a19bf4f4e0a967d711cb57f651453f2fdd30`.
Reviewed commit / working tree: branch `m4-dynamic-ev` at `4d75a19`, with tracked `docs/CURRENT_STATUS.md`, `docs/architecture/M4A_ARCHITECTURE.md`, `src/polymaker/engine.py`, `src/polymaker/favorite_live.py`, `tests/test_research_audit.py` modifications and the untracked artifacts listed below. No A1 commit or tag exists. This PASS applies to this working tree and A1 scope only.
Architecture reference: AE-1 section A in `docs/architecture/M4A_ARCHITECTURE.md`.
Implementation report reference: `docs/reports/M4A_A1_IMPLEMENTATION_REPORT.md`, repair section.

### Findings

P0: **None.**
P1: **None.** The initial supplied-context P1 is closed.
P2: The FIRST-fill integration test injects an accepted event into the real Research capture rather than traversing gateway/Fill Truth. This test depth is sufficient for A1 and remains outside the present repair. Two unchanged tests hang in this environment; the full suite cannot be claimed as wholly passed.

### Required verdicts

| Check | Result | Evidence |
|---|---|---|
| Actual Engine config authoritative | **PASS** | `Engine.attach_favorite_first` passes its actual `config` to `validate_research_config_identity` before constructing EpisodeLifecycle. |
| Context canonical value validation | **PASS** | The validator derives expected normalized items from the actual config with `research_config_identity` and compares every stored item, version and session binding. |
| Context digest validation | **PASS** | The same canonical helper computes the expected digest; a changed `config_id` is rejected. The validation digest is used only for comparison, while Manifest and Episode continue to use the session context ID. |
| Mismatched config rejected | **PASS** | A same-object forged 0.75 claim against actual 0.50 and a context from a different valid config both failed Research attachment. |
| Digest tamper rejected | **PASS** | A correct normalized context with a changed digest failed Research attachment. |
| Failed attach produces no valid Episode | **PASS** | All three direct probes left `research_lifecycle=None` and the Research queue empty. With the live wrapper's writer failure provider, the mismatched session closed `INCOMPLETE`, with zero persisted Episodes. |
| Manifest / Episode identity | **PASS** | Matching FIRST-fill fixture persisted the actual config digest in Manifest and all five Episode revisions. |
| Auditor strictness | **PASS** | Auditor source is unchanged. A persisted Episode ID mutation, with the file checksum updated, returned `integrity=FAIL` and gate `identity` through normal `audit_session`. |
| Normal FIRST-fill → audit path | **PASS** | Matching context → real Engine attach → real capture/lifecycle/writer → `audit_session` returned integrity PASS. |
| Live trading semantics changed | **NO** | Diff changes Research identity creation, validation and plumbing only; strategy, gateway, Fill Truth, pricing, quantity, risk and order/cancel logic are untouched. |
| Actual M4A readiness | **NOT READY** | LIVE provenance and other acceptance evidence remain outside A1. |

### Independent probes and tests

I independently constructed the three claimed-context variants against an actual `CONTROLLED_CONFIG` Engine, outside the test functions. Each yielded one Research failure, no lifecycle and no queued Episode. A separate writer-backed probe for the different-config case produced `clean_close=False`, `manifest.close_status=INCOMPLETE` and zero Episode rows. The matching FIRST-fill fixture produced five Episode revisions and audit PASS; changing the first persisted Episode config ID and updating the artifact checksum produced `FAIL ['identity']`.

- `.venv/bin/pytest tests/test_research_audit.py -q`: **57 passed**.
- `.venv/bin/pytest tests/test_research_*.py -q` and `.venv/bin/pytest -q` were attempted and timed out. The unchanged `test_startup_disk_work_runs_on_writer_thread` and `test_required_spender_follows_client_order_version_and_market_risk` each timed out individually after eight seconds. Their test and production files have no diff from `m4a5-pass`; this is **not** a full-suite PASS.
- Research tests excluding the writer hang: **117 passed, 1 deselected**.
- Full tests excluding both hangs: **653 passed, 2 skipped, 2 deselected**.
- Relevant Ruff, `mypy src/polymaker/research`, and `git diff --check`: clean.

### Untracked artifact and handoff audit

Reviewed untracked files: `src/polymaker/research/identity.py`, `docs/reports/M4A_A1_IMPLEMENTATION_REPORT.md`, `docs/reports/M4A_ACCEPTANCE_PREFLIGHT.md`, and this `docs/reviews/M4A_A1_REVIEW.md`. All must be included in a complete reviewed checkpoint; ordinary `git diff` omits them. The pre-existing Architecture and preflight documentation changes were retained, not overwritten. No PASS tag or live order was created by this Review.

Required fixes: **None for A1.** Deferred acceptance evidence remains outside this verdict. Final verdict: **PASS** for M4A-A1 identity repair on the exact reviewed working tree. Next: create a complete reviewed checkpoint/tag, then follow the user's manually supervised one-market FL BTC 5min Research validation plan; this Review does not perform or itself certify that live run.

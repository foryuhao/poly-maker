# M4A-A1 Implementation Report

Status: **IMPLEMENTED — AWAITING RE-REVIEW**
Commit / working tree base: `m4a5-pass` (`4d75a19`), with pre-existing uncommitted Architecture, status and preflight-document changes preserved.
Reviewed predecessor baseline: `m4a5-pass` (`4d75a19bf4f4e0a967d711cb57f651453f2fdd30`).
Architecture reference: `docs/architecture/M4A_ARCHITECTURE.md`, AE-1 section A.

## Bug and root cause

The live wrapper hashed its projected effective config with compact sorted JSON. Engine independently hashed the raw FavoriteFirst dataclass with default JSON whitespace. The raw form retained `probation_passive_offset_ticks` and `probation_max_s`, while the effective form removed the former and renamed the latter. A pre-fix reproduction with `CONTROLLED_CONFIG` produced Manifest ID `5709c9932ff81a3286828d7daae1b7314118a5b0289c10390b47d5956bef2ff7` and Episode ID `9aa0693fbe98e3da7a38a8a0426b89743b680f26bf6987e9cf837ee96776878c`.

## Fix and identity owner

`research/identity.py` defines the versioned `favorite-first-effective-v1` projection, canonical JSON and SHA-256. It checks the expected strategy fields and rejects invalid or nonfinite values. The live wrapper creates one immutable Research session identity from the actual config object and uses its digest and effective config in the Manifest. It passes the same context to Engine, which supplies the digest to EpisodeLifecycle. Direct Research-enabled Engine callers construct the same context once at attachment. Session/strategy binding mismatches degrade Research without changing trading control. Auditor identity comparison remains strict and unchanged.

Files added: `src/polymaker/research/identity.py`, `docs/reports/M4A_A1_IMPLEMENTATION_REPORT.md`.
Files modified: `src/polymaker/favorite_live.py`, `src/polymaker/engine.py`, `tests/test_research_audit.py`, `docs/CURRENT_STATUS.md`.
Deviations from architecture: None for A1 identity; acceptance evidence is later work.

## Regression and audit result

The new integration fixture creates a FavoriteFirst Research context, uses the live wrapper's Manifest builder, attaches a real Engine, persists a FIRST-fill Episode through the real capture/lifecycle/writer, and runs `audit_session`. Manifest and all Episode revisions share the same ID; session integrity and its config identity gate pass. `audit_collection` remains below overall readiness PASS. A second test checks deterministic IDs across sessions, dict order independence and sensitivity to a strategy parameter change.

## Tests executed

- `.venv/bin/pytest tests/test_research_audit.py -q`: **52 passed**.
- `.venv/bin/pytest tests/test_research_*.py -q -k 'not test_startup_disk_work_runs_on_writer_thread'`: **112 passed, 1 deselected**.
- `.venv/bin/pytest -q -k 'not test_startup_disk_work_runs_on_writer_thread and not test_required_spender_follows_client_order_version_and_market_risk'`: **648 passed, 2 skipped, 2 deselected**.
- Relevant Ruff, Research-targeted mypy and `git diff --check`: clean.

The unfiltered Research and full runs were attempted. Research stopped at the existing `test_startup_disk_work_runs_on_writer_thread`; that test also timed out in isolation after 15 seconds, with the writer waiting for queue work and the test event loop still active. The full run stopped earlier at `test_required_spender_follows_client_order_version_and_market_risk`. Both were excluded for the completed full run. Neither test nor its production path was changed by A1. Broader mypy over Engine/live reports five errors in unchanged code locations; Research-targeted mypy passes.

Live trading semantics changed: **NO**. No live order was submitted; a real FIRST-fill session is still needed after independent Review PASS to verify the live environment.

## Known limitations and handoff

Actual M4A readiness: **NOT READY**. Trusted LIVE provenance, latency, loop-lag and isolation evidence remain outside this repair. No M4B work or PASS tag was created. Next authorized action: **Independent M4A-A1 Review only**.

## Repair after Independent Review P1

The first independent Review (`docs/reviews/M4A_A1_REVIEW.md`) found that a caller could attach a context pointing to the actual strategy object while supplying normalized values and digest from a different config. Such a session could audit clean despite misrepresenting the actual strategy.

`validate_research_config_identity` now derives the expected normalized values and digest from the actual Engine config using the existing A1 helper, then checks the supplied context's config object, dataset/session IDs, normalization version, values and digest. Engine validates before creating EpisodeLifecycle; `research_manifest` validates before producing a Manifest. A mismatch increments the existing Research failure counter and leaves the lifecycle unattached without changing FavoriteFirst control. Manifest and Episode still use the one session `config_id`; the auditor was not modified.

New regressions cover a matching context through FIRST fill, persisted Episode and audit; a same-object context forged with `0.75` while Engine runs `0.50`; a context from a different config; a tampered digest; and an artifact-level Manifest/Episode ID mismatch that fails audit identity. The forged and tampered contexts fail at attachment, before any Episode can be produced. A wrapper-style writer test confirms an attach failure makes the session Manifest `INCOMPLETE` with no Episode.

Validation after repair:

- `.venv/bin/pytest tests/test_research_audit.py -q`: **57 passed**.
- Unfiltered Research and full pytest were attempted; they timed out in the same unchanged writer-startup and FavoriteFirst spender tests documented above. These runs are **not** full-suite PASS.
- Research tests excluding the writer hang: **117 passed, 1 deselected**.
- Full tests excluding both hangs: **653 passed, 2 skipped, 2 deselected**.
- Relevant Ruff, `mypy src/polymaker/research` and `git diff --check`: clean. Broader mypy still reports the five unchanged Engine/live type errors noted above.

Live trading semantics changed: **NO**. Actual M4A readiness: **NOT READY**. No PASS tag or real trade. Next authorized action: **Independent M4A-A1 Re-review only**.

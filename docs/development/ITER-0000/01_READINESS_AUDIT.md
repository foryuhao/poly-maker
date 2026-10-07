# ITER-0000 — Final Readiness Audit

Current status: READY_FOR_RDP_DEVELOPMENT after final clean checkpoint and remote verification.
Human architecture decision: reconstruct a minimum FL predecessor from 97ab0f9, excluding unsupported mixed 19c322a changes.

## Current FL Provenance Reconstruction
Canonical FL: fl-reconstructed-baseline / 76fe93ffeb8cc1f8ca440587ab901ea402221228; branch reconstruct/fl-canonical-20261007.
Root: 97ab0f9. Whole 19c322a and im-paused are proven NOT ancestors (merge-base --is-ancestor exits 1); root ancestry exits 0. Four source/test/config files reconstructed; exact diff, included evidence, excluded neighbors and full 93-file historical change classification in FL_PROVENANCE_RECONSTRUCTION.md. Capture identity matches 42/42; 41 corresponding run records match, one absent. No exact historical committed-workspace claim.

## Current Test Gate
Known historical predecessor spender hang independently reproduced in earlier 19c322a run (timeout 15 exit 124); currently exact test passes alone, so no waived test in final acceptance. Authorized broad exclusion gate: 655 passed, 2 skipped, 1 deselected. Stronger FL full suite: 656 passed, 2 skipped. Separate isolated RDP full suite: 656 passed, 2 skipped in 10.23s. All other applicable tests passed; no test fix, extra exclusion or failure relabeling. Commands/logs preserved in reconstruction document and preservation/.

## IM Preservation
im-paused / a03b59fd0b3c42955029193b30893a395e03dfa4 / im-paused-20261007 remain unchanged and remotely archived. Incomplete/PAUSED, preservation only, not new PASS and not RDP predecessor. Original dirty IM workspace remains untouched, including unexplained signature_type=2 diff and control files. Canonical recovered config remains signature_type=3.

## Data Preservation
Original research_data/m4a/ 42 sessions, 5,180,619,849 bytes, 8,284,525 records, 38 slugs; 32 historically replay-usable markets. Sampled JSON/JSONL readable, manifests match prior inventory hashes. No bulk data Git inclusion/move. Original research_outputs/, research_audits/, logs/runs/, livecfg/logs/ remain at original locations. These historical FL assets do not establish RDP edge or full telemetry acceptance.

## RDP Baseline
rdp-phase0-dev derives from canonical reconstructed FL. rdp-phase0-baseline identifies its exact documentation-only starting checkpoint. Commit is resolvable from tag; no IM ancestry wholesale, no shared-core included, no RDP strategy feature. Canonical-FL-to-IM classification in FL_TO_IM_CLASSIFICATION.md. Signing/identity/readiness/order-read candidates deferred to later explicit minimal iterations.

## Readiness safety and limits
No AGENTS.md/custom agent framework installed. Legacy historical FL RPC/merge paths are preserved rather than silently changed; they confer NO RDP runtime authority. RDP implementation must obey official Polymarket HTTP/WebSocket-only interface rule through an explicit Architect contract. No real order/cancel/authenticated trading action or new dependency. No main merge/PR/history rewrite. New FL and RDP checkpoints are additive; historical tags preserved.

## Checkpoint and remote procedure
Clean FL worktree accepted and tagged; FL branch/tag push succeeded. RDP full gate passes; source/test/config tree matches FL exactly. Documentation-only final RDP commit/tag/push and exact remote branch/peeled-tag verification are performed before final success. Original 18 file fingerprints and original HEAD/dirty state are checked unchanged. Final Git evidence and remote completion are recorded as archival receipt on pre-rdp-controls-20261007 without altering either baseline tag.

## Next authorized action
After successful verified readiness: archive historical AGENTS.md, install RDP Phase 0-specific AGENTS.md and custom Architect/Worker/Reviewer configuration, then begin ITER-0001. STOP ITER-0000 before doing those actions.

## Historical investigations — retained verbatim below
Earlier BLOCKED statuses and pending-checkpoint language below describe earlier attempts; current status above supersedes them without erasing evidence.

---

# ITER-0000 — Pre-RDP Codebase Isolation

Status: BLOCKED — HUMAN_DECISION_REQUIRED
Date: 2026-10-07

## Initial blocked attempt and resumed authority
The initial attempt stopped on mixed dirty work. Human disposition authorized preservation and classification, without canonical inclusion or RDP implementation. Initial records are retained under preservation/INITIAL_BLOCKED_*. AGENTS.md and relevant current reports/reviews were read. Git status/history/branches/tags/remotes were inspected before operations.

## Repository state before work
Original /home/ubuntu/poly-maker; m4-dynamic-ev; HEAD 57a9e9ce2f260e4015fedfd632771fcc37fd6b8d, one commit ahead of local origin tracking ref; no staged changes. Exact original HEAD, branch, status, tracked binary patch, staged patch, untracked list and fingerprints are stored under preservation/. No stash/reset/clean/force/history rewrite or checkout of the original tree occurred.

## Dirty-state disposition

| Original files/group | Classification | Preservation | Canonical inclusion |
|---|---|---|---|
| README.md; docs/CURRENT_STATUS.md; src/polymaker/cli.py; src/polymaker/execution/gateway.py; src/polymaker/mi_cash_reservation.py; tests/test_execution.py | UNFINISHED_IM_WORK | Exact originals committed on im-paused | IM archive only; exclude RDP |
| docs/architecture/MI_M3_ARCHITECTURE.md; docs/reports/MI_M3_IMPLEMENTATION_REPORT.md; docs/reviews/MI_M3_REVIEW.md; src/polymaker/mi_micro_live.py; tests/test_mi_micro_live.py | UNFINISHED_IM_WORK | Exact previously untracked originals committed on im-paused | IM archive only; exclude RDP |
| AGENTS_RDP.md; .codex/config.toml; .codex/tmp/agents/architect.toml; .codex/tmp/agents/worker.toml; .codex/tmp/agents/reviewer.toml; docs/history/AGENTS_LEGACY_PRE_RDP.md; docs/strategy/RDP_PHASE0_FROZEN.md | PRE_RDP_CONTROL_ARTIFACTS | Exact inert copies under preserved-controls/ at a184014; originals unchanged | Documentation archive only; NOT installed |
| research_outputs/ | Generated prior research | Existing location unchanged; inventory persisted | Excluded from Git/RDP |
| research_data/m4a/, research_audits/, logs/runs/, livecfg/logs/ | Historical runtime/research data | Existing locations unchanged; inventories persisted | Local assets, no Git inclusion |
| livecfg/config.toml | UNEXPLAINED_LOCAL_CONFIG_CHANGE | Existing local value=2 unchanged, exact patch stored | Excluded from IM/RDP; canonical committed value=3 |

Literal-secret candidate scan of 18 source/test/control artifacts found no likely embedded credential. Existing .env was neither read nor copied. No blind git add was performed. Original dirty files and data remain unchanged; additional evidence exists in isolated worktrees and Git.

## Signature type investigation
Only file history commit a18e1ac (Hardening); blame attributes signature_type=3 to it. fff2842, 97ab0f9, 19c322a and committed IM HEAD 57a9e9c all retain 3. PRE_MI_GIT_CHECKPOINT_REPORT says the local 3 -> 2 change lacked rationale and was excluded. No durable operational explanation was established. No operational signature-type decision was made; future canonical branches use the selected committed baseline unless separately authorized.

## IM paused canonical preservation
Branch im-paused; commit a03b59fd0b3c42955029193b30893a395e03dfa4; annotated tag im-paused-20261007. Adds exactly 11 attributable M3 files plus docs/history/IM_PAUSED.md above original HEAD. Program incomplete and paused per human decision: edge is insufficiently stable to justify continued development before evidence. Preservation grants no PASS and is NOT an RDP predecessor. Historical review text/fingerprints are retained unchanged; later gateway order-read changes are not certified merely by prior review PASS. M3 implementation/review report fake/offline testing, not a completed real-money lifecycle.

## FL canonical baseline investigation — unresolved
FL is repository FavoriteFirst (also called FL in MI M3 Review); IM is repository MakerInventory/MI.

- fff2842baa147b7a66a295f30a21b590f73b93bd, m3c-settlement-live-pass: FavoriteFirst live/observability predecessor; predates durable M4A capture. logs/runs/20261001T160401976113Z_btc-updown-5m-1790871000/run.json records COMPLETED, first YES 5 @ 0.62, paired NO 5 @ 0.50, final open orders=0. Another historical run records incomplete reconciliation. These are preserved historical records, not new trading or independently authenticated claims.
- 97ab0f98168f989b1878e4058022044c8372dcd3, m4a-a1-pass: reviewed config identity/capture predecessor, named by all 42 capture manifests. Its committed wrapper uses fixed CONTROLLED_CONFIG and retains RPC startup/on-chain reconciliation/auto-merge paths. Existing reports document subsequent startup/profile and RPC issues. Actual M4A acceptance remained NOT READY.
- 19c322ae2c12621351e034dfdc2705ea2f9ad98f, no tag: final pre-MI archival checkpoint; immediate successor 5befd9e introduces MI. It contains configurable FL entry, startup-profile repair, official Data API trading-core cleanup, and prior offline research tools. PRE_MI_GIT_CHECKPOINT_REPORT explicitly says material production changes remain unreviewed; historical test result 689 passed/2 skipped.

Capture manifests name 97ab0f9 but record varying favorite intervals and timing that its committed wrapper cannot supply. Corresponding support was later archived in 19c322a. Thus manifest code_commit does not identify the exact dirty source/config tree used live. No exact source fingerprints establish that every material 19c322a change was live-tested. Selecting reviewed/capture-named 97ab0f9 versus unreviewed archival 19c322a requires a predecessor evidence decision. FL_CANONICAL_BASELINE NOT SELECTED. No tag name was treated as sufficient proof.

## FL historical capture dataset
research_data/m4a/: 42 sessions, 38 distinct slugs, 210 files, 5,180,619,849 bytes, 8,284,525 persisted records; all manifests report COMPLETE. Historical EXECUTION_REPLAY_DATA_AUDIT_20261003.md selects 32 distinct execution-replay-usable markets and excludes 10 short sessions; stricter telemetry audit passed 29/42. Manifest/quality JSON and first nonempty row of each events/episodes/states JSONL were readable for all 42 sessions. Full 5GB replay/checksum revalidation was not performed. Manifest inventory and each manifest SHA256 are persisted.

Classification: FL historical capture dataset. Not RDP strategy-edge evidence or settlement-labeled actual PnL. Ignored/untracked; no dataset/log files returned by git ls-files. Original paths unchanged. research_outputs/: 749 files, 917,944,014 bytes of prior DP/CIP/P0/Grid/Whiskas CSV/JSON/Markdown/Python outputs, not raw FL capture or M3 source. research_audits/: 2 files; logs/runs/:16; livecfg/logs/:172. Exact inventory under preservation/data-inventory.json. These local datasets are not protected by code GitHub archival; deleting the workspace or cleaning ignored files could destroy them. No bulk data backup claimed.

## Provisional candidate-to-IM classification
Compared actual 19c322a..im-paused: 36 files, 6,290 insertions, 61 deletions. This is provisional because FL canonical selection remains unresolved. Intermediate commits: 5befd9e M1, f25a5a5 M2A, 57a9e9c M2B gates, a03b59f M3 pause archive. Buckets exactly A strategy-specific/exclude, B unfinished or uncertain/exclude, C shared-core review candidate only, D confirmed eligible fix. No D selected.

| File | Bucket | Disposition |
|---|---|---|
| `README.md` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `docs/CURRENT_STATUS.md` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `docs/architecture/MI_ARCHITECTURE.md` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `docs/architecture/MI_M3_ARCHITECTURE.md` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `docs/history/IM_PAUSED.md` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `docs/reports/MI_M1_IMPLEMENTATION_REPORT.md` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `docs/reports/MI_M2A_IMPLEMENTATION_REPORT.md` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `docs/reports/MI_M2B_0A_IMPLEMENTATION_REPORT.md` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `docs/reports/MI_M2B_0B_IMPLEMENTATION_REPORT.md` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `docs/reports/MI_M2B_IMPLEMENTATION_REPORT.md` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `docs/reports/MI_M3_IMPLEMENTATION_REPORT.md` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `docs/reviews/MI_M1_REVIEW.md` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `docs/reviews/MI_M2A_REVIEW.md` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `docs/reviews/MI_M2B_0A_REVIEW.md` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `docs/reviews/MI_M2B_0B_REVIEW.md` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `docs/reviews/MI_M3_REVIEW.md` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `src/polymaker/btc5m.py` | B | MI-required strict metadata/cache controls: EXCLUDE_FROM_RDP |
| `src/polymaker/cli.py` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `src/polymaker/config.py` | C | Shared authorization/identity/readiness or gateway helper/tests: REVIEW_CANDIDATE_ONLY; deferred as bundled with IM |
| `src/polymaker/engine.py` | C | Shared authorization/identity/readiness or gateway helper/tests: REVIEW_CANDIDATE_ONLY; deferred as bundled with IM |
| `src/polymaker/execution/gateway.py` | C | Shared authorization/identity/readiness or gateway helper/tests: REVIEW_CANDIDATE_ONLY; deferred as bundled with IM |
| `src/polymaker/favorite_live.py` | C | Shared authorization/identity/readiness or gateway helper/tests: REVIEW_CANDIDATE_ONLY; deferred as bundled with IM |
| `src/polymaker/live_authorization.py` | C | Shared authorization/identity/readiness or gateway helper/tests: REVIEW_CANDIDATE_ONLY; deferred as bundled with IM |
| `src/polymaker/marketdata/service.py` | C | Shared authorization/identity/readiness or gateway helper/tests: REVIEW_CANDIDATE_ONLY; deferred as bundled with IM |
| `src/polymaker/mi_cash_reservation.py` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `src/polymaker/mi_live.py` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `src/polymaker/mi_micro_live.py` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `src/polymaker/mi_readonly_account.py` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `src/polymaker/strategy/maker_inventory.py` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `tests/test_execution.py` | C | Shared authorization/identity/readiness or gateway helper/tests: REVIEW_CANDIDATE_ONLY; deferred as bundled with IM |
| `tests/test_favorite_live.py` | C | Shared authorization/identity/readiness or gateway helper/tests: REVIEW_CANDIDATE_ONLY; deferred as bundled with IM |
| `tests/test_live_authorization.py` | C | Shared authorization/identity/readiness or gateway helper/tests: REVIEW_CANDIDATE_ONLY; deferred as bundled with IM |
| `tests/test_maker_inventory.py` | A | IM integration/program/source/tests: EXCLUDE_FROM_RDP |
| `tests/test_mi_cash_reservation.py` | C | Shared authorization/identity/readiness or gateway helper/tests: REVIEW_CANDIDATE_ONLY; deferred as bundled with IM |
| `tests/test_mi_live.py` | C | Shared authorization/identity/readiness or gateway helper/tests: REVIEW_CANDIDATE_ONLY; deferred as bundled with IM |
| `tests/test_mi_micro_live.py` | C | Shared authorization/identity/readiness or gateway helper/tests: REVIEW_CANDIDATE_ONLY; deferred as bundled with IM |

Accepted shared-core fixes: NONE. Deferred candidates: live_authorization identity/gates and FL integration; books_fresh extraction; eth_utils address validation; SDK signing-price normalization; order read envelope/not-found/mismatch handling. Historical fake/regression tests support candidacy, not independently validated isolated reuse. M2A bundles shared work with MI runtime; M3 snapshot bundles gateway changes with unfinished lifecycle. No isolated commit qualifies for automatic cherry-pick. No transplant/merge performed. Deferral permits a future clean FL baseline with no shared-core reuse.

## Tests
Existing supported .venv: Python 3.12.14; pytest 9.1.1. Detached candidate worktree /tmp/poly-maker-fl-test-20261007 at 19c322a, explicit PYTHONPATH pointing to that worktree/src. No mixed-original-tree test called baseline evidence.

Focused command:
```sh
PYTHONPATH=/tmp/poly-maker-fl-test-20261007/src PYTHONDONTWRITEBYTECODE=1 timeout 120 /home/ubuntu/poly-maker/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_favorite_first.py tests/test_favorite_first_review.py tests/test_execution.py tests/test_filltruth.py tests/test_live_marketdata.py tests/test_engine.py tests/test_btc5m.py tests/test_config_aliases.py
```
190 passed, 2 skipped in 1.44s.

Full command: same environment; timeout 180 /home/ubuntu/poly-maker/.venv/bin/python -m pytest -q -p no:cacheprovider. Final outcome in RESULT.md. Isolated test_required_spender_follows_client_order_version_and_market_risk, same environment and pytest -vv with timeout 15: exit 124. This hang is previously documented in M4A/MI reports, not newly attributed regression. No full-suite PASS. No fresh IM test required for preservation; RDP not constructed or tested.

## Git operations and check results
im-paused added above original HEAD; pre-rdp-controls-20261007 added above original committed HEAD for documentation-only archival. Neither is an RDP predecessor. Original workspace untouched. Detached FL-candidate worktree for tests only. Existing historical tags preserved. No RDP code/dependency/runtime interface, config installation, PR or main merge. Initial Git write required sandbox escalation; authorized additive operations only.

Pause diff check passed. Archived frozen document has a pre-existing trailing blank line reported by diff --check; preserved byte-for-byte intentionally. Newly authored documents checked separately. Exact preservation evidence includes tracked.patch/staged.patch/untracked list; generated data remains at original paths. A first documentation-copy attempt failed before the audit was authored because docs/history did not exist in the documentation worktree; directory was created and copy completed, with no original files changed.

## Blockers and next authorized action
Human decision required: select acceptable FL predecessor evidence standard or authorize a separate review/evidence iteration for the pre-MI official-interface cleanup. 97ab0f9 retains RPC trading paths; 19c322a removes those paths but contains unreviewed changes with incomplete live provenance. Predecessor full regression gate is also incomplete due reproducible documented hang; establish a completable/explicitly accepted test standard. Resume ITER-0000 only. No RDP AGENTS/config install or ITER-0001 until READY.

## Final topology and remote
IM pause and control-audit archive are siblings above original 57a9e9c, which descends from pre-MI 19c322a. RDP branch/tag NOT CREATED. Final remote ref verification is in RESULT.md. Original local branch retains its original dirty state; that is intentional preservation, not a clean baseline claim.

Full candidate suite final outcome: timeout 180, EXIT 124. No full test PASS.

Final remote verification: IM branch/tag peeled commit matches a03b59f; documentation archive pushed at 23a4d4e with this verification/status follow-up to be pushed next. pre-rdp-controls CURRENT_STATUS updated additively with original historical text retained below; original workspace status file remains unchanged. Final diff against 57a9e9c is documentation-only. Both archive worktrees were clean after checkpoint. Git ancestry 19c322a -> im-paused proved (exit 0). No RDP refs exist.

## Human-authorized FL Provenance Reconstruction (resumption)

The previous BLOCKED decisions remain historical. Current authority reconstructs parent 97ab0f9 minimally. See FL_PROVENANCE_RECONSTRUCTION.md for every included/excluded hunk, complete source-archive classification, exact patch, 42-manifest/41-run correlation and test gate. Full reconstructed suite: 656 passed, 2 skipped; prior spender hang did not reproduce currently, so final acceptance uses no test waiver. IM/data/control/config dispositions remain unchanged. FL tag and RDP checkpoint metadata follow in canonical BASELINE/RESULT.

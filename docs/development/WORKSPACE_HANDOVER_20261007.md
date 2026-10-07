# Canonical RDP Workspace Handover — 2026-10-07

Status: READY_FOR_INDEX_INITIALIZATION. Canonical root: `/home/ubuntu/poly-maker`.
Branch: `rdp-phase0-dev`; starting HEAD: `6a5ed93c84d66960eaa73b27a0fe34acfa0bf6c9`.
Final handover HEAD is the commit containing this receipt; resolve with Git.

## Accepted lineage

FL `76fe93f` / `fl-reconstructed-baseline`, IM `a03b59f` /
`im-paused-20261007`, and RDP `6a5ed93` / `rdp-phase0-baseline` retained unchanged.
Exact branch heads verified on origin before handover. No readiness reconstruction.
IM remains PAUSED and is not an RDP predecessor.

## Control preservation

Temporary RDP worktree was registered, clean, and at `6a5ed93`; its control
configuration was not installed. Root RDP AGENTS, Codex configuration and
three agent files, archived historical AGENTS, and Frozen SOP existed locally.
Transferred these exact files from a local preservation copy; only AGENTS
received the requested Single Repository / Shared Data Rule addition.
Temporary worktree released normally after preservation.

## Local change classification

Exact file-content comparison confirmed all dirty IM files match `im-paused`:
README.md, docs/CURRENT_STATUS.md, src/polymaker/cli.py,
src/polymaker/execution/gateway.py, src/polymaker/mi_cash_reservation.py,
src/polymaker/mi_micro_live.py, tests/test_execution.py,
tests/test_mi_micro_live.py, docs/architecture/MI_M3_ARCHITECTURE.md,
docs/reports/MI_M3_IMPLEMENTATION_REPORT.md, docs/reviews/MI_M3_REVIEW.md.
Pre-RDP audit controls remain archived on `pre-rdp-controls-20261007` at `8805ae6`.

Remaining non-data residue: RDP control files listed above, AGENTS_RDP.md,
and local livecfg/config.toml signature_type=2. Audited explicit paths preserved
in named stash `pre-rdp-local-residue-20261007`, object `e1b860bc24d05d4ede488759c54e9a2508e8ddd8`.
No literal credential fields found in this non-data set. Stash NOT applied to RDP.
RDP livecfg/config.toml retains signature_type=3. Research outputs (all previously
untracked files under research_outputs/) remain in place and outside the stash.
Ignored data was excluded. No destructive reset/clean used.

## Data and validation

research_data/m4a remains in place: 42 session directories, 210 files,
5,180,619,849 bytes. All files readable. Before/after path, inode, size and
mtime metadata maps match exactly. Existing BASELINE records 32 historically
replay-usable markets; expensive replay was not repeated.
Control-file byte comparisons passed. Required control/baseline/result files
coexist with src, tests and research_data in the canonical root.
`git merge-base --is-ancestor rdp-phase0-baseline HEAD` passed.
`git pull --ff-only origin rdp-phase0-dev` reported already up to date.
No source/tests edited; no strategy implementation, tag, main merge, data
movement, history rewrite or real-money trading. Documentation validation:
`git diff --check` and staged diff check required before commit.

## Next authorized action

Initialize docs/development/ITERATION_INDEX.md. ITER-0001 has not started.
Handover commit to be pushed normally to origin/rdp-phase0-dev and verified.

# ITER-0029 — Re-review03 scratch quota observation and recovery

The configured independent Reviewer reported minimal Python/shell writes failing with OSError/Errno122 Disk quota exceeded in `/tmp`. Truncated test logs, closed-file logging, ResearchWriter.close failures and exits120 from affected attempts are incomplete environment-contaminated evidence, not a production verdict or a pass. Original scratch outputs are preserved unchanged.

Orchestrator read-only capacity inspection: `/tmp` is a separate966MB tmpfs (773MB used,194MB available); `/var/tmp` and repository root use `/dev/vda3` with23GB available. A task-specific directory `/var/tmp/poly-maker-ITER-0029-rereview-03/` was created and actual Python write/read succeeded there.

The same configured Reviewer is authorized to continue exact-tree checks with new logs and temporary output on that volume, setting task TMPDIR accordingly. No original evidence is deleted or overwritten, no historical process is killed, no source/test/runtime interface is changed, and no role or permission restriction is bypassed. Report delivery location is now `/var/tmp/poly-maker-ITER-0029-rereview-03/REVIEWER-REREVIEW-03.md`; immutable original report must be saved and SHA256 delivered before Orchestrator persists09.

Branch rdp-phase0-dev; HEAD3286e56ac4e8ac3f72ccf0edda7722d9dcbc3827. Current source/test hashes remain Worker08 values. DG-004 remains OPEN, Stage REVIEWER_REREVIEW_03, no MICRO_LIVE_READY, real orders/cancels or main merge authorization.

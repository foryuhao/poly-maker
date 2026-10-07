# IM Paused Preservation Record

Status: PAUSED
Date: 2026-10-07

Reason: IM edge is currently considered insufficiently stable to justify continued development before further evidence (human disposition decision).

Code status: INCOMPLETE. Repository calls this strategy MakerInventory (MI); the user calls it IM. M1/M2 primitives and M3 implementation/review evidence are preserved, but the program has not completed live validation.

Canonical preserved branch: im-paused
Canonical preserved commit: resolve im-paused-20261007^{commit}; the tagged snapshot contains this record and the exact attributable M3 working files.
Canonical preserved tag: im-paused-20261007
Committed predecessor: 57a9e9ce2f260e4015fedfd632771fcc37fd6b8d

Live validation status: M3 reports/reviews record offline/fake tests and no real trading during implementation/review. They do not establish a completed real-money lifecycle. Historical PASS statements apply to their original reviewed scope only; this preservation operation grants no PASS or correctness certification.

Classification: UNFINISHED_IM_WORK. Snapshot includes README.md, docs/CURRENT_STATUS.md, src/polymaker/cli.py, src/polymaker/execution/gateway.py, src/polymaker/mi_cash_reservation.py, tests/test_execution.py, docs/architecture/MI_M3_ARCHITECTURE.md, docs/reports/MI_M3_IMPLEMENTATION_REPORT.md, docs/reviews/MI_M3_REVIEW.md, src/polymaker/mi_micro_live.py and tests/test_mi_micro_live.py, copied byte-for-byte from the original dirty tree.

Excluded: unexplained local livecfg/config.toml change, generated research outputs, and PRE_RDP_CONTROL_ARTIFACTS. They remain preserved in the original workspace and separate evidence/archive locations. The canonical Git wallet signature_type remains 3; no operational identity decision was made.

Important: IM development is paused and preserved for future research. This snapshot exists to preserve work, is NOT an RDP predecessor baseline, and does not authorize trading. The pre-existing CURRENT_STATUS text is retained as historical evidence; this pause record supersedes its next-action guidance for this branch.

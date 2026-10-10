# ITER-0028 Worker Fix 01 — Command Detail Addendum

This addendum supplements immutable `04_WORKER_FIX_01.md` without changing its implementation findings, outcomes, or scope. The archived Worker report remains byte-identical at SHA256 `c2a52808a971ca5e5e946cabdf58d3a30416f3e6286a2df05ca230c09cc92538`.

All commands below ran from `/home/ubuntu/poly-maker`; output is in the named immutable-under-/tmp logs. Each final runner output reports `REVIEW_NETWORK_ATTEMPTS []`.

- Reviewer v3 placement probes, `/tmp/iter0028_fix01_reviewer_probes_final2.log`:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no docs/development/ITER-0028/review_evidence/iter0028_reviewer_probes_v3.py` — 3 passed.
- Reviewer lifecycle, `/tmp/iter0028_fix01_reviewer_lifecycle_final3.log`:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no docs/development/ITER-0028/review_evidence/iter0028_reviewer_lifecycle_v3.py -k 'held_subsequent_close or later_zero_fill or reliable_no_send or subsequent_complete'` — 1 failed at the old `c.posts==0` expectation, 5 passed, 3 deselected.
- Reviewer window/current-evidence checks, `/tmp/iter0028_fix01_reviewer_window_final2.log`:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no docs/development/ITER-0028/review_evidence/iter0028_reviewer_window_v2.py -k 'later_window or nonboolean or subsequent_guard_current'` — 13 passed, 9 deselected.
- Reviewer clock/higher-cash checks, `/tmp/iter0028_fix01_reviewer_terminal_final2.log`:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no docs/development/ITER-0028/review_evidence/iter0028_reviewer_terminal.py -k 'clock_failure_terminal or higher_current_cash'` — 2 passed, 22 deselected.
- Reviewer conflict checks, `/tmp/iter0028_fix01_reviewer_conflicts_final.log`:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no docs/development/ITER-0028/review_evidence/iter0028_reviewer_conflicts.py` — 1 passed.

The repository-owned direct regression covers the fresh re-admission and freshly guarded POST path that supersedes the lifecycle probe's no-post assertion. No source/test changes followed final suite validation.

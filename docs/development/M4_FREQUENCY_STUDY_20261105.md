# M4 frequency research v2

This document records the original 2026-11-05 calibration. On 2026-11-09 the
user authorized adding a 20% minimum weak-Ask loss/initial ratio to the current
M4 detector. See [the current M4 contract](M4_LIVE_BASELINE_CUTOVER.md) for that
amendment. The historical counts and original profile below are not recalculated
or relabeled under the new rule.

The user requested enough M4 signals per session to support forward research.
M2, M3 and M4 share one public feed and remain separate statistical ledgers.
The user explicitly requested replacing baseline M4 rather than retaining a
parallel baseline strategy. Historical v1 records remain in the original database.
No account access or real orders are enabled; M1 is untouched.

## Outcome-blind calibration

At the 2026-11-05 16:22 Beijing snapshot, 180 complete five-minute sessions
contained 83 eligible M2 samples (0.461/session; 58 covered sessions), and
226 eligible M3 samples (1.256/session; 124 covered sessions).

Six predeclared profiles were evaluated using recorded qualified M4 rounds and
all recorded queue resets. Baseline component counts reproduced exactly: 63 RF,
one DF, zero candidates. Widening time windows alone still produced no candidate.
The selected broad profile estimated 89 roots (0.494/session) across 61 sessions.
Its first 120 sessions yielded 0.575 roots/session, and the last 60 yielded 0.333.
M2's corresponding rates were 0.533 and 0.317. These are historical frequency
checks, not prospective validation, and no PnL or outcome was used for selection.

The replay lacks raw book updates, live quote admission, complete clock bounds,
the 250 ms observation trace, and the ordering of invalidation within a message.
Its counts are candidate-frequency estimates, not verified fills or trade returns.
Queries, parameters and estimated candidates are retained in
`research_outputs/m2_virtual/analysis/m4_frequency_probe_20261105.json` and its
adjacent reproducible script.

## Frozen v2 definition

The implementation in `src/polymaker/m4_frequency_signal.py` consumes only the
original M4's qualified 500 ms rounds and queue-ending events. The original M4
state machine becomes a round extractor when `--m4-frequency-study` is enabled;
its v1 RF/DF combination is not run in parallel.

- Bid support: one stable round, with unchanged 100 ms hold arithmetic and at
  least 50% final recovery; valid for eight seconds. This is weaker evidence than
  baseline two-round RF and is explicitly called single-round bid support.
- Ask reference: one stable round with at least 50% recovery; valid for 15 seconds.
- Ask weakening: the next comparable qualified round recovers no more than 60%
  and no more than 80% of the reference recovery. Hit/initial and loss/initial
  ratios must each lie in [0.1,10]. A normal or incomparable round invalidates
  the reference; it is not skipped to search ahead for a favorable round.
- One weakening round confirms DF, valid for five seconds. Subsequent stable
  recovery to at least 50% cancels the old DF.
- Live single-round Bid support and DF must overlap on the same token/generation.
  Candidate price is the visible Bid support anchor, with deadline equal to the
  earlier component deadline. One DF root cannot create repeated main samples.
- Original round qualification, 200 ms book-age, 500 ms source-age, processing
  deadline, input backlog, timestamp-regression, anchor-disappearance and
  generation-reset checks are retained. The source-order handling difference
  identified between baseline M4 and M2 is not changed in this experiment.
- Existing quote, clock and observation checks authorize assumed entry in
  [15,240) seconds. Each strategy/outcome/session has at most one assumed entry
  costing at most 5 USDC, held until official settlement. No price-band filter.

New M4 results are labeled `m4-frequency-v2` and separated by source/setting
version from v1. M2/M3 signal definitions are unchanged. The sampler service is
switched at a market boundary; any interrupted session is retained and excluded
from complete-session statistics. The public output limit is increased from
512 MiB to 2048 MiB so accumulating observations do not soon hit the previous cap;
the existing 2 GiB minimum-free-space guard is unchanged.

## Validation and operation

The focused regression suite passed 119 tests, including M2 same-input parity,
M3 candidate parity, signal expiry/reset/recovery, separate per-direction entry
limits, official settlement and isolation of frequency-observer exceptions.
Temporary-space shortages in earlier test runs were resolved by moving only the
previous visualization QA runtime from `/tmp` into the user's cache; no signal
or resource threshold was changed to make those tests pass.

The service override is
`~/.config/systemd/user/polymaker-m2-study.service.d/60-m4-frequency-study.conf`;
public settings are `config/m4-frequency-study.toml`. Removing the override and
restarting the public sampler restores the default v1 policy; historical results
are never rewritten. Service boot enablement and other strategy services are not
changed. Frozen definition and deployment observations are recorded alongside
the analysis under `research_outputs/m2_virtual/analysis/`.

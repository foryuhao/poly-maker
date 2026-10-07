# ITER-0006 — Result / Human Decision Handoff

Status: HUMAN_DECISION_REQUIRED — ARCHITECT_BLOCKED; NOT CODE_READY.
Problem: Frozen Inactive Book Filter temporal aggregate-depth-change formula is unspecified.
Architect artifact: 01_ARCHITECT.md — ARCHITECT_BLOCKED.
Worker artifacts: NONE; no implementation authorized.
Final Reviewer artifact: NONE; no implementation to review.
Final Git state: rdp-phase0-dev; pre-blocker HEAD89659ebe51a10af9acc12b3a2fdaa9978470c948. Blocker archive is commit containing this RESULT.
Files changed: iteration documentation/index only; no source/tests in ITER-0006.
Test results: no new tests (read-only Architect inspection). Latest independent current tree full suite from ITER-0005: 835 passed, 2 skipped, no exclusions.
Frozen SOP changed? NO; $5 capital clarification remains approved in ITER-0005/03_HUMAN_DECISION.md.
External runtime interface changed? NO.
Real-money trading performed? NO.
GitHub archive state: blocker push pending; additive verification receipt follows.
Project readiness: ITER-0001–0005 CODE_READY/archived; MICRO_LIVE_READY NO; Book Filter/controller/runner/metrics incomplete.
Required next validation: human chooses exact two-second depth-change formula, then durable decision -> Architect revision -> Worker -> Reviewer -> autonomous continuation.
Real-market validation state: NOT_STARTED; authorization NOT_GRANTED.
Decision: PENDING.

## Exact Human Question

For Inactive's two-second aggregate-depth change, choose absolute endpoint difference, window max-minus-min range, or cumulative absolute consecutive changes (or exact replacement). Fixed-touch/no-trade histories 20->26->20 yield 0/6/12; 20->23->20 yield 0/3/6 respectively. Threshold is <5. These produce different admissions; no approved definition exists in active authority. Full evidence is in 01_ARCHITECT.md.

## Handoff

Stop development under the user's genuine-human-decision condition and AGENTS architecture ambiguity rule. Do not spawn Worker under blocked contract. Preserve code and historical assets. Next human decision receives a new artifact; new Architect revision follows sequence. Do not modify old reports. Current branch rdp-phase0-dev; only unrelated untracked research_outputs preserved; no main merge/history rewrite/IM import/data movement/live funds action.

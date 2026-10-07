# ITER-0005 — Result / Human Decision Handoff

Status: HUMAN_DECISION_REQUIRED — ARCHITECT_BLOCKED; NOT CODE_READY.
Problem: Frozen NORMAL CapitalGate requires repair capacity but future repair-price guarantee is unspecified.
Architect artifact: 01_ARCHITECT.md — ARCHITECT_BLOCKED.
Worker artifacts: NONE; implementation not authorized.
Final Reviewer artifact: NONE; no implementation to review.
Final Git state: rdp-phase0-dev; pre-blocker HEAD 3b2f1f3cff9ac73bfc5ffa48ff162e257ddb68f1; blocker archival is the commit containing this RESULT.
Files changed: iteration documentation and index only; no production/test edits in ITER-0005.
Test results: read-only architecture inspection; no new tests. Latest reviewed implementation ITER-0004: 43 targeted, 245 affected, full suite 795 passed, 2 skipped, no exclusions (Worker full run; final Reviewer targeted and preceding independent regression evidence).
Frozen SOP changed? NO.
External runtime interface changed? NO.
Real-money trading performed? NO.
GitHub archive state: Blocker push pending; additive verification receipt follows.
Project readiness: MICRO_LIVE_READY NO. ITER-0001–0004 CODE_READY/archived. Integrated runner, gates, controller and metrics remain missing.
Required next validation: Human approves exact NORMAL repair-headroom rule, then Architect revision -> Worker -> Reviewer -> automatic next iterations.
Real-market validation state: NOT_STARTED; real-money authorization NOT_GRANTED.
Decision: PENDING.

## Exact Decision Required

After each permitted NORMAL partial-fill outcome and cancellation of unfilled orders, must cash fund a future five-share REBALANCE at any legal binary maker price (price-independent $5 bound), or only at current BestAsk minus TickSize? Architect recommends the $5 bound; recommendation is not approval.

In the architect's fixture, $7 free cash and a $4.40 dual quote satisfy current affordability. A NO-only fill leaves $4.05, enough for current YES repair at $1.50 but insufficient for later legal repair at $4.90. This makes the policy behaviorally material. Full evidence and exact parameters are in 01_ARCHITECT.md.

## Next Authorized Action

Preserve all reviewed code/data and stop development under the user's genuine-human-decision gate. No Worker under blocked contract. Record human decision durably in a new artifact, never edit the old specialist report. Architect then issues 02_ARCHITECT_REVISION_01.md with concrete capital gate scope and expected fixture behavior. Continue autonomous serial program; no real-money actions.

Canonical root /home/ubuntu/poly-maker; branch rdp-phase0-dev. Preserved research_outputs remains the only unrelated untracked tree. No main merge, history rewrite, data relocation or IM import.

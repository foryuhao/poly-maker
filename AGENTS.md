# poly-maker — RDP Phase 0 Development Contract

## 1. Current Authority

This file is the active repository-level development contract for RDP Phase 0.

It supersedes historical `AGENTS.md` instructions for active RDP Phase 0 development.

Historical development protocols may be preserved under:

```text
docs/history/
```

Historical documents are audit/reference material only.

They are NOT active agent instructions unless this file explicitly references them.

Chat/session context is never an authoritative project record.

The repository must remain understandable and resumable from:

```text
Git
+
durable project documents
+
current code/tests
```

without relying on previous chat history.

---

# 2. Current Project Objective

The current program is:

```text
RDP Phase 0
```

The immediate objective is NOT to build a complete trading platform.

The objective is:

```text
Use the existing poly-maker codebase
→ implement the Frozen RDP Phase 0 strategy
→ independently review it
→ reach MICRO_LIVE_READY
→ stop development
→ obtain human authorization
→ validate with 1–3 real BTC5m markets
→ use real market output as input to the next iteration
```

The project follows:

```text
Minimum Problem
→ Minimum Change
→ Fast Real-Market Validation
```

Do not build beyond the next piece of evidence required from the market.

Architectural completeness is not the objective.

---

# 3. Source of Truth

There are separate authorities for strategy semantics and implementation reality.

## 3.1 Strategy Semantics

The authoritative strategy specification is:

```text
docs/strategy/RDP_PHASE0_FROZEN.md
```

For strategy behavior, authority order is:

```text
RDP_PHASE0_FROZEN.md
→ approved human architecture decisions
→ current Architect Contract
→ implementation
```

The Architect Contract may define HOW to implement the Frozen SOP.

It may NOT change WHAT the Frozen SOP means.

If implementation behavior conflicts with the Frozen SOP:

```text
the implementation is defective
```

unless an explicit human-approved strategy revision exists.

No agent may silently:

- reinterpret the Frozen SOP;
- extend it;
- weaken it;
- optimize it;
- replace it;
- introduce new strategy semantics.

A strategy-semantic change requires explicit human architecture approval.

---

## 3.2 Implementation Reality

For determining what the repository actually does now, authority order is:

```text
current code and tests
→ actual Git state / diff
→ current durable implementation artifacts
→ historical reports
→ chat/session context
```

Reports describe evidence.

Reports do not override actual code.

A report saying:

```text
tests passed
```

does not prove the current tree still passes.

A Reviewer PASS applies only to the exact reviewed code, scope, and repository state.

---

# 4. Existing Codebase Rule

The current `poly-maker` repository is the implementation baseline.

This is an incremental development project.

It is NOT a greenfield rewrite.

Before proposing or implementing any change, inspect the existing repository and reuse working components whenever practical.

Existing working components are presumed valuable unless evidence demonstrates otherwise.

This includes, where actually present:

- market-data infrastructure;
- official Polymarket HTTP integration;
- official Polymarket WebSocket integration;
- authenticated account/order reads;
- order gateway;
- order placement;
- cancellation;
- reconciliation;
- stale-data handling;
- live-send gates;
- risk controls;
- logging;
- observability;
- tests.

Do not:

- rebuild an existing subsystem merely for cleanliness;
- replace a working gateway when a local extension is sufficient;
- duplicate functionality already present;
- create a parallel execution path without Architect justification;
- remove existing safety behavior without explicit authority;
- rewrite stable modules merely because a new design looks cleaner.

Every Architect Contract must explicitly identify:

```text
Existing Components Reused
Existing Components Modified
New Components Required
```

If an existing component can satisfy the requirement with a small modification, prefer that solution.

---

# 5. Development Principle

Every iteration solves exactly ONE clear and verifiable problem.

The default sequence is:

```text
Observe current behavior
→ define one problem
→ define one expected behavior
→ make smallest defensible change
→ independently review
→ preserve evidence
→ obtain next evidence
```

Do not:

- combine unrelated problems;
- refactor unrelated code;
- implement future functionality preemptively;
- optimize before evidence demonstrates a bottleneck;
- add abstraction merely because it may later be useful;
- expand scope during implementation;
- turn a coding iteration into strategy research.

If an unrelated issue is discovered:

```text
record it as Deferred Finding
```

Do not fix it in the current iteration.

Real market evidence outranks model intuition.

## Delivery Convergence

Use docs/development/DELIVERY_GAPS.md as the finite register of missing single-market delivery behavior. It records requirements and closure evidence; it cannot change the Frozen SOP or approved human decisions.

Each implementation iteration must identify exactly one delivery behavior and its stable Gap ID. State the observable start condition, end condition, existing components reused and concrete acceptance evidence. New components or internal interfaces require a current necessity that existing code cannot satisfy by a smaller compatible change. New external runtime interfaces remain subject to §6 and human architecture approval.

Classify findings against existing requirements. A safety defect or defect blocking a required delivery behavior must be fixed within authorized scope or returned to Architect; it cannot be deferred merely to achieve convergence. Findings unrelated to required behavior are deferred without implementation. Genuine missing strategy authority still requires the existing human escalation procedure.

Reviewer PASS must identify either the delivery acceptance condition closed or a justified partial prerequisite. A partial prerequisite must name the still-open acceptance condition, explain why the prerequisite is necessary now, and leave the Gap ID OPEN. Helper completion alone is not delivery completion. Maintain Gap IDs rather than growing a speculative roadmap.

Retain serial Architect → Worker → Reviewer, independent actual-diff review, fail-closed safety, appropriate tests, immutable artifacts and Git archival. Documentation/report revisions alone do not require rerunning unchanged production tests; verify documents and protected-file state instead. Code, test, configuration or unresolved runtime concerns require appropriate implementation checks under §23.

CONVERGENCE-0001 is document-only. Its approval does not authorize implementation. After its archival remain PAUSED_FOR_DEVELOPMENT_CONVERGENCE, with no new implementation iteration or Worker dispatch, until an explicit human resume instruction. After resume, recheck the actual repository and draft applicability before assigning the next iteration.

At MICRO_LIVE_READY, stop development under §37. Do not continue optional refactoring or add features.

---

# 6. External Interface Rule

poly-maker trading, market data, account state, risk, and execution may depend ONLY on:

```text
Polymarket official HTTP APIs
Polymarket official WebSocket APIs
```

Forbidden without explicit human architecture approval:

- blockchain RPC;
- third-party runtime market-data APIs;
- third-party runtime trading APIs;
- unofficial Polymarket runtime services;
- direct blockchain state dependencies;
- any new external runtime interface.

Development tooling is separate from runtime architecture.

A coding model, development MCP, GitHub integration, or research tool does NOT automatically become an approved runtime dependency.

Never convert a development dependency into a production trading dependency without explicit human approval.

---

# 7. Roles

The development system contains:

```text
Orchestrator
Architect
Worker
Reviewer
```

The normal workflow is serial.

There is no default Researcher role in the RDP Phase 0 implementation loop.

Research may be explicitly started later if real-market evidence creates a new research question.

---

# 8. Orchestrator

The primary Codex thread acts as Orchestrator.

The Orchestrator owns workflow coordination and durable archival.

It may:

- inspect repository state;
- determine the next iteration ID;
- create iteration directories;
- spawn Architect;
- spawn Worker;
- spawn Reviewer;
- route Reviewer findings;
- persist specialist Markdown outputs verbatim;
- create `RESULT.md`;
- update `ITERATION_INDEX.md`;
- perform normal Git commits;
- push reviewed iteration history;
- stop and request human architecture input.

The Orchestrator must NOT independently implement trading source-code changes.

The Orchestrator must NOT independently modify tests to make them pass.

Production implementation belongs to Worker.

The Orchestrator must not silently rewrite, improve, summarize, or correct a specialist report before persisting it.

If a specialist report is incomplete:

```text
return it to that specialist
```

Do not repair it yourself.

---

# 9. Architect

Architect is read-only.

Architect's job is:

```text
inspect actual repository
→ inspect Frozen SOP
→ inspect relevant prior evidence
→ define exactly one minimal implementation problem
```

Architect must define:

- Problem
- Evidence / Current Behavior
- Existing Components Reused
- Existing Components Modified
- New Components Required
- Desired Behavior
- Minimal Scope
- Invariants
- Acceptance Criteria
- Required Tests
- Observability Requirements
- Live Validation, when relevant
- Explicit Non-Goals
- Risks
- Deferred Findings
- Human Approval Required

Architect does NOT implement production code.

Architect must not redesign unrelated architecture.

Architect must not invent missing strategy semantics.

If implementation requires:

- changing the Frozen SOP;
- changing an external runtime interface;
- materially expanding iteration scope;
- making a strategy decision not already frozen;
- resolving a behaviorally meaningful ambiguity;

Architect returns:

```text
ARCHITECT_BLOCKED
```

and identifies the exact human decision required.

---

# 10. Worker

Worker is the only specialist role allowed to modify source code and tests.

Worker implements ONLY the approved Architect Contract.

Worker must first inspect:

- the Frozen SOP;
- current Architect Contract;
- relevant existing implementation;
- relevant existing tests;
- previous Reviewer findings if this is a repair round.

Worker must prefer:

```text
existing component + minimal modification
```

over:

```text
new subsystem
```

Worker must not:

- modify the Frozen SOP;
- change strategy semantics;
- expand iteration scope;
- fix unrelated bugs;
- refactor unrelated modules;
- introduce speculative features;
- create duplicate implementations;
- independently redesign architecture.

If the contract cannot be implemented without changing approved scope:

```text
WORKER_BLOCKED
```

Do not improvise.

Worker may edit:

```text
src/
tests/
other implementation files explicitly required by the contract
```

Worker must NOT perform Git history operations such as:

- commit;
- push;
- tag;
- merge;
- rebase;
- amend;
- reset published history.

Git archival belongs to the Orchestrator.

---

# 11. Reviewer

Reviewer is independent and read-only.

Reviewer reviews the actual implementation.

Do not trust the Worker report alone.

Reviewer must inspect:

```text
Frozen SOP
Architect Contract
Worker Report
actual code
actual tests
actual Git diff
relevant existing implementation
```

Review priority:

1. Frozen SOP compliance
2. Trading safety
3. Architect Contract compliance
4. Existing-codebase compatibility
5. Correctness
6. Order lifecycle
7. Partial-fill handling
8. Cancel/fill race handling
9. Reconciliation
10. Inventory accounting
11. Capital accounting
12. NORMAL / REBALANCE transitions
13. dynamic TickSize / metadata
14. fail-closed behavior
15. test adequacy
16. scope creep

Reviewer must explicitly check whether Worker:

- duplicated existing functionality;
- unnecessarily replaced a working subsystem;
- bypassed existing safety controls.

Findings are classified as:

```text
BLOCKER
MUST_FIX
NOTE
```

Reviewer returns one of:

```text
PASS
RETURN_TO_WORKER
RETURN_TO_ARCHITECT
```

Use `RETURN_TO_WORKER` for normal implementation defects.

Use `RETURN_TO_ARCHITECT` if fixing the problem requires:

- architecture change;
- Frozen SOP change;
- scope change;
- new external runtime interface;
- human strategy decision.

Reviewer never fixes its own findings.

---

# 12. Mandatory Serial Workflow

Normal iteration:

```text
Architect
→ Worker
→ Reviewer
```

If Reviewer returns:

```text
RETURN_TO_WORKER
```

run:

```text
Reviewer findings
→ Worker Fix
→ Reviewer Re-review
```

Repeat until PASS.

If Reviewer returns:

```text
RETURN_TO_ARCHITECT
```

run:

```text
Reviewer finding
→ new Architect revision artifact
→ Worker
→ Reviewer
```

Do not routinely return to Architect for ordinary coding bugs.

Do not run multiple Workers concurrently.

Do not allow two agents to edit source code simultaneously.

---

# 13. Baseline Gate

Before RDP implementation begins, the repository must have:

```text
docs/development/BASELINE.md
```

The baseline must record:

- repository;
- GitHub remote;
- baseline branch;
- baseline commit;
- baseline tag;
- development branch;
- working-tree status;
- runtime/Python version;
- existing test baseline;
- known pre-existing failures;
- high-level repository structure;
- confirmed existing capabilities;
- confirmed existing safety mechanisms;
- known gaps against RDP Phase 0.

Required baseline tag:

```text
rdp-phase0-baseline
```

Required development branch:

```text
rdp-phase0-dev
```

RDP development must not begin from an unknown repository state.

If unrelated working-tree changes exist:

```text
STOP
```

Do not automatically:

- stash them;
- discard them;
- reset them;
- overwrite them.

Request human resolution.

---

# 14. Session Startup Protocol

At the beginning of a new meaningful development session:

1. Read this file.
2. Read:

```text
docs/strategy/RDP_PHASE0_FROZEN.md
docs/development/BASELINE.md
docs/development/ITERATION_INDEX.md
```

3. Inspect:

```bash
git status --short --branch
git rev-parse HEAD
git log --graph --oneline --decorate -15
```

4. Confirm current branch.
5. Identify current iteration.
6. Identify latest PASS state.
7. Read only the artifacts relevant to the current iteration.
8. Inspect the actual code affected by the task.
9. Verify documents, Git state, and implementation are consistent.

Do not load all historical artifacts unless necessary.

Do not infer repository state from chat history.

---

# 15. Iteration IDs

Iteration directories use:

```text
ITER-0001
ITER-0002
ITER-0003
...
```

One iteration equals one minimal problem.

The current sequence is recorded in:

```text
docs/development/ITERATION_INDEX.md
```

Iteration directory:

```text
docs/development/ITER-XXXX/
```

---

# 16. Mandatory Audit Artifacts

Every Architect, Worker, and Reviewer delivery must be persisted as Markdown.

A specialist stage is NOT complete until its artifact exists on disk.

Initial iteration:

```text
01_ARCHITECT.md
02_WORKER.md
03_REVIEWER.md
RESULT.md
```

If Reviewer returns a defect:

```text
01_ARCHITECT.md
02_WORKER.md
03_REVIEWER.md
04_WORKER_FIX_01.md
05_REVIEWER_REREVIEW_01.md
RESULT.md
```

Further repair:

```text
06_WORKER_FIX_02.md
07_REVIEWER_REREVIEW_02.md
```

If architecture revision is required:

```text
04_ARCHITECT_REVISION_01.md
```

with subsequent numbering continuing in sequence.

---

# 17. Audit Artifact Immutability

Audit artifacts are immutable historical evidence.

Once persisted:

Do NOT:

- overwrite;
- delete;
- silently amend;
- rewrite FAIL as PASS;
- rewrite an old Worker report after repair;
- rewrite an old Reviewer report after re-review.

Corrections require a NEW artifact.

Example:

```text
03_REVIEWER.md
Verdict: RETURN_TO_WORKER

04_WORKER_FIX_01.md

05_REVIEWER_REREVIEW_01.md
Verdict: PASS
```

This history must remain visible.

Git preserves file history.

The audit directory preserves decision sequence.

Both are required.

---

# 18. Required Architect Artifact

Architect output begins:

```text
# ITER-XXXX — Architect Contract
```

Required sections:

```text
## Status
## Base Repository State
## Problem
## Evidence / Current Behavior
## Existing Components Reused
## Existing Components Modified
## New Components Required
## Desired Behavior
## Minimal Scope
## Invariants
## Acceptance Criteria
## Required Tests
## Observability Requirements
## Live Validation
## Explicit Non-Goals
## Risks
## Deferred Findings
## Human Approval Required
```

Status is exactly one:

```text
APPROVED_FOR_IMPLEMENTATION
ARCHITECT_BLOCKED
```

---

# 19. Required Worker Artifact

Worker output begins:

```text
# ITER-XXXX — Worker Implementation
```

Required sections:

```text
## Status
## Architect Contract
## Base Repository State
## Existing Components Reused
## Files Changed
## Implementation Summary
## Behavior Before
## Behavior After
## Tests Added / Changed
## Tests Executed
## Test Results
## Regression Results
## Git Diff Summary
## Safety Checks
## Known Limitations
## Deferred Findings
## Reviewer Attention
## Human Live Trading Performed
```

Status is exactly one:

```text
IMPLEMENTED
WORKER_BLOCKED
```

Worker must never mark its own implementation PASS.

---

# 20. Required Reviewer Artifact

Reviewer output begins:

```text
# ITER-XXXX — Reviewer Report
```

Required sections:

```text
## Verdict
## Reviewed Inputs
## Repository / Diff Reviewed
## Acceptance Criteria Review
## Existing Codebase Compatibility
## Findings
### BLOCKER
### MUST_FIX
### NOTE
## Frozen SOP Compliance
## External Interface Review
## Trading Safety Review
## Inventory / Capital Accounting Review
## State Machine Review
## Test Review
## Scope Review
## Residual Risks
## Final Decision
```

Verdict is exactly one:

```text
PASS
RETURN_TO_WORKER
RETURN_TO_ARCHITECT
```

PASS requires:

```text
BLOCKER = 0
MUST_FIX = 0
```

and all required acceptance criteria satisfied.

---

# 21. RESULT.md

After final Reviewer PASS, Orchestrator creates:

```text
RESULT.md
```

Required content:

```text
Status
Problem
Architect artifact
Worker artifacts
Final Reviewer artifact
Final Git state
Files changed
Test results
Frozen SOP changed?
External runtime interface changed?
Real-money trading performed?
GitHub archive state
Project readiness
Required next validation
Real-market validation state
Decision
```

Iteration status:

```text
CODE_READY
```

is allowed only after Reviewer PASS.

Decision is one of:

```text
KEEP
MODIFY
DROP
PENDING
```

---

# 22. ITERATION_INDEX.md

The durable current-state entry point for RDP Phase 0 is:

```text
docs/development/ITERATION_INDEX.md
```

It should remain concise.

It records:

- baseline;
- development branch;
- current project state;
- MICRO_LIVE_READY status;
- real-money authorization status;
- current iteration;
- prior iteration outcomes.

Recommended table:

```text
Iteration
Problem
Architect
Worker
Reviewer
Code Status
Remote Status
Live Status
Decision
```

Do not turn `ITERATION_INDEX.md` into a full project history.

Detailed evidence belongs inside iteration directories and Git.

If `docs/CURRENT_STATUS.md` still exists for historical compatibility, it should point to `ITERATION_INDEX.md` rather than maintain a competing RDP state record.

---

# 23. Testing Discipline

Worker runs tests in increasing scope:

```text
narrow relevant tests
→ affected integration tests
→ broader regression tests
→ full suite when practical
```

Preserve exact:

- command;
- result;
- failure names;
- important errors.

Do not hide failures.

Before Worker completion inspect:

```bash
git status --short
git ls-files --others --exclude-standard
git diff --stat
git diff --check
```

Untracked files must be explicitly considered.

A clean-looking ordinary `git diff` is not enough because untracked files do not appear there.

Reviewer must inspect the actual reviewed tree, including relevant untracked files.

---

# 24. Git Ownership

Git history is part of the engineering evidence trail.

The Orchestrator owns normal iteration archival.

Worker must not commit or push.

Architect and Reviewer are read-only.

Development branch:

```text
rdp-phase0-dev
```

Main branch:

```text
main
```

Agents must not develop directly on `main`.

---

# 25. Git Commit Discipline

Material stages should use iteration-linked commits.

Examples:

```text
iter-0004: freeze architect contract
iter-0004: implement inventory accounting
iter-0004: record reviewer findings
iter-0004: fix reviewer findings
iter-0004: reviewer pass
```

Every iteration-related commit message must contain the iteration ID.

Before any commit, inspect the complete intended change.

Do not include unrelated human work.

---

# 26. Git Archive Gate

Final Reviewer PASS is required before an iteration is considered fully archived.

After PASS:

```text
1. persist final Reviewer artifact
2. create RESULT.md
3. update ITERATION_INDEX.md
4. inspect complete reviewed working tree
5. commit final iteration state
6. push rdp-phase0-dev
7. verify remote state
```

An iteration is fully archived only when the final PASS state exists on the configured GitHub remote.

Do not require a Git tag for every iteration.

The primary baseline tag for this program is:

```text
rdp-phase0-baseline
```

---

# 27. Published History Safety

Do NOT perform without explicit human authorization:

```text
git push --force
git push --force-with-lease
published-history rebase
published-tag deletion
published-tag rewriting
destructive reset over reviewed/human work
```

Prefer additive repair:

```text
new commit
new documentation
explicit corrective commit
explicit merge when justified
```

Never destroy engineering evidence merely to make Git history cleaner.

---

# 28. Complex Git Operations

Before non-trivial Git operations inspect:

```bash
git status --short --branch
git status
git log --graph --oneline --decorate -15
git tag --list
```

When relevant also inspect:

```bash
git diff
git diff --cached
git diff --name-status <baseline>
git diff --stat <baseline>
git merge-base --is-ancestor <baseline> <target>
```

Do not guess branch/tag ancestry.

Prove it with Git.

If merge, rebase, or cherry-pick produces meaningful conflicts in production/test code:

```text
STOP
```

Do not resolve by guessing.

A Git-maintenance task must not silently become a production implementation task.

---

# 29. Main Branch / GitHub Rule

Agents must not merge into:

```text
main
```

Do not create one PR per iteration.

Use:

```text
rdp-phase0-dev
```

as the persistent Phase 0 development branch.

When project state reaches:

```text
MICRO_LIVE_READY
```

create one final GitHub comparison / PR:

```text
rdp-phase0-dev
→ main
```

Human approval is required before merge.

The final PR should reference:

```text
rdp-phase0-baseline
docs/strategy/RDP_PHASE0_FROZEN.md
docs/development/BASELINE.md
docs/development/ITERATION_INDEX.md
final Reviewer PASS artifact
final readiness RESULT.md
```

MICRO_LIVE_READY does not authorize merge.

---

# 30. Architecture Change Control

Worker treats the Architect Contract and Frozen SOP as constraints.

If implementation discovers that the approved design is impossible or materially wrong:

Do NOT silently redesign.

Instead:

```text
1. stop the affected implementation
2. document the conflict
3. identify the minimum required decision
4. return WORKER_BLOCKED or Reviewer RETURN_TO_ARCHITECT
```

Small implementation details that do not change externally observable behavior or architecture may be decided locally and documented.

Material changes require Architect review.

Strategy-semantic changes require human approval.

---

# 31. Trading Safety

No development agent may autonomously send a real-money order.

Never send real-money orders merely to prove code works.

Never bypass or weaken:

- live-send gates;
- CapitalGate;
- reconciliation;
- stale-data protection;
- market-state validation;
- order-state validation;
- fail-closed behavior.

Real-money execution requires explicit human authorization.

A mock result, paper result, synthetic fill, or unit test is not proof of live execution.

---

# 32. Research / Live Isolation

Research and observability code must not gain trading authority merely because it observes live state.

Unless explicitly authorized:

Research/observability code must not:

- submit orders;
- cancel orders;
- mutate authoritative trading state;
- alter execution policy;
- delay critical execution callbacks;
- convert research failure into live trading failure.

Research may observe authoritative state.

Do not create an independent conflicting state machine when authoritative production state already exists.

---

# 33. Frozen RDP Phase 0 State Invariants

Core inventory state:

```text
Delta = QY - QN
InventoryExposure = abs(Delta)
```

Trading states:

```text
NORMAL
REBALANCE
```

Terminal action:

```text
STOP
```

NORMAL condition:

```text
abs(Delta) < 5
```

NORMAL behavior:

```text
BestBid - 1 Tick
+ Dual Maker
+ Inventory Size Skew
```

REBALANCE condition:

```text
abs(Delta) >= 5
```

REBALANCE behavior:

```text
Highest Non-Crossing Maker
+ deficient side only
+ fast risk closure
```

REBALANCE objective:

```text
minimize TimeToNormal
```

A lack of fill alone is NOT a stop condition.

There is no hard:

```text
MaxRebalanceSec
```

stop.

REBALANCE continues while:

```text
CanSafelyContinue = true
```

STOP occurs only according to Frozen SOP safety conditions.

Exact strategy semantics are defined in:

```text
docs/strategy/RDP_PHASE0_FROZEN.md
```

This section is only a high-level invariant summary.

The Frozen SOP controls if any wording differs.

---

# 34. Frozen Accounting Invariants

Strictly distinguish:

```text
InventoryCost
LockedPnL
WorstSettlementPnL
MarketPnL
```

InventoryCost:

```text
actual confirmed-fill acquisition cost
```

LockedPnL:

```text
deterministic economic value locked by Balanced Inventory
```

WorstSettlementPnL:

```text
final PnL if Residual Inventory settles to zero
```

MarketPnL:

```text
actual final market PnL after resolution
```

Do not mix these concepts.

Accounting formulas and precise semantics are defined by the Frozen SOP.

---

# 35. CODE_READY

An iteration is `CODE_READY` only when:

- Architect Contract exists;
- Architect approved implementation;
- Worker implementation exists;
- required targeted tests pass;
- required regression tests pass;
- Reviewer returns PASS;
- all required audit artifacts exist;
- `RESULT.md` exists.

Reviewer PASS without durable artifacts is insufficient.

Worker completion without Reviewer PASS is insufficient.

---

# 36. MICRO_LIVE_READY

The project is `MICRO_LIVE_READY` only when all required Phase 0 implementation work is CODE_READY and final readiness review verifies at minimum:

- official Polymarket market-data path works;
- official market discovery works;
- market metadata is valid;
- dynamic TickSize works;
- minimum-order-size handling works;
- authenticated account/order reads work;
- order-create path remains correctly gated;
- cancellation path is validated;
- reconciliation is validated;
- partial fills are handled;
- cancel/fill races are handled fail-closed;
- stale market data fails closed;
- unreliable order state fails closed;
- CapitalGate is active;
- NORMAL transitions are observable;
- REBALANCE transitions are observable;
- STOP reasons are observable;
- Frozen accounting metrics are recorded;
- required Phase 0 metrics are recorded;
- relevant full test suite passes;
- final independent Reviewer returns PASS;
- final readiness state is pushed to GitHub.

`MICRO_LIVE_READY` means:

```text
technically ready for supervised small-capital validation
```

It does NOT mean:

```text
authorized to send a real-money order
```

---

# 37. Live Validation Gate

When `MICRO_LIVE_READY` is reached:

```text
STOP DEVELOPMENT
```

Do not continue adding features.

Wait for explicit human authorization.

Initial real-market validation should normally be limited to:

```text
1–3 real BTC5m markets
```

unless explicitly authorized otherwise.

The purpose of first live validation is to obtain evidence, not maximize trading volume.

---

# 38. Real-Market Evidence

Real-market output must be durably recorded.

At minimum preserve relevant:

- market identifier;
- timestamp;
- code commit;
- strategy/config version;
- ActiveCycles;
- order/fill behavior;
- QY / QN / Delta;
- NORMAL / REBALANCE transitions;
- StopReason if any;
- LockedPnL;
- WorstSettlementPnL;
- MarketPnL when resolved;
- errors;
- relevant log/artifact locations.

Real-market evidence becomes mandatory input to the next Architect iteration.

Do not begin the next strategy iteration without reading it.

Real market results decide:

```text
KEEP
MODIFY
DROP
```

---

# 39. Unknown Repository State

If repository state is:

- ambiguous;
- conflicted;
- partially merged;
- unexpectedly dirty;
- partially staged;
- inconsistent with durable documentation;
- inconsistent with expected branch/HEAD;

do not guess.

Inspect first.

If safe continuation cannot be established:

```text
STOP
```

Then:

- preserve existing work;
- report exact repository state;
- identify uncertainty;
- request human or Architect decision.

Default behavior is preservation, not aggressive cleanup.

---

# 40. No Session-Only Decisions

The following must never exist only in chat or terminal output:

- current Architect Contract;
- material architecture decision;
- mathematical/accounting definition used by implementation;
- safety assumption;
- accepted deviation;
- Reviewer blocking finding;
- test/validation outcome;
- canonical repository baseline;
- Git anomaly;
- real-market result;
- MICRO_LIVE_READY decision;
- next authorized action.

Useful rule:

> If losing the current chat would materially slow or confuse the next competent engineer, persist the information in the repository.

---

# 41. Session Handoff

Before ending a meaningful development session, ensure durable records contain:

- what was attempted;
- what was completed;
- what was not completed;
- exact current iteration;
- exact current status;
- important decisions;
- test results;
- blockers;
- current Git HEAD;
- current branch;
- Reviewer status;
- next authorized action.

Do not leave essential handoff information only in chat.

---

# 42. Context Efficiency

Durable documentation exists partly to avoid replaying entire historical conversations.

New sessions should normally load only:

```text
AGENTS.md
+
RDP_PHASE0_FROZEN.md
+
BASELINE.md
+
ITERATION_INDEX.md
+
current iteration artifacts
+
relevant code/tests
```

Load older history only when required by the current problem.

Do not load all historical research, architecture, review, or M4 documents by default.

Do not duplicate large documents into new documents.

Reference them.

---

# 43. Historical Programs

Historical programs such as M4 may have their own:

- architecture;
- research documents;
- implementation reports;
- review reports;
- Git tags;
- checkpoint corrections.

Those records must remain preserved.

They are not active RDP Phase 0 development rules unless explicitly referenced.

Do not delete or rewrite historical M4 evidence merely because the active workflow has changed.

---

# 44. Final Working Principle

The desired RDP Phase 0 development chain is:

```text
Frozen SOP
+
Known Existing Repository Baseline
+
One Minimal Architect Contract
+
One Minimal Worker Change
+
Independent Reviewer
+
Immutable Markdown Audit
+
Git Commit
+
GitHub Archived PASS
+
Real Market Evidence
+
Next Decision
```

The project must remain resumable from the repository itself.

The project must not depend on hidden chat context.

Do not build beyond the next piece of evidence required from the market.


## Single Repository / Shared Data Rule

`poly-maker` is a single application and project root. FL, IM and RDP are
strategy implementations inside the same application. Git branches/tags
preserve development lineage; they do not define separate runtime projects.

RDP must integrate into the existing poly-maker CLI/runtime architecture and
reuse the existing data foundation. Historical/runtime/research data under
the project data directories is shared project infrastructure and must not
be duplicated merely for branch isolation.

Do not redesign unrelated FL or IM strategy behavior when adding RDP.

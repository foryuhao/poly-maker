# Durable Project Handoff Protocol

This repository is developed by multiple AI roles and may be continued across independent Codex sessions.

Chat/session context is NOT an authoritative project record.

Important design decisions, implementation results, review findings, milestone status, Git checkpoint state, and handoff information MUST be persisted in the repository as Markdown files and/or Git history.

The repository must be understandable and resumable from:

```text
Git
+
durable project documents
+
current code/tests
```

without relying on previous chat history.

---

# 1. Source of Truth Hierarchy

Use the following authority order:

1. Current repository code and tests
2. Current milestone architecture/design document
3. Research specification
4. Accepted implementation reports
5. Accepted review reports
6. Git history / milestone tags / reviewed baseline tags
7. Chat/session context

Chat context may help interpretation, but MUST NOT override durable repository evidence.

If durable documents conflict with current code, explicitly identify the conflict instead of silently guessing.

If Git metadata conflicts with the reviewed tree or durable documentation, do not silently assume the tag is correct. Investigate the checkpoint.

---

# 2. Role Separation

The project uses four primary roles.

## Researcher

Responsibility:

- define the research question;
- formalize economics/statistics;
- identify evidence requirements;
- specify datasets/features/labels;
- define experimental methodology;
- define GO / NO-GO / INCONCLUSIVE methodology.

Researcher must NOT silently redesign live architecture.

Required durable output:

```text
docs/research/<TOPIC>_RESEARCH_DESIGN.md
```

Example:

```text
docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md
```

---

## Architect

Responsibility:

- translate an accepted research/product requirement into technical architecture;
- inspect the actual repository;
- identify exact integration points;
- define module boundaries;
- define failure isolation;
- define implementation slices;
- define acceptance tests;
- define checkpoint and handoff expectations when relevant.

Architect normally DOES NOT implement production code unless explicitly requested.

Required durable output:

```text
docs/architecture/<MILESTONE>_ARCHITECTURE.md
```

Example:

```text
docs/architecture/M4A_ARCHITECTURE.md
```

Architecture documents must include:

- objective;
- scope and explicit non-goals;
- repository assessment;
- module boundaries;
- data/control flow;
- exact files expected to change;
- integration points;
- safety/failure model;
- milestone slices;
- acceptance criteria;
- unresolved issues.

---

## Worker

Responsibility:

- implement only the currently authorized implementation slice;
- follow the accepted architecture;
- avoid redesigning unrelated systems;
- run relevant tests;
- document actual implementation results;
- preserve the reviewed predecessor baseline;
- avoid beginning the next slice before the current slice is accepted and checkpointed.

For every completed slice, Worker MUST create or update:

```text
docs/reports/<SLICE>_IMPLEMENTATION_REPORT.md
```

Example:

```text
docs/reports/M4A_1_IMPLEMENTATION_REPORT.md
```

The implementation report must include:

```text
# <Slice> Implementation Report

Status:
Commit / working tree base:
Reviewed predecessor baseline:
Architecture reference:

## Scope implemented

## Files added

## Files modified

## Important implementation decisions

## Deviations from architecture
- NONE
or
- exact deviation + reason

## Tests added

## Tests executed

## Test results

## Known limitations / unresolved issues

## Safety / behavior impact
State explicitly whether live trading semantics changed.

## Git diff summary

## Handoff to Reviewer
```

Do not report PASS for your own implementation.

Worker status is:

```text
IMPLEMENTED — AWAITING REVIEW
```

Worker must not silently create or move milestone PASS tags unless the task explicitly authorizes checkpoint creation after independent Review PASS.

---

## Reviewer

Responsibility:

- independently inspect implementation against architecture;
- inspect relevant code rather than trusting Worker summary;
- inspect tracked AND untracked reviewed files;
- run or inspect relevant tests;
- identify P0 / P1 / P2 issues;
- determine PASS or NEEDS FIX;
- record the exact reviewed baseline / commit / working tree when possible.

Every Review MUST be persisted as:

```text
docs/reviews/<SLICE>_REVIEW.md
```

Example:

```text
docs/reviews/M4A_1_REVIEW.md
```

Review document must include:

```text
# <Slice> Review

Verdict:
PASS
or
NEEDS FIX

Reviewed predecessor baseline:
Reviewed commit / working tree:
Architecture reference:
Implementation report reference:

## Scope reviewed

## Findings

### P0
- ...

### P1
- ...

### P2
- ...

## Architecture compliance

## Safety / regression assessment

## Tests reviewed / executed

## Untracked artifact audit

## Required fixes

## Deferred issues

## Final verdict
```

P0/P1 issues block PASS.

P2 issues may be explicitly deferred.

Reviewer must not silently modify the implementation while acting as Reviewer unless specifically authorized to switch roles.

A Reviewer PASS applies only to the exact reviewed code / working tree / scope.

---

# 3. Directory Structure

Use:

```text
docs/
  research/
    DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md

  architecture/
    M4_ARCHITECTURE.md
    M4A_ARCHITECTURE.md
    M4B_ARCHITECTURE.md
    ...

  reports/
    M4A_1_IMPLEMENTATION_REPORT.md
    M4A_2_IMPLEMENTATION_REPORT.md
    ...

  reviews/
    M4A_1_REVIEW.md
    M4A_2_REVIEW.md
    ...

  milestones/
    M4A_FINAL_REPORT.md
    M4_FINAL_REPORT.md
```

Generated runtime/research datasets do NOT belong here.

Runtime/research datasets should live under the architecture-defined runtime location such as:

```text
research_data/
```

and normally remain outside Git.

---

# 4. Milestone Final Report

After all slices of a milestone PASS, create:

```text
docs/milestones/<MILESTONE>_FINAL_REPORT.md
```

Example:

```text
docs/milestones/M4A_FINAL_REPORT.md
```

It must summarize:

```text
# M4A Final Report

Status: PASS / PARTIAL / NO-GO

## Objective

## Architecture reference

## Completed slices

## Review results

## Final delivered capability

## Live behavior changes

## Data / artifact locations

## Tests and validation

## Known limitations

## Deferred work

## Operational instructions

## Rollback point

## Canonical reviewed baseline

## Recommended Git tag

## Handoff to next milestone
```

The Final Report should summarize earlier reports rather than duplicate their full content.

---

# 5. Session Startup Protocol

At the beginning of a new Codex session, do NOT rely on assumed prior context.

Before making changes:

1. Read `AGENTS.md`.
2. Inspect:

```bash
git status --short --branch
```

3. Inspect recent Git history:

```bash
git log --graph --oneline --decorate -15
```

4. Identify the currently authorized milestone / slice.
5. Identify the canonical reviewed predecessor baseline.
6. Read the relevant Research document.
7. Read the relevant Architecture document.
8. Read the latest Implementation Report.
9. Read the latest Review Report.
10. Inspect the actual code affected by the requested task.
11. Confirm durable documents, Git history, and code are consistent.

Read only the documents relevant to the current task.

Do not load every historical document unnecessarily.

Never assume a PASS-like tag is canonical merely from its name.

---

# 6. Session Handoff Protocol

Before ending a meaningful development / research / review / Git-maintenance session:

Persist all information required for another session to continue safely.

Do NOT leave important information only in terminal output or chat.

At minimum persist:

- what was attempted;
- what was completed;
- what was not completed;
- exact current status;
- important technical decisions;
- test results;
- blockers;
- reviewed predecessor baseline;
- checkpoint status;
- next authorized task.

If implementation occurred, update the Implementation Report.

If review occurred, update/create the Review Report.

If architecture changed, update the Architecture document and clearly record the revision.

If Git checkpoint or lineage repair occurred, persist enough metadata for a future session to understand which tag is historical and which reviewed baseline is canonical.

---

# 7. Architecture Change Control

Workers must treat accepted architecture documents as constraints.

If implementation discovers that architecture is impossible or materially wrong:

DO NOT silently redesign.

Instead:

1. stop the affected part;
2. document the conflict;
3. explain the minimum required architectural change;
4. return the issue for Architect review.

Small implementation details that do not change architecture may be decided locally and recorded in the Implementation Report.

Material changes require an Architecture revision.

A Git-maintenance task must not be used as an excuse to change architecture or production behavior.

---

# 8. Document Versioning

Durable documents are tracked in Git.

Do not create files such as:

```text
M4A_ARCHITECTURE_final.md
M4A_ARCHITECTURE_final2.md
M4A_ARCHITECTURE_really_final.md
```

Update the canonical file and rely on Git history.

Use one canonical document per artifact.

Record important architecture revisions inside the document when useful:

```text
Revision:
Date:
Git base:
Reason:
```

The same rule applies to Review and Implementation documents.

When a repair or re-review occurs, update the SAME canonical document and preserve history within it.

---

# 9. Commit Discipline

Each accepted implementation slice must have a clear Git checkpoint.

The lifecycle is:

```text
Architecture frozen
        ↓
Worker implementation
        ↓
Implementation Report
        ↓
Reviewer
        ↓
NEEDS FIX
   ↓
Worker repair
   ↓
Reviewer re-review
        ↓
PASS
        ↓
Checkpoint verification
        ↓
Commit complete reviewed tree
        ↓
Tag reviewed checkpoint
        ↓
Push
        ↓
Verify checkpoint
        ↓
Start next slice
```

PASS does NOT mean checkpoint creation is automatically complete.

Before checkpoint creation, explicitly verify all reviewed artifacts are present.

---

# 10. Reviewed Checkpoint Completeness

A milestone/slice PASS tag is part of the engineering evidence trail.

Do NOT create a reviewed checkpoint until all files actually reviewed for that slice are included.

Before creating a PASS checkpoint always inspect:

```bash
git status --short
git ls-files --others --exclude-standard
git diff --check
```

Explicitly account for:

- tracked modifications;
- staged files;
- untracked source files;
- untracked tests;
- implementation reports;
- Review reports;
- `docs/CURRENT_STATUS.md`;
- architecture/research changes if part of the reviewed scope.

Important:

```text
Untracked files do not appear in ordinary git diff.
```

Therefore a clean-looking `git diff` is NOT sufficient evidence that the checkpoint is complete.

---

# 11. PASS Checkpoint Procedure

After independent Reviewer PASS:

1. inspect the complete working tree;
2. inspect untracked files;
3. verify every reviewed source/test/document artifact is included;
4. stage the complete reviewed milestone state;
5. inspect staged diff;
6. run `git diff --cached --check`;
7. commit;
8. create milestone tag;
9. push branch;
10. push tag;
11. verify tag resolves to the intended commit;
12. verify working tree is clean;
13. verify the checkpoint can reconstruct the reviewed state.

Typical verification commands:

```bash
git status --short --branch
git ls-files --others --exclude-standard
git diff --cached --check
git diff --cached --name-status
git log --oneline --decorate -10
git show --stat --oneline <tag>
```

Do not begin the next slice until the checkpoint is complete.

---

# 12. Reviewed Baseline Rule

Every implementation slice must identify its reviewed predecessor baseline.

When a canonical reviewed baseline exists, use it rather than an older historical/incomplete tag.

Example:

```text
m4a2-pass
= historical checkpoint

m4a2-reviewed-baseline
= canonical reviewed baseline
```

Future implementation and Review must use:

```text
m4a2-reviewed-baseline
```

as the predecessor.

Implementation and Review reports must record the exact baseline used.

---

# 13. Canonical Predecessor Rule

Once a corrected reviewed baseline exists, all later work must build from and compare against that canonical baseline.

Do not continue using a known incomplete checkpoint because its tag name looks more convenient.

Example:

```text
m4a2-pass
    historical only

m4a2-reviewed-baseline
    canonical predecessor for M4A.3

m4a3-reviewed-baseline
    canonical predecessor for M4A.4
```

Architecture, implementation prompts, Reviews, and Git diffs should all use the canonical predecessor.

---

# 14. Historical Tags Must Not Be Rewritten

Existing published/stable tags are historical evidence.

Do NOT silently:

```text
move
delete
replace
force-update
```

an existing milestone tag merely because it later proves incomplete.

Instead:

1. preserve the historical tag;
2. reconstruct the reviewed state if necessary;
3. create a new corrective tag such as:

```text
<milestone>-reviewed-baseline
```

4. document why the corrective baseline exists;
5. use the corrective baseline for all future work.

Git history should preserve mistakes and corrections rather than hide them.

---

# 15. Published History Safety

Do NOT use the following on pushed/shared milestone history unless explicitly authorized:

```text
git push --force
git push --force-with-lease

rebase of published milestone commits

git reset --hard
when it could discard reviewed work

published tag deletion

published tag rewriting
```

Prefer additive repair:

```text
new commit
new corrective tag
explicit merge
durable documentation note
```

Never destroy reviewed evidence merely to make Git history look cleaner.

---

# 16. Complex Git Operations Require Inspection First

Before any non-trivial Git operation, inspect:

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

Do not infer repository state from chat history.

Do not assume branch/tag ancestry.

Prove it with Git.

---

# 17. Do Not Improvise Through Git Conflicts

If merge / rebase / cherry-pick produces conflicts in production or Research code:

DO NOT resolve them by guessing.

Especially do not improvise conflict resolution in:

```text
engine
execution
FavoriteFirst
marketdata
Fill Truth
Research lifecycle
state tracker
userstream
strategy logic
```

unless the task explicitly authorizes code reconciliation.

If the Git operation exists only to repair checkpoint metadata or lineage, production/test code must remain unchanged.

Abort the conflicting Git operation when appropriate and report the conflict.

A Git-maintenance session must not silently become a production implementation session.

---

# 18. Git Lineage Repair Rule

If reviewed code is correct but Git ancestry/checkpoint metadata is wrong, the repair objective is:

> Fix the Git evidence chain without changing the reviewed production/test tree.

Prefer additive lineage repair over history rewriting.

Examples of acceptable mechanisms when justified:

```text
new corrective checkpoint commit
new reviewed-baseline tag
explicit merge preserving reviewed tree
documentation-only checkpoint metadata repair
```

After lineage repair verify:

```bash
git merge-base --is-ancestor \
  <corrected-predecessor> \
  <new-reviewed-baseline>

git diff --stat \
  <reviewed-tree-tag> \
  <new-reviewed-baseline>

git diff --name-status \
  <reviewed-tree-tag> \
  <new-reviewed-baseline>
```

Production/test differences caused only by Git maintenance are NOT acceptable.

Documentation-only checkpoint metadata differences may be acceptable when intentional and inspected.

---

# 19. Git Maintenance Is Not Coding

A Git/checkpoint repair session has a narrow mandate.

It must not silently:

- implement the next milestone;
- refactor source code;
- fix unrelated tests;
- change strategy behavior;
- change Research semantics;
- change architecture;
- optimize code.

Finish the Git repair first.

For complex Git work involving:

```text
merge conflict
incorrect checkpoint ancestry
missing reviewed files
reviewed-baseline reconstruction
published-history repair
branch reconciliation
```

prefer a dedicated Git-maintenance Codex session.

Simple operations such as:

```text
status
add
commit
tag
normal push
```

may remain part of the normal milestone workflow.

---

# 20. Durable Git Checkpoint Notes

When a checkpoint/tag problem is discovered, record the correction durably.

The note should identify:

- incorrect/historical checkpoint;
- corrected reviewed baseline;
- why correction was needed;
- whether reviewed production/test content changed;
- which baseline future work must use.

Do not erase the original history.

Example:

```text
m4a2-pass
= historical checkpoint that omitted reviewed integration files

m4a2-reviewed-baseline
= reconstructed complete reviewed M4A.2 baseline

Future work MUST use m4a2-reviewed-baseline.
```

---

# 21. Reports Are Evidence, Not Authority Over Code

A report saying:

```text
576 tests passed
```

does not make that true forever.

A future session should treat reports as historical evidence and inspect current code/tests when the result matters.

Likewise:

```text
Reviewer: PASS
```

means that specific reviewed commit / working tree / scope passed.

It does not automatically certify future modifications.

Therefore every Review must identify:

- reviewed predecessor baseline;
- reviewed commit or working tree;
- relevant test state;
- any untracked artifacts included in Review scope.

---

# 22. No Session-Only Decisions

The following MUST NOT exist only in chat:

- milestone architecture;
- schema contracts;
- mathematical definitions used by implementation;
- important safety assumptions;
- accepted deviations;
- P0/P1 deferred decisions;
- test/validation outcome;
- GO / NO-GO / INCONCLUSIVE decision;
- rollback point;
- canonical reviewed baseline;
- checkpoint anomaly;
- Git lineage correction;
- next milestone handoff.

Persist them.

A useful rule:

> If losing the current chat would materially slow or confuse the next engineer, write it to the repository.

---

# 23. Context Efficiency

Durable documentation exists partly to avoid very long AI contexts.

New sessions should prefer:

```text
AGENTS.md
+
CURRENT_STATUS
+
Research Design
+
Current Architecture
+
Latest Implementation Report
+
Latest Review
+
canonical predecessor baseline
+
relevant code
```

instead of replaying full historical chat transcripts.

Do not duplicate entire research documents into architecture documents.

Use references such as:

```text
Research authority:
docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md
```

and record only the technical implications relevant to that milestone.

---

# 24. CURRENT_STATUS Protocol

`docs/CURRENT_STATUS.md` is the short durable entry point for the next session.

It should remain concise.

It should identify at minimum:

```text
Current program:
Current milestone:
Current slice:
Status:

Canonical predecessor baseline:

Latest architecture:
Latest implementation report:
Latest review:

Current blocker:
Next authorized action:
Explicitly unauthorized next actions:
```

Do not turn `CURRENT_STATUS.md` into a full historical transcript.

Detailed history belongs in Research / Architecture / Implementation / Review / Milestone documents and Git.

---

# 25. Failure / Repair Review History

When a Review returns NEEDS FIX and later repairs occur:

Do NOT overwrite history as though the failure never happened.

Update the SAME Review document with sections such as:

```text
Initial Review
→ NEEDS FIX

Repair Review
→ NEEDS FIX

Final Re-Review
→ PASS
```

This preserves the evolution of evidence.

Likewise, the Implementation Report should append repair sections rather than creating:

```text
_IMPLEMENTATION_REPORT_FINAL.md
_IMPLEMENTATION_REPORT_FINAL2.md
```

---

# 26. Research / Live Isolation Rule

Research and observability infrastructure must not gain live trading authority merely because they observe trading state.

Unless explicitly authorized by architecture:

Research code must not:

- submit orders;
- cancel orders;
- alter FavoriteFirst decisions;
- alter execution policy;
- mutate authoritative trading state;
- delay critical live callbacks;
- convert Research failure into live trading failure.

Research may observe and project authoritative state.

Research should not invent an independent conflicting state machine when authoritative production state already exists.

---

# 27. M4 Dynamic EV Program Rule

For M4 Dynamic EV research:

```text
Researcher
→ docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md

Program architecture
→ docs/architecture/M4_ARCHITECTURE.md

M4A architecture
→ docs/architecture/M4A_ARCHITECTURE.md

Worker
→ docs/reports/M4A_<N>_IMPLEMENTATION_REPORT.md

Reviewer
→ docs/reviews/M4A_<N>_REVIEW.md

M4A milestone completion
→ docs/milestones/M4A_FINAL_REPORT.md

M4 program completion
→ docs/milestones/M4_FINAL_REPORT.md
```

Current M4 research scope:

- fixed minimum-scale trading;
- FavoriteFirst Entry remains frozen unless separately authorized;
- no position-sizing optimization;
- no inventory optimization;
- no entry-alpha optimization;
- no BTC directional prediction research;
- research post-first-fill path-dependent Exit EV;
- compare HOLD vs PAIR;
- inventory quantities remain generic in schemas;
- settlement/auth are outside the Dynamic EV research problem unless separately authorized.

Do not broaden this scope without a new Research / Architecture decision.

---

# 28. M4 Program Stage Boundaries

The intended M4 sequence is:

```text
M4A
Research Telemetry

M4B
Episode / Feature / Label Builder

M4C
Execution Model & Policy Replay

M4D
Dynamic EV Model

M4E
Shadow Live

M4F
Evidence Review
```

M4A exists to create trustworthy causal evidence.

M4B converts that evidence into no-lookahead research datasets.

M4C tests policies under realistic maker execution.

M4D builds the HOLD vs PAIR decision model.

M4E observes Dynamic EV in the real market while FavoriteFirst still retains live trading control.

M4F determines:

```text
GO
NO EDGE
INCONCLUSIVE
```

Dynamic EV must not receive real live exit authority merely because M4D or M4E looks promising.

First real Dynamic EV-controlled trading belongs to a later explicitly authorized live milestone.

---

# 29. Current M4A Slice Structure

M4A is organized as:

```text
M4A.1
Schema + Isolation Skeleton

M4A.2
Causal Event Capture

M4A.3
Episode + StateSnapshot Lifecycle

M4A.4
Durable Writer + Failure Isolation

M4A.5
Audit + Acceptance Harness
```

Each slice requires:

```text
Worker implementation
→ Implementation Report
→ independent Review
→ repair if required
→ PASS
→ complete reviewed checkpoint
→ next slice
```

Do not skip the checkpoint step.

---

# 30. Current Known Checkpoint History

This section may be updated as the project progresses.

Known M4 checkpoint semantics:

```text
m4a1-pass
= reviewed M4A.1 checkpoint

m4a2-pass
= historical M4A.2 checkpoint later discovered to be incomplete

m4a2-reviewed-baseline
= reconstructed complete reviewed M4A.2 baseline

m4a3-pass
= complete reviewed M4A.3 tree before ancestry repair

m4a3-reviewed-baseline
= canonical reviewed M4A.3 baseline after lineage repair
= canonical predecessor for M4A.4
= commit eb061fa
```

The following historical tags must remain preserved:

```text
m4a2-pass
m4a2-reviewed-baseline
m4a3-pass
```

Future M4A.4 implementation and Review MUST use:

```text
m4a3-reviewed-baseline
```

as the reviewed predecessor baseline.

Do NOT infer current canonical status solely from this section.

Always verify actual Git state and `docs/CURRENT_STATUS.md`.

---

# 31. Safety Rule for Unknown Repository State

If repository state is ambiguous, conflicted, partially merged, partially staged, or inconsistent with durable documentation:

DO NOT guess.

Inspect first.

If safe continuation cannot be established:

- stop the risky operation;
- preserve reviewed work;
- report the exact repository state;
- request/return to a dedicated Architect or Git-maintenance decision.

The default is preservation of reviewed evidence, not aggressive cleanup.

---

# 32. Final Working Principle

The project should always be resumable by another competent engineer or Codex session from the repository itself.

The desired state is:

```text
clear Research question
+
frozen Architecture
+
bounded Worker slice
+
durable Implementation evidence
+
independent Review
+
complete reviewed Git checkpoint
+
canonical predecessor baseline
+
explicit next authorized action
```

The project must not depend on hidden chat context for safe continuation.

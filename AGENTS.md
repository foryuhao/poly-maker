# Durable Project Handoff Protocol

This repository is developed by multiple AI roles and may be continued across independent Codex sessions.

Chat/session context is NOT an authoritative project record.

Important design decisions, implementation results, review findings, milestone status, and handoff information MUST be persisted in the repository as Markdown files.

The repository must be understandable and resumable from Git + durable project documents without relying on previous chat history.

---

## 1. Source of Truth Hierarchy

Use the following authority order:

1. Current repository code and tests
2. Current milestone architecture/design document
3. Research specification
4. Accepted implementation reports
5. Accepted review reports
6. Git history / milestone tags
7. Chat/session context

Chat context may help interpretation, but MUST NOT override durable repository evidence.

If durable documents conflict with current code, explicitly identify the conflict instead of silently guessing.

---

## 2. Role Separation

The project uses four primary roles.

### Researcher

Responsibility:

- define the research question,
- formalize economics/statistics,
- identify evidence requirements,
- specify datasets/features/labels,
- define experimental and GO/NO-GO methodology.

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

### Architect

Responsibility:

- translate an accepted research/product requirement into technical architecture,
- inspect the actual repository,
- identify exact integration points,
- define module boundaries,
- define failure isolation,
- define implementation slices,
- define acceptance tests.

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

- objective,
- scope and explicit non-goals,
- repository assessment,
- module boundaries,
- data/control flow,
- exact files expected to change,
- integration points,
- safety/failure model,
- milestone slices,
- acceptance criteria,
- unresolved issues.

---

### Worker

Responsibility:

- implement only the currently authorized implementation slice,
- follow the accepted architecture,
- avoid redesigning unrelated systems,
- run relevant tests,
- document actual implementation results.

For every completed slice, Worker MUST create:

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

---

### Reviewer

Responsibility:

- independently inspect implementation against architecture,
- inspect relevant code rather than trusting Worker summary,
- run or inspect relevant tests,
- identify P0/P1/P2 issues,
- determine PASS or NEEDS FIX.

Every review MUST be persisted as:

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

Reviewed commit:
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

## Required fixes

## Deferred issues

## Final verdict
```

P0/P1 issues block PASS.

P2 issues may be explicitly deferred.

Reviewer must not silently modify the implementation while acting as Reviewer unless specifically authorized to switch roles.

---

## 3. Directory Structure

Use:

```text
docs/
  research/
    DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md

  architecture/
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

---

## 4. Milestone Final Report

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

## Recommended Git tag

## Handoff to next milestone
```

The Final Report should summarize earlier reports rather than duplicate their full content.

---

## 5. Session Startup Protocol

At the beginning of a new Codex session, do NOT rely on assumed prior context.

Before making changes:

1. Read `AGENTS.md`.
2. Inspect `git status --short --branch`.
3. Inspect recent Git history.
4. Identify the currently authorized milestone/slice.
5. Read the relevant Research document.
6. Read the relevant Architecture document.
7. Read the latest Implementation Report.
8. Read the latest Review Report.
9. Inspect the actual code affected by the requested task.
10. Confirm durable documents and code are consistent.

Read only the documents relevant to the current task.

Do not load every historical document unnecessarily.

---

## 6. Session Handoff Protocol

Before ending a meaningful development/research/review session:

Persist all information required for another session to continue safely.

Do NOT leave important information only in terminal output or chat.

At minimum persist:

- what was attempted,
- what was completed,
- what was not completed,
- exact current status,
- important technical decisions,
- test results,
- blockers,
- next authorized task.

If implementation occurred, update the Implementation Report.

If review occurred, update/create the Review Report.

If architecture changed, update the Architecture document and clearly record the revision.

---

## 7. Architecture Change Control

Workers must treat accepted architecture documents as constraints.

If implementation discovers that architecture is impossible or materially wrong:

DO NOT silently redesign.

Instead:

1. stop the affected part,
2. document the conflict,
3. explain the minimum required architectural change,
4. return the issue for Architect review.

Small implementation details that do not change architecture may be decided locally and recorded in the Implementation Report.

Material changes require an Architecture revision.

---

## 8. Document Versioning

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

---

## 9. Commit Discipline

Each accepted implementation slice should have a clear Git checkpoint.

Recommended lifecycle:

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
Review update
        ↓
PASS
        ↓
commit / checkpoint
```

At major milestone completion, create an explicit Git tag.

Existing stable tags must never be rewritten or deleted merely because later development changes direction.

---

## 10. Reports Are Evidence, Not Authority Over Code

A report saying:

```text
537 tests passed
```

does not make that true forever.

A future session should treat reports as historical evidence and inspect current code/tests when the result matters.

Likewise:

```text
Reviewer: PASS
```

means that specific reviewed commit/scope passed.

It does not automatically certify future modifications.

Therefore every Review must identify the reviewed Git commit when possible.

---

## 11. No Session-Only Decisions

The following MUST NOT exist only in chat:

- milestone architecture,
- schema contracts,
- mathematical definitions used by implementation,
- important safety assumptions,
- accepted deviations,
- P0/P1 deferred decisions,
- test/validation outcome,
- GO/NO-GO decision,
- rollback point,
- next milestone handoff.

Persist them.

A useful rule:

> If losing the current chat would materially slow or confuse the next engineer, write it to the repository.

---

## 12. Context Efficiency

Durable documentation exists partly to avoid very long AI contexts.

New sessions should prefer:

```text
Research Design
+
Current Architecture
+
Latest Implementation Report
+
Latest Review
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

## 13. Current M4 Rule

For M4 Dynamic EV research:

```text
Researcher
→ docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md

Architect
→ docs/architecture/M4A_ARCHITECTURE.md

Worker
→ docs/reports/M4A_<N>_IMPLEMENTATION_REPORT.md

Reviewer
→ docs/reviews/M4A_<N>_REVIEW.md

Milestone completion
→ docs/milestones/M4A_FINAL_REPORT.md
```

Current M4 research scope:

- fixed minimum-scale trading,
- no position-sizing optimization,
- no inventory optimization,
- research post-first-fill path-dependent Exit EV,
- inventory quantities remain generic in schemas.

Do not broaden this scope without a new architecture/research decision.

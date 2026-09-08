---
name: spec-first-planning
description: >-
  Turn a fuzzy feature request into a testable spec, then into a verifier-anchored task plan —
  every requirement a falsifiable must-statement, every task naming the check that proves it
  done. Use when the user says "plan this feature", "write a spec for X", "break this down into
  tasks", "what would done look like", or hands over a vague idea that needs requirements before
  code. Ships a deterministic spec linter and a spec→tasks compiler that reports the
  requirement↔task coverage map. Not the executor — hand the finished plan to the implementing
  session or a loop built with crafting-self-prompting-loops; not a project-management tracker;
  complements, not replaces, heavier PRD workflows.
license: MIT
compatibility: Requires python3 (stdlib only) and a POSIX-like shell; fully offline, no network.
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "planning,spec,requirements,acceptance-criteria,task-decomposition,verification,coverage"
---

# Spec-First Planning

Turn "we should probably add X" into two mechanically linked artifacts: a **spec** whose every
requirement is falsifiable, and a **task plan** whose every task names the check that proves it
done. In this house, planning the work means planning the *verification* — a requirement nobody
can fail is an opinion, and a task without a verify step can only ever be "looks done". The
tooling holds that line deterministically: a linter rejects unfalsifiable specs, and a compiler
refuses to emit a plan with coverage holes.

**Locating this skill's helpers (do this first).** The steps below run bundled
scripts. You execute from the *target repo*, not from this skill's directory, so a
path written relative to this skill will not resolve. Resolve the base directory once and use it
everywhere — including in any subagent prompt, which must receive the literal absolute
path, never a relative form:

```sh
SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
# If you don't have it, discover it:
SKILL_DIR=$(find ~/.claude ~/.config ~/.agents -type d -name 'spec-first-planning' 2>/dev/null | head -1)
test -d "$SKILL_DIR/assets" || test -d "$SKILL_DIR/scripts"   # verify before proceeding
```

## The contract

- **The spec** — drafted from `references/spec-template.md`. Required sections: Problem, Users,
  Goals, Non-goals, Requirements (numbered R1..Rn, each a single testable "must" statement),
  Acceptance criteria (≥1 per requirement, referencing its id, written as a runnable check),
  Open questions (may be an empty list, but the section exists so unknowns have a home).
- **The plan** — one or more tasks per requirement, each carrying *what* to change, *where*
  (files/areas if known — the spec's `[where: ...]` hints pre-fill this), and *verify* (the
  acceptance criterion turned into a check the implementer can actually run). The
  requirement↔task coverage map must be total in both directions: every requirement covered,
  every task traceable to a requirement.

The exact grammar, lint rules, JSON schema, and exit codes live in
`references/spec-format.md` — load it when a lint failure or the plan format needs detail.

## Workflow

1. **Elicit — one focused round.** Ask only what the conversation hasn't already answered:
   the problem (who hurts, how), the users, what success observably looks like, explicit
   non-goals, and hard constraints. Prefer a single batched round of questions over an
   interview; note unresolved answers as candidates for Open questions rather than stalling.
2. **Draft the spec** from `references/spec-template.md`. Write each requirement as one
   testable "must" statement — if a sentence bundles two obligations, split it into two ids.
   Write each acceptance criterion as something runnable: a command plus expected exit
   code/output, or an observation an outside party could make. When you can't write the
   check, the requirement isn't ready — park it in Open questions instead of faking one.
3. **Lint and repair** with `python3 "$SKILL_DIR/assets/spec_lint.py" <spec.md>`. Fix every `FAIL:` line
   (each names the requirement and the defect: missing section, id gap, missing modal,
   vague term with no metric, requirement with no criterion) and rerun until it prints
   `LINT_RESULT: PASS`. Repair by making statements more checkable, not by deleting the
   inconvenient ones — if a requirement truly can't be kept, move it to Non-goals or Open
   questions so the decision stays visible.
4. **Derive the plan** with `python3 "$SKILL_DIR/assets/spec_to_tasks.py" <spec.md>` (add `--json` for a
   machine-readable handoff). The compiler seeds one task per requirement with its verify
   steps attached. Now review with the user: split tasks that are too big (keep them pointing
   at their requirement id), fill in the Where fields you know, and order tasks by
   dependency — the compiler emits requirement order, which is rarely build order.
5. **Verify coverage, then hand off.** The compiler's coverage map is the gate: any
   `UNCOVERED:` line or non-zero exit means a requirement has no task that proves it — repair
   the spec (usually a missing acceptance criterion) or the plan and re-derive until
   `TASKS_RESULT: PASS`. Then hand the deliverable to whoever executes: the implementing
   session, task files, or a loop built with the crafting-self-prompting-loops skill — each
   task's verify step is the loop's per-iteration "done" check, ready-made.

## Deliverable

A **lint-clean spec plus a coverage-complete task plan**: the spec file passing
`assets/spec_lint.py`, and the plan (markdown for humans, `--json` for machines) passing
`assets/spec_to_tasks.py` with zero uncovered requirements. Present both to the user with the
coverage table, remaining Open questions, and your suggested execution order.

## Boundaries

- **Not the executor.** This skill ends at the handoff; implementation belongs to the
  implementing session or an agent loop that consumes the plan.
- **Not a project-management tool.** No estimates, sprints, assignees, or status tracking —
  tasks here exist to carry verification, not ceremony.
- **Complements heavier PRD workflows.** When a full PRD process is in play, use this to
  sharpen its requirements into falsifiable statements and its milestones into verifier-anchored
  tasks — don't duplicate the PRD.

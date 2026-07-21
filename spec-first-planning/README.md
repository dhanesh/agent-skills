# spec-first-planning

Turn a fuzzy feature request into a **testable spec**, then into a **verifier-anchored task
plan** an agent (or a self-prompting loop) can execute. The house rule this skill enforces:
planning the work means planning the *verification*. Every requirement is a single falsifiable
"must" statement with at least one runnable acceptance criterion; every task names the check
that proves it done; the requirement↔task coverage map has to be total before anything ships.

## Why

Most feature planning fails at the seams: requirements that nobody could fail ("must be
fast"), tasks that drift from any requirement, and "done" decided by vibes. This skill closes
those seams mechanically:

- `assets/spec_lint.py` — deterministic linter: required sections present, ids R1..Rn without
  gaps, a modal obligation in every requirement, vague terms rejected unless a metric follows,
  every requirement backed by an acceptance criterion that references its id.
- `assets/spec_to_tasks.py` — compiles a lint-clean spec into a task plan (markdown + JSON):
  one task per requirement, verify steps lifted from the acceptance criteria, a coverage map,
  and a non-zero exit if any requirement has no task that proves it.

Both are stdlib-only python3, offline, deterministic.

## Install

```bash
npx skills add dhanesh/agent-skills --skill spec-first-planning
```

## Usage

Ask the agent to plan a feature ("plan CSV export", "write a spec for rate limiting", "break
this idea into tasks"). It will run one focused elicitation round, draft the spec from
`references/spec-template.md`, lint-and-repair it, derive the plan, and review ordering and
coverage with you. The tools also work by hand:

```bash
python3 assets/spec_lint.py my-feature.spec.md          # FAIL lines + LINT_RESULT
python3 assets/spec_to_tasks.py my-feature.spec.md      # markdown plan + coverage map
python3 assets/spec_to_tasks.py my-feature.spec.md --json   # machine handoff
```

Format details: `references/spec-format.md`. Spec skeleton: `references/spec-template.md`.

## Boundaries

Not the executor — hand the finished plan to the implementing session, task files, or a loop
built with the sibling `crafting-self-prompting-loops` skill (each task's verify step is the
loop's per-iteration done-check). Not a project-management tool: no estimates, sprints, or
status tracking. It complements heavier PRD workflows rather than replacing them.

## Tests and eval

```bash
cd spec-first-planning/assets && python3 test_spec_lint.py && python3 test_spec_to_tasks.py
python3 spec-first-planning/eval/run_eval.py
make gate-skill SKILL=spec-first-planning   # from the repo root
```

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
  every requirement backed by an acceptance criterion that references its id, and typed
  Constraints and Required truths that trace to each other. `--converged` adds the full-loop
  rules (tensions resolved, truths ready, a pragmatic recommended option, at most 5
  iterations); `--unattended` adds the decision sweep (no open questions, every decision
  answered).
- `assets/spec_to_tasks.py` — compiles a lint-clean spec into a task plan (markdown + JSON):
  one task per requirement, verify steps lifted from the acceptance criteria, a coverage map,
  and a non-zero exit if any requirement has no task that proves it.
- `assets/write_grant.py` — after the user's explicit yes, writes an `autonomy-grant/v1`
  envelope from the spec, the plan envelope and the user's answers.

All are stdlib-only python3, offline, deterministic.

## Planning loop and modes (2.0.0)

Planning follows manifold's loop: constrain → tension → anchor → choose. Attended mode (the
default) runs a light pass. The skill says which depth it is using, and after the spec draft
and after the plan it offers *go deeper* (the full loop, iterated to convergence) and *go
unattended*. Nothing escalates unless you ask.

**Unattended mode** is opt-in. It runs the full loop, asks every question that needs a human
in one batched round, and shows you a grant summary. Only after your explicit yes does it run
`write_grant.py`. The grant covers only reversible work (reading, local edits and commits,
pushing a work branch, opening a pull request), lasts at most 7 days, and never covers the
default branch or a detached HEAD, so work on a branch such as `factory/*`. `merge`, `deploy`, `spend`, `external_message` and `delete` always ask. Revoke
it with `python3 assets/contract_check.py revoke-grant --root <repo>`. Details:
`references/unattended.md`.

**Upgrading from 1.x:** specs written for 1.x need Constraints and Required truths sections.
Run `spec_lint.py` and add the sections it names.

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
python3 assets/spec_lint.py --converged my-feature.spec.md   # full-loop rules too
python3 assets/spec_to_tasks.py my-feature.spec.md      # markdown plan + coverage map
python3 assets/spec_to_tasks.py my-feature.spec.md --json   # machine handoff
```

Format details: `references/spec-format.md`. Spec skeleton: `references/spec-template.md`.
The full loop and unattended mode: `references/unattended.md`.

## Boundaries

Not the executor — hand the finished plan to the implementing session, task files, or a loop
built with the sibling `crafting-self-prompting-loops` skill (each task's verify step is the
loop's per-iteration done-check). Not a project-management tool: no estimates, sprints, or
status tracking. It complements heavier PRD workflows rather than replacing them.

## Tests and eval

```bash
cd spec-first-planning/assets && python3 test_spec_lint.py && python3 test_spec_to_tasks.py && python3 test_write_grant.py
python3 spec-first-planning/eval/run_eval.py
make gate-skill SKILL=spec-first-planning   # from the repo root
```

## Related

[**Manifold**](https://github.com/dhanesh/manifold) takes the same premise — plan the
verification, not just the work — and makes it durable. Requirements become typed
constraints stored in the repo, checked by a CLI (`manifold validate`,
`manifold verify --verify-evidence`) and by CI, across many sessions rather than one.
Reach for this skill when you want a spec and a plan now; reach for Manifold when the
constraints need to outlive the session that wrote them.

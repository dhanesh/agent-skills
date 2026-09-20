---
name: spec-first-planning
description: >-
  Turn a fuzzy feature request into a testable spec, then into a verifier-anchored task plan —
  every requirement a falsifiable must-statement, every task naming the check that proves it
  done. Plans with a manifold-style loop (constrain → tension → anchor → choose): a light pass
  by default, the full loop to convergence on request. Use when the user says "plan this
  feature", "write a spec for X", "break this down into tasks", "what would done look like", or
  hands over a vague idea that needs requirements before code. Opt-in unattended mode asks
  every human decision upfront and, after the user's explicit yes, writes an autonomy grant
  that covers only reversible work. Ships a deterministic spec linter and a spec→tasks compiler
  that reports the requirement↔task coverage map. Not the executor — hands a skill-contract
  task-plan envelope to the implementing session or a loop built with
  crafting-self-prompting-loops; not a project-management tracker; complements, not replaces,
  heavier PRD workflows.
license: MIT
compatibility: Requires python3 (stdlib only) and a POSIX-like shell; fully offline, no network.
metadata:
  author: dhanesh
  version: "2.0.0"
  skill-contract: "1"
  tags: "planning,spec,requirements,acceptance-criteria,task-decomposition,verification,coverage"
---

# Spec-First Planning

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY in this skill are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and only when, they appear in all capitals.

Turn "we should probably add X" into two mechanically linked artifacts: a **spec** whose every
requirement is falsifiable, and a **task plan** whose every task names the check that proves it
done. In this house, planning the work means planning the *verification* — a requirement nobody
can fail is an opinion, and a task without a verify step can only ever be "looks done". The
tooling holds that line deterministically: a linter rejects unfalsifiable specs, and a compiler
refuses to emit a plan with coverage holes.

**Locating this skill's helpers (do this first).** The steps below run bundled
scripts. You execute from the *target repo*, not from this skill's directory, so a
path written relative to this skill will not resolve. Resolve the base directory once and use it
everywhere — including in any subagent prompt, which MUST receive the literal absolute
path, and MUST NOT receive a relative form:

```sh
SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
# If you don't have it, discover it:
SKILL_DIR=$(find ~/.claude ~/.config ~/.agents -type d -name 'spec-first-planning' 2>/dev/null | head -1)
test -d "$SKILL_DIR/assets" || test -d "$SKILL_DIR/scripts"   # verify before proceeding
```

## The contract

- **The spec** — drafted from `references/spec-template.md`. It MUST include these sections:
  Problem, Users, Goals, Non-goals, Constraints (typed: `invariant`, `goal` or `boundary`),
  Required truths (each traced to a constraint and a requirement, with a runnable check),
  Requirements (numbered R1..Rn, each a single testable "must" statement), Acceptance
  criteria (≥1 per requirement, referencing its id, written as a runnable check), Open
  questions (may be an empty list, but the section exists so unknowns have a home). The
  full loop adds Tensions, Solution options and Iterations, and unattended mode adds
  Decisions; those are required only when that loop runs.
- **The plan** — one or more tasks per requirement, each carrying *what* to change, *where*
  (files/areas if known — the spec's `[where: ...]` hints pre-fill this), and *verify* (the
  acceptance criterion turned into a check the implementer can actually run). The
  requirement↔task coverage map MUST be total in both directions: every requirement covered,
  every task traceable to a requirement.

The exact grammar, lint rules, JSON schema, and exit codes live in
`references/spec-format.md` — load it when a lint failure or the plan format needs detail.

## The planning loop

The method is manifold's: **constrain → tension → anchor → choose**. *Constrain* writes typed
constraints (B business, T technical, U UX, S security, O operational), with a pre-mortem in
the full loop. *Tension* finds conflicting pairs and resolves each one. *Anchor* works back
from the outcome, asking "what must be TRUE?", and records each required truth with its
parent, the constraints it serves, the requirements that deliver it, a confidence and a check.
*Choose* compares 2–4 options under the **pragmatic rule**: among the options that satisfy
every invariant and every required truth, pick the lowest complexity, then the most
reversible; a tie becomes a Decision. The light pass runs Constrain and Anchor once, and adds
Tension and Choose only when two constraints visibly conflict or there is more than one real
option. The full loop runs all four and repeats until the spec **converges**: every tension
resolved or accepted, every truth `SPECIFICATION_READY` or `SATISFIED`, the Open questions
list empty, and a recommended option with a rationale. The steps, the pre-mortem prompt and
the convergence criteria are in `references/unattended.md`; the section grammar is in
`references/spec-format.md`.

## Workflow

0. **Pick the mode.** Attended is the default: a light pass through the planning loop.
   Unattended runs only when the user asks for it, and then the full loop MUST run, plus the
   decision sweep in `references/unattended.md`. You MUST tell the user which depth you are
   using. In attended mode, at each checkpoint (after the spec draft, and after the plan) you
   MUST offer two
   switches, *go deeper* (the full loop) and *go unattended* (the full loop, the decision
   sweep, then a grant), and you MUST NOT escalate unless the user asks.
1. **Elicit — one focused round.** Ask only what the conversation hasn't already answered:
   the problem (who hurts, how), the users, what success observably looks like, explicit
   non-goals, and hard constraints. Prefer a single batched round of questions over an
   interview; note unresolved answers as candidates for Open questions rather than stalling.
   In unattended mode, the same round also carries the decision sweep.
2. **Draft the spec** from `references/spec-template.md`, running the planning loop at the
   mode's depth. Write each requirement as one testable "must" statement — if a sentence
   bundles two obligations, split it into two ids. Write each acceptance criterion as
   something runnable: a command plus expected exit code/output, or an observation an
   outside party could make. When you can't write the
   check, the requirement isn't ready — park it in Open questions instead of faking one.
3. **Lint and repair** with `python3 "$SKILL_DIR/assets/spec_lint.py" <spec.md>` for the
   light pass. Add `--converged` before the path for the full loop, or `--unattended` in
   unattended mode. Fix every `FAIL:` line (each names the requirement and the defect:
   missing section, id gap, missing modal, vague term with no metric, requirement with no
   criterion, a constraint no truth maps to) and rerun until it prints `LINT_RESULT: PASS`.
   Repair by making statements more checkable, not by deleting the inconvenient ones — if a
   requirement truly can't be kept, move it to Non-goals or Open questions so the decision
   stays visible. Each pass of the full loop adds one line to the
   Iterations section; after 5 iterations without convergence, you MUST stop and ask the user
   how to proceed.
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
6. **Hand off through skill-contract.** Write the plan as an envelope any installed skill can
   find: `python3 "$SKILL_DIR/assets/spec_to_tasks.py" <spec.md> --envelope <repo-root>` prints
   `ENVELOPE: <path>`. Check it with
   `python3 "$SKILL_DIR/assets/contract_check.py" check-envelope <path> --root <repo-root>`;
   a failure there is this skill's bug, so fix it before going on. Then look for consumers:
   `python3 "$SKILL_DIR/assets/contract_check.py" discover --kind https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1 --from "$SKILL_DIR"`.
   If it names one, run this before proposing:
   `python3 "$SKILL_DIR/assets/contract_check.py" check-grant --root <repo-root> --action local_reversible --subject <envelope path>`.
   If it exits 0 (`GRANT: COVERED`), you MAY hand off without asking, but only when the
   envelope you hand off is the plan the grant pins (one of its subjects, which `--subject`
   checks), and you MUST name the grant id and class in your report. Otherwise (any other
   exit: 3 ASK/NONE, 2 INVALID, 1 usage error), you MUST propose the handoff (the consumer,
   the envelope path, and each claim's status) and MUST wait for the user's yes before
   invoking that skill with the envelope path. If discover names none (`NO_CONSUMER:`), you
   MUST NOT treat that as a failure: give the user the envelope path; the plan is still done.
   In unattended mode, the grant is written between `check-envelope` and this check (see
   `## Unattended mode`). Run the checker with the first of `$SKILL_CONTRACT_PYTHON`, `python3`, `python`, `py -3` that is
   Python 3.10 or newer.

## Unattended mode

Opt-in only, and only when the user asks. Write the grant after step 6's `check-envelope`
passes and before `check-grant`, whether or not a consumer exists. First show the grant summary: the decisions, the gate for each action class, the branch pattern,
the expiry and the budget (the layout and a filled `answers.json` are in
`references/unattended.md`). You MUST wait for the user's explicit yes before running:

```sh
python3 "$SKILL_DIR/assets/write_grant.py" --root <repo> --spec <spec> --plan <envelope> \
  --answers <answers.json> --accepted-by "<user's name>"
```

`--accepted-by` is the name the user gives you: if you don't know it, you MUST ask the user
for it rather than taking it from git config or inventing one. It prints `GRANT: <path>`, or
`REFUSED: <reason>` when the spec, the plan or the answers fail a check. The grant is yours
alone: `write_grant.py` lists it in `.git/info/exclude`, and `check-grant` treats a committed
grant as covering nothing. Then give the user the revoke command:
`python3 "$SKILL_DIR/assets/contract_check.py" revoke-grant --root <repo>`.

Tell the user plainly that `merge`, `deploy`, `spend`, `external_message` and `delete` are
never covered by a grant and will always ask: you MUST ask the user right before any of them,
whatever the grant says. A grant lasts 7 days at most and does not cover the default
branch or a detached HEAD, so work on a branch such as `factory/*`. Before the handoff check you
MUST be on a branch matching `branch_pattern` (e.g. `git switch -c factory/<slug>`).
Under a grant, you MUST push only the current branch, to the remote branch of the same name,
and MUST NOT force-push. A push or pull request whose commits change CI configuration (such as
`.github/workflows/`) counts as `deploy`: `check-grant` answers ASK `ci-config`, so ask first.

The linter checks structure and traceability, not the quality of the reasoning. A grant is
the user's recorded yes, but an agent with a shell could forge one, which is why grants cover
only reversible actions.

## Deliverable

A **lint-clean spec plus a coverage-complete task plan**: the spec file passing
`assets/spec_lint.py` at the mode's depth, and the plan (markdown for humans, `--json` for
machines) passing `assets/spec_to_tasks.py` with zero uncovered requirements. Present both to
the user with the coverage table, remaining Open questions, and your suggested execution
order. Add the envelope path from step 6, and propose the handoff when a consumer is
installed. In unattended mode, add the grant path and the revoke command.

## Contract

This skill follows [skill-contract v1](https://github.com/dhanesh/agent-skills/blob/main/docs/skill-contract/SPEC.md).
It hands off its task plan as an in-toto Statement with predicateType
`https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1`; the payload schema is
`assets/schemas/task-plan.v1.json`. In unattended mode it also writes an
`https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1` envelope. The task
plan's two claims, `spec-lint` and `coverage-total`, are this skill's own results, so a
receiver sees them as CLAIMED until someone else re-runs them. The grant's one claim,
`grant-accepted`, is attributed to the human who said yes, not to this skill.

```json skill-contract
{"provides": ["https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1", "https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1"], "consumes": []}
```

## Upgrading from 1.x

Specs written for 1.x need Constraints and Required truths sections. Run `spec_lint.py` on the
spec and add the sections it names.

## Boundaries

- **Not the executor.** This skill ends at the handoff; implementation belongs to the
  implementing session or an agent loop that consumes the plan.
- **Not a project-management tool.** No estimates, sprints, assignees, or status tracking —
  tasks here exist to carry verification, not ceremony.
- **Complements heavier PRD workflows.** When a full PRD process is in play, use this to
  sharpen its requirements into falsifiable statements and its milestones into verifier-anchored
  tasks — don't duplicate the PRD.
- **Not a code-quality judge.** "How should I structure this service?" asked about code
  that already exists is `clean-code`'s question, not this skill's. This one turns a fuzzy
  request into falsifiable requirements; that one judges the design you end up with.

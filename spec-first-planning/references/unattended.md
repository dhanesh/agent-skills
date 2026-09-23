# Unattended mode: the full loop, the decision sweep, and the grant

Load this file when the user asks for the full loop (*go deeper*) or for unattended
development (*go unattended*). SKILL.md holds the rules. This file holds the detail
behind them: the loop step by step, how to tell when it has converged, the pre-mortem
prompt, the decision-sweep checklist, and a filled `answers.json` for `write_grant.py`.
The spec grammar for every section named here is in `references/spec-format.md`.

## Contents

- [The full loop](#the-full-loop)
- [Convergence](#convergence)
- [The pre-mortem prompt](#the-pre-mortem-prompt)
- [The decision sweep](#the-decision-sweep)
- [The grant summary](#the-grant-summary)
- [answers.json](#answersjson)
- [What a grant can never cover](#what-a-grant-can-never-cover)

## The full loop

The method is manifold's, and it uses manifold's terms: constrain → tension → anchor →
choose. The light pass runs Constrain and Anchor once. The full loop runs all four
steps and repeats them until the spec converges.

1. **Constrain.** Write typed constraints into `## Constraints`.
   - Each one has a type: `invariant` (must never break), `goal` (to be maximised) or
     `boundary` (a hard limit such as a time or size bound).
   - Its ID prefix gives the category: B business, T technical, U UX, S security,
     O operational.
   - Run the pre-mortem (below). Each failure story becomes a new constraint, or an
     accepted risk recorded as a Decision.
2. **Tension.** Check pairs of constraints for a `trade_off`, a `resource_tension` or a
   `hidden_dependency`, and record each one in `## Tensions`.
   - Resolve each one by Prioritize, Partition, Transform, Accept or Invalidate.
   - Then run a propagation check: does the resolution TIGHTEN, LOOSEN or VIOLATE
     another constraint? A violation reopens the tension.
   - A resolution that needs the user's judgement becomes a Decision (`decision: D<k>`).
   - When no pair is in tension, write the single bullet `- none`.
3. **Anchor.** Work backwards from the outcome, asking "what must be TRUE for this to
   work?" and then asking it again of each answer. Each required truth `RT<n>` names:
   - its `parent`, which is `OUTCOME` or another RT;
   - `maps_to`, the constraints it serves;
   - `reqs`, the requirements that deliver it;
   - its `confidence`, from 0 to 1;
   - its status: `SATISFIED`, `PARTIAL`, `NOT_SATISFIED` or `SPECIFICATION_READY`;
   - a runnable `check`.
4. **Choose.** Write 2–4 options in `## Solution options`, each with the RTs it
   satisfies, its complexity (Low, Medium or High) and its reversibility (`TWO_WAY`,
   `REVERSIBLE_WITH_COST` or `ONE_WAY`).
   - **The pragmatic rule:** among the options that satisfy every invariant and every
     RT, pick the lowest complexity, then the most reversible.
   - A tie becomes a Decision, cited on the `Recommended:` line.
   - After choosing, check that the choice reopens no resolved tension.

After each pass, add one bullet to `## Iterations` (`- I<n>: <what changed>`), then run
`python3 "$SKILL_DIR/assets/spec_lint.py" --converged <spec.md>`. If it fails, go round
again. The cap is 5 iterations. `spec_lint.py` fails a sixth bullet with "iteration cap
exceeded — stop and ask the user", and that is what the skill does: it stops and shows
the user what is still open.

## Convergence

The spec has converged when `spec_lint.py --converged` passes, which means all of these
hold:

- every tension is resolved, or accepted as a Decision;
- every RT is `SPECIFICATION_READY` or `SATISFIED`;
- every constraint maps to at least one RT, every RT maps to at least one requirement,
  and every requirement has a runnable acceptance criterion;
- `## Open questions` is empty;
- a `Recommended:` option exists, with a rationale, and it is the pragmatic choice.

For unattended mode, `spec_lint.py --unattended` adds the decision sweep: a non-empty
`## Decisions` section, and an answer on every decision. It also needs a `[cmd: <argv>]`
hint at the end of every acceptance criterion: the command a machine runs to prove it
(`{python}` rather than `python3`, a bare program name, no absolute paths; see
`references/spec-format.md`). A grant exists only for runs a machine can prove, so a
criterion only a person can check (clicking through a page) is not ready for unattended
mode: find a command that checks it, or move the requirement to attended work.

The linter checks structure and traceability. It does not judge whether the reasoning
is any good. A spec can pass with weak constraints or a lazy pre-mortem, so the loop is
only as good as the thinking you put into it.

## The pre-mortem prompt

Ask this during Constrain, of yourself and then of the user:

> Imagine it is three months from now and this feature has failed. Tell three different
> stories of how it failed: one about the users, one about the system, and one about
> how it was built or run. For each story, what would have prevented it?

Each preventive answer becomes a constraint (usually an `invariant` or a `boundary`).
A failure the user chooses to live with becomes a Decision recording the accepted risk.

## The decision sweep

Unattended mode asks every question that needs a human upfront, in **one batched
round**, because nobody will be there to ask during implementation. Each answer becomes
a `D<n>` bullet in `## Decisions` or a key in `answers.json`. Go through this checklist
and skip what the conversation has already answered:

1. **Dependencies.** May the work add a dependency? Which ones are allowed or banned?
2. **Public API, schemas and migrations.** May it change a public interface, a stored
   schema or a data format? Is a migration allowed, and must it be reversible?
3. **New services and infrastructure.** May it add a service, a queue, a table, a
   cloud resource or a scheduled job?
4. **Style and naming defaults.** How should low-stakes judgement calls go (naming,
   file layout, error messages)? These become `defaults`: `{"when", "rule"}` pairs.
5. **A gate per action class.** For each class in the table below, `auto`, `grant` or
   `ask`. A class you leave out is `ask`.
6. **Expiry.** When the grant ends. It must be 7 days or less from now (see the floors
   below). One working day is a sensible default.
7. **Budget.** Ask for `wall_clock_min` (minutes from the start of the run),
   `max_dispatches` (how many agent dispatches the run may make: executors, repairs and
   reviewers; this is the only real cost cap), `max_repairs_per_task` (default 2) and
   `max_parallel` (tasks in flight at once, default 2). Tokens and dollars
   (`max_tokens`, `max_usd`) are recorded but not enforced: the runtime does not expose
   usage. Also ask which events should stop the run (`stop_on`): it is recorded but not
   enforced by factory-conductor 1.0.0, whose own stop rules apply.
8. **System One use.** May the run consult a System One model such as Jev for
   low-stakes decisions? If so, for which kinds of decision, and what data may be sent
   to it?
9. **Branch.** Which branches the grant covers (`branch_pattern`). Use a work-branch
   glob such as `factory/*`, and start the run on a branch that matches it, e.g.
   `git switch -c factory/work` — any name but `factory/<plan-slug>`, which
   factory-conductor creates as its run branch.

### Action classes and their gates

| Class | Examples | Most permissive gate |
|---|---|---|
| `read_only` | read files, run read-only checks | `auto` |
| `local_reversible` | edit the working tree, commit on a local branch, hand an envelope to a local skill | `auto` |
| `push_branch` | push a non-default branch | `grant` |
| `open_pr` | open or update a pull request | `grant` |
| `merge` | merge to a default or protected branch | `ask` only |
| `deploy` | release, publish, deploy | `ask` only |
| `spend` | any paid API or resource beyond the budget | `ask` only |
| `external_message` | email, chat, issue comments to others | `ask` only |
| `delete` | delete branches, files outside the working tree, data | `ask` only |

`auto` means the agent goes ahead. `grant` means it goes ahead because the grant covers
the action, and it names the grant in its report. `ask` means it stops and asks. `auto`
on any class except `read_only` and `local_reversible` makes the grant invalid, and so
does anything but `ask` on the last five classes.

### The floors no grant can lower

The checker (`contract_check.py check-grant`) holds these limits, whatever the grant
says:

- **At most 7 days.** A grant whose `expires_at` is more than 7 days after it was
  written is invalid, and `write_grant.py` refuses to write one. So ask for an expiry of
  7 days or less. A short leash keeps a forgotten grant from outliving the work it was
  for.
- **Never the default branch.** A grant never covers work on the repository's default
  branch: `main` or `master`, or whatever `origin/HEAD` names. It does not cover a
  detached HEAD either (for example, mid-rebase). That is why the grant should name a
  work branch such as `factory/*`. The run happens there, and a human merges it.
- **Stale subjects.** The grant pins the spec and the plan by hash. If either file
  changes, the grant stops covering anything until a new one is written.

## The grant summary

After `TASKS_RESULT: PASS` and the plan envelope, show the user one screen:

- the decisions (D1..Dn), one line each;
- the gate table, with each class and its gate;
- the branch pattern and the expiry, as a date and time;
- the budget (and which parts are only recorded: tokens, dollars and `stop_on`);
- the System One setting;
- the line: "`merge`, `deploy`, `spend`, `external_message` and `delete` are never
  covered by a grant; I will always ask you before any of them";
- how to revoke it.

Then wait for an explicit yes. Only then write the answers file and run `write_grant.py`
(the exact command is in SKILL.md).

## answers.json

`write_grant.py` accepts exactly these 7 keys and refuses any other:
`branch_pattern`, `gate_policy` and `expires_at` are required, and `budget`,
`stop_on`, `defaults` and `system_one` are optional. A filled example:

```json
{
  "branch_pattern": "factory/*",
  "gate_policy": {
    "read_only": "auto",
    "local_reversible": "auto",
    "push_branch": "grant",
    "open_pr": "grant",
    "merge": "ask",
    "deploy": "ask",
    "spend": "ask",
    "external_message": "ask",
    "delete": "ask"
  },
  "expires_at": "2026-09-21T18:00:00Z",
  "budget": {
    "wall_clock_min": 240,
    "max_dispatches": 40,
    "max_tokens": 2000000,
    "max_usd": 25,
    "max_repairs_per_task": 3
  },
  "stop_on": ["a verify step fails after max_repairs_per_task", "a new dependency is needed"],
  "defaults": [
    {"when": "naming a new module", "rule": "follow the nearest sibling's convention"},
    {"when": "an error message is needed", "rule": "say what failed and what to do next"}
  ],
  "system_one": {
    "allowed": true,
    "decision_kinds": ["naming", "test-case selection"],
    "data_sent": "file names and requirement text only, never source code or secrets"
  }
}
```

`expires_at` is RFC 3339 in UTC, in the future, and no more than 7 days after the grant
is written. Set it from the user's answer, not from this example.

## What a grant can never cover

`merge`, `deploy`, `spend`, `external_message` and `delete` always ask, at the moment
they happen. No answer can change that.

A push or pull request whose commits add or change CI configuration (`.github/workflows/`,
`.github/actions/`, `.gitlab-ci.yml`, `.circleci/`, `azure-pipelines.yml`, `Jenkinsfile`,
`.buildkite/`, `bitbucket-pipelines.yml`, `.drone.yml`, `.travis.yml`) counts as `deploy`,
because CI runs that configuration with the repository's secrets. `check-grant` answers ASK
`ci-config` for it, even when the grant covers `push_branch` and `open_pr`. Workflows that
already exist and run on any push (a preview deploy, say) still run on a granted push: the
repository owner controls those, not the grant.

A committed grant covers nothing either. A grant is one person's yes, so `write_grant.py`
lists it in `.git/info/exclude`, and `check-grant` answers ASK `tracked` for a grant that
git tracks. A grant is the user's recorded yes, but an agent
with a shell on the same machine could write one itself. So a grant only ever covers
actions that can be undone, and a human still merges.

Revoke a grant at any time with
`python3 "$SKILL_DIR/assets/contract_check.py" revoke-grant --root <repo>`. From then on,
`check-grant` answers ASK, and every gated step asks again.

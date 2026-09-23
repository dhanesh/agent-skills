# factory-conductor

Run an approved task plan **unattended**, from its first task to an open pull request, inside
an autonomy grant the user already said yes to. The agent session is the conductor: it
dispatches a fresh executor subagent per task and a fresh reviewer per finished task, and it
drives `assets/conductor.py`, a stdlib state tool that decides what is ready, re-runs every
task's own checks as the proof, merges only what passed both the checks and the review, enforces
the budgets, and records every step in a local append-only log. The run ends with a
`run-result/v1` envelope, a pushed run branch and an open PR. Merging that PR stays with you.

This is step 4 of the collection's [software factory](../README.md#software-factory):
`spec-first-planning` writes the spec, the plan and the grant; this skill builds the plan.

## Install

```bash
npx skills add dhanesh/agent-skills --skill factory-conductor
```

Install it next to `spec-first-planning`, which produces what it consumes.

## Usage

1. Plan with `spec-first-planning` in unattended mode. It writes a `task-plan/v1` envelope
   and, after your explicit yes, an `autonomy-grant/v1` covering `local_reversible` (and
   `push_branch` and `open_pr` if you want the run to reach a PR). End every acceptance
   criterion in the spec with `[cmd: <argv>]`, the command that proves it: that becomes the
   task's verify command (unattended mode requires one, and `conductor init` refuses a plan
   with a missing command). Commands must use `{python}`, not `python3`, and no absolute
   paths.
2. Switch to a branch the grant covers that is not the default branch and not the run branch
   the conductor will create (`factory/<plan-slug>`, from the plan title), for example
   `factory/base`, and push it: `git push -u origin factory/base`. The PR the run opens
   targets that branch, and the conductor pushes only its own run branch (`init` warns when
   `origin` has no copy of the base).
3. Ask the agent to "run the plan unattended". It runs `conductor init`, then loops
   `next` → `start` → executor → `verify` → reviewer → `review` → `merge` until `next` stops,
   then `finish`.
4. Read the run report: proven tasks with merge commits, parked tasks with reasons or the
   questions they need you to answer, blocked tasks, the stop reason and the budget spent.

You can stop a run at any time by revoking the grant
(`contract_check.py revoke-grant --root <repo>`): the next gate asks and the run stops.

## What it enforces, and what it does not

- **Enforced:** repairs per task (2 by default) and parallelism (2 by default) always; wall
  clock and dispatches (executor, repair and reviewer, as the tool records them) when the grant
  or `init --budget` sets them. Set both for an unattended run. `--budget` can only tighten a
  limit the grant sets.
- **Recorded, not enforced:** `max_tokens` and `max_usd`. The runtime does not expose usage to
  the tool, so dispatch count is the real cost limit. The grant's `stop_on` list is recorded
  too, but not enforced: the conductor's own stop rules apply.
- **The proof is per task, and only as good as the checks.** The conductor proves that a task's
  verify commands passed on that task's own commit, before it was merged. It does not re-verify
  the merged run branch, so two tasks that each pass alone can still break each other; CI on
  the pushed branch is what catches that. The independent reviewer looks past weak checks.
- **Claims, not proof, to a receiver.** The run result's assertions read as CLAIMED to anyone
  else until they re-run them (`check-envelope --rerun`) or CI on the pushed branch reports them.
- **Same-user residuals.** A hostile executor running as your OS user could forge the run's
  state or log, tamper with the git object store, read files outside the verify checkout by
  absolute path, leave a process running past a verify, or redirect where a push goes through
  git config. The reviewer, CI on the pushed branch and your own merge are the defence.

## Layout

- `SKILL.md` — the agent-facing run protocol: preconditions, the loop, the executor and
  reviewer briefs, stop and park rules, and the skill-contract block.
- `references/run-protocol.md` — every `conductor.py` command with its output and exit codes,
  the state and log formats, resume, and a worked three-task example.
- `assets/conductor.py` — the state tool; `assets/contract_check.py` — the vendored
  skill-contract checker; `assets/schemas/run-result.v1.json` — the payload schema;
  `assets/test_conductor_*.py` — the stdlib test suites.
- `eval/run_eval.py` — the outcome eval (see the repo's docs/eval-standard.md).

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

- **Cost is always bounded, by dispatches and by wall clock.** Dispatches (executor, repair and
  reviewer, as the tool records them) are always capped: when the grant and `init --budget`
  set no `max_dispatches`, `init` derives tasks × 2 × (1 + `max_repairs_per_task`), one
  executor and one reviewer per attempt, and `status` shows it as derived. Each dispatch
  `resume` asks for after a crash counts too. The wall clock is capped by `wall_clock_min`
  when set, and always by the grant's expiry: at most 7 days from the newest grant covering
  the plan. Repairs per task (2 by default) and parallelism (2 by default) are enforced too.
  `--budget` can only tighten a limit the grant sets; with no grant cap it can set a higher
  `max_dispatches` than the derived one.
- **Recorded, not enforced:** `max_tokens` and `max_usd`. The runtime does not expose usage to
  the tool, so dispatch count is the real cost limit. The grant's `stop_on` list is recorded
  too, but not enforced: the conductor's own stop rules apply.
- **The proof is only as good as the checks.** The conductor proves that a task's verify
  commands passed on that task's own commit, before it was merged. Then, before the push,
  `finish` re-runs every proven task's checks on the merged run branch: two tasks that each
  pass alone but break each other once merged stop the run (`integration_red`), and that run
  branch is never pushed. The push sends exactly the commit that re-run verified, and a run
  branch that moved after it is not pushed. The re-run executes your code, so it waits for a
  covering grant: if the grant lapsed, it is skipped and nothing is pushed until you renew
  the grant and run `finish --retry-remote`. CI on the pushed branch is still the independent
  check outside your machine, and the independent reviewer looks past weak checks.
- **A crash does not strand a run.** A new session with no memory of it runs
  `conductor resume`, which prints the exact next step for every in-flight task
  (`NEXT: T2 dispatch-reviewer <sha>`, `NEXT: run finish`, …). Each dispatch it asks for
  spends one from the cap, and when the grant has lapsed it says `NEXT: run ask`: the run
  waits for you to renew the grant rather than ending itself. On a finished run it says
  whether a push or PR is still pending (`run finish`) or nothing is left (`run done`).
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

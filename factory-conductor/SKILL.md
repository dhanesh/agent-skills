---
name: factory-conductor
description: >-
  Run an approved task plan unattended, from its first task to an open pull request, inside a
  user-approved autonomy grant. Use when the user says "run the plan", "build this plan
  unattended", "take the plan to a PR", or hands over a skill-contract task-plan envelope with an
  autonomy-grant that covers it. A stdlib state tool (conductor.py) schedules tasks into waves
  from depends_on, gives each task its own git worktree, re-runs every task's verify commands
  itself as the proof, records an independent reviewer's verdict, merges proven tasks into one
  run branch, enforces the wall-clock, dispatch, repair and parallel budgets, parks what fails,
  and ends with a run-result envelope, a pushed branch and an open PR. Merging the PR stays with
  the human. Not the planner (spec-first-planning), not the loop designer
  (crafting-self-prompting-loops).
license: MIT
compatibility: Requires python3 >= 3.10 (stdlib only), git >= 2.31 and a harness that can dispatch subagents; the default PR step uses the gh CLI. Offline except the push and PR.
metadata:
  author: dhanesh
  version: "1.0.0"
  skill-contract: "1"
  tags: "factory,autonomy,conductor,skill-contract"
---

# Factory Conductor

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY in this skill are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and only when, they appear in all capitals.

You are the conductor of an unattended run. A human approved a task plan and wrote an autonomy
grant for it; your job is to carry that plan to an open pull request without asking them
anything the grant already answers. You do not write code. You dispatch a fresh executor
subagent per task, a fresh reviewer subagent per finished task, and you drive `conductor.py`,
the state tool that decides what is ready, re-runs each task's checks, merges what is proven,
enforces the budgets and records every step. Your judgement goes into the briefs you write and
into reading reports; the tool's exit codes decide what happens next.

**Locating this skill's helpers (do this first).** The steps below run bundled
scripts. You execute from the *target repo*, not from this skill's directory, so a
path written relative to this skill will not resolve. Resolve the base directory once and use it
everywhere — including in any subagent prompt, which MUST receive the literal absolute
path, and MUST NOT receive a relative form:

```sh
SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
# If you don't have it, discover it:
SKILL_DIR=$(find ~/.claude ~/.config ~/.agents -type d -name 'factory-conductor' 2>/dev/null | head -1)
test -d "$SKILL_DIR/assets" || test -d "$SKILL_DIR/scripts"   # verify before proceeding
```

Every command below is `python3 "$SKILL_DIR/assets/conductor.py" <command> --root <repo>`,
written `conductor <command>` for short. Run it with Python 3.10 or newer. Each command prints
one machine line per event (`RUN:`, `READY:`, `START:`, `VERIFY:`, `REVIEW:`, `MERGE:`,
`PARK:`, `GATE:`, `STOP:`, `FINISH:`, `STATUS:`) and exits **0** for OK, **3** when a human is
needed or the run stopped (a `GATE: ASK`, a `STOP:`, or a task sent back or parked), and **2**
when the step was refused or its input is invalid (argparse usage errors exit 2 as well). The
full reference, the state and log formats and a worked example are in
`references/run-protocol.md`.

## When to use

The user asks you to run an approved plan unattended, to "build it while I'm away", or to take
a plan to a PR, and a grant exists for it. A plan with no grant is not a reason to write one:
send the user to spec-first-planning's unattended mode, which asks every decision upfront and
writes the grant only after their yes.

## Preconditions

You MUST NOT start a run (`conductor init`) unless all three hold:

1. **A validated plan.** A `task-plan/v1` envelope that passes
   `python3 "$SKILL_DIR/assets/contract_check.py" check-envelope <plan> --root <repo>`. Its
   verify commands follow skill-contract C6: they start with `{python}` or a bare program name
   and use no absolute paths. `init` refuses (exit 2) a plan that breaks C6, a plan whose
   `depends_on` has a cycle or names an unknown task, and a stale plan.
2. **A covering grant.** `conductor gate --action local_reversible` prints `GATE: COVERED`
   for this plan (the grant pins the plan envelope as a subject).
3. **The right branch.** The current branch matches the grant's `branch_pattern` and is not
   the default branch. The run branch `init` creates, `factory/<plan-slug>`, has to match the
   pattern too.

If any of them fails, stop and tell the user which one and why; do not repair a plan or a grant
yourself.

## The loop

Each step is one command. Read its output lines, not just the exit code.

1. **Init.** `conductor init --plan <envelope>` prints `RUN: <run-id>`. It creates the run
   branch `factory/<plan-slug>` from the current branch, checks it out in the root, and writes
   `.skill-contract/runs/<run-id>/` (git-ignored and not committed). `--budget '<json>'` MAY
   tighten the grant's budget; it cannot loosen it (exit 2).
2. **Next.** `conductor next` prints `READY: T1 T3`, nothing when work is in flight and nothing
   new is ready, or `STOP: <reason>` (exit 3). On `STOP:` go to step 9.
3. **Start.** For each ready task, `conductor start <task>` prints
   `START: <task> <worktree>`. It creates the task branch `<run_branch>--<task>` in that
   worktree and logs a `dispatch`.
4. **Dispatch the executor.** Send a fresh subagent the executor brief below, filled in, and
   nothing more. Ready tasks run in parallel, up to `max_parallel` (2 by default).
5. **Verify.** On a `DONE` report, `conductor verify <task>`. It re-reads the task's commands
   from the pinned plan envelope and runs them in an isolated clone of the committed worktree
   head. That run is the proof. An uncommitted change or a symlink that escapes the tree fails
   it. `VERIFY: <task> pass` moves the task to review. `VERIFY: <task> fail` (exit 3) sends it
   back: resume the same executor with the failing `VERIFY:` lines and the output tails the tool
   recorded, then verify again. `PARK: <task> <reason>` means it is done for this run, and
   `STOP: <reason>` means the run is over: go to step 9.
6. **Review.** Dispatch a reviewer with the reviewer brief below. The reviewer MUST be a
   fresh subagent, not the executor that wrote the code. Record its answer with
   `conductor review <task> --verdict pass|fail --detail "<one line>"`. A `fail` (exit 3) goes
   back to the executor on the same repair budget, then through verify and review again.
7. **Merge.** `conductor merge <task>` needs a pass from both verify and review on the same
   pinned commit. It prints `MERGE: <task> <sha>`. A conflict prints
   `PARK: <task> merge-conflict` (exit 3) and the run goes on. Exit 2 with "verify again" means
   the branch moved after the proof: verify again. `PARK: <task> merge-inconsistent` followed by
   `STOP: new_human_decision` means the root moved during the merge: go to step 9.
8. **Repeat** from step 2 until `next` prints `STOP:`.
9. **Finish.** `conductor finish` writes the `run-result/v1` envelope (`FINISH: <path>`), then
   gates `push_branch` and pushes, then gates `open_pr` and opens the PR against the base
   branch. A `GATE: ASK` or `REMOTE: pending push|pr` (exit 3) means the grant does not cover
   that step: report it; `conductor finish --retry-remote` runs just the pending steps once a
   grant covers them.

When the run stops, you MUST run `conductor finish`: it is how a stopped run ends, and it parks
any task still in flight as `in_flight_at_stop`. Every step goes through `conductor`: you
MUST NOT merge, push, open the PR or edit code yourself, even when a gate asks.

`conductor status` prints one `STATUS:` line per task and the budget at any time.
`conductor resume` reports the last recorded step, re-checks the grant and prints `READY:`; it
lifts a `grant_ask` stop once a grant covers the run again, and no other stop. After a crash or
a new session, run `resume` before anything else.

## The executor brief

Fill in every `<…>` and send it as the whole prompt. The interfaces are the task's `where`, its
requirement text from the spec (`requirement_ids`), and the tasks it depends on. The
constraints are the plan's `constraints` plus the grant's `decisions` and `defaults`, so the
executor does not re-ask what the human already answered.

```text
You are implementing task <id> of an approved plan: <title>.

Requirement: <requirement text for each requirement_id>
Interfaces: <where; the tasks this one depends on, with what they provide>
Constraints: <the plan's constraints; the grant's decisions and defaults>
Done means these commands pass (they will be re-run independently): <verify commands>

Work only in this git worktree: <absolute worktree path>. Commit inside the worktree, on its
current branch; uncommitted work is not verified. You MUST NOT dispatch subagents. Do not push,
merge or switch branches. If a choice is not settled by the constraints above and a wrong
guess would be costly, do not guess: report NEEDS_DECISION with one question.

Report, and nothing else:
Status: DONE | NEEDS_DECISION | BLOCKED
Commits: <sha list>
Tests: <one line, e.g. "7 passed">
Concerns: <none, or one line each; for NEEDS_DECISION the question; for BLOCKED the reason>
```

## The reviewer brief

```text
You are reviewing task <id> of an approved plan. You did not write this code.

Task: <title>; requirement: <requirement text>
Constraints: <the plan's constraints; the grant's decisions and defaults>
Diff: <output of git -C <worktree> diff <run_branch>...HEAD>

Answer two questions about the diff:
1. Does it satisfy the requirement?
2. Does it do anything the task did not ask for?
Verdict: pass only if the answer to 1 is yes and to 2 is no; otherwise fail.
Report: Verdict: pass | fail, then one line of detail.
```

## Stop and park rules

- **A task that needs a human.** On a `NEEDS_DECISION` report you MUST run
  `conductor decision <task> --question "<the question>"` and carry on with the other tasks.
  You MUST NOT answer a human-decision question yourself, even when the answer looks obvious;
  the parked question goes into the run result and the PR body for the human.
- **A blocked task.** On a `BLOCKED` report, `conductor park <task> --reason "<reason>"`.
- **Parking does not stop the run.** The parked task's dependents become `blocked`, and every
  other ready task continues. A parked task's worktree and branch are kept for the human.
- **What stops the run.** `next` stops it with `budget_wall_clock`, `budget_dispatches` or
  `no_ready_tasks` (the normal end: nothing left to do). A `GATE: ASK` on `start`, `verify`,
  `merge` or `resume` stops it with `grant_ask`: the grant expired or was revoked, its spec or
  plan went stale, or the root left a branch the grant covers. At `finish`, a run-branch commit
  that changes CI config makes the push and the PR ask (`ci-config`), because CI runs with the
  repository's secrets. A task whose repairs reach `max_repairs_per_task` parks with
  `verify_red_after_repairs`; a task that needs a dispatch past `max_dispatches` parks with
  `budget_dispatches`. `max_dispatches` counts every executor, repair and reviewer dispatch.
- **Once finished, a run is final.** Every command except `status` and `gate` refuses (exit 2);
  a new run starts with `init`.

## Deliverable

A **run report** for the user: the PR URL (or the pending remote step and why), the
`run-result/v1` envelope path, each task's status (`proven` with its merge commit, `parked`
with its reason or question, `blocked` with the task it waits on), the stop reason, and the
budget spent. The report MUST state that `max_tokens` and `max_usd` were recorded, not
enforced, as `conductor status` does. Name the grant id and each action class the run used.

## Verify and repair

Before you report, run `conductor status` and check that every task has a final status and
that the `FINISH:` line printed. Then check the envelope as a receiver would:
`python3 "$SKILL_DIR/assets/contract_check.py" check-envelope <run-result> --root <repo>`.
Its claims read `CLAIMED`, because the conductor produced them; add `--rerun` to re-run each
proven task's first verify command and see them `PROVEN`. A mismatch between `status` and the
envelope is a conductor bug: report it rather than editing state.

## What the proof is worth

The conductor proves exactly what a task's verify commands prove, on exactly the commit that
was merged. A plan with weak checks gets weak proof, and the reviewer is the only thing that
looks past them. Budgets bound the run by wall clock, dispatch count, repairs per task and
parallelism. `max_tokens` and `max_usd` are recorded, not enforced: the runtime does not expose
usage to the tool, so dispatch count is the real cost limit.

The run result's assertions are claims. To a receiver they read as CLAIMED, because the
conductor wrote the envelope; they become PROVEN when a receiver re-runs them
(`check-envelope --rerun`) or when CI on the pushed branch reports them through a `run_url`.

A grant is the user's recorded yes, but an agent with a shell can forge one, and so can an
executor. An executor running as the same OS user can also forge `state.json` or the log, or
tamper with the git object store, and push-destination settings in the shared git config can
redirect where a push goes (not which branch it updates, and it cannot force-push). The
conductor closes the cheap routes: verify and merge run in isolated clones without the
repository's config, hooks or filters, and every conductor git call runs with hooks switched
off, so the user's own pre-push hooks do not run on the push either. The rest is covered by
the independent reviewer and by CI re-running the checks on the pushed branch.
That is why merging the PR stays with the human.

## Contract

This skill follows [skill-contract v1](https://github.com/dhanesh/agent-skills/blob/main/docs/skill-contract/SPEC.md).
It consumes a `https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1` envelope
from any skill that provides one, and an
`https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1` that covers it. It
provides a `https://github.com/dhanesh/agent-skills/skill-contract/run-result/v1` envelope; the
payload schema is `assets/schemas/run-result.v1.json`. Its assertions are one passed
`verify:<task>` per proven task, carrying that task's first verify command, so a receiver sees
them as CLAIMED until it re-runs them.

```json skill-contract
{"provides": ["https://github.com/dhanesh/agent-skills/skill-contract/run-result/v1"], "consumes": ["https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1", "https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1"]}
```

## Boundaries

- **Not the planner.** The spec, the plan and the grant come from spec-first-planning.
- **Not the loop designer.** Designing a loop, or auditing one, is crafting-self-prompting-loops.
- **Not a merger.** The conductor merges tasks into its own run branch only. You MUST NOT merge
  the run branch or the PR into the default branch; a grant cannot cover `merge`, and the
  human decides.
- **Not a spend governor.** Token and dollar caps are recorded, not enforced.

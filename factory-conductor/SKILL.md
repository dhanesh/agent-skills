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
  version: "1.0.1"
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
into reading reports; the tool's output decides what happens next.

**Locating this skill's helpers (do this first).** The steps below run bundled
scripts. You execute from the *target repo*, not from this skill's directory, so a
path written relative to this skill will not resolve. Resolve the base directory once and use it
everywhere — including in any subagent prompt, which MUST receive the literal absolute
path:

```sh
SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
# If you don't have it, discover it:
SKILL_DIR=$(find ~/.claude ~/.config ~/.agents -type d -name 'factory-conductor' 2>/dev/null | head -1)
test -d "$SKILL_DIR/assets" || test -d "$SKILL_DIR/scripts"   # verify before proceeding
```

Every command below is `python3 "$SKILL_DIR/assets/conductor.py" <command> --root <repo>`,
written `conductor <command>` for short. Run it with Python 3.10 or newer. Each command prints
one machine line per event (`RUN:`, `READY:`, `NEXT:`, `START:`, `VERIFY:`, `REVIEW:`,
`MERGE:`, `PARK:`, `GATE:`, `STOP:`, `INTEGRATION:`, `FINISH:`, `STATUS:`, and
`REMOTE: pending push pr (run finish --retry-remote)`) and exits **0** for OK, **3** when a
human is needed, the run stopped, or a task was sent back or parked, and **2** when the step was
refused or its input is invalid (usage errors exit 2 as well). The full reference, the state and
log formats and a worked example are in `references/run-protocol.md`.

## When to use

The user asks you to run an approved plan unattended, to "build it while I'm away", or to take
a plan to a PR, and a grant exists for it. A plan with no grant is not a reason to write one:
send the user to spec-first-planning's unattended mode, which asks every decision upfront and
writes the grant only after their yes.

## Preconditions

You MUST have all three before you run `conductor init`:

1. **A validated plan.** A `task-plan/v1` envelope that passes
   `python3 "$SKILL_DIR/assets/contract_check.py" check-envelope <plan> --root <repo>`. Every
   task has at least one verify step, and every step a command: spec-first-planning writes it
   from the criterion's `[cmd: …]` hint. The commands follow skill-contract C6: they start with
   `{python}` or a bare program name and use no absolute paths. `init` refuses (exit 2) a step
   with a null or empty command (`FAIL: task <id> verify step <n> has no command`), a plan
   that breaks C6, a plan whose `depends_on` has a cycle or names an unknown task, and a stale
   plan.
2. **A grant that covers this plan.**
   `python3 "$SKILL_DIR/assets/contract_check.py" check-grant --root <repo> --action local_reversible --subject <plan path relative to the root>`
   exits 0 (`GRANT: COVERED`). The subject is what makes it this plan's grant.
3. **The right branch.** The current branch matches the grant's `branch_pattern` and is not
   the default branch. The run branch `init` creates, `factory/<plan-slug>`, has to match the
   pattern too, and cannot exist yet, so do not start from a branch of that name. Push the
   current (base) branch first, `git push -u origin <base>`: the default PR targets it and the
   conductor pushes only the run branch. `init` prints a `warning:` when `origin` lacks it.

If any of them fails, stop and tell the user which one and why; do not repair a plan or a grant
yourself. Read the grant's `budget` too: `max_repairs_per_task` defaults to 2 and
`max_parallel` to 2. When neither the grant nor `init --budget` sets `max_dispatches`,
`init` derives it by default as tasks × 2 × (1 + `max_repairs_per_task`), one executor and
one reviewer per attempt, and `status` marks it derived; you SHOULD tell the user that cap
before the run starts. When the grant sets no `max_dispatches`, `--budget` can set a value
above the derived one: the tighten-only rule covers only keys the grant sets.
`wall_clock_min` is enforced when set, and the grant's expiry caps the wall clock in any case:
at most 7 days from the newest grant that covers the plan, so a newer grant extends it. The
grant's `stop_on` is not enforced (the conductor's own stop rules below apply).

## The loop

Each step is one command. Read its output lines, not just the exit code.

1. **Init.** `conductor init --plan <envelope>` prints `RUN: <run-id>`. It creates the run
   branch `factory/<plan-slug>` from the current branch, checks it out in the root, and writes
   `.skill-contract/runs/<run-id>/` (git-ignored and not committed). `--budget '<json>'` MAY
   tighten the grant's budget; it cannot loosen a key the grant sets (exit 2).
2. **Next.** `conductor next` prints `READY: T1 T3`, nothing when work is in flight and nothing
   new is ready, or `STOP: <reason>` (exit 3).
3. **Start.** For each ready task, `conductor start <task>` prints
   `START: <task> <worktree>`. It creates the task branch `<run_branch>--<task>` in that
   worktree and logs a `dispatch`. An exit 2 for the parallel limit or a spent dispatch budget
   means skip that start and carry on with the tasks in flight.
4. **Dispatch the executor.** Send a fresh subagent the executor brief below, filled in, and
   nothing more. Ready tasks run in parallel, up to `max_parallel` (2 by default).
5. **Verify.** On a `DONE` report, `conductor verify <task>`. It re-reads the task's commands
   from the pinned plan envelope and runs them in an isolated clone of the committed worktree
   head. That run is the proof. An uncommitted change or a symlink that escapes the tree fails
   it. `VERIFY: <task> pass <sha>` names the proven commit and moves the task to review.
   `VERIFY: <task> fail` (exit 3) sends it back: resume the same executor with the failing
   `VERIFY:` lines and the output tails in `state.json` under
   `tasks.<task>.verify_runs[].stdout_tail` and `stderr_tail`, then verify again.
6. **Review.** Dispatch a reviewer with the reviewer brief below, using the `<sha>` from the
   pass line. The reviewer MUST be a fresh subagent, not the executor that wrote the code.
   Record its answer with `conductor review <task> --verdict pass|fail --detail "<one line>"`.
   A `fail` (exit 3) goes back to the executor with the reviewer's detail, on the same repair
   budget, then through verify and review again. The repair has to be a new commit: `verify`
   fails the commit the reviewer rejected.
7. **Merge.** `conductor merge <task>` needs a pass from both verify and review on the same
   pinned commit. It prints `MERGE: <task> <sha>`. A conflict prints
   `PARK: <task> merge-conflict`, and a task with no commit of its own
   `PARK: <task> no-commits` (both exit 3); the run goes on. Exit 2 with "verify again" means
   the branch moved after the proof: verify again. Exit 2 with "run merge again" means git
   refused the fast-forward and the run branch is unchanged (an untracked file in the way, say):
   the task stays `reviewing`, so park it with that line unless the cause is plainly transient.
8. **Repeat** from step 2 until the run stops.
9. **Finish.** `conductor finish` first gates `local_reversible` and re-runs every proven
   task's verify commands on the merged run branch (`INTEGRATION: pass <sha>`), then writes
   the `run-result/v1` envelope (`FINISH: <path>`), then gates `push_branch` and pushes exactly
   that verified commit, then gates `open_pr` and opens the PR against the base branch. See
   "Ending the run" below.

**Reading any step's output.**

- `STOP: <reason>` from any command (`next`, but also `start`, `verify`, `review`, `merge` or
  `resume`, for example when the wall clock runs out) ends the loop: go to step 9. From
  `resume`, follow its `NEXT:` line instead: `NEXT: run ask` waits for a renewed grant.
- `PARK: <task> <reason>` means that task is done for this run; carry on with the rest.
- An exit 2 that the step's own instruction above does not cover, such as the root being off
  the run branch or dirty, a merge in progress, or a plan edited since `init`, means park it:
  `conductor park <task> --reason "<the stderr line>"`. Run an unchanged command at most twice.
- A report that does not follow its brief's format: ask that subagent once for the format.
  If it still does not follow it, treat an executor as `BLOCKED` and a reviewer as a `fail`.

**When nothing moves.** If `next` prints nothing and you are waiting on no subagent, run
`conductor status` and park every in-flight task (`running`, `verifying`, `reviewing`) you are
not waiting on, with the reason it is stuck. Otherwise the run cannot end.

**Ending the run.** When the run stops for any reason but `grant_ask`, you MUST stop or wait for
the subagents still working, then run `conductor finish`: it is how a stopped run ends, and it parks any task still in flight
as `in_flight_at_stop`. A `GATE: ASK`, or `REMOTE: pending …` (exit 3), means the grant does
not cover that remote step: report it. `conductor finish --retry-remote` runs just the pending
steps once a grant covers them. An exit 3 from `finish` with no `GATE:` line means the push or
the PR command failed: report its stderr, and retry at most once. `INTEGRATION: fail <task>
<command>` lines and `STOP: integration_red` (exit 3) mean tasks that each passed alone break
each other once merged: the envelope is written, but nothing is pushed and no PR is opened.
Report the failing lines; `--retry-remote` refuses the run (exit 2). The run stays finished:
the human fixes the conflict by hand on the run branch and pushes it themselves, or starts a
new run (`init`) from a plan that orders or merges the two tasks.
`INTEGRATION: skipped grant_ask` and `STOP: grant_ask` (exit 3) mean the grant lapsed before
`finish`, so no check ran and nothing is pushed: report it. Once the user renews the grant,
`conductor finish --retry-remote` runs the integration on the head the skip recorded, writes a
new envelope (its `FINISH:` line; the old one is kept as `superseded`), and pushes and opens the
PR. If the run branch moved since the skip, the retry refuses (exit 2): the new commits were
never reviewed, so report them for a human to check. An exit 2 saying the run branch "moved since the integration re-run" means a commit landed
after the check: report it; the tool pushes only the verified commit. An exit 2 from `finish` (for
example, the plan envelope was edited after `init`, or the grant file is gone) means report its
stderr and stop: you MUST leave restoring the plan or the grant to a human. Once finished, the run is
final: `finish` reprints its `INTEGRATION:` and `FINISH:` lines, `--retry-remote` still runs
pending steps, `resume` reports what is left, every other command except `status` and `gate`
refuses (exit 2), and `init` starts a new run.

**Hands off.** Every step goes through `conductor`: you MUST NOT merge, push, force-push, open
the PR or edit code yourself, even when a gate asks, because only `conductor` checks the grant
before each step and records it in the run's log. You MUST NOT pass a `--pr-cmd` or
`--push-cmd` that does anything but push the run branch or open the PR, because the conductor runs it in place of
the push or PR step the grant approved. They exist for stubs and for hosts without `gh`, so by
default pass neither. You MUST NOT edit, delete or recreate
`state.json`, `autonomy-log.jsonl` or anything under `.skill-contract/`, because they are the
run's record that `resume` and `finish` read back and the human audits; report a mismatch
instead.

## Resume after a crash

A fresh agent session with no memory of the run can resume it: a new session, a
`claude --continue`, or a scheduled re-entry. Run `conductor resume` and do what each `NEXT:`
line says, in order. It first prints the last recorded step (`STATUS: run=<run-id> …`) and
re-checks the grant; it lifts a `grant_ask` stop once a grant covers the run again, and no
other stop. Then it prints one `NEXT:` line per in-flight task and a last line for the run.
A task's worktree is `<repo>/.skill-contract/runs/<run-id>/wt/<task>`; a `running` or
`verifying` task whose worktree is gone prints `PARK: <task> worktree-missing` instead.

- `NEXT: <task> dispatch-executor`: the executor stopped mid-task. Dispatch the executor brief
  again into the existing worktree (not `start`), then `conductor verify <task>` on its report.
- `NEXT: <task> dispatch-repair verify`: its verify failed. Send the executor the failing
  commands and the `stdout_tail`/`stderr_tail` from `tasks.<task>.verify_runs` in `state.json`
  (read only) into the existing worktree, then verify on its report.
- `NEXT: <task> dispatch-repair review`: its reviewer failed it. Send the executor
  `tasks.<task>.review.detail` from `state.json`, then verify on its report.
- `NEXT: <task> dispatch-reviewer <sha>`: dispatch a reviewer on that commit, then record its
  verdict with `conductor review`.
- `NEXT: <task> verify`: `merge` found the task's branch moved past the proven commit. Run
  `conductor verify <task>`; nothing is dispatched.
- `NEXT: <task> merge`: run `conductor merge <task>`. If a crash hit after the merge reached
  the run branch, `merge` finds that merge, records the task as proven and prints `MERGE:`.
- `NEXT: run next`: carry on with the loop from step 2.
- `NEXT: run ask`: the grant asks (`GATE: ASK`, `STOP: grant_ask`; on a finished run, the
  gate of its pending push or PR). Report it to the user and wait; once they renew the
  grant, run `conductor resume` again.
- `NEXT: run finish`: the run is stopped (its `STOP:` line says why, and `resume` records a
  stop rule that fires, as `next` does) or nothing is left to do: go to "Ending the run". On
  a finished run (resume prints its `FINISH:` line) it means a remote step is pending and
  its gate now covers it: run `conductor finish --retry-remote`.
- `NEXT: run done`: the run is finished and nothing is left to run: report it.

Each `dispatch-executor`, `dispatch-repair` and `dispatch-reviewer` line spends one dispatch
from `max_dispatches` when `resume` prints it, even when a failed verify already counted one
for the same repair. That is conservative: every `resume` call that asks for a dispatch
spends one, so a session that keeps crashing still stops. A task past the cap prints
`PARK: <task> budget_dispatches` and gets no `NEXT:` line.

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
Done means these commands pass; they will be re-run independently on your last commit, from
the repository root, and {python} means a Python 3.10+ interpreter: <verify commands>

Work only in this git worktree: <absolute worktree path>. Commit inside the worktree, on its
current branch; uncommitted work is not verified. You MUST NOT dispatch subagents, push, merge
or switch branches. If a choice is not settled by the constraints above and a wrong guess would
be costly, do not guess: report NEEDS_DECISION with one question.

Report, and nothing else:
Status: DONE | NEEDS_DECISION | BLOCKED
Commits: <sha list>
Tests: <one line, e.g. "7 passed">
Concerns: <none, or one line each; for NEEDS_DECISION the question; for BLOCKED the reason>
```

## The reviewer brief

`<sha>` is the commit from `VERIFY: <task> pass <sha>`. The diff command ignores replace refs
and switches off external diff drivers, text conversion, binary attributes, colour, fsmonitor
and hooks. That neutralises the known config, attribute and replace-ref tricks an executor could
plant in the shared git directory; the §7a assumptions in "What the proof is worth" still
apply.

```text
You are reviewing task <id> of an approved plan. You did not write this code. You MUST NOT
dispatch subagents or edit any file.

Task: <title>; requirement: <requirement text>
Constraints: <the plan's constraints; the grant's decisions and defaults>
Diff: <output of git --no-replace-objects -C <worktree> -c core.fsmonitor=false -c core.hooksPath=/dev/null diff --no-ext-diff --no-textconv --text --no-color <run_branch>...<sha>>

Answer two questions about the diff:
1. Does it satisfy the requirement?
2. Does it do anything the task did not ask for?
Verdict: pass only if the answer to 1 is yes and to 2 is no; otherwise fail.
Report: Verdict: pass | fail, then one line of detail.
```

## Stop and park rules

- **A task that needs a human.** On a `NEEDS_DECISION` report you MUST run
  `conductor decision <task> --question "<the question>"` and carry on with the other tasks.
  You MUST NOT answer a human-decision question yourself, even when the answer looks obvious,
  because the grant does not delegate that decision; the parked question goes into the run
  result and the PR body for the human.
- **A blocked task.** On a `BLOCKED` report, `conductor park <task> --reason "<reason>"`.
- **Parking does not stop the run.** The parked task's dependents become `blocked`, and every
  other ready task continues. A parked task's worktree and branch are kept for the human.
- **What stops the run.** `next` stops it with `budget_wall_clock`, `budget_dispatches` or
  `no_ready_tasks` (the normal end: nothing left to do); `start`, `verify`, `review` and
  `merge` can stop it with `budget_wall_clock` too, and `start` with `budget_dispatches`. A
  `GATE: ASK` on `start`, `verify`, `merge` or `resume` stops it with `grant_ask`: the grant
  expired or was revoked, its spec or plan went stale, or the root left a branch the grant
  covers. A `merge-inconsistent` park (the root moved during a merge) stops it with
  `new_human_decision`. At `finish`, a merged run branch that fails a proven task's own checks
  stops it with `integration_red` and is not pushed, and a run-branch commit that changes CI
  config makes the push and the PR ask (`ci-config`), because CI runs with the repository's
  secrets.
- **What parks a task.** Repairs reaching `max_repairs_per_task` (2 by default) park it with
  `verify_red_after_repairs`; a dispatch it needs past `max_dispatches` parks it with
  `budget_dispatches`. `max_dispatches` counts every executor, repair and reviewer dispatch the
  tool records.

## Deliverable

A **run report** for the user: the PR URL (or the pending remote step and why), the
`run-result/v1` envelope path, each task's status (`proven` with its merge commit, `parked`
with its reason or question, `blocked` with the task it waits on), the stop reason, the
integration result (`INTEGRATION: pass <sha>`, the failing lines, or `skipped`), and the
budget spent.
The report MUST state that `max_tokens` and `max_usd` were recorded, not enforced, as
`conductor status` does. Name the grant id and each action class the run used.

## Verify and repair

Before you report, run `conductor status` and check that every task has a final status and
that the `FINISH:` line printed. Then check the envelope as a receiver would:
`python3 "$SKILL_DIR/assets/contract_check.py" check-envelope <run-result> --root <repo>`.
Its claims read `CLAIMED`, because the conductor produced them; add `--rerun` to re-run each
assertion's one command from the root and see them `PROVEN`. A mismatch between `status` and the
envelope is a conductor bug: report it rather than editing state.

## What the proof is worth

The conductor proves that each task's verify commands passed on that task's own commit, cut
from the run branch as it stood when the task started, and that a reviewer passed the same
commit. The commands run in a clone under `<root>/.skill-contract/runs/…/verify/` that holds
only committed files: dependency directories such as `node_modules` or `.venv` are absent, so a
command that needs them fails unless it installs them or uses tooling installed globally, and a lookup that
walks up parent directories can reach the root's own files. Before the push, `finish`
re-verifies the merged run branch: it re-runs every proven task's verify commands on its head
in the same kind of clone, so two tasks that each pass alone but break each other once merged
are caught there and not pushed. The push sends exactly the commit that re-run verified, and
refuses when the run branch has moved since. CI on the pushed branch is still the independent
check outside this machine. A plan with weak checks gets weak proof, and the reviewer is the only
thing that looks past them.

Cost is bounded in every run, by dispatches and by wall clock: `max_dispatches` is derived when the
grant sets none, and the grant's expiry caps the wall clock at 7 days even without
`wall_clock_min`. Repairs per task and parallelism are bounded too. `max_dispatches` counts
the dispatches the conductor records, including each one `resume` asks for; a subagent an
executor starts against its brief is not counted. `max_tokens` and `max_usd` are recorded,
not enforced: the runtime does not expose usage to the tool.

The run result's assertions are claims. To a receiver they read as CLAIMED, because the
conductor wrote the envelope; they become PROVEN when a receiver re-runs them
(`check-envelope --rerun`) or when CI on the pushed branch reports them through a `run_url`.
A rerun runs the one command an assertion carries, in the receiver's own tree: for
`integration:<head>` that is one command, not the whole re-run on the merged run branch.

A grant is the user's recorded yes, but an agent with a shell can forge one, and so can an
executor. An executor running as the same OS user can also forge `state.json` or the log,
tamper with the git object store, commit code that reads its uncommitted files by absolute path
from outside the verify checkout, or leave a process running after a verify by escaping its
process group with `setsid()`. Push-destination settings in the shared git config can redirect
where a push goes, though not which branch it updates, and the push is not forced. The
reviewer's diff runs under the same assumptions: its flags neutralise the known config,
attribute and replace-ref tricks, but the repository it reads is the executor's. The conductor closes the cheap routes: verify and
merge run in isolated clones without the repository's config, hooks or filters, and every
conductor git call runs with hooks switched off, so the user's own pre-push hooks do not run on
the push either. The rest is covered by the independent reviewer and by CI re-running the checks
on the pushed branch.
That is why merging the PR stays with the human.

## Contract

This skill follows [skill-contract v1](https://github.com/dhanesh/agent-skills/blob/main/docs/skill-contract/SPEC.md).
It consumes a `https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1` envelope
from any skill that provides one, and an
`https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1` that covers it. It
provides a `https://github.com/dhanesh/agent-skills/skill-contract/run-result/v1` envelope; the
payload schema is `assets/schemas/run-result.v1.json`. Its assertions are one passed
`verify:<task>` per proven task, carrying that task's first verify command. An
`integration:<head>` assertion is added when the re-run on the merged run branch ran a
command (passed or failed), or as `untested` when the grant asked and a task is proven; there
is none when no task is proven. A receiver sees the passed ones as CLAIMED until it re-runs
them.

```json skill-contract
{"provides": ["https://github.com/dhanesh/agent-skills/skill-contract/run-result/v1"], "consumes": ["https://github.com/dhanesh/agent-skills/skill-contract/task-plan/v1", "https://github.com/dhanesh/agent-skills/skill-contract/autonomy-grant/v1"]}
```

## Boundaries

- **Not the planner.** The spec, the plan and the grant come from spec-first-planning.
- **Not the loop designer.** Designing a loop, or auditing one, is crafting-self-prompting-loops.
- **Not a merger.** The conductor merges tasks into its own run branch only. You MUST leave
  merging the run branch or the PR into the default branch to the human; a grant cannot cover
  `merge`.
- **Not a spend governor.** Token and dollar caps are recorded, not enforced.

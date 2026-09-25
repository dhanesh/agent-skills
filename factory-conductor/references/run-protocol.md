# Run protocol: the conductor.py reference

`conductor.py` is the factory-conductor's state tool: stdlib Python 3.10+, git 2.31+, one file
beside the vendored skill-contract checker. Every command takes `--root <repo>` (default `.`)
and acts on the newest run under `<root>/.skill-contract/runs/`. This page is the full
reference; SKILL.md holds the protocol an agent follows.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | OK. The step happened. |
| 3 | A human is needed or the run stopped: a `GATE: ASK`, a `STOP:`, a task sent back for repair (`VERIFY: … fail`, `REVIEW: … fail`) or parked (`PARK:`), a merged run branch that failed its integration re-run (`STOP: integration_red`), or a remote step that asked, failed or is still pending after `finish`. |
| 2 | Refused or invalid: bad input, a task in the wrong status, a finished run, a precondition not met. Argparse usage errors also exit 2. |

The conductor never exits 1. The vendored checker's `check-grant` does exit 1 on a usage
error, but the conductor treats any non-zero checker exit as an ASK (`reason=checker-error`).

An exit 3 does not always mean the run stopped. Read the lines: `STOP:` means it did,
`PARK:` means one task is done for this run, and a bare `VERIFY: <task> fail` or
`REVIEW: <task> fail` means the task goes back to its executor.

## Commands

| Command | Prints | Exits |
|---|---|---|
| `init --plan <envelope> [--budget JSON]` | `RUN: <run-id>`; on stderr, `FAIL:` lines for a refused plan, and a `warning:` when `origin` has no copy of the base branch | 0; 2 invalid plan, budget or branch; 3 `GATE: ASK` |
| `gate --action <class>` | `GATE: COVERED id=… class=… gate=…` or `GATE: ASK reason=…` | 0 covered; 3 ask |
| `next [--max N]` | `READY: <ids>`, nothing, or `STOP: <reason>` | 0; 3 stop; 2 finished |
| `start <task>` | `START: <task> <worktree>` | 0; 2 not ready, parallel limit, dispatch budget spent with work in flight; 3 gate or stop |
| `verify <task>` | `VERIFY: <task> ok\|fail <argv>` per command, then `VERIFY: <task> pass <sha>` or `VERIFY: <task> fail` | 0 pass; 3 fail, park or stop; 2 refused |
| `review <task> --verdict pass\|fail [--detail T]` | `REVIEW: <task> pass\|fail` | 0 pass; 3 fail or park; 2 refused |
| `merge <task>` | `MERGE: <task> <sha>` | 0; 3 conflict or no commits (parked), or stop; 2 refused, or a fast-forward git refused with the root unchanged (the task stays `reviewing`) |
| `park <task> --reason R` | `PARK: <task> <reason>` | 0; 2 refused |
| `decision <task> --question Q` | `PARK: <task> new_human_decision` | 0; 2 refused |
| `status` | one `STATUS: <task> <status> [verified_head=<sha>] [review=pass\|fail] [reason=…] [question=…]` per task (`verified_head` only while `reviewing`: the commit awaiting review; `review=` whenever a review is recorded), the budget line (ending ` derived=max_dispatches` when `init` derived the cap), the budget note | 0 |
| `resume` | `STATUS: run=<id> last_event=<e> at=<t>`, then any `PARK: <task> worktree-missing\|budget_dispatches`, `READY: <ids>`, one `NEXT: <task> <action>` per in-flight task and `NEXT: run next`; or `STOP: <reason>` and `NEXT: run finish`; or `GATE: ASK`, `STOP: grant_ask` and `NEXT: run ask`; on a finished run, `FINISH: <envelope>` and `NEXT: run finish\|done` (or `run ask`) | 0, including a finished run; 3 stopped (now or before) or gate asks |
| `finish [--push-cmd JSON] [--pr-cmd JSON] [--retry-remote]` | `INTEGRATION: pass <sha>`, `INTEGRATION: fail <task> <command>` lines or `INTEGRATION: skipped grant_ask`, `FINISH: <envelope>`, then `STOP: integration_red` or `STOP: grant_ask`, or any `GATE: ASK` or `REMOTE: pending push pr (run finish --retry-remote)` | 0 all done; 3 integration red or skipped, or a remote step asked, failed or is pending; 2 refused, including a run branch that moved since the integration re-run |

### init

Checks, in order: the `--budget` JSON; the envelope (skill-contract C3–C7, fresh subjects, the
`task-plan/v1` kind); a schedulable plan (no `depends_on` cycle, no unknown or duplicate task
id, no two ids that differ only in case); at least one verify step per task and a command on
every step (a null or empty one prints `FAIL: task <id> verify step <n> has no command`, exit
2; spec-first-planning writes the command from a criterion's `[cmd: …]` hint); every verify
command against skill-contract C6 (it starts with `{python}`, not `python3`, or a bare program
name, and uses no absolute path); and a git work tree. Then it asks `check-grant --action local_reversible --subject <plan>`, so a
grant covers only the plan it pins. It creates the run branch `factory/<plan-slug>` (the slug is
the plan title, lower-cased, runs of other characters turned into `-`), which must match the
grant's `branch_pattern` and must not exist yet, checks it out in the root, and writes the run
directory. When `refs/remotes/origin/<base>` is absent it prints a stderr `warning:`: the
default PR's `--base` is the base branch, which the conductor never pushes, so push it first
(`git push -u origin <base>`).

**Budgets.** The run's budget is `{"max_parallel": 2, "max_repairs_per_task": 2}`, overlaid with the grant's `budget`,
overlaid with `--budget`; then, if `max_dispatches` is still unset, `init` derives it. `--budget` can only tighten: a value above the grant's for the same
key exits 2, and a key the grant does not set can be added. An unknown key in `--budget` exits
2; an unknown key in the grant is warned about, logged as `budget_warning` and dropped.

| Key | Enforced by |
|---|---|
| `wall_clock_min` (when set) | `next`, `start`, `verify`, `review`, `merge`: `STOP: budget_wall_clock` once that many minutes have passed since `init` |
| `max_dispatches` (always; derived when unset) | one dispatch per executor start, per repair send-back, per reviewer; `start` refuses past it, a task that needs one parks with `budget_dispatches`, and `next` stops the run once nothing is in flight. When neither the grant nor `--budget` sets it, `init` sets it to tasks × 2 × (1 + `max_repairs_per_task`), one executor and one reviewer per attempt. Each `dispatch-*` line `resume` prints spends one too (see "Resume"). Only dispatches the tool records count: a subagent an executor starts itself does not |
| `max_repairs_per_task` (2 by default) | every failing verify or review after the first is a repair; at the cap the task parks with `verify_red_after_repairs` |
| `max_parallel` | `start` refuses past it; `next --max` is clamped to it |
| `max_tokens`, `max_usd` | recorded, not enforced: the runtime does not expose usage to the tool |

The grant's `stop_on` list is not enforced: it is recorded in the grant, but factory-conductor
1.0.0 applies only its own stop rules (see "Task statuses and stop reasons").

The effective values are recorded in `state.json` and shown by `status`. A derived
`max_dispatches` is listed in `budget_derived`, logged on the `init` event as
`"derived": true` (with `max_dispatches`), and marked `derived=max_dispatches` on the
`status` budget line. A grant or `--budget` value always wins over the derivation, and
`--budget` still cannot loosen a key the grant sets. `wall_clock_min` is enforced only when
set, but the grant's expiry ends every run's gates: at most 7 days from the newest grant that
covers the plan (a newer grant extends it), so cost is always bounded by dispatches and by wall
clock. Because the repair, parallel and dispatch defaults are the conductor's, not the grant's,
`--budget` can raise them when the grant does not set them: a `--budget` `max_dispatches` above
the derived value is accepted when the grant sets none.

### gate

Asks the vendored checker whether the newest grant covers `--action` now, with the newest
run's plan envelope as `--subject`. With no run under the root it passes no subject, so before
`init` check the plan you are about to run with
`contract_check.py check-grant --root <repo> --action local_reversible --subject <plan>`
instead: `conductor gate` could otherwise answer for another plan, or for no plan at all. Every
consequential step calls the gate and proceeds only on `COVERED`. A git older than 2.31
fails closed (`reason=git-too-old`). Later gates do not pin the grant read at `init`: any grant
that covers this plan covers the run.

### next

Stop rules, in order: `budget_wall_clock`; `budget_dispatches` (spent, a task is ready, nothing
in flight); `no_ready_tasks` (nothing ready, nothing in flight). `no_ready_tasks` is also how a
run that proved everything ends. A task is ready when it is `pending` and every task in its
`depends_on` is `proven`; the ready list is capped by the free parallel slots and the dispatches
left, in plan order.

### start

Needs the task `pending` and ready, a free parallel slot and a dispatch left, and a covering
`local_reversible` gate. Creates the worktree `<run>/wt/<task>` on the task branch
`<run_branch>--<task>` (not `<run_branch>/<task>`: git cannot hold a ref under an existing
branch name), cut from the run branch, and logs a `dispatch` of kind `executor`.

### verify

Re-reads the task's verify commands from the pinned plan envelope, which must still hash to the
sha256 recorded at `init`; state.json is never the source of the commands. Then:

- the worktree must be clean and committed: an uncommitted or untracked file fails the verify;
- the commands run as argv lists (never a shell), with `{python}` resolved, in a fresh isolated
  clone of the worktree's head, checked out with no inherited config, attributes, hooks or
  filters, and removed afterwards;
- a committed symlink whose target is absolute or leaves the tree fails the verify;
- each command's process group is killed when it ends (timeout 600 s);
- a commit made while the verify ran fails it;
- the very commit a failed review rejected fails it before any command runs ("no new commit
  since the review failed"), counted like any failing verify: a repair is a new commit.

The commands run with the conductor's environment minus the variables that point git at
another repository; they do not inherit its `GIT_NO_REPLACE_OBJECTS` or `GIT_GRAFT_FILE`.

The clone lives inside the root, at `<root>/.skill-contract/runs/<run-id>/verify/`, and holds
only committed files. Two consequences follow. Ignored files are absent, so dependency
directories such as `node_modules` or `.venv` are not there: a command must install its
dependencies or rely on tooling installed globally. And a lookup that walks up parent
directories (a config file search, say) can reach the root's own files above the clone.

A pass prints `VERIFY: <task> pass <verified_head>`, records that sha, moves the task to
`reviewing` and spends a reviewer dispatch. The reviewer must judge exactly that commit:
`git --no-replace-objects -C <worktree> -c core.fsmonitor=false -c core.hooksPath=/dev/null diff --no-ext-diff --no-textconv --text --no-color <run_branch>...<verified_head>`.
Those flags neutralise the known config, attribute and replace-ref tricks; the §7a assumptions
still apply, because the repository it reads is the executor's. A
fail spends a repair dispatch, or parks the task (`verify_red_after_repairs`,
`budget_dispatches`). A task with no runnable command parks with `unrunnable-verify`; `init`
refuses such a plan, so this is defence in depth.

### review

Records the reviewer's verdict against the verified commit. A fail counts against the same
repair budget as a failing verify and sends the task back.

### merge

Needs a passing verify and a passing review on the same pinned sha, the task branch still at
that sha (otherwise the task goes back to `verifying`, exit 2), and the root on the run branch,
clean, with no merge in progress (a tracked edit in the root exits 2 and leaves the task
`reviewing`). The `--no-ff` merge of the pinned sha runs in an isolated clone; the root then
fetches the merge commit (with `GIT_ALLOW_PROTOCOL=file`, so a planted `insteadOf` cannot
turn the fetch into an `ext::` command) and fast-forwards to it. A fast-forward git refuses
while the root HEAD has not moved (an untracked file in the way, say) logs `merge` with
`reason: "fast-forward refused"`, exits 2 and leaves the task `reviewing`: clear the cause and
run `merge` again. On success the worktree
and task branch are removed. A task whose verified commit adds nothing to the run branch parks
`no-commits` (exit 3), because retrying could not change that. If a merge commit whose second
parent is the pinned sha is already on the run branch's first-parent line (a crash hit between
the root's fast-forward and saving state), `merge` records that commit, logs `merge` with
`recovered: true`, removes the worktree and exits 0. A conflict parks the task `merge-conflict`, leaves the root
untouched and keeps the worktree. A root that moved during the merge (or a fast-forward that left
HEAD anywhere but where it was), or a merge that is not a
two-parent merge of the expected commits, parks `merge-inconsistent` and stops the run with
`new_human_decision`.

### park and decision

`park` records any reason. `decision` records the question and parks with
`new_human_decision`; the run goes on. Both mark every transitive dependent `blocked`. A proven
task cannot be parked.

### resume

Prints the last recorded event, re-checks `local_reversible`, and prints `READY:`, then the
exact next action as `NEXT:` lines (see "Resume" below). It lifts a `grant_ask` stop once a
grant covers the run again. A stop that a crashed `finish` recorded over an earlier one (it
carries `prior`) is first undone to that earlier stop, so a sticky stop such as
`new_human_decision` is never lifted by a renewed grant. While the grant still asks it prints `GATE: ASK`,
`STOP: grant_ask` and `NEXT: run ask` (exit 3): a human renews the grant, then `resume` runs
again. Any other stop stays, and `resume` exits 3 with its `STOP:` line and
`NEXT: run finish`. Like `next`, it records a stop rule that fires (`budget_wall_clock`,
`budget_dispatches`, `no_ready_tasks`), prints `STOP:` and `NEXT: run finish`, and exits 3.
A `running` or `verifying` task whose worktree is gone parks with `worktree-missing`. Each
`dispatch-*` line spends a dispatch (logged as `dispatch` with `resume: true`); a task past
`max_dispatches` parks with `budget_dispatches` and gets no line. It logs a `resume` event.
On a finished run it prints `FINISH: <envelope>` and exits 0. A pending remote step is gated
first (`push_branch` or `open_pr`, logged): `NEXT: run finish` when it is covered (run
`finish --retry-remote`), `NEXT: run ask` while it asks. `NEXT: run done` when every step is
done or the integration was red, and, for an integration skipped because the grant asked,
`NEXT: run finish` once a grant covers the run again or `NEXT: run ask` until then.

### finish

- **When.** On a run that is not stopped, it refuses while any task is `running`, `verifying`
  or `reviewing`. On a stopped run it first parks those tasks with `in_flight_at_stop`.
- **Integration.** Before the envelope and before any remote step, it re-runs every proven
  task's verify commands on the run branch's head, each task in a fresh isolated checkout of
  that commit, with the same symlink check, command environment and 600 s timeout as
  `verify` (through the same functions). It logs an `integration` event (`head`, `passed`,
  `runs`) and prints `INTEGRATION: pass <sha>`, or one `INTEGRATION: fail <task> <command>`
  per failing command. Two tasks that each passed alone can break each other once merged;
  this is where that shows. A red result records the stop `integration_red` (the reason the
  run had before is kept as `previous`, and a `stop` event is logged), still writes the
  envelope, prints `STOP: integration_red` after `FINISH:` and exits 3: nothing is pushed and
  no PR is opened, and `--retry-remote` refuses the run (exit 2). The run stays finished: a
  human fixes the conflict on the run branch and pushes it by hand, or starts a new run from
  a plan that orders the two tasks (`depends_on`) or merges them.
- **Integration is gated.** The re-run executes the repository's code, so `finish` first asks
  `local_reversible`. On an ASK nothing runs: it prints `INTEGRATION: skipped grant_ask`,
  records `integration: {head, passed: false, skipped: "<the GATE line>", runs: []}`, records
  the stop `grant_ask` (with `previous`), writes the envelope, prints `STOP: grant_ask` and
  exits 3; nothing is pushed. `finish --retry-remote` on such a run re-gates: while the grant
  still asks it prints `STOP: grant_ask` (exit 3) and writes nothing. It re-verifies only the
  head the skip recorded: a run branch that moved since is refused (exit 2, a `push_refused`
  event, "a human must check the new commits"), because no task review covered the new
  commits. Once a grant covers the run it re-runs the integration on that head, logs it, writes a new
  envelope (a new id, whose log prefix covers that `integration` event), keeps the old path
  as `finished.superseded`, and goes on to the push and the PR. A red re-run stops with
  `integration_red` as above.
- **Recorded results.** A later plain `finish` reprints the recorded `INTEGRATION:` line(s)
  and never re-runs them. A stop that an earlier, crashed `finish` recorded (it keeps the
  stop it replaced under `prior`) is undone before the next attempt judges the run branch.
- **The envelope.** It writes `run-result/v1` under `.skill-contract/envelopes/` and prints
  `FINISH: <path>`. The subjects pin the plan envelope and the grant. The payload
  (`assets/schemas/run-result.v1.json`) carries each task's status, verify runs in the plan's own
  `{python}` form, review, merge commit, park reason and question, plus the stop, the budget and
  its note, `budget_derived`, the `integration` result (`{head, passed, skipped?, runs:
  [{task, command, ok, returncode}]}`), and the worktrees kept for a human. `log_sha256` is the digest of the log's
  first `log_bytes` bytes, which end with the `finish` event, so it covers the `integration`
  event too. An exit 2 here writes nothing: the plan
  envelope no longer matches its pinned sha256, the grant file is gone, or a task is in flight
  on a run that is not stopped. Report it and stop; a human must restore the plan or the
  grant. There is one passed `verify:<task>`
  assertion per proven task, carrying that task's first verify command, and, when the
  integration re-run ran any command, one `integration:<first 12 of head>` assertion, passed
  or failed, carrying its first failing command (its first command on a pass). A skipped
  re-run gives an `untested` one carrying the first proven task's first command; with no
  proven task there is none. A receiver's `check-envelope --rerun` re-runs only that one
  command, from its own root: not the whole re-run on the merged run branch.
- **Push.** It gates `push_branch`, then runs `--push-cmd`, by default
  `["git","push","-u","origin","{run_branch}"]`, only while the root is on the run branch. The
  allowlist is `git push [--set-upstream|--porcelain|--quiet|-u|-q] <remote> <run_branch>`, with
  a remote name only (not a URL or path), no refspec, no force and no other option. The
  conductor rewrites the branch to the explicit, non-forced refspec
  `<integration head>:refs/heads/<rb>`, so a `remote.<name>.push` mapping cannot retarget it
  and the commit pushed is exactly the one the integration re-run verified. Right before
  each push, on the first `finish` and on `--retry-remote` alike, it refuses (exit 2, a
  `push_refused` log event) when `refs/heads/<rb>` has moved since that re-run. The
  push runs with `GIT_ALLOW_PROTOCOL=file:git:http:https:ssh`, so a planted `insteadOf` cannot
  turn it into an `ext::` command. Hooks are off, so the user's pre-push hooks do not run.
  The envelope write before it is deliberately not gated: a revoked or expired grant still
  lets a run end.
- **Pull request.** It gates `open_pr`, then runs `--pr-cmd`, by default
  `["gh","pr","create","--title","{title}","--base","{base_branch}","--body-file","{body_file}"]`.
  The conductor never pushes the base branch, so it must already be on the remote (`init`
  warns when `origin` has no copy of it).
  Placeholders are whole tokens: `{run_branch}`, `{base_branch}`, `{title}`, `{body_file}`. The
  body lists proven, parked and blocked tasks, the stop, the budget and the budget note. Every
  string from the plan, a reviewer or a parker sits inside an inline code span, so it cannot add
  headings, links, @mentions or closing keywords.
- **Remote steps.** The first ASK or failure skips the rest and exits 3. An exit 3 with no
  `GATE:` line means the push or PR command itself failed: its stderr says why, and a
  `--retry-remote` loop will not fix it. A later plain `finish`
  prints the same `FINISH:` line, plus `REMOTE: pending push|pr` and exit 3 while a step is
  pending. `finish --retry-remote` re-gates and runs only the pending steps, and refuses (exit 2)
  if the envelope changed since `finish` wrote it.
- **After finish.** `next`, `start`, `verify`, `review`, `merge`, `park` and `decision`
  refuse with exit 2 ("run finished"). `status` and `gate` still work, `resume` reports what
  is left (see `resume`), `finish` reprints its `INTEGRATION:` and `FINISH:` lines,
  `finish --retry-remote` still runs pending remote steps (and a skipped integration), and
  `init` starts a new run.

## Task statuses and stop reasons

Statuses: `pending`, `running` (started), `verifying` (verify failed, awaiting repair),
`reviewing` (verify passed, or review recorded as pass), `proven` (merged), `parked`,
`blocked` (a dependency is parked).

Stop reasons the tool records: `budget_wall_clock` (from `next`, `resume`, `start`, `verify`,
`review` or `merge`), `budget_dispatches` (from `next`, `resume` or `start`), `grant_ask` (a gate ASK in `start`,
`verify`, `merge`, `resume` or `finish`), `new_human_decision` (a `merge-inconsistent` park) and
`no_ready_tasks` (from `next` or `resume`), and `integration_red` (from `finish`, when the merged
run branch fails a proven task's own checks). The grant's `stop_on` is not enforced.

Park reasons: `verify_red_after_repairs`, `budget_dispatches`, `unrunnable-verify`,
`no-commits`, `merge-conflict`, `merge-inconsistent`, `new_human_decision`,
`in_flight_at_stop`, `worktree-missing` (from `resume`), and any `park --reason` text.

## The run directory

`<root>/.skill-contract/runs/<run-id>/`, with run id `run-<yyyymmddThhmmssZ>-<6 hex>`. The
conductor writes a `.gitignore` of `*` into `.skill-contract/runs/`, so a run never dirties the
repository and is never committed.

- `state.json`, rewritten atomically on every change: `root`, `run_id`, `plan_envelope`,
  `plan_sha256`, `grant_id`, `run_branch`, `base_branch`, `created_at`, `budget`,
  `budget_derived` (the budget keys `init` derived, e.g. `["max_dispatches"]`), `dispatches`,
  `stopped` (null or `{reason, at, detail?, task?, previous?, prior?}`; `prior` is the full
  stop a `finish` stop replaced, restored by the next `finish`), `finished` (null or
  `{envelope, id, sha256, at, pushed, pr, integration, superseded?}`), `order` (task ids in plan order) and `tasks`. Each task holds
  `status`, `depends_on`, `verify`, `repairs`, `failures`, `branch`, `worktree`, `verify_runs`
  (per command: `command`, `ok`, `returncode`, `stdout_tail`, `stderr_tail`, the last 2000
  characters each), `verified_head`, `review`, `merge_commit`, `park_reason`, `question`.
- `autonomy-log.jsonl`, append-only, one JSON object per line, each with `at` (RFC 3339 UTC)
  and `event`: `init` (with the waves, `max_dispatches` and `derived`), `gate`, `dispatch` (kind `executor`, `repair` or
  `reviewer`; `resume: true` when a `resume` line spent it), `dispatch_refused`, `verify` (each command and its outcome), `review`, `merge`,
  `park`, `decision`, `stop`, `resume`, `budget_warning`, `integration`, `finish`, `push`,
  `push_refused`, `pr`.
- `wt/<task>/`, the task worktrees. `verify/` and `merge/` hold the short-lived isolated
  clones (the integration re-run's are `verify/integration-<task>-<sha>`).

## Resume

A crash or a new session loses nothing that was recorded, and the session that resumes needs
no memory of the run: a new session, a `claude --continue`, or a scheduled re-entry all work
the same way. Run `conductor resume` and do what each `NEXT:` line says. It reads state.json,
prints the last event, re-checks the grant, prints `READY:`, then one `NEXT:` line per
in-flight task, in plan order, and one last line for the run. A run resumed after its grant
expired asks (`NEXT: run ask`) and stays stopped until a new grant covers it.

| Line | When | What to do |
|---|---|---|
| `NEXT: <task> dispatch-executor` | `running` | dispatch the executor brief again into the existing worktree, `<root>/.skill-contract/runs/<run-id>/wt/<task>` (not `start`), then `conductor verify <task>` on its report |
| `NEXT: <task> dispatch-repair verify` | `verifying` after a failing verify | send the executor the failing commands and the tails from `tasks.<task>.verify_runs` in `state.json` (read, never written), into the existing worktree, then `conductor verify <task>` on its report |
| `NEXT: <task> dispatch-repair review` | `verifying` after a failed review | send the executor `tasks.<task>.review.detail`, then `conductor verify <task>` on its report |
| `NEXT: <task> dispatch-reviewer <sha>` | `reviewing`, no verdict | dispatch a reviewer on `<sha>`, the verified head, then `conductor review` |
| `NEXT: <task> verify` | `verifying`, last verify passed, no review (merge found the branch moved) | `conductor verify <task>`; nothing is dispatched |
| `NEXT: <task> merge` | `reviewing`, verdict pass | `conductor merge <task>`; a merge that already reached the run branch before the crash is found and recorded (`recovered: true` in the log) |
| `NEXT: run next` | work is in flight, or a task is ready | carry on with the loop (`conductor next`) |
| `NEXT: run ask` | the grant asks, now or as a `grant_ask` stop it still does not lift | report it to the user and wait; once they renew the grant, run `conductor resume` again |
| `NEXT: run finish` | the run is stopped for any other reason (its `STOP:` line says why), or nothing is in flight and nothing is ready (resume records that stop, as `next` would) | `conductor finish`; a stopped run prints no task lines, because it takes no further step and `finish` parks its in-flight tasks |
| `FINISH: …`, `NEXT: run finish` | the run is finished and a remote step is pending whose gate covers it (or a skipped integration can now run) | `conductor finish --retry-remote` |
| `FINISH: …`, `NEXT: run ask` | the run is finished, and the pending push or PR (or the skipped integration) is asked by its gate | report it; once the user renews the grant, `conductor resume` again |
| `FINISH: …`, `NEXT: run done` | the run is finished: every step done, or the integration was red | report it; nothing is left to run |
| `PARK: <task> worktree-missing` | `running` or `verifying`, its worktree is gone | nothing: the task is done for this run |
| `PARK: <task> budget_dispatches` | the dispatch this line would spend is past `max_dispatches` | nothing: the task is done for this run |

Every line starts `NEXT: `, then a task id or `run`, then the action; that grammar is stable
for tools that read it. Each `dispatch-executor`, `dispatch-repair` and `dispatch-reviewer`
line spends one dispatch when `resume` prints it, even when a failed verify already counted
one for the same repair. That is conservative, and intended: every `resume` that asks for a
dispatch spends one, so a session that keeps crashing still runs into the cap. `verify` and
`merge` lines spend none. A task that stays stuck after that is parked
(`conductor park <task> --reason …`), so the run can end.

## Worked example: three tasks, one failing

A plan titled "Add greeting": T1, T2 (depends on T1) and T3 (independent). Its grant covers
`local_reversible`, `push_branch` and `open_pr` on `factory/*`, with budget
`{"max_repairs_per_task": 1, "max_dispatches": 12, "wall_clock_min": 120, "max_usd": 5}`. The
root is on `factory/p`. Output below is from a real run of this version, with the repository
path shortened to `<repo>` and the resolved interpreter to `/usr/bin/python3`.

```text
$ conductor init --plan <repo>/.skill-contract/envelopes/task-plan-v1-20260922T211854Z-19fa38.json
RUN: run-20260922T211854Z-7e841e
$ conductor next
READY: T1 T3
$ conductor start T1
START: T1 <repo>/.skill-contract/runs/run-20260922T211854Z-7e841e/wt/T1
$ conductor start T3
START: T3 <repo>/.skill-contract/runs/run-20260922T211854Z-7e841e/wt/T3
```

Two executors run in parallel. T1 reports DONE. The pass line names the commit the reviewer
must judge:

```text
$ conductor verify T1
VERIFY: T1 ok /usr/bin/python3 -c import os,sys; sys.exit(0 if os.path.exists('t1.txt') else 1)
VERIFY: T1 pass ba427d8c22870ca5ef46eacea2895a95e4bac9c7
$ conductor review T1 --verdict pass --detail "matches R1"
REVIEW: T1 pass
$ conductor merge T1
MERGE: T1 f39ea744f45fa5f788a4b744353e8465af8a998f
```

T3 reports DONE, but its check fails. The conductor spends one repair (this grant caps repairs
at 1), the executor tries again, and the check still fails, so T3 parks:

```text
$ conductor verify T3                                          (exit 3: send it back)
VERIFY: T3 fail /usr/bin/python3 -c import os,sys; sys.exit(0 if os.path.exists('t3.txt') else 1)
VERIFY: T3 fail
$ conductor verify T3                                          (exit 3: done for this run)
VERIFY: T3 fail /usr/bin/python3 -c import os,sys; sys.exit(0 if os.path.exists('t3.txt') else 1)
VERIFY: T3 fail
PARK: T3 verify_red_after_repairs
```

T1 is proven, so T2 is ready:

```text
$ conductor next
READY: T2
$ conductor start T2
START: T2 <repo>/.skill-contract/runs/run-20260922T211854Z-7e841e/wt/T2
$ conductor verify T2
VERIFY: T2 ok /usr/bin/python3 -c import os,sys; sys.exit(0 if os.path.exists('t2.txt') else 1)
VERIFY: T2 pass 074070aa65ddeba650d97205bc61a785e0412a04
$ conductor review T2 --verdict pass --detail ok
REVIEW: T2 pass
$ conductor merge T2
MERGE: T2 52bf22e3013fc203fb897c612f6f2e0a63907c41
$ conductor next                                               (exit 3)
STOP: no_ready_tasks
$ conductor status
STATUS: T1 proven
STATUS: T2 proven
STATUS: T3 parked reason="verify_red_after_repairs"
STATUS: budget {"max_dispatches": 12, "max_parallel": 2, "max_repairs_per_task": 1, "max_usd": 5, "wall_clock_min": 120} dispatches=6
STATUS: budget max_tokens/max_usd recorded, not enforced (the runtime does not expose usage)
$ conductor finish --pr-cmd '["echo","https://github.com/o/r/pull/7"]'
INTEGRATION: pass 52bf22e3013fc203fb897c612f6f2e0a63907c41
FINISH: <repo>/.skill-contract/envelopes/run-result-v1-20260922T211857Z-e57aa8.json
$ conductor next                                               (exit 2)
run finished: <repo>/.skill-contract/envelopes/run-result-v1-20260922T211857Z-e57aa8.json; start a new run with init
```

Six dispatches: three executors, one repair, two reviewers. `finish` first re-ran T1's and
T2's checks on the merged run branch (its head is T2's merge commit), then pushed
`52bf22e3013fc203fb897c612f6f2e0a63907c41:refs/heads/factory/add-greeting` (the verified head) to a local bare `origin` and
ran the PR step, stubbed here with `echo`, each after its gate said COVERED. T3's worktree and
branch `factory/add-greeting--T3` are kept for the human. A receiver checks the result:

```text
$ python3 "$SKILL_DIR/assets/contract_check.py" check-envelope <repo>/.skill-contract/envelopes/run-result-v1-20260922T211857Z-e57aa8.json --root <repo>
ENVELOPE: FRESH
CLAIM: integration:52bf22e3013f CLAIMED
CLAIM: verify:T1 CLAIMED
CLAIM: verify:T2 CLAIMED
CONTRACT_RESULT: PASS
```

With `--rerun`, the checker re-runs the commands and the claims read `PROVEN`.

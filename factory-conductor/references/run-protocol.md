# Run protocol: the conductor.py reference

`conductor.py` is the factory-conductor's state tool: stdlib Python 3.10+, git 2.31+, one file
beside the vendored skill-contract checker. Every command takes `--root <repo>` (default `.`)
and acts on the newest run under `<root>/.skill-contract/runs/`. This page is the full
reference; SKILL.md holds the protocol an agent follows.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | OK. The step happened. |
| 3 | A human is needed or the run stopped: a `GATE: ASK`, a `STOP:`, a task sent back for repair (`VERIFY: … fail`, `REVIEW: … fail`) or parked (`PARK:`), or a remote step that asked, failed or is still pending after `finish`. |
| 2 | Refused or invalid: bad input, a task in the wrong status, a finished run, a precondition not met. Argparse usage errors also exit 2. |

The conductor never exits 1. The vendored checker's `check-grant` does exit 1 on a usage
error, but the conductor treats any non-zero checker exit as an ASK (`reason=checker-error`).

An exit 3 does not always mean the run stopped. Read the lines: `STOP:` means it did,
`PARK:` means one task is done for this run, and a bare `VERIFY: <task> fail` or
`REVIEW: <task> fail` means the task goes back to its executor.

## Commands

| Command | Prints | Exits |
|---|---|---|
| `init --plan <envelope> [--budget JSON]` | `RUN: <run-id>` | 0; 2 invalid plan, budget or branch; 3 `GATE: ASK` |
| `gate --action <class>` | `GATE: COVERED id=… class=… gate=…` or `GATE: ASK reason=…` | 0 covered; 3 ask |
| `next [--max N]` | `READY: <ids>`, nothing, or `STOP: <reason>` | 0; 3 stop; 2 finished |
| `start <task>` | `START: <task> <worktree>` | 0; 2 not ready, parallel limit, dispatch budget spent with work in flight; 3 gate or stop |
| `verify <task>` | `VERIFY: <task> ok\|fail <argv>` per command, then `VERIFY: <task> pass <sha>` or `VERIFY: <task> fail` | 0 pass; 3 fail, park or stop; 2 refused |
| `review <task> --verdict pass\|fail [--detail T]` | `REVIEW: <task> pass\|fail` | 0 pass; 3 fail or park; 2 refused |
| `merge <task>` | `MERGE: <task> <sha>` | 0; 3 conflict or no commits (parked), or stop; 2 refused |
| `park <task> --reason R` | `PARK: <task> <reason>` | 0; 2 refused |
| `decision <task> --question Q` | `PARK: <task> new_human_decision` | 0; 2 refused |
| `status` | one `STATUS: <task> <status> [verified_head=<sha>] [reason=…] [question=…]` per task (`verified_head` only while `reviewing`: the commit awaiting review), the budget line, the budget note | 0 |
| `resume` | `STATUS: run=<id> last_event=<e> at=<t>`, then `READY: <ids>` | 0; 3 still stopped or gate asks; 2 finished |
| `finish [--push-cmd JSON] [--pr-cmd JSON] [--retry-remote]` | `FINISH: <envelope>`, then any `GATE: ASK` or `REMOTE: pending push pr (run finish --retry-remote)` | 0 all done; 3 a remote step asked, failed or is pending; 2 refused |

### init

Checks, in order: the `--budget` JSON; the envelope (skill-contract C3–C7, fresh subjects, the
`task-plan/v1` kind); a schedulable plan (no `depends_on` cycle, no unknown or duplicate task
id, no two ids that differ only in case); every verify command against skill-contract C6 (it
starts with `{python}`, not `python3`, or a bare program name, and uses no absolute path); and
a git work tree. Then it asks `check-grant --action local_reversible --subject <plan>`, so a
grant covers only the plan it pins. It creates the run branch `factory/<plan-slug>` (the slug is
the plan title, lower-cased, runs of other characters turned into `-`), which must match the
grant's `branch_pattern` and must not exist yet, checks it out in the root, and writes the run
directory.

**Budgets.** The run's budget is `{"max_parallel": 2, "max_repairs_per_task": 2}`, overlaid with the grant's `budget`,
overlaid with `--budget`. `--budget` can only tighten: a value above the grant's for the same
key exits 2, and a key the grant does not set can be added. An unknown key in `--budget` exits
2; an unknown key in the grant is warned about, logged as `budget_warning` and dropped.

| Key | Enforced by |
|---|---|
| `wall_clock_min` (when set) | `next`, `start`, `verify`, `review`, `merge`: `STOP: budget_wall_clock` once that many minutes have passed since `init` |
| `max_dispatches` (when set) | one dispatch per executor start, per repair send-back, per reviewer; `start` refuses past it, a task that needs one parks with `budget_dispatches`, and `next` stops the run once nothing is in flight. Only dispatches the tool records count: a re-dispatch after a crash, or a subagent an executor starts itself, does not |
| `max_repairs_per_task` (2 by default) | every failing verify or review after the first is a repair; at the cap the task parks with `verify_red_after_repairs` |
| `max_parallel` | `start` refuses past it; `next --max` is clamped to it |
| `max_tokens`, `max_usd` | recorded, not enforced: the runtime does not expose usage to the tool |

The effective values are recorded in `state.json` and shown by `status`. `max_dispatches` and
`wall_clock_min` are not enforced when the grant and `--budget` both leave them unset, so an
unattended run should set them. Because the repair and parallel defaults are the conductor's,
not the grant's, `--budget` can raise them when the grant does not set them.

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
- a commit made while the verify ran fails it.

A pass prints `VERIFY: <task> pass <verified_head>`, records that sha, moves the task to
`reviewing` and spends a reviewer dispatch. The reviewer must judge exactly that commit:
`git --no-replace-objects -C <worktree> -c core.fsmonitor=false -c core.hooksPath=/dev/null diff --no-ext-diff --no-textconv --text --no-color <run_branch>...<verified_head>`.
Those flags neutralise the known config, attribute and replace-ref tricks; the §7a assumptions
still apply, because the repository it reads is the executor's. A
fail spends a repair dispatch, or parks the task (`verify_red_after_repairs`,
`budget_dispatches`). A task with no runnable command parks with `unrunnable-verify`.

### review

Records the reviewer's verdict against the verified commit. A fail counts against the same
repair budget as a failing verify and sends the task back.

### merge

Needs a passing verify and a passing review on the same pinned sha, the task branch still at
that sha (otherwise the task goes back to `verifying`, exit 2), and the root on the run branch,
clean, with no merge in progress. The `--no-ff` merge of the pinned sha runs in an isolated
clone; the root then fetches the merge commit and fast-forwards to it. On success the worktree
and task branch are removed. A task whose verified commit adds nothing to the run branch parks
`no-commits` (exit 3), because retrying could not change that. If a merge commit whose second
parent is the pinned sha is already on the run branch's first-parent line (a crash hit between
the root's fast-forward and saving state), `merge` records that commit, logs `merge` with
`recovered: true`, removes the worktree and exits 0. A conflict parks the task `merge-conflict`, leaves the root
untouched and keeps the worktree. A root that moved during the merge, or a merge that is not a
two-parent merge of the expected commits, parks `merge-inconsistent` and stops the run with
`new_human_decision`.

### park and decision

`park` records any reason. `decision` records the question and parks with
`new_human_decision`; the run goes on. Both mark every transitive dependent `blocked`. A proven
task cannot be parked.

### resume

Prints the last recorded event, re-checks `local_reversible`, and prints `READY:`. It lifts a
`grant_ask` stop once a grant covers the run again; any other stop stays, and `resume` exits 3
with its `STOP:` line. It logs a `resume` event.

### finish

- **When.** On a run that is not stopped, it refuses while any task is `running`, `verifying`
  or `reviewing`. On a stopped run it first parks those tasks with `in_flight_at_stop`.
- **The envelope.** It writes `run-result/v1` under `.skill-contract/envelopes/` and prints
  `FINISH: <path>`. The subjects pin the plan envelope and the grant. The payload
  (`assets/schemas/run-result.v1.json`) carries each task's status, verify runs in the plan's own
  `{python}` form, review, merge commit, park reason and question, plus the stop, the budget and
  its note, and the worktrees kept for a human. `log_sha256` is the digest of the log's first
  `log_bytes` bytes, which end with the `finish` event. An exit 2 here writes nothing: the plan
  envelope no longer matches its pinned sha256, the grant file is gone, or a task is in flight
  on a run that is not stopped. Report it and stop; a human must restore the plan or the
  grant. There is one passed `verify:<task>`
  assertion per proven task, carrying that task's first verify command.
- **Push.** It gates `push_branch`, then runs `--push-cmd`, by default
  `["git","push","-u","origin","{run_branch}"]`, only while the root is on the run branch. The
  allowlist is `git push [--set-upstream|--porcelain|--quiet|-u|-q] <remote> <run_branch>`, with
  a remote name only (not a URL or path), no refspec, no force and no other option. The
  conductor rewrites the branch to the explicit, non-forced refspec
  `refs/heads/<rb>:refs/heads/<rb>`, so a `remote.<name>.push` mapping cannot retarget it. Hooks
  are off, so the user's pre-push hooks do not run.
- **Pull request.** It gates `open_pr`, then runs `--pr-cmd`, by default
  `["gh","pr","create","--title","{title}","--base","{base_branch}","--body-file","{body_file}"]`.
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
- **After finish.** `next`, `resume`, `start`, `verify`, `review`, `merge`, `park` and
  `decision` refuse with exit 2 ("run finished"). `status` and `gate` still work, `finish`
  reprints its `FINISH:` line, `finish --retry-remote` still runs pending remote steps, and
  `init` starts a new run.

## Task statuses and stop reasons

Statuses: `pending`, `running` (started), `verifying` (verify failed, awaiting repair),
`reviewing` (verify passed, or review recorded as pass), `proven` (merged), `parked`,
`blocked` (a dependency is parked).

Stop reasons the tool records: `budget_wall_clock` (from `next`, `start`, `verify`, `review`
or `merge`), `budget_dispatches` (from `next` or `start`), `grant_ask` (a gate ASK in `start`,
`verify`, `merge` or `resume`), `new_human_decision` (a `merge-inconsistent` park) and
`no_ready_tasks`. The schema's stop-reason list also names `verify_red_after_repairs`, but the
tool uses it only as a park reason: a red task parks and the run goes on.

Park reasons: `verify_red_after_repairs`, `budget_dispatches`, `unrunnable-verify`,
`no-commits`, `merge-conflict`, `merge-inconsistent`, `new_human_decision`,
`in_flight_at_stop`, and any `park --reason` text.

## The run directory

`<root>/.skill-contract/runs/<run-id>/`, with run id `run-<yyyymmddThhmmssZ>-<6 hex>`. The
conductor writes a `.gitignore` of `*` into `.skill-contract/runs/`, so a run never dirties the
repository and is never committed.

- `state.json`, rewritten atomically on every change: `root`, `run_id`, `plan_envelope`,
  `plan_sha256`, `grant_id`, `run_branch`, `base_branch`, `created_at`, `budget`, `dispatches`,
  `stopped` (null or `{reason, at, detail?, task?}`), `finished` (null or `{envelope, id,
  sha256, at, pushed, pr}`), `order` (task ids in plan order) and `tasks`. Each task holds
  `status`, `depends_on`, `verify`, `repairs`, `failures`, `branch`, `worktree`, `verify_runs`
  (per command: `command`, `ok`, `returncode`, `stdout_tail`, `stderr_tail`, the last 2000
  characters each), `verified_head`, `review`, `merge_commit`, `park_reason`, `question`.
- `autonomy-log.jsonl`, append-only, one JSON object per line, each with `at` (RFC 3339 UTC)
  and `event`: `init` (with the waves), `gate`, `dispatch` (kind `executor`, `repair` or
  `reviewer`), `dispatch_refused`, `verify` (each command and its outcome), `review`, `merge`,
  `park`, `decision`, `stop`, `resume`, `budget_warning`, `finish`, `push`, `pr`.
- `wt/<task>/`, the task worktrees. `verify/` and `merge/` hold the short-lived isolated
  clones.

## Resume

A crash or a new session loses nothing that was recorded. Run `conductor resume`: it reads
state.json, prints the last event, re-checks the grant and prints what is ready. A run resumed
after its grant expired asks and stays stopped until a new grant covers it, or ends with
`finish`. Then run `conductor status` and pick up each in-flight task by its status:

| Status | What to do |
|---|---|
| `running` | dispatch the executor brief again into the existing worktree; do not run `start` |
| `verifying` | a repair was in flight: read `state.json` without writing it. If `tasks.<task>.review.verdict` is `fail`, send the executor `tasks.<task>.review.detail`; otherwise send the failing commands and tails from `tasks.<task>.verify_runs`. Dispatch it into the existing worktree, then `conductor verify <task>` on its report |
| `reviewing`, `tasks.<task>.review` null | dispatch a reviewer on the `verified_head` that `status` shows |
| `reviewing`, review pass recorded | `conductor merge <task>`; a merge that already reached the run branch before the crash is found and recorded (`recovered: true` in the log) |

These re-dispatches are not counted in `max_dispatches`: the tool counts only the dispatches
that `start`, `verify` and `review` record. A task that stays stuck after that is parked
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
FINISH: <repo>/.skill-contract/envelopes/run-result-v1-20260922T211857Z-e57aa8.json
$ conductor next                                               (exit 2)
run finished: <repo>/.skill-contract/envelopes/run-result-v1-20260922T211857Z-e57aa8.json; start a new run with init
```

Six dispatches: three executors, one repair, two reviewers. `finish` pushed
`refs/heads/factory/add-greeting:refs/heads/factory/add-greeting` to a local bare `origin` and
ran the PR step, stubbed here with `echo`, each after its gate said COVERED. T3's worktree and
branch `factory/add-greeting--T3` are kept for the human. A receiver checks the result:

```text
$ python3 "$SKILL_DIR/assets/contract_check.py" check-envelope <repo>/.skill-contract/envelopes/run-result-v1-20260922T211857Z-e57aa8.json --root <repo>
ENVELOPE: FRESH
CLAIM: verify:T1 CLAIMED
CLAIM: verify:T2 CLAIMED
CONTRACT_RESULT: PASS
```

With `--rerun`, the checker re-runs both commands and the claims read `PROVEN`.

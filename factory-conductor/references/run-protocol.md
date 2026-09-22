# Run protocol: the conductor.py reference

`conductor.py` is the factory-conductor's state tool: stdlib Python 3.10+, git 2.31+, one file
beside the vendored skill-contract checker. Every command takes `--root <repo>` (default `.`)
and acts on the newest run under `<root>/.skill-contract/runs/`. This page is the full
reference; SKILL.md holds the protocol an agent follows.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | OK. The step happened. |
| 3 | A human is needed or the run stopped: a `GATE: ASK`, a `STOP:`, a task sent back for repair (`VERIFY: … fail`, `REVIEW: … fail`) or parked (`PARK:`), or a remote step still pending after `finish`. |
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
| `verify <task>` | `VERIFY: <task> ok\|fail <argv>` per command, then `VERIFY: <task> pass\|fail` | 0 pass; 3 fail, park or stop; 2 refused |
| `review <task> --verdict pass\|fail [--detail T]` | `REVIEW: <task> pass\|fail` | 0 pass; 3 fail or park; 2 refused |
| `merge <task>` | `MERGE: <task> <sha>` | 0; 3 conflict (parked) or stop; 2 refused |
| `park <task> --reason R` | `PARK: <task> <reason>` | 0; 2 refused |
| `decision <task> --question Q` | `PARK: <task> new_human_decision` | 0; 2 refused |
| `status` | one `STATUS: <task> <status> [reason=…] [question=…]` per task, the budget line, the budget note | 0 |
| `resume` | `STATUS: run=<id> last_event=<e> at=<t>`, then `READY: <ids>` | 0; 3 still stopped or gate asks; 2 finished |
| `finish [--push-cmd JSON] [--pr-cmd JSON] [--retry-remote]` | `FINISH: <envelope>`, then any `GATE: ASK` or `REMOTE: pending push\|pr` | 0 all done; 3 a remote step asked, failed or is pending; 2 refused |

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

**Budgets.** The run's budget is `{"max_parallel": 2}`, overlaid with the grant's `budget`,
overlaid with `--budget`. `--budget` can only tighten: a value above the grant's for the same
key exits 2, and a key the grant does not set can be added. An unknown key in `--budget` exits
2; an unknown key in the grant is warned about, logged as `budget_warning` and dropped.

| Key | Enforced by |
|---|---|
| `wall_clock_min` | `next`, `start`, `verify`, `review`, `merge`: `STOP: budget_wall_clock` once that many minutes have passed since `init` |
| `max_dispatches` | one dispatch per executor start, per repair send-back, per reviewer; `start` refuses past it, a task that needs one parks with `budget_dispatches`, and `next` stops the run once nothing is in flight |
| `max_repairs_per_task` | every failing verify or review after the first is a repair; at the cap the task parks with `verify_red_after_repairs` |
| `max_parallel` | `start` refuses past it; `next --max` is clamped to it |
| `max_tokens`, `max_usd` | recorded, not enforced: the runtime does not expose usage to the tool |

A key the grant and `--budget` both leave unset is not enforced at all.

### gate

Asks the vendored checker whether the newest grant covers `--action` for this run's plan
now. Every consequential step calls it and proceeds only on `COVERED`. A git older than 2.31
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

A pass records `verified_head`, moves the task to `reviewing` and spends a reviewer dispatch. A
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
and task branch are removed. A conflict parks the task `merge-conflict`, leaves the root
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
  `log_bytes` bytes, which end with the `finish` event. There is one passed `verify:<task>`
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
- **Remote steps.** The first ASK or failure skips the rest and exits 3. A later plain `finish`
  prints the same `FINISH:` line, plus `REMOTE: pending push|pr` and exit 3 while a step is
  pending. `finish --retry-remote` re-gates and runs only the pending steps, and refuses (exit 2)
  if the envelope changed since `finish` wrote it.
- **After finish.** `next`, `resume`, `start`, `verify`, `review`, `merge`, `park` and
  `decision` refuse with exit 2 ("run finished").

## Task statuses and stop reasons

Statuses: `pending`, `running` (started), `verifying` (verify failed, awaiting repair),
`reviewing` (verify passed, or review recorded as pass), `proven` (merged), `parked`,
`blocked` (a dependency is parked).

Stop reasons: `budget_wall_clock`, `budget_dispatches`, `grant_ask`, `new_human_decision`
(a `merge-inconsistent` park), `verify_red_after_repairs`, `no_ready_tasks`. Park reasons include
`verify_red_after_repairs`, `budget_dispatches`, `unrunnable-verify`, `merge-conflict`,
`merge-inconsistent`, `new_human_decision`, `in_flight_at_stop`, and any `park --reason` text.

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

A crash or a new session loses nothing that was recorded: `conductor resume` reads state.json,
prints the last event, re-checks the grant and prints what is ready. A task left `running`
still has its worktree: resume its executor or dispatch a new one with the same brief. A run
resumed after its grant expired asks and stays stopped until a new grant covers it, or ends
with `finish`.

## Worked example: three tasks, one failing

A plan titled "Add greeting": T1, T2 (depends on T1) and T3 (independent). Its grant covers
`local_reversible`, `push_branch` and `open_pr` on `factory/*`, with budget
`{"max_repairs_per_task": 1, "max_dispatches": 12, "wall_clock_min": 120, "max_usd": 5}`. The
root is on `factory/p`. Output below is from a real run, with paths shortened to `…`.

```text
$ conductor init --plan …/task-plan-v1-….json
RUN: run-20260922T205119Z-080fab
$ conductor next
READY: T1 T3
$ conductor start T1
START: T1 …/runs/run-20260922T205119Z-080fab/wt/T1
$ conductor start T3
START: T3 …/runs/run-20260922T205119Z-080fab/wt/T3
```

Two executors run in parallel. T1 reports DONE:

```text
$ conductor verify T1
VERIFY: T1 ok …/python3 -c import os,sys; sys.exit(0 if os.path.exists('t1.txt') else 1)
VERIFY: T1 pass
$ conductor review T1 --verdict pass --detail "matches R1"
REVIEW: T1 pass
$ conductor merge T1
MERGE: T1 35b4b0bac9cf8ab70ed55f8a055ce6fcc60bc1a2
```

T3 reports DONE, but its check fails. The conductor spends one repair, the executor tries
again, and the check still fails, so T3 parks:

```text
$ conductor verify T3
VERIFY: T3 fail …/python3 -c import os,sys; sys.exit(0 if os.path.exists('t3.txt') else 1)
VERIFY: T3 fail                                   (exit 3: send it back)
$ conductor verify T3
VERIFY: T3 fail …
VERIFY: T3 fail
PARK: T3 verify_red_after_repairs                 (exit 3: done for this run)
```

T1 is proven, so T2 is ready:

```text
$ conductor next
READY: T2
$ conductor start T2
START: T2 …/wt/T2
$ conductor verify T2
VERIFY: T2 pass
$ conductor review T2 --verdict pass --detail ok
REVIEW: T2 pass
$ conductor merge T2
MERGE: T2 60246757de94619824fdd1ef24a525ef4f2804a6
$ conductor next
STOP: no_ready_tasks                              (exit 3)
$ conductor status
STATUS: T1 proven
STATUS: T2 proven
STATUS: T3 parked reason="verify_red_after_repairs"
STATUS: budget {"max_dispatches": 12, "max_parallel": 2, "max_repairs_per_task": 1, "max_usd": 5, "wall_clock_min": 120} dispatches=6
STATUS: budget max_tokens/max_usd recorded, not enforced (the runtime does not expose usage)
$ conductor finish
FINISH: …/.skill-contract/envelopes/run-result-v1-20260922T205122Z-4a5a4d.json
```

Six dispatches: three executors, one repair, two reviewers. `finish` pushed
`refs/heads/factory/add-greeting:refs/heads/factory/add-greeting` to a local bare `origin` and
ran the PR step (stubbed here with `--pr-cmd '["echo","<url>"]'`), each after its gate said
COVERED. T3's worktree and branch `factory/add-greeting--T3` are kept for the
human. A receiver checks the result:

```text
$ python3 "$SKILL_DIR/assets/contract_check.py" check-envelope …/run-result-v1-….json --root <repo>
ENVELOPE: FRESH
CLAIM: verify:T1 CLAIMED
CLAIM: verify:T2 CLAIMED
CONTRACT_RESULT: PASS
```

With `--rerun`, the checker re-runs both commands and the claims read `PROVEN`.

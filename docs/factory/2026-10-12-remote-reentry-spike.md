# Remote re-entry for factory-conductor: design spike (2026-10-12)

> **Dated snapshot.** This is a design spike, not shipped code. It reads `main` at
> `3f3bbf4`. Line numbers refer to that commit. Add a new file for a later state rather
> than editing this one.

## The question

Owner decision, 2026-10-12. Today factory-conductor can relaunch a stalled unattended run
only on the machine that started it. The run state, the task worktrees and the run lock are
in the git-ignored `<root>/.skill-contract/runs/<run-id>/`. A launchd, systemd or cron timer
on that machine runs `conductor watch`. The owner asks:

- Can a stalled run be resumed **remotely**, from GitHub Actions or from a hosted cloud
  agent session, while the owner's machine is off?
- If it can, how, and at what risk?

The factory assessment ranks "re-entry needs the machine on and the user logged in" as the
top limit for unattended runs (`docs/factory/2026-09-19-assessment.md:274`).

## Short answer

Do not build remote resume now. Support an **always-on host** instead (design C below). A
small Linux box or VM with a systemd user timer and `loginctl enable-linger` removes the
"machine on, user logged in" limit with the code that exists today. Remote resume (design A
or B) is possible, but it needs about 9 conductor tasks. It also needs the owner to answer
decisions 1 to 4 at the end of this page first. The largest decision is whether a grant may live outside the clone that the owner
approved.

## Facts found

### 1. What a run holds, and where

| Item | Where it lives | On the remote mid-run? | Source |
|---|---|---|---|
| Run directory | `<root>/.skill-contract/runs/<run-id>/`. The directory ignores itself with a `*` `.gitignore`. | No | `factory-conductor/assets/conductor.py:305`, `:824-830`, `:458` |
| `state.json` | Run directory. Rewritten atomically. | No | `conductor.py:306`, `:526-542` |
| `autonomy-log.jsonl` | Run directory. Append-only. | No | `conductor.py:307`, `:544` |
| `.lock` (fcntl `flock`) | Run directory. Local to one kernel. | No | `factory-conductor/assets/reentry.py:15`, `:32-63` |
| `lease.json` | Run directory. Carries `host` and `pid`. | No | `reentry.py:16`, `:96-116` |
| Task worktrees | `<run>/wt/<task>` on the branch `<run_branch>--<task>` | No | `conductor.py:1829-1831` |
| Task branches | Local refs. Deleted at merge. | No | `conductor.py:1830`, `:2760-2762` |
| Run branch | Local. Pushed only by `finish`. | Only after `finish` | `conductor.py:301`, `:3774-3807` |
| Verify and merge clones | `<run>/verify/`, `<run>/merge/`. Temporary. | No | `conductor.py:309-310`, `:1956-1995` |
| Grant | `.skill-contract/envelopes/autonomy-grant-v1-*`, listed in `.git/info/exclude` | Never: a tracked grant covers nothing | `spec-first-planning/assets/write_grant.py:87-89`, `factory-conductor/assets/contract_check.py:1451-1452` |
| Plan and spec | Pinned by sha256 in the grant | Only if the owner commits them | `contract_check.py:560-571` |
| Evidence records (evidence gate) | `<root>/.verify`, `<root>/.verify-run`, both ignored | No | `conductor.py:1428-1430`, `:3952-3958` |
| Spend usage | `state.json` `usage` and the log | No | `conductor.py:410-412` |
| Re-entry agent logs | `<run>/reentry-<n>.log` | No | `conductor.py:3993` |

**Mid-run, the git remote holds nothing of the run.** The only push in the conductor is in
`finish` (`conductor.py:3774-3807`). The push allowlist accepts only the run branch
(`conductor.py:3454-3482`). Task branches are never pushed, and `merge` deletes them
(`conductor.py:2760-2762`). Work an executor has not committed exists only in its local
worktree.

**`state.json` stores absolute paths.** `root` is made absolute at `conductor.py:455`.
`plan_envelope` is `os.path.abspath(args.plan)` (`conductor.py:1312`, `:1423`). Each task's
`worktree` is built from the run directory (`conductor.py:1829`). A snapshot restored at a
different checkout path (a CI runner's work directory, for example) does not work without a
path rewrite.

**A lost worktree parks the task.** `resume` parks a `running` or `verifying` task whose
worktree is gone with `worktree-missing` (`conductor.py:1683-1688`). Parking blocks every
dependent (`conductor.py:598-612`). So a remote resumer that only has the run branch would
park every in-flight task.

### 2. Locks and leases work on one machine only

- The run lock is `fcntl.flock` on `<run>/.lock` (`reentry.py:32-63`). It does not reach
  another machine.
- A reentry lease is live only when `lease.host` equals this host's `gethostname()` and the
  pid lives (`reentry.py:228`). Another machine always reads it as dead. `renew_lease` keeps a
  reentry lease only on the same host (`reentry.py:107`).
- A session lease is live while `renewed_at` is within `stall_min`, or while a file in an
  in-flight worktree changed within `stall_min` (`reentry.py:208-235`). Files restored from a
  snapshot get fresh mtimes. A restored run therefore looks live for one `stall_min` window.
- **Result:** there is no mutual exclusion across machines. A laptop that wakes up during a
  remote run sees a stale local state and a dead lease. Its timer can start a second driver.
  The second driver cannot force-push (`conductor.py:3454-3499`), so the remote run branch is
  safe. But the two runs diverge, and one of them wastes its dispatches.

### 3. What a remote resumer needs

| Need | Today | Gap |
|---|---|---|
| Run state | Local only | A snapshot on the remote, and a restore with a path rewrite |
| Committed task work | Local task branches | Push task branch heads at checkpoints |
| The grant | Untracked on purpose. A tracked grant answers ASK `tracked` (`contract_check.py:1451-1452`). | An out-of-band channel, for example a CI secret written into the checkout at job start |
| Revocation | `revoke-grant` writes a local revision envelope (`contract_check.py:1471-1472`) | A local revoke does not reach a remote copy. The remote needs its own kill switch. |
| `agent_cmd` | An argv with the owner's absolute local paths. Its `--allowedTools` rules name the conductor's absolute path (`spec-first-planning/references/unattended.md:135-163`, `:296-325`). | A remote host needs its own `agent_cmd`, so a new grant and a new yes |
| Agent spawn | `watch` starts the agent detached, in its own session, and exits (`reentry.py:276-290`) | A CI job probably kills orphan processes when it ends (runner behaviour, not checked in this spike). CI needs a foreground mode. |
| Agent CLI and key | The owner's logged-in CLI | `claude -p` with an `ANTHROPIC_API_KEY` secret |
| Verify toolchain | The owner's machine | The runner must install every program the plan's verify commands use. Verify clones hold only committed files (`factory-conductor/references/run-protocol.md:139-145`). |
| Metered usage | With `budget.enforce`, a dead session's dispatches are unmetered. `resume` stops with `budget_unmetered` (`conductor.py:1664-1668`), and `watch` says `waiting-human` (`reentry.py:250-253`). | None possible. Only a human can record that usage. In this common crash state a remote resumer does nothing. |
| Default-branch floor | `detect_default_branches` reads `origin/HEAD`, and falls back to `main` and `master` (`contract_check.py:1089-1101`) | A CI checkout may not set `origin/HEAD`. A repo whose default branch has another name then loses that floor unless the job runs `git remote set-head origin -a`. |
| The workflow file | Not present | A push that adds or changes `.github/workflows/` counts as `deploy` (ASK `ci-config`, `contract_check.py:1461-1466`). The owner commits the workflow by hand. A run never does. |

### 4. The grant floors, as code

The skill-contract spec labels these floors A7 (checker-held floors) and A8 (ask-only
classes) (`docs/skill-contract/reference/contract_check.py:72-73`). The floors are checked at
every gate, on whatever host runs the gate (`contract_check.py:1372-1468`):

- 7-day maximum: `MAX_GRANT_LIFETIME` (`contract_check.py:75`, `:1424-1427`);
- never the default branch, never a detached HEAD (`contract_check.py:1437-1448`);
- `merge`, `deploy`, `spend`, `external_message` and `delete` always ask (`unattended.md:330-334`);
- a push is a non-forced `refs/heads/<rb>:refs/heads/<rb>` only (`conductor.py:3485-3499`).

A remote resumer that runs the same conductor keeps these checks. The risk is not that the
code skips them. The risk is in their inputs: a grant copied to a place the owner cannot
revoke from, a missing `origin/HEAD`, and a `state.json` that anyone with push access can
edit.

### 5. GitHub Actions facts (fetched 2026-10-12)

From the GitHub docs page "Events that trigger workflows":

- "Scheduled workflows will only run on the default branch."
- "The shortest interval you can run scheduled workflows is once every 5 minutes."
- "The `schedule` event can be delayed during periods of high loads."
- "In a public repository, scheduled workflows are automatically disabled when no repository
  activity has occurred in 60 days."
- "With the exception of `workflow_dispatch` and `repository_dispatch`, other
  `GITHUB_TOKEN`-triggered events do not create workflow runs at all." So a PR that a CI
  resumer opens with `GITHUB_TOKEN` gets no CI checks.

From the GitHub Actions billing page: GitHub Free includes 2,000 minutes a month for private
repositories, and standard runners are free for public repositories. The page does not say
how job minutes are rounded. Not checked: whether a push to a non-branch ref such as
`refs/factory/*` triggers `push` workflows.

## The designs

### Design A: snapshot refs on the remote, plus a scheduled Actions resume

**How it works.**

1. At each checkpoint (after `start`, `verify`, `review`, `merge`, `park`), the conductor
   pushes a small snapshot to `refs/factory/<run-id>/*` on `origin`. The snapshot holds
   `state.json`, the log, and the head of each committed task branch. It does not hold the
   grant.
2. A lease ref `refs/factory/<run-id>/lease` moves only by a non-forced push. Git refuses a
   non-fast-forward push, so the push acts as a compare-and-swap between machines. This is an
   idea to test, not a finding.
3. Before its own `watch`, the laptop reads the remote lease. It does not start a driver while
   a remote one holds the lease.
4. A scheduled workflow, committed by the owner on the default branch, restores the snapshot.
   It writes the grant from a repo secret and runs `watch` in a new foreground mode, with
   `claude -p` and an `ANTHROPIC_API_KEY` secret.

**Conductor changes.** Snapshot and restore with a path rewrite. The remote lease ref.
Foreground `watch`. A laptop `watch` that checks the remote lease first. On restore, treat
`state.json` as untrusted: re-derive the budget from the grant, and refuse a state whose
`plan_sha256` or `grant_id` differs.

**For.**

- It closes the stated gap: a run goes on while the laptop is off.
- The floors stay in code and run on the runner.
- It uses only GitHub, which the owner already uses. Public repositories pay nothing for
  minutes.

**Against.**

- **The grant leaves the clone the owner approved.** A secret copy defeats the intent of the
  `tracked` rule: one person's yes for one clone. A local `revoke-grant` does not reach it.
  The kill switch becomes "delete the secret" or `gh workflow disable`.
- **Two machines, one run.** Without the lease ref, a laptop that wakes up starts a second
  driver.
- **The common crash case gains nothing.** With `enforce` on, the run waits for a human
  anyway (`budget_unmetered`).
- **Uncommitted executor work is lost.** Only committed task heads travel.
- **Secrets on the runner.** The agent runs with `ANTHROPIC_API_KEY` and a `GITHUB_TOKEN`
  that can write. Runners have open network egress. Repo content and executor output can
  carry a prompt injection that tries to send a key out. On the laptop the agent has the
  owner's credentials too, but the CI job puts the repo's secrets in one place.
- **Trust surface.** Anyone with write access can push `refs/factory/*` and edit the state
  or the lease. In a solo repo that means the owner and any bot with write access.
- **Cost.** A 10-minute schedule is about 4,300 job starts a month. Most of them are no-op
  ticks. If each tick bills at least one minute, that is more than the 2,000 free minutes of
  a private repo. An hourly schedule is about 720 starts a month.
- **PRs without checks.** A PR opened with `GITHUB_TOKEN` starts no workflow, so the human
  sees an unchecked PR. A personal access token fixes that, but it is one more secret.
- **Effort.** About 9 tasks (see "If the owner wants remote resume later").

### Design B: snapshot refs, plus a hosted cloud agent session

**How it works.** The same snapshot refs as design A. A scheduled hosted agent session (a
cloud routine) clones the repo, restores the snapshot and resumes. The grant and the API key
live in the hosted environment's settings.

**For.**

- It needs no Actions minutes and no workflow file, so no `ci-config` question.
- The hosted session is already an agent, so it has no detached-spawn problem.

**Against.**

- It has every snapshot, lease, grant and metering problem of design A.
- This spike did not check the hosted sandbox. Unknowns: its toolchain for the plan's verify
  commands, its network limits, how it stores a grant, and whether its schedule runs while
  no one is logged in.
- The owner configures the hosted environment by hand. The repo cannot test that setup.
- It adds a second trust domain (the hosting service) that holds the grant.

### Design C: no remote resume; support an always-on host

**How it works.** The owner starts the run on an always-on Linux box or VM. A run cannot
move there later: `state.json` holds absolute paths, and nothing exports or restores a run.
So a run already in flight on the laptop cannot be handed over. The
existing systemd user timer already runs `watch` there. `loginctl enable-linger` keeps it
running after logout (`factory-conductor/SKILL.md:381-384`,
`factory-conductor/references/run-protocol.md:412-425`). A laptop-only owner accepts that
re-entry needs the machine on.

**For.**

- No code change. Every lock, lease, grant and floor works as tested today.
- The grant stays in one clone, and a local revoke works.
- No CI secret, no Actions minutes.

**Against.**

- A second machine to keep running and patched, with the agent CLI logged in there.
- It does not help when that host is down too.
- On macOS, launchd runs the timer only while the user is logged in. The fix is the Linux
  host, not the Mac.

## Jev's scores

One batched call to `jev`, 2026-10-12. Each design got the same fact block (the facts
above, as code and metadata only: no secrets, no customer data, and no earlier Jev
verdict). Scores use four levels: 0 "not worth building", 1 "worth it only after major open
decisions", 2 "worth a limited first step", 3 "clearly worth building now".

| Question | Result | Confidence | Read |
|---|---|---|---|
| Design A, worth building | 0.65 (P0 0.46, P1 0.43, P2 0.11, P3 0.00) | 0.35 | Uncertain (confidence < 0.6). Low either way. |
| Design B, worth building | 0.42 (P0 0.61, P1 0.37, P2 0.02, P3 0.00) | 0.59 | Uncertain (confidence < 0.6). Lowest. |
| Design C, worth building | 2.49 (P0 0.03, P1 0.05, P2 0.31, P3 0.61) | 0.50 | Uncertain (confidence < 0.6). Highest. |
| A keeps every grant floor (Noul) | 0.43 | – | Uncertain (0.3–0.7) |
| B keeps every grant floor (Noul) | 0.46 | – | Uncertain (0.3–0.7) |
| A secret-delivered grant keeps the intent of the `tracked` rule (Noul) | 0.26 | – | Leans no |
| Which to choose now (Choice) | C, P = 0.96 (A 0.03, B 0.00, snapshot only 0.01) | 0.95 | Confident |

The three scores are each uncertain alone, but they agree in order (C well above A and B).
The choice question is confident. The floor questions sit near a coin flip. That matches the
facts: the code checks the floors on any host, but the inputs to those checks get weaker
on a remote host.

## Recommendation

1. **Now: design C.** Write it down as the supported answer to "re-entry needs the machine
   on". Effort: 1 task. Add a short "Always-on host" section to
   `factory-conductor/SKILL.md` "Scheduled re-entry". Make `reentry install` print a
   `warning:` when it installs a systemd user timer and linger is off. Add one test.
2. **Do not build design A or B yet.** Revisit only when the owner has answered decisions
   1 to 4 below, and only if runs often crash without `enforce`. With `enforce` on, a
   remote resumer stops at `waiting-human` in the usual crash state, so it adds little.

### If the owner wants remote resume later

Start with the part that is useful on its own: durability. About 9 tasks for all of
design A, in this order:

1. Push committed task branch heads and a state snapshot to `refs/factory/<run-id>/*` at
   each checkpoint. This needs a new push shape in the allowlist and a grant gate for it.
2. `restore` from those refs, with a path rewrite and an untrusted-state check.
3. The lease ref as a compare-and-swap, with a stale-lease takeover rule.
4. A laptop `watch` that reads the remote lease first.
5. A foreground `watch` mode for a job that must not exit early.
6. A per-host `agent_cmd`, and a grant interview question for it.
7. A remote kill switch that `revoke-grant` also triggers, or documents.
8. A workflow template the owner copies by hand. It sets `origin/HEAD` and passes only the
   API key to the agent step.
9. Tests, the outcome eval, and `ab-validate` rows for the new guards.

Steps 1 to 4 alone give durability and safe hand-off between two of the owner's machines.
That is a smaller first step than CI resume.

## Open risks

- **Two drivers across machines.** No cross-machine lock exists today. Any remote design that
  ships without the lease ref can start two drivers.
- **Revocation does not travel.** A grant copied to a secret or a hosted setting outlives a
  local `revoke-grant` until someone deletes that copy.
- **Prompt injection on a host with secrets.** The resumed agent reads repo content and
  executor output. On a runner with open egress, a key can leak.
- **Edited state on the remote.** Anyone who can push `refs/factory/*` can edit
  `state.json`. The plan hash and the grant floors still hold, but budgets and counters would
  need a re-check.
- **Default-branch floor on CI.** Without `origin/HEAD`, a default branch not named `main` or
  `master` is not protected.
- **Unverified items.** Orphan-process cleanup at job end, minute rounding per job, whether
  pushes to `refs/factory/*` trigger workflows, and the hosted sandbox's limits. Check each
  one before any build.

## What the owner has to decide

1. **Grant location.** May a grant live outside the clone the owner approved (a repo secret,
   or a hosted setting)? Today the `tracked` rule says no, by intent. Jev leans no (0.26).
2. **Kill switch.** If yes to 1: is "delete the secret" or `gh workflow disable` an
   acceptable kill switch next to `revoke-grant`?
3. **Secrets on CI.** May an `ANTHROPIC_API_KEY` (and possibly a personal access token, so
   the PR gets checks) live in the repo's Actions secrets?
4. **Cost ceiling.** Which schedule is acceptable: every 10 minutes (about 4,300 job starts a
   month), hourly (about 720), or only while a run is active (the owner enables the workflow
   by hand)?
5. **Always-on host.** Is a small Linux box or VM with linger acceptable as the supported
   answer for now (design C)?

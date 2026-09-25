# Scheduled re-entry: finishing a stalled factory run without the human

**Status:** The owner approved the design in brainstorming on 2026-09-25. This spec is written for the owner's review.

**Context:** PR #63 closed three Q3 gaps. Q3 is the owner's acceptance-test question "does unattended mode work". Those gaps were mechanical resume (`NEXT:` lines), bounded cost, and integration before push. After PR #64 merged, Jev re-judged Q3 as **yes 0.53 vs partly 0.45**. It named the remaining limit at 0.81: the conductor runs inside an agent session, so a dead session stalls the run until someone resumes it. This spec closes that gap.

**Goal:** a run whose driving session died is resumed and carried to its end, `NEXT: run done` or `NEXT: run ask`, without the human. The resume happens only inside the grant the human accepted. It never happens while another session is driving the run, and never while the run waits on the human.

**Decision support:** Jev (`jev-1.13.0`) was consulted on each decision below. The owner made every decision.

## Owner decisions

| # | Decision | Jev | Rejected alternatives |
|---|---|---|---|
| R1 | **Where: a local scheduler.** On request the conductor installs an OS timer: launchd on macOS, a systemd user timer or cron on Linux. The timer runs `conductor watch`, which detects a stalled run and starts an agent to resume it. Run state and worktrees are local and git-ignored, so re-entry happens where they live. | local 0.99 | A cloud scheduled agent (0.00), because run state would have to leave the machine and the agent would run with repo secrets. A supervisor loop only (0.01), because it does not survive reboots or a closed terminal. |
| R2 | **Permissions: the user supplies the agent command.** The user gives the exact agent command, permission flags included. The command is recorded in the grant and shown back when the grant is written. The conductor never chooses a permission mode. The docs recommend an allowlist and warn against bypass modes. | user command 0.97 | A sandboxed bypass (0.03). Inheriting the user's settings (0.00), because a needed permission prompt would fail headless and stall the run again. |
| R3 | **Consent per run, in the grant**, asked upfront by spec-first-planning's unattended interview. There is no global always-on switch. | 0.87 | Re-entry being always on once installed. |
| R4 | **A lease**, so that a live session and an automatic re-entry never drive the same run. | 0.85 | Relying on timing alone. |
| R5 | **Mechanism: a one-shot watch plus an OS timer.** `conductor watch` checks once and exits. The timer calls it every `interval_min`. | owner | A long-running watch loop. Hosting in tmux-agent-herdr-lite, which would make one optional skill depend on another and would work only under tmux. |

## 1. Consent: the grant's `reentry` block

`autonomy-grant/v1` gains an optional, additive `payload.reentry`:

```json
"reentry": {
  "agent_cmd": ["claude", "-p", "{prompt}", "--allowedTools", "..."],
  "interval_min": 10,
  "stall_min": 30,
  "max_reentries": 5
}
```

- **`agent_cmd`** is an argv list. It is never a shell string. It carries one placeholder token, `{prompt}`, which is replaced by the fixed resume prompt (§3). It MAY also carry `{root}`, the repository root.
  - The reference checker (`contract_check.py`) validates the shape.
  - It rejects a string, an empty list, and a first element that is a shell (`sh`, `bash`, `zsh`, `fish`, `dash`, `cmd`, `powershell`, `pwsh`), because a shell turns the argv back into a string that is evaluated.
  - It rejects any token other than `{prompt}`, `{root}` and literals.
  - It rejects a list with no `{prompt}`.
- **`interval_min`** is an integer from 5 to 60. The default, when the interviewer offers no other value, is 10.
- **`stall_min`** is an integer from 15 to 240, with a default of 30. It MUST be at least 2 × `interval_min`.
- **`max_reentries`** is an integer from 1 to 20, with a default of 5.

**How the block gets into the grant.** spec-first-planning's unattended interview gains one question at the grant step: "Enable automatic re-entry if the session dies? If so, give the exact agent command." `write_grant.py` gains `--reentry-cmd '<json argv>'` and the three numeric flags. It shows the argv back before the human's yes. Without a `reentry` block there is no re-entry: `watch` prints `REENTRY: disabled` and exits 0.

Because the command lives in the accepted grant, the executor cannot change it by editing a run file. A hostile same-user executor that forges the grant file stays the §7a residual of the conductor spec, as it is for every grant field.

## 2. The lease and the run lock

- **Run lock.** Every conductor command that mutates state takes an exclusive `fcntl.flock` on `runs/<id>/.lock` for its duration. Two drivers can then never interleave writes to `state.json` or the log. A command that cannot get the lock within 30 s exits 2 with `run locked`.
- **Lease** (`runs/<id>/lease.json`): `{holder: "session"|"reentry", host, pid, renewed_at, n}`.
  - Every conductor command renews it with `holder: "session"`, `pid: null` and `renewed_at: now`. The only exception is a command the watch's child runs, which keeps `holder: "reentry"` and the child's pid.
  - **A reentry lease** is live while that pid is alive on the same host. `os.kill(pid, 0)` checks this exactly.
  - **A session lease** is live while `now - renewed_at < stall_min`, **or** while any in-flight task's worktree shows activity within `stall_min`. Activity means the newest mtime among the worktree's tracked and untracked files and its git `HEAD`/`index`. That covers an executor subagent that works for a long time without calling the conductor.
- **Residual, stated.** A driving session whose agent is still alive, but has made no conductor call and no worktree change for `stall_min`, looks stalled. Re-entry can then start a second driver. The run lock prevents corruption. The existing guards bound the damage:
  - `start` refuses a task that is already running;
  - `verify` needs a clean committed tree and pins the sha;
  - `merge` pins the verified head;
  - every re-dispatch spends the dispatch budget.

  At worst, dispatches are wasted. The default `stall_min` of 30 is chosen to make this rare. The SKILL states the residual in plain prose.

## 3. `conductor watch`

`conductor watch --root <repo>` checks the newest run once and exits. It starts the agent only when all of these hold, checked in this order:

1. A run exists and is not finished. If it is finished and its timer is installed, `watch` uninstalls the timer, prints `REENTRY: done`, and exits 0.
2. The run is not stopped with `grant_ask`, because that run waits on the human.
3. The grant still covers `local_reversible` and still carries a valid `reentry` block, judged against the plan envelope as subject, the same way every gate judges it. An expired or revoked grant prints `REENTRY: ask` and starts nothing.
4. No live lease exists (§2).
5. The log has no event in the last `stall_min`.
6. The run's re-entry count is below `max_reentries`. Otherwise it prints `REENTRY: exhausted`, records the stop `reentry_exhausted`, and starts nothing.

When every check passes, it:
- takes the run lock;
- writes a `reentry` log event (n, the argv with `{prompt}` elided, the lease it replaced);
- sets the lease to `holder: "reentry"`;
- starts the agent command detached (`start_new_session`) in the repo, with stdin from `/dev/null` and stdout and stderr to `runs/<id>/reentry-<n>.log`;
- records the child pid in the lease;
- prints `REENTRY: started <n> pid=<pid>`.

A re-entry spends no dispatch itself. The agent's own `resume` spends dispatches for what it re-dispatches, as it does today.

**The fixed resume prompt** replaces `{prompt}`. Its wording is fixed in the conductor, so a plan or grant can never inject text into it:

> You are resuming a factory-conductor run in `<root>`. Load the factory-conductor skill. Run `conductor resume --root <root>` and do exactly what each `NEXT:` line says, as the skill's resume section describes, until resume prints `NEXT: run done` or `NEXT: run ask`. On `NEXT: run ask`, stop: do not finish the run and do not ask anyone. Do nothing the skill does not tell you to do.

## 4. Installing and removing the timer

`conductor reentry install|uninstall|status --root <repo>`:

- **Install** needs a grant with a valid `reentry` block and a `local_reversible` gate that is COVERED. It writes one timer per run:
  - **macOS:** `~/Library/LaunchAgents/io.agent-skills.factory-conductor.<run-id>.plist`, with `StartInterval` set to `interval_min × 60` and `ProgramArguments` set to the absolute interpreter, the absolute `conductor.py`, `watch` and `--root <abs root>`. It is loaded with `launchctl bootstrap gui/<uid>`.
  - **Linux:** a systemd user `.service` and `.timer` pair in `~/.config/systemd/user/`, with `OnUnitActiveSec` set, enabled with `systemctl --user enable --now`. If there is no user systemd, it falls back to one crontab line tagged `# factory-conductor <run-id>`.
  - **Other platforms** print `REENTRY: failed` and exit 2 (the reason on stderr). `watch` still works when run by hand. *(Amended 2026-09-26: the implementation reports every install failure, an unsupported platform included, as `failed`.)*
- **Uninstall** removes exactly what install wrote, identified by the run id, and unloads it. *(Amended 2026-09-26: it sweeps every timer kind by run id, not only the kind detected now.)*
- **Status** prints the installed timer, the lease, the re-entry count and the last recorded `reentry` event. *(Amended 2026-09-26, Task 6: it was "the last `watch` decision". `watch` does not log every tick, because a log event per tick would reset the stall clock and defeat stall detection, so the last recorded attempt is what `status` can show.)*

The SKILL instructs the driving agent to run `reentry install` right after `init` when the grant carries a `reentry` block. `watch` uninstalls the timer when the run finishes, so no timers are left behind. `revoke-grant` stops re-entry at the next `watch`, and the timer then uninstalls itself once the run finishes. `conductor reentry uninstall` stops it at once.

Tests render the plist, the units and the crontab into a temp directory through an override of the target directories and the loader commands. They never touch the real system.

## 5. Stop rules, budget and honesty

- A new stop reason, `reentry_exhausted`, is added to `STOP_REASONS` and to the run-result schema enum. It ends the run the same way any other stop does: the next driver, or the human, runs `finish`.
- **Cost stays bounded** by the dispatch cap, which re-dispatches count against, and by `max_reentries`.
- **What the SKILL states plainly:**
  - re-entry needs the machine to be on and the user to be logged in (launchd agents and systemd user timers);
  - it runs the user's own agent command with the user's own permissions;
  - the lease residual in §2;
  - it is not a hosted service.

## 6. Acceptance criteria

- **AC1.** Unit tests cover:
  - each `watch` decision (disabled, finished, `grant_ask`, lapsed grant, live reentry lease, live session lease by heartbeat, live session lease by worktree activity, not stalled, exhausted, started);
  - the run lock;
  - the lease semantics;
  - the `agent_cmd` validation, including the shell, string, empty and missing-`{prompt}` cases;
  - install and uninstall rendering on macOS, systemd and cron into temp directories.
- **AC2.** An end-to-end test:
  1. starts a real run;
  2. kills the driving process mid-task;
  3. ages the log and the lease;
  4. runs `watch`, which starts a stub agent command. The stub follows only `NEXT:` lines, the way a memoryless agent does.
  5. The run reaches `FINISH:`. A second `watch` uninstalls the timer. The run was never driven while a lease was live.
- **AC3.** Eval NEGATIVE fixtures. Each asserts that `watch` starts nothing:
  - while a lease is live;
  - on `grant_ask`;
  - under a revoked grant;
  - beyond `max_reentries`;
  - with a shell as `agent_cmd`.
- **AC4.** A/B rows (`SINCE_REENTRY`): "stalled runs resumed without the human" goes 0 → 1. The guards for the AC3 cases hold at 0 → 0, each with a sanity arm.
- **AC5.** The reference checker validates `reentry`. SPEC.md documents the field. The checker is re-vendored byte-identical to every adopter, and `make contract` passes.
- **AC6.** spec-first-planning asks the re-entry question in unattended mode, and `write_grant.py` writes the block. There are tests for the flags and for the argv shown back.
- **AC7.** BCP 14 and PP-5: new rules carry reasons and register rows. `make gate`, `make ab-validate` (0 WORSE, 0 UNPROVEN) and `make readme` pass. The gate also passes in `docker --platform linux/amd64` as non-root with no git identity.
- **AC8.** After merge, Jev re-judges Q3, targeting a clear yes, and "plan to open PR without the human". Both results are recorded in the assessment doc.

## Out of scope

- Cloud or remote re-entry.
- Windows Task Scheduler.
- A tool-restricted executor plugin.
- Re-entry for anything other than a factory-conductor run.

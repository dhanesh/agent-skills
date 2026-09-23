# The factory conductor: running a plan end to end under a grant (roadmap step 4, with 3B folded in)

**Status:** The owner approved every section in the brainstorm on 2026-09-22. The implementation plan comes next.
**Context:** Step 3A shipped in PR #61: `autonomy-grant/v1`, `check-grant`, spec-first-planning 2.0.0 and four gated skills. After it, Jev put the owner's acceptance test at Q1 partly (0.64), Q2 yes (0.74) and Q3 partly (0.64), and scored "implementation runs end to end without the human" at **P = 0.13**. That number is what this spec targets.
**Goal:** a conductor that carries a task plan from its first task to an open pull request, inside a user-approved grant, with proof for every task it merges.
**Decision support:** Jev (`jev-1.13.0`) was consulted on each decision below; the owner decided each one.

## Owner decisions

| # | Decision | Jev | Rejected alternatives |
|---|---|---|---|
| C1 | **Runtime: a skill plus a state tool.** A new `factory-conductor` skill drives the session; `assets/conductor.py` (stdlib only) holds the schedule, the log, budgets, stop rules, gate calls and resume. It installs through `npx skills`. | 0.99 | A Claude Code plugin first; a Workflow script |
| C2 | **Delivery: one run branch, one PR per plan.** Parallel tasks each get a git worktree on a task branch, merged into the run branch after proof. Conflicts and failures park. | 1.00 | A PR per task; strictly sequential |
| C3 | **Proof: the tool re-runs the checks, plus an independent review.** The conductor's own tool re-runs each task's verify commands (no model), and a reviewer subagent judges the diff against the requirement. CI adds a `run_url` when the branch is pushed. | 0.94 | A verifier subagent alone; CI only |
| C4 | **Scheduling (3B folded in):** `depends_on` per task, waves derived from it, and a `[after: …]` spec hint. | — | A separate 3B spec first |
| C5 | **Budgets:** wall clock, repairs per task and agent dispatches are enforced; tokens and dollars are recorded but not enforced, because the runtime does not expose usage. | — | Claiming a spend cap the tool cannot keep |

## 1. The pieces

**`factory-conductor` (a new skill).** Its SKILL.md is the run protocol: what to dispatch, what to hand it, what to do with the report, when to stop. It never edits code itself.

**`assets/conductor.py` (stdlib only).** One state machine with these commands:

| Command | What it does |
|---|---|
| `init --plan <envelope> --root <repo>` | validates the plan and the grant, creates the run branch and the run directory, computes the waves |
| `next [--max N]` | prints the task ids that are ready now, honouring `depends_on` and the parallel limit |
| `start <task>` | creates the worktree and task branch, records the start |
| `verify <task>` | re-runs that task's verify commands in the worktree and records each command with its outcome |
| `review <task> --verdict pass\|fail --detail …` | records the reviewer's verdict |
| `merge <task>` | merges the task branch into the run branch, removes the worktree; a conflict parks the task |
| `park <task> --reason …` | records a parked task and its reason |
| `gate --action <class>` | calls `check-grant`; prints COVERED or ASK with the reason |
| `status` / `resume` | prints the run summary; resumes from the last recorded step |
| `finish` | writes the `run-result/v1` envelope, pushes and opens the PR when the grant covers it |

The run directory is `.skill-contract/runs/<run-id>/`, holding `state.json`, `autonomy-log.jsonl` and the worktrees' bookkeeping. It is git-ignored.

## 2. The run loop

1. **`init`.** Validate the task-plan envelope (C3–C7, C9). Require a grant that covers `local_reversible`. Create `factory/<plan-slug>` from the current branch, which MUST match the grant's `branch_pattern` and MUST NOT be the default branch.
2. **`next`.** Tasks with every dependency merged are ready. The parallel limit is `budget.max_parallel`, default 2.
3. **`start` and dispatch.** The session dispatches a fresh executor subagent per ready task with: the task's text, its interfaces, the global constraints, and its worktree path. Nothing else.
4. **`verify`.** On the executor's report, the tool re-runs the task's verify commands in the worktree. **This run is the proof** (C7: the conductor is not the producer). A failure sends the task back to the same executor, up to `budget.max_repairs_per_task`.
5. **`review`.** A reviewer subagent gets the task, the diff and the constraints, and answers: does it satisfy the requirement, and does it do anything the task did not ask for. A fail sends it back on the same repair budget.
6. **`merge`.** After both pass, merge the task branch into the run branch and drop the worktree. A conflict parks the task.
7. **Loop** until no task is ready, then **`finish`**.

**Parking never stops the run.** A parked task records its reason; the remaining ready tasks continue. Dependents of a parked task become blocked and are reported as such.

## 3. Gates, budgets and stop rules

**Every consequential action calls `check-grant` first**, and proceeds only on exit 0:

| Step | Action class |
|---|---|
| worktree, edits, commits, merge into the run branch | `local_reversible` |
| push the run branch | `push_branch` |
| open or update the PR | `open_pr` |
| anything else | its own class, and A8 keeps `merge`, `deploy`, `spend`, `external_message` and `delete` at ask |

The grant is re-checked before each action, so an expiry, a revocation, a changed spec or plan, a switch to the default branch, or a CI-config change in the run branch's commits stops the run at the next gate.

**Budgets** (`budget` in the grant):

| Budget | Status |
|---|---|
| `wall_clock_min` | enforced, measured from `init` |
| `max_repairs_per_task` | enforced, counted |
| `max_dispatches` | enforced, counted; this is the cost limit |
| `max_parallel` | enforced, default 2 |
| `max_tokens`, `max_usd` | **recorded, not enforced.** The runtime does not expose usage to the tool. The SKILL.md and the run report MUST say so. |

**Stop rules.** The run stops, then `finish`es with whatever is proven:
- a budget is exhausted (wall clock or dispatches);
- the grant expired, was revoked, or its subjects went stale;
- every remaining task is parked or blocked.

Two events PARK the task instead of stopping the run (amended in the final fix wave,
2026-09-23): a task that needs a human decision the grant's `decisions` and `defaults` do not
cover parks with `new_human_decision`, and a verify that stays red after `max_repairs_per_task`
parks with `verify_red_after_repairs`. Their dependents become `blocked`, and every other ready
task goes on. (A `merge-inconsistent` park, where the root moved during a merge, does stop the
run with `new_human_decision`.) The grant's `stop_on` list is recorded but not enforced by
factory-conductor 1.0.0; these rules apply.

The user can stop a run at any moment with `revoke-grant`: the next gate asks.

## 4. The log and the evidence

- **`autonomy-log.jsonl`** is append-only, one JSON object per event: `init`, `dispatch`, `verify` (with each command and outcome), `review`, `merge`, `gate`, `park`, `stop`, `finish`. It stays local and is never committed.
- **`run-result/v1`**, a new kind, is written by `finish`. Per task: status (`proven`, `parked`, `blocked`), the verify commands with outcomes, the review verdict, the merge commit and the park reason. It pins the plan envelope, the grant and the log's sha256. Its assertions carry each verify command, `assertedBy` the conductor skill. Under C7 a receiver reads them as CLAIMED, because the conductor produced the envelope; they become PROVEN when a receiver re-runs them (`check-envelope --rerun`) or when CI on the pushed branch reports them (`run_url`). Inside the run, the conductor's own re-run is what licenses each merge.
- **The PR body** lists proven tasks, parked tasks with reasons, the stop rule that ended the run, and the budget note from C5.

## 5. Scheduling (3B)

- **The spec** may carry an `[after: R2, R3]` hint on a requirement, beside `[where: …]`.
- **The plan payload** gains an optional `depends_on: ["T1", …]` per task. It is additive, so `task-plan/v1` stays v1.
- **`spec_to_tasks.py --waves`** prints the wave grouping and the critical path. A cycle, or a dependency on an unknown task, fails with a non-zero exit.
- **A plan with no `depends_on`** is one wave, and the conductor runs it up to `max_parallel`.
- **Verify commands** (amended in the final fix wave, 2026-09-23). An acceptance criterion may
  end with `[cmd: <argv>]`, the command that proves it, split with `shlex` and checked against
  C6 by `spec_lint.py`; `spec_to_tasks.py` writes it as the verify step's `command`, and
  `--unattended` requires one on every criterion. `conductor init` refuses a plan with a null
  or empty command (exit 2). Without this, every planner-derived task carried
  `"command": null` and parked at verify, so only hand-built plans could be proven.

## 6. Notifications, resume and packaging

- **While running:** one line per event on stdout, plus the log.
- **tmux `blocked` marker: a documented follow-up** (amended in the final fix wave, 2026-09-23). The earlier text had a parked task set tmux-agent-herdr-lite's pane `blocked` marker. A park does not block the run, whose other tasks go on, so marking the pane blocked would mislabel it; a marker for a run that stopped for a human is the follow-up. No code ships for it.
- **Resume:** `conductor resume` reads `state.json`, re-checks the grant and continues. A run resumed after expiry stops instead.
- **Packaging:** an ordinary skill. The executor and reviewer are subagents the session dispatches. A Claude Code plugin that restricts their tools is a documented follow-up, not part of this spec.

## 7. Acceptance criteria

- **AC1.** `conductor.py` unit tests cover: wave computation; a dependency cycle and an unknown dependency (both fail); the parallel limit; repair counting; each stop rule; append-only logging; resume after each step; park and blocked propagation.
- **AC2.** Every gate call goes through the vendored `contract_check.py check-grant`, and the conductor proceeds only on exit 0. A test proves that a revoked grant stops the run at the next gate, and that a CI-config commit on the run branch makes the push ask.
- **AC3.** The eval carries NEGATIVE fixtures: a task whose verify fails is never merged; a task that needs a decision parks; an expired grant stops the run; `max_dispatches` stops the run; a merge conflict parks.
- **AC4.** An end-to-end test runs a three-task fixture plan (one task depending on another, one failing) from `init` to `finish` in a temporary repo, with push and PR stubbed, and asserts: two tasks proven and merged, one parked, a `run-result/v1` envelope that validates, and a log whose digest matches the envelope.
- **AC5.** `factory-conductor` adopts skill-contract: it consumes `task-plan/v1` and `autonomy-grant/v1`, provides `run-result/v1`, and vendors the checker byte-identical. That makes 5 adopters.
- **AC6.** A/B rows: plans runnable end to end 0 → 1; adopters 4 → 5; a guard that a task with a failing verify is never merged, 0 → 0.
- **AC7.** BCP 14: the declaration, PP-7 passing, PP-5 not newly advisory, and a classification row for every hard rule. The skill ships a README and an eval, and `make gate` is green.
- **AC8.** After merge, Jev re-judges Q3 and "implementation runs end to end without the human", and the result goes into the assessment doc.

## 7a. Threat model (added 2026-09-23, from the Task 2 reviews)

The conductor's proof ("this run of the task's own checks passed on exactly the commit that was merged") is defended against two kinds of executor:

- **Honest but sloppy (MUST be caught):** uncommitted or untracked work, files present only because they are git-ignored or excluded, commits made after verify, amends and resets, branches moved or deleted, a merge left pending, crash leftovers, and verify commands that write caches. Each of these fails verify or refuses the merge.
- **Deliberately hostile, same OS user (stated residual):** an executor that rewrites git internals or the conductor's own files — forging `state.json` or the log, tampering with loose objects or alternates in the shared object store, or racing writes into the conductor's scratch clones. Such an executor already has the power to forge the conductor's state, so no in-process check can be a real boundary against it. The mitigations are the independent reviewer, and CI re-running the checks on the pushed branch outside this machine, which counts as proof under C7.

Cheap hardening that removes whole classes of silent tampering is still applied: verify and merge run in isolated clones with no inherited config, attributes, hooks, filters, merge drivers or fsmonitor; replace objects, grafts and the commit-graph are ignored for every conductor git call; symlinks that escape the tree fail verify. The final fast-forward of the root to the merged commit runs under the root's own config, so the root's filters can change its working-tree files, but never the merged commit, which is content-addressed and checked before and after.

## 8. Risks

- **Model quality bounds everything.** The conductor proves what a task's verify commands prove. A plan with weak checks yields weak proof. The SKILL.md states this, as spec-first-planning states that its linter checks structure and not reasoning.
- **Parallel merges conflict.** Mitigations: waves keep dependents apart, worktrees isolate, and a conflict parks rather than forcing.
- **A long run drifts from the spec.** Mitigations: staleness is checked at every gate, and the 7-day grant lifetime caps a run.
- **Cost.** Only dispatch counts and wall clock bound it. This is stated, not hidden.
- **A parked task can be missed.** Mitigations: the PR body and the run report.

## Out of scope

- Token and dollar budget enforcement.
- Merging, deploying and releasing, which A8 keeps with the human.
- Roadmap steps 5 and 6: release and operations intake, and the business-loop skills.
- A Claude Code plugin with tool-restricted agents.
- New contract kinds beyond `run-result/v1`.

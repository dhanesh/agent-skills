# factory-pipeline: the stages of one tick

Status: built. `pipeline tick` ships with all ten stages. The design is `docs/factory/2026-11-04-autonomous-factory-spec.md`
in the agent-skills repository (requirements R12 to R21).

## The lock, before everything else

Only one tick runs at a time in one repository (R14). A tick can last longer than the
timer interval, because one tool call can run for up to 3600 seconds. The lock stops two
ticks from running at the same time.

1. The tick makes `<repo>/.skill-contract/pipeline/` with a `.gitignore` that holds `*`.
   It writes the lock file only after it proves that this `.gitignore` is a regular file
   with `*` and no `!` line. When it cannot prove this (for example, the directory is a
   symlink), it prints `STOP: state-dir reason=<reason> dir=.skill-contract/pipeline` and
   exits 2.
2. The tick takes a non-blocking exclusive `flock` on `.skill-contract/pipeline/.lock`.
   It does this before the grant check and before any stage.
3. When another process holds the lock, the tick prints
   `STOP: locked: another pipeline tick holds .skill-contract/pipeline/.lock` and exits 3.
   It runs no grant check and no stage, so the queue does not change.
4. The tick holds the lock until it exits, through every stage and every tool call.

When the process that holds the lock dies, the system releases the lock. A killed tick
thus never blocks the next tick. The lock file stays on disk, and that is correct: only
the `flock` counts. The tools that the tick runs do not inherit the lock. ops-intake's own
run lock is a different file (`.skill-contract/intake/.lock`), so the two locks never wait
for each other.

## The grant check, before any stage

Before the first stage, the tick runs spec-first-planning's vendored
`contract_check.py check-pipeline-grant --root <repo>` (R13). The checker judges the newest
pipeline grant that no revision supersedes, as the skill-contract SPEC says. With
`tick --grant <id or envelope path>`, it judges that grant instead. The tick goes on only
when the checker exits 0 with `VALID`. Then it prints `TICK: grant <id> expires_at <time>`,
and the derive-grant stage uses that id and no other.

In every other case the tick runs no stage, so it sweeps, picks and plans nothing. It
prints `STOP: no-grant reason=<reason>` and exits 2. The reasons are:

| Reason | Meaning |
|---|---|
| `none` | There is no pipeline grant (or no grant with the `--grant` id). |
| `revoked` | The grant is revoked, by its id or by the kill switch (`revoke-grant` with no id). |
| `superseded` | A newer revision replaces the grant. |
| `expired` | `expires_at` is past. |
| `lifetime` | `expires_at` is more than 7 days from now. |
| `stale` | The pick policy or the command allowlist changed after the owner accepted the grant. |
| `tracked` | Git tracks the grant file. A grant is one person's acceptance and stays out of commits. |
| `invalid` | The envelope breaks the contract (the checker prints a `FAIL:` line for each problem). |
| `checker-unavailable` | spec-first-planning, or its vendored `contract_check.py`, is not installed. |
| `checker-error` | The checker did not start, timed out or gave an answer the tick cannot read. |
| `not-an-envelope` | `--grant` names no pipeline grant id and no envelope under `.skill-contract/envelopes/`. |

The checker's own lines are printed with the `  grant| ` prefix.

## The stages, in order

One `pipeline tick` runs these stages and prints one `STAGE: <name> <result>` line for each.
A detail can follow the result, for example `exit=3` or `picked=2`.

| Stage | What the tick does today | Skill and tool it calls |
|---|---|---|
| `sweep` | Runs the sweep commands of the owner's committed intake config. | `ops-intake`: `intake sweep` |
| `incident-probe` | When `.release/recipe.json` exists, runs the recipe's read-only probes (`health`, `version_probe`, `prod_smoke`) once. The detail names the result: `probe=pass` or `probe=fail`, `commands=<n>` and `failed=<n>`. A failed probe gives `attention`. With no recipe, the stage runs nothing and the result is `skip no-recipe`. It runs no other recipe command: no rollback, no deploy and no build. It runs before the first planner, so the probes run with the owner's PATH. | `incident-watch`: `incident --root <repo> --skills-dir <root>... probe` |
| `growth-review` | Reviews the watched export when it changed, and imports each breach into the queue. The tick runs two processes and passes the bytes between them. It uses no shell pipe. | `growth-review`: `growth auto --source <name>`, then `ops-intake`: `intake import --format intake-signals-jsonl --source <name>` |
| `idea-follow-up` | Lists each undecided idea with an assumption due within 3 days or expired, and hands each `go` idea-brief envelope with no plan yet to the plan stage (R20). See "The idea follow-up stage". With nothing to list or plan, the result is `idle`. | `idea-validation`: `ideas list` and `ideas status <slug>` (read-only); `spec-first-planning`: `contract_check.py check-envelope` |
| `autopick` | Picks items inside the registered pick policy. The plan stage plans them after the go ideas of the idea stage. The tick keeps each picked intake-item envelope for the plan stage. | `ops-intake`: `intake autopick` |
| `plan` | For each picked envelope, writes the spec skeleton into the git-ignored spec directory, runs the owner's planner to complete it, and hands the plan on only when lint and the allowlist pass (R17). See "The plan stage". With no pick, the result is `idle`. | `spec-first-planning`: `intake_request.py` (or `idea_request.py`), `spec_lint.py --unattended`, `spec_to_tasks.py --allowlist`, `spec_to_tasks.py --envelope`; the planner from the grant's `planner` field |
| `derive-grant` | For each plan that the plan stage hands on, derives a per-plan grant from the pipeline grant. With no plan, the result is `idle`. | `spec-first-planning`: `write_grant.py --under-pipeline <grant> --root <repo> --plan <plan>` |
| `build` | Starts one factory-conductor run for the oldest derived grant that no run names yet, while no run in the root is unfinished (R18). See "The build stage". With nothing to build, the result is `idle`. | `factory-conductor`: `conductor init --plan <plan> --root <repo>`, then `conductor reentry install --root <repo>` when the grant has a reentry block; `spec-first-planning`: `contract_check.py check-grant` |
| `release-stage` | Runs `release stage` once for the release that `release prep` prepared, only after the owner merged its release pull request and while the release grant covers it (R19). See "The release stage". With no release, the result is `idle`. | `release-conductor`: `release stage --root <repo> --unattended`; a read-only probe that asks release-conductor's own merged-check and grant check |
| `digest` | Writes `.skill-contract/pipeline/digest.md`: each human gate that waits, with the command the owner runs next (R21). See "The digest". With nothing waiting, the file says so. Result: `ok waiting=<n>`. | none: it reads the state files of factory-conductor, release-conductor, incident-watch and the tick, and the `ideas list` output of the idea stage |

The one-tick lock (R14), the caps (R15), the plan stage (R17), the build stage (R18), the
release stage (R19), the idea follow-up stage (R20), the digest (R21) and the schedule
(R16) ship; see their sections.

## The idea follow-up stage

The idea follow-up stage (R20) runs after growth review and before autopick. It runs
before the first planner, so it is outside the tamper tripwire (D18). It still runs
idea-validation's tool with `python -I -S -B`, as it runs every tool.

The stage reads idea-validation's state and changes none of it. It runs only two
commands: `ideas list` and `ideas status <slug>`. It never runs `new`, `register`,
`record`, `decide` or `handoff`, and it never passes `--today` (idea-validation refuses
`--today` outside its own tests). Experiments and idea decisions stay with the owner (B1).

**Due ideas.**

1. The stage runs `ideas list` and keeps each registered idea that has no decision and is
   not closed.
2. For each such idea, it runs `ideas status <slug>` and reads each `ASSUMPTION:` line. It
   reads the deadline (`by`) of each assumption from the idea's
   `.skill-contract/ideas/<slug>/state.json` (the latest registration).
3. An assumption is due when its status is not `pass`, `fail` or `expired` and its
   deadline is 0 to 3 days after today. It is expired when its status is `expired`, or
   when its deadline is before today and its status is not `pass` or `fail`.
4. For each idea with a due or expired assumption, the stage prints
   `IDEA: due <slug> A1@<date>[(expired)],... next: ideas status <slug>` and adds a
   waiting entry for the owner to the digest list. The entry names `ideas status <slug>`
   as the next command.

**Go ideas.**

1. The stage lists `.skill-contract/ideas/envelopes/*.json`, newest first.
2. It skips an envelope whose decision is not `go`: a kill or a pivot is the owner's
   record, not a request.
3. It runs spec-first-planning's `contract_check.py check-envelope <envelope> --root
   <repo>`. When the checker refuses the envelope, the stage prints `IDEA: waiting
   <envelope> reason=check-envelope-failed`, and the envelope waits in the digest.
4. It skips an idea that has a plan already. The idea has a plan when one of these is
   true: the spec `.skill-contract/ideas/specs/<slug>.md` exists, a task-plan envelope in
   `.skill-contract/envelopes/` names that spec as its subject, or
   `.skill-contract/pipeline/idea-plans.jsonl` has a line for the slug. The plan stage
   writes that line when it hands on a plan for the idea. So no later tick plans the same
   idea again, even when the owner moves the spec away.
5. Otherwise the stage prints `IDEA: plan <slug> envelope=<envelope>` and adds the
   envelope to the plan stage's list, before the envelopes that autopick picks. The plan
   stage's lint, allowlist, caps and tamper rules apply to it unchanged.

The stage line is one of these:

| Line | Meaning |
|---|---|
| `STAGE: idea-follow-up ok due=<n> go=<n>` | `<n>` ideas listed as due, `<n>` go ideas handed to the plan stage. |
| `STAGE: idea-follow-up attention due=<n> go=<n> waiting=<n>` | An envelope waits, or an `ideas status` call failed. |
| `STAGE: idea-follow-up idle` | Nothing to list and nothing to plan. |
| `STAGE: idea-follow-up unavailable idea-validation due=0 go=<n>` | idea-validation is not installed. Go envelopes are still handed on. |
| `STAGE: idea-follow-up refused due=0 go=<n>` (or `failed`) | `ideas list` refused or failed. Go envelopes are still handed on. |

## The plan stage

The plan stage (R17) drafts a spec for each item that autopick picked in this tick.

The agent command comes only from the pipeline grant's optional `planner` field. The owner
writes it in the answers file of `write_grant.py --pipeline` and accepts it with the
grant. `check-pipeline-grant` checks it: an argv list, no shell as the first element,
`{prompt}` exactly once, `{root}` optional, and no permission-bypass flag. A command never
comes from the queue or from the evidence. With no `planner`, the stage prints
`STAGE: plan unavailable no-planner picked=<n>`, and each picked item waits for the owner.

For each picked envelope, the stage does these steps in order. When a step fails, the
stage stops for that item, prints `PLAN: waiting <spec> reason=<reason>`, adds the item to
the digest list as waiting for the owner's read, and hands nothing on.

| Step | What the stage does | Reason when it fails |
|---|---|---|
| 1 | Reads the envelope kind and the item id (or the idea slug). | `not-a-request`, `envelope-unreadable`, `envelope-outside-root` |
| 2 | Makes sure that git ignores `.skill-contract/intake/specs/<id>.md` (`git check-ignore`), and that no spec is there yet. | `spec-not-ignored`, `spec-exists`, `spec-dir` |
| 3 | Writes the skeleton with `intake_request.py` (or `idea_request.py`). | `request-refused`, `spec-write` |
| 3a | Takes the tamper snapshot. When the repository is too large for it, the planner does not run. | `snapshot-too-large-files`, `snapshot-too-large-bytes` |
| 4 | Runs the planner with no shell, in its own process group, with the tick's timeout. Then it kills what the planner left running. | `planner-exit=<n>`, `planner-timeout`, `planner-failed` |
| 5 | Takes the tamper snapshot again and compares it with the one taken before the planner (see below). Then it makes sure that the spec is still a regular file, and that the allowlist still has the pinned sha256. | `planner-changed-the-repo`, `planner-changed-git`, `planner-changed-tools`, `planner-changed-state`, `spec-replaced`, `allowlist-changed` |
| 6 | Runs `spec_lint.py --unattended <spec>` with `python -I -S -B`, only when the tool's files and the program roots match the baseline. | `lint-failed`, `planner-tamper` |
| 7 | Runs `spec_to_tasks.py <spec> --allowlist <allowlist>`. | `allowlist-unlisted` (exit 3), `allowlist-error` (exit 2), `allowlist-failed` |
| 8 | Runs `spec_to_tasks.py <spec> --envelope <repo>` and reads its `ENVELOPE:` line. | `envelope-failed` |

The tamper snapshot holds the sha256 of these files:

- every file under the work tree: tracked, untracked and git-ignored. It leaves out the
  spec, `.git`, `.skill-contract/pipeline/` (the tick's state) and `.skill-contract/runs/`
  (the conductor's runs and their worktrees);
- `config`, `config.worktree`, `HEAD`, `packed-refs`, `hooks/`, `info/` and `refs/` of
  the git directory and the common directory. It never reads `objects/`;
- `~/.gitconfig`, the XDG git config, and every `core.hooksPath` directory that one of
  these config files names;
- every file of the spec-first-planning skill that the stage runs, `__pycache__`
  included, at its real path;
- the tamper marker `.skill-contract/pipeline/tamper.json` (see below), or the fact that
  it is not there.

In the work tree and the git files, a symlink is recorded by its target and is not
followed. The skill files are different. The `skills` CLI installs each skill directory
as a symlink (for example `~/.claude/skills/spec-first-planning` ->
`~/.agents/skills/spec-first-planning`). So the tick resolves the skill directory and
each tool path one part at a time. It records every symlink that it finds on the way,
with its `readlink` target: a parent directory, the skill directory itself, and a file or
a directory inside the skill. Then it follows the link and hashes every file at its real
path. These changes all poison the tick:

- an edit to a tool file through the link;
- a link that points to a new target;
- a new link in the skill or on the way to it.

The snapshot stops at 200000
files or 2 GiB. Past that limit, the plan waits before the planner starts. A repository
that large plans by hand, or keeps its bulk out of the work tree.

Every git call of the tick is hardened: `git --no-replace-objects -c
core.fsmonitor=false -c core.hooksPath=/dev/null -c core.commitGraph=false`, with no
`GIT_*` variable from the caller and with `GIT_CONFIG_NOSYSTEM=1`. Every tool that the
tick runs gets the same three settings through `GIT_CONFIG_COUNT`. So a hook or an
fsmonitor command that the planner plants never runs in a tick.

### A poisoned tick

A `planner-changed-*` reason poisons the tick. So does a spec-first-planning file or a
program root that differs from the baseline. The tick takes that baseline after the
grant check, before any planner runs, and checks it again before every run of a
spec-first-planning tool: the cap counter, each plan check and each derivation. Then the
tick:

- prints `STOP: planner-tamper <the-repo|git|tools|programs>`;
- runs no further spec-first-planning tool;
- moves every plan of this tick, the ready ones too, and every derived grant to the
  digest list, with the reason `planner-tamper-<what>`;
- prints `STAGE: <name> skipped planner-tamper=<what>` for autopick, plan, derive grant,
  build and release stage;
- still runs digest, because it runs no sibling code;
- ends `TICK: done stages=10 tamper=<what>` and exits 4;
- writes the tamper marker `.skill-contract/pipeline/tamper.json` (the time, the reason
  and the changed paths), and appends a `poison` line to `tamper-log.jsonl`.

### A poison outlives the tick

Without the marker, the next tick would take a fresh baseline from the changed files and
trust them. So every later tick reads the marker right after the grant check, before any
stage. While the marker exists, the tick:

- prints `STOP: planner-tamper persisted <reason>` and the paths that changed;
- runs no stage and calls no tool;
- exits 4.

Only the owner clears the marker:

```sh
python3 factory-pipeline/assets/pipeline.py --root <repo> tamper-clear --by <name>
```

The command removes the marker and appends a `clear` line with the name to
`tamper-log.jsonl`. It prints `TAMPER: cleared <reason> by <name>`, or `TAMPER: none`
when there is no marker, and exits 0. Without `--by`, it exits 2 and keeps the marker.
While a tick holds the lock, it exits 3. The owner must inspect every file that the
marker names, and restore it, before clearing it. The next tick trusts what those files
hold.

The marker is in the tamper snapshot. A planner that writes, edits or removes it during
a tick poisons that tick with `planner-changed-state`.

Every Python tool that the tick starts runs as `python -I -S -B`. Python then loads no
`site` module, so it reads no `site-packages` directory, no `.pth` file, no
`sitecustomize.py` or `usercustomize.py` and no `PYTHON*` variable. It writes no
bytecode. A small launcher sets `sys.path` to the tool's own directory and the stdlib
directories only.

### The program baseline

The baseline also holds the program roots: every program and code root that the tick
runs after a planner. The tick finds them once, before any planner runs. It asks the
interpreter that the tools run with, not its own interpreter.

- The interpreter: its path and its real file (sha256), `pyvenv.cfg` beside it and one
  level up, `<name>._pth` and `pythonXY._pth` beside it, and its `libpython*` files in
  `<prefix>/lib`.
- The stdlib: every directory and zip on the interpreter's `-I -S` path, and its
  `stdlib` and `platstdlib` directories from `sysconfig`. This includes `site-packages`
  and `__pycache__`, because a planted `.pth` or `.pyc` file lives there.
- The pinned programs: each program that the tick or a tool it starts runs by name.
  These are `git`, `python3`, `python` and, on macOS, `xcode-select`, which
  `contract_check.py` can run. The tick finds each one on the owner's `PATH` once and
  pins its absolute path (sha256). It also records git's `git --exec-path` directory, and
  the planner's own program (by stat).
- The private `PATH` directory: the tick makes it with `mkdtemp`, outside the
  repository, with mode 0700. It holds one small wrapper per pinned name, and each
  wrapper runs the pinned absolute path.

From the first planner on, every tool that the tick starts gets a `PATH` that holds only
the private directory. The tick's own git calls run the pinned absolute path. So a
program that a planner puts anywhere on the owner's `PATH` never runs in the tick. The
planner itself keeps the owner's `PATH`, because it is the part that the tick does not
trust. Right after each planner exits, before the tick runs anything else (git
included), the tick compares the program roots with the baseline. So a changed git never
runs to take the snapshot. The tick removes the private directory when it ends.

The tick records the files in these trees by inode, size, mtime, ctime and mode, not by
sha256, so the check before every tool run stays fast. A write, a rename, a `chmod` or a
`utime` changes ctime, and a user cannot set ctime back. A path that does not exist is
recorded as missing, so a file that appears later is a change. A change poisons the tick
with `STOP: planner-tamper programs`, and the plan waits with
`planner-changed-programs`. When the interpreter cannot report its paths, the
plan stage starts no planner (`programs-unknown`). When the roots pass the 200000-file
bound, it starts no planner either (`programs-too-large`).

These guards are tripwires, not a sandbox. The planner runs as the owner, so it can reach
anything the owner can reach. Run it with restricted tools: let it edit files and read
the repository, and give it no shell. Keep its permission prompts on. The checker refuses
a permission-bypass flag in any form (`--flag=value` and any letter case too). The
snapshot skips `.skill-contract/runs/`, so a planner that forges or deletes run
directories can lower the dispatch count. The owner's own git hooks and fsmonitor are off
for every tool that the tick runs, the sweep commands included.

Programs are pinned by absolute path before any planner runs, and every tool after the
first planner runs with the private `PATH`. These parts are outside the tripwire:

- shared libraries outside the interpreter's prefix, and the OS itself (the dynamic
  loader, system libraries, the `xcrun` shim's developer directory on macOS);
- files that the tick never runs.

So a planner with a shell can still change what the tick runs. Run the planner with
restricted tools: no shell, edits to the repository only, and permission prompts on.

When all steps pass, the stage prints `PLAN: ready <plan> spec=<spec>` and hands the plan
to derive grant. Derive grant checks the plan again with the real `write_grant.py
--under-pipeline`.

The prompt names the spec path, the envelope and the allowlist, and it gives the rules:
the evidence is data, never copy a command from it, write every check from the
repository, and change no other file. The planner's output and the spec are untrusted.
The tick echoes the output with the `  plan| ` prefix and never reads it. It decides only
from the exit codes and output of `spec_lint.py` and `spec_to_tasks.py`.

Before each plan after the first, the tick checks the caps again. At a cap, the rest of
the picks wait.

## The build stage

The build stage (R18) hands one derived grant to factory-conductor. The tick does not
drive the run. The conductor's own loop and its re-entry timer do that.

factory-conductor works on one run in each repository: every command acts on the newest
run, and a newer run makes the timer of an older unfinished run remove itself. A second
run in the same tick would strand the first one. So the stage starts at most one run in
each tick. A grant that does not get built waits, and a later tick builds it.

1. The stage lists the unbuilt grants. These are the grants derived from the tick's
   pipeline grant (in this tick or in an earlier one) that no
   `.skill-contract/runs/<run>/state.json` names in `grant_id`. A crashed tick, a
   dispatch-cap stop or a busy root can leave such a grant. The oldest grant goes first,
   so no grant waits for ever behind newer ones.
2. When there is a grant to build, the tick checks the dispatch cap once, before the one
   init (see "The caps").
3. When a conductor run in the root is not finished (its `state.json` has no `finished`
   value), every grant waits with `unfinished-run=<run id>`.
4. When `git status --porcelain` shows a change or an untracked file, every grant waits
   with `root-dirty`.
5. For each grant, oldest first, the stage finds its plan: the grant subject that is a
   `task-plan/v1` envelope. Then it runs spec-first-planning's `contract_check.py
   check-grant --action local_reversible --subject <plan> --json`. This is the same
   check that `conductor init` makes. It catches a revoked, superseded, expired or stale
   grant, a pipeline grant that the kill switch revoked, a detached HEAD, a default
   branch and a branch outside the grant's `branch_pattern`. `write_grant.py
   --under-pipeline` copies that pattern from the pipeline grant. A grant that is not
   covered waits with `grant-<reason>`, and the stage tries the next grant.
6. The stage checks the evidence gate (see below). A mismatch waits, and the stage tries
   the next grant.
7. The stage runs `conductor init --plan <plan> --root <repo>` for the first grant that
   passes. It prints `BUILD: started <grant> run=<run id> gate=<on|off> plan=<plan>`.
   Every later grant waits with `unfinished-run=<the new run id>`.
8. When the grant has a `reentry` block, the stage then runs
   `conductor reentry install --root <repo>`. This is the step that factory-conductor
   documents right after `init` for an unattended start: the owner's timer resumes the
   run.

Each grant that waits prints `BUILD: waiting <grant> <reason>` and goes on the digest
list. The reasons are:

| Reason | Meaning |
|---|---|
| `unfinished-run=<run id>` | A conductor run in the root is not finished. |
| `root-dirty` | The work tree has a change or an untracked file. |
| `root-unknown` | git cannot report the status of the work tree. |
| `grant-<reason>` | `check-grant` does not cover the plan, for example `grant-revoked`, `grant-default-branch`, `grant-branch` or `grant-pipeline`. |
| `evidence-gate-off` | The repository has a verify skill, but the plan has no `verification` block. |
| `verify-skill-missing` | The plan's `verification` block names a verify skill that is not in the repository. |
| `verification-invalid` | The plan's `verification` block is not an object with a `skill` string. |
| `no-plan`, `grant-unreadable`, `plan-unreadable` | The stage cannot read the grant or its plan. |
| `init-exit=<n>` | `conductor init` failed. A later tick tries again. |
| `one-build-per-tick` | Another grant's init failed in this tick. |
| `no-factory-conductor` | factory-conductor is not installed. |

The stage itself never checks out, creates or resets a branch, and it never edits a
plan. `conductor init` cuts the run branch `factory/<plan slug>` from the branch that the
root has checked out. The owner keeps the root on a branch that the grant's
`branch_pattern` covers.

The STAGE line is one of these:

- `STAGE: build ok built=1 waiting=<n> gate=<on|off>`, with `reentry=<result>` when the
  stage ran `reentry install`;
- `STAGE: build attention built=0 waiting=<n>` when every grant waits;
- `STAGE: build idle grants=0` when there is nothing to build;
- `STAGE: build unavailable factory-conductor grants=<n>`;
- the result word of `conductor init` when it fails, for example
  `STAGE: build refused built=0 waiting=1 init exit=2`.

### The evidence gate

factory-conductor has no `init` flag for its evidence gate. The gate is on only when the
plan envelope has a `verification` block (`{"skill": <verify skill dir>}`).
spec-first-planning writes that block from the spec's `Verify skill:` line. The derived
grant pins the sha256 of the plan, so the stage cannot add or remove the block. The
stage thus builds:

- a plan with a `verification` block only when the block names a `verify-<app>`
  directory in the repository with `SKILL.md` and `features/` (gate on);
- a plan with no block only when the repository has no verify skill under
  `.claude/skills/verify-*` or `.agents/skills/verify-*` (gate off).

In practice, every plan from the real plan stage is gated, because
`spec_lint.py --unattended` refuses a spec with no `Verify skill:` line.

### The tamper baseline of the build stage

The build stage runs factory-conductor's code after the planner. So every file of the
factory-conductor skill is in the same tamper baseline as spec-first-planning's files, at
its real path, with every symlink on the way. A planner that changes `conductor.py`, or
any file beside it, poisons the tick before any conductor call:
`STOP: planner-tamper tools` and `STAGE: build skipped planner-tamper=tools`. The stage
checks the baseline again before each conductor call and before `check-grant`. Every
conductor call runs as `python -I -S -B` with the pinned interpreter. After a planner,
it also runs with the private `PATH`.

## The release stage

The release stage (R19) starts release-conductor's `release stage` for a release that the
owner prepared with `release prep`. The tick never writes or accepts a release grant, never
merges a pull request, and runs no other release command: no `prep`, `deploy`,
`verify-prod`, `rollback` or `abandon` (B1, D19). The production deploy and the rollback
stay with the owner.

The tick keeps no release state of its own. release-conductor's state in
`.skill-contract/releases/<version>/state.json` holds the truth. Before it runs anything,
the stage asks release-conductor's own code, read-only, the questions that `release stage`
asks itself. A small probe imports `release.py` with the pinned interpreter
(`python -I -S -B`):

1. When there is no unfinished release, the result is `idle`.
2. When the release is past `prepped`, or its state already has a `stage` record, the
   result is `idle release=<v> status=<status>`. `release stage` writes that record before
   it runs anything, so a later tick never runs the stage again for the same release.
3. When prep has steps left (no push or no pull request yet), the release waits with
   `release-prep`.
4. Merged: release.py's own check. The release commit is the first-parent commit on the
   local default branch where the version file becomes the version that the grant pins.
   The owner's merge reaches the local branch with `git pull`. The tick fetches nothing
   and calls no forge. When the commit is not there, the release waits with
   `release-merge`.
5. Granted: release-conductor's vendored `contract_check.check_grant` for
   `local_reversible` (the first gate of `release stage`), with the grant named by its
   path and the recipe as the subject, on the branch `release/<v>-stage`. A revoked,
   superseded, expired, stale or tracked grant does not cover. The release then waits with
   `release-grant`.

Each gate that waits prints `RELEASE: waiting <v> <gate> <detail>` and goes on the digest
list with the exact command that the owner runs next. When both gates hold, the stage runs
`release stage --root <repo> --unattended` once and prints `RELEASE: staged <v>
commit=<sha>`. release-conductor then builds the commit and asks for a verifier
(`NEXT: dispatch-verifier`). That step, the evidence, the production deploy and the
rollback are not the tick's.

The STAGE line is one of these:

- `STAGE: release-stage ok staged=1 release=<v>` (or the result word of `release stage`
  when it exits non-zero, for example `attention staged=1 release=<v> exit=3`);
- `STAGE: release-stage waiting reason=<gate>[,<gate>]`, for example
  `reason=release-merge` or `reason=release-merge,release-grant`;
- `STAGE: release-stage idle`, with `release=<v> status=<status>` when the stage has
  started;
- `STAGE: release-stage idle deferred=planner-ran release=<v>` (see below);
- `STAGE: release-stage unavailable release-conductor` when a release exists but
  release-conductor is not installed;
- `STAGE: release-stage failed probe` when the probe gives no answer. Then the stage runs
  nothing. The probe uses release.py's internal names, so a changed release-conductor
  fails closed.

A tick in which a planner ran gives its tools only the private `PATH` of pinned programs.
`release stage` runs the owner's recipe commands (the build), and that `PATH` cannot find
them. A failed build ends the release. So such a tick does not start the stage. It reports
`idle deferred=planner-ran`, and the next tick in which no planner runs starts it.

release-conductor's files are in the tamper baseline, like spec-first-planning's and
factory-conductor's, at their real paths with every symlink on the way. A planner that
changes `release.py`, or any file beside it, poisons the tick:
`STAGE: release-stage skipped planner-tamper=tools`, and no release code runs. The stage
checks the baseline again before the probe and before `release stage`.

## The digest

The digest stage (R21) runs on every tick that reaches it. It also runs after a cap stop
and after a planner tamper in the same tick. A tick that stops on a persisted tamper runs
no stage, so the digest of the tick that found the tamper stays on disk.

The stage:

1. proves that `.skill-contract/pipeline/` is git-ignored, with the same check as the lock
   (`STAGE: digest refused state-dir=<reason>` when it cannot);
2. collects the entries (see below);
3. writes them to a temp file in that directory and renames it over `digest.md`. A
   symlink at `digest.md` is replaced, not followed;
4. prints `DIGEST: .skill-contract/pipeline/digest.md waiting=<n>` and
   `STAGE: digest ok waiting=<n>`, where `<n>` is the number of entries.

The file has a title, the time, the pipeline grant id and `Waiting: <n>`. With no entry, it
says "Nothing waits for the owner." Otherwise it has one `## <section> (<n>)` heading per
section that has entries. Each entry is one `- ` line, and each command is one `  - <label>:`
line under it.

| Section | What the digest lists | Source | The owner's command |
|---|---|---|---|
| Pull requests to merge | A factory-conductor run that opened a pull request that is not merged. | The run's `state.json` `finished` (`pr`, `integration.head`), `run_branch` and `base_branch`. | `gh pr merge <url>` when the PR command printed a URL; else `gh pr list --head <run branch> --state open`, then `gh pr merge <number>`. |
| Pull requests to merge | A prepared release whose release pull request is not merged. | The release stage's `release-merge` wait, or the release's `state.json` when the release stage did not check it in this tick. | The same `gh` commands for `release/<version>`, then `git -C <repo> pull`. |
| Release grants to accept | A prepared release whose release grant does not cover it. | The release stage's `release-grant` wait. | `release abandon --root <repo> --approved-by <name> --reason <why>`, then `release prep --root <repo> --approved-by <name> --driver <agent-id>`. |
| Production deploys | A release that is `staged` or `awaiting_deploy`. | The release's `state.json`. | `release deploy --root <repo>` (it shows the summary and waits), then the same with `--approved-by <name>`. |
| Rollbacks | A release that is `prod_failed` or `outcome_unknown`. | The release's `state.json`. | `release verify-prod --root <repo>` first for `outcome_unknown`, then `release rollback --root <repo>`, then the same with `--approved-by <name>`. |
| Rollbacks | An open incident record. | incident-watch's `records/<id>.json`. | incident-watch's own commands (`release rollback`, then `release rollback --approved-by "<their name>"`), never the record's strings: a record whose strings differ is flagged. The recipe's rollback argv comes word for word from release-conductor's `load_recipe`, and a record whose copy differs is flagged. When no release is `prod_failed` or `outcome_unknown`, the owner rolls back by hand with that argv. Then `incident close --by <name> --id <id>`. |
| Plans waiting for a read | A plan that the plan or derive-grant stage left for the owner. | `tick.waiting`. | `spec_to_tasks.py <spec> --envelope <repo>` for a spec, `write_grant.py --under-pipeline <grant> --root <repo> --plan <plan>` for a plan, `intake_request.py` or `idea_request.py` for a picked envelope, and `tamper-clear` after a tamper. |
| Idea decisions | An idea whose computed verdict is `GO`, `KILL` or `STALLED`, with no decision and not closed. | The idea stage's `ideas list` output. | `ideas decide <slug> --verdict go\|kill\|pivot --by <name> --reason <words>`. |
| Idea assumptions due | An idea with an assumption due within 3 days or expired. | `tick.waiting`. | `ideas status <slug>`. |
| Planner tamper | The persisted tamper marker. | `.skill-contract/pipeline/tamper.json`. | `cat .skill-contract/pipeline/tamper.json`, then `pipeline.py --root <repo> tamper-clear --by <name>`. |
| Cap stops | Each cap that stopped this tick, with what waits for it. | The tick's cap stops. | `write_grant.py --pipeline` for a new grant, or `tail -n 1 .skill-contract/pipeline/cap-stops.jsonl` for `uncounted`. |
| Other waits | A build that waits, an idea envelope that waits, a release that stopped. | `tick.waiting`. | A command when one helps, for example `git -C <repo> status --short` for `root-dirty`. |

A merged run leaves the digest when its head is an ancestor of the local base branch, or
when its run branch is gone from the local repository. The digest asks only the local git.
After a squash merge, the run stays listed until the owner deletes the local run branch.

What the digest does not do:

- It runs none of the commands it lists. Each one is a human gate (B1).
- It runs no spec-first-planning or factory-conductor tool. Of release-conductor it runs
  only the read-only recipe reader (`load_recipe`), for the recipe's rollback. It calls no
  forge and opens no network connection. It runs git only to check a merge. It runs
  neither git nor the recipe reader after a planner tamper.
- It puts no free text from a state file into a command. A value becomes a command
  argument only when it passes a strict check: an id, a slug, a version, a branch name,
  a PR URL, or a repo-relative path that does not start with `-` or `/` and has no `..`
  part. A value that fails gets no command. Its entry shows it as a span and says that it
  does not match the expected form.
- It runs `ideas list` itself only when the idea stage left no output and no planner ran
  in the tick. That is the same read-only command, never with `--today`.

Every value from a state file, a tool or a stage is data that a person or a planner can
write. The digest shows each one as an inline code span. Backticks, backslashes, control
characters, format and bidi characters (such as U+202E) and line separators are escaped
as `\uXXXX`. So a value cannot end its span, start a line, or forge a heading or an
entry. Each line starts with the digest's own fixed text. Each command is a fixed
template, and its real arguments are quoted with `shlex.quote`.

## The caps, before each stage that starts a plan or run

The pipeline grant has three caps (R15). Each is an integer of 1 or more, and each counts
per UTC calendar day:

| Cap | What the tick counts | Where the count comes from |
|---|---|---|
| `max_plans_per_day` | Grants derived from this pipeline grant today. | spec-first-planning `write_grant.py --pipeline-caps <grant> --root <repo>` |
| `max_open_runs` | Derived grants that have no `run-result/v1` envelope yet. | the same `--pipeline-caps` call |
| `max_dispatches_per_day` | `dispatch` events dated today in the `autonomy-log.jsonl` of each factory-conductor run whose `state.json` `grant_id` is a grant derived from this pipeline grant. | `.skill-contract/runs/<run>/` |

`--pipeline-caps` uses the same counter that `write_grant.py --under-pipeline` uses when it
refuses a derivation past a cap. The tick and the derivation thus never disagree.

Four stages can start a new plan or run: `autopick`, `plan`, `derive-grant` and `build`.
The tick checks all three caps before `autopick`, `plan` and `derive-grant`, because that
is where a plan or an open run gets counted. In `derive-grant` it checks them again before
each plan after the first, because the grant it just derived can reach a cap. Before
`build`, and only when there are grants to build, it checks only
`max_dispatches_per_day`. A grant that `derive-grant` already derived was counted then,
so building it starts no new counted plan or open run. If build were gated on those caps,
the grant would never run, never get a run result, and stay open for ever. The other stages only read, sweep or report. They run in this order, with or
without a cap stop: `sweep`, `incident-probe`, `growth-review` and `idea-follow-up` before the gates, and
`release-stage` and `digest` after them.

When a count is equal to or more than its cap, the tick:

1. prints `STOP: cap <name> count=<n> limit=<m> stage=<stage>`;
2. appends one JSON line with the same facts to `.skill-contract/pipeline/cap-stops.jsonl`
   (git-ignored), and keeps the stop for the digest;
3. prints `STAGE: <name> skipped cap=<name>` for each later stage that the same cap
   gates. A plans or open-runs stop skips `autopick`, `plan` and `derive-grant`, so no new
   plan starts, and the grants already derived still reach `build`. A dispatches stop
   also skips `build`. An `uncounted` stop skips all four;
4. runs the stages that only read, sweep or report, and ends
   `TICK: done stages=10 cap=<name>[,<name>]` with exit 0.

The tick checks the dispatch cap once before a build starts, because a tick starts at most
one build. It does not stop a build that is already running. A build that starts below the cap can thus pass it: factory-conductor's
own per-run `max_dispatches` budget bounds that run. So one tick passes the daily cap by
at most that budget, and the next build, or the next tick, stops.

A count that the tick cannot make is a stop, not a zero. The tick prints
`STOP: cap uncounted source=<what> reason=<why> stage=<stage>` when:

- `--pipeline-caps` refuses (for example, an envelope it cannot read), fails or is not
  installed (`source=write_grant`);
- a run's `state.json` cannot be read (`reason=unreadable`), or a run directory has an
  `autonomy-log.jsonl` but no `state.json` (`reason=missing`). A run directory with
  neither file is a run that factory-conductor is still making, and the tick skips it;
- a derived run's `autonomy-log.jsonl` is missing while its `state.json` shows
  dispatches, or the log cannot be read, has a line that is
  not a JSON object (`reason=not-json`), or has a `dispatch` event with no time
  (`reason=no-time`).

The stop stays until the owner fixes the file or a new UTC day starts.

## The schedule

`pipeline schedule install --every <minutes>` installs one timer on this machine. The
timer runs `pipeline tick` for the repo every `<minutes>` (R16). The schedule runs on the
owner's always-on machine. There is no hosted scheduler (decision D5).

1. The tool checks `--every` first, before it reads or installs anything. The value must
   be a whole number from 15 to 1440. Another value (for example `10`, `1500`, `15.0` or
   `abc`) prints `STOP: schedule every=<value> reason=<why>` and exits 2. The reason is
   `out-of-range` or `not-a-whole-number`. Nothing is installed.
2. The tool checks the pipeline grant, as the tick does. With no live grant, it prints
   `STOP: no-grant reason=<reason>`, installs nothing and exits 2.
3. The tool loads factory-conductor's timer module, `reentry_timer.py`, from the
   place where factory-conductor is installed. It finds that skill in the same roots as
   the other siblings. It imports the module and does not copy it. When factory-conductor
   is not installed, it prints `SCHEDULE: unavailable factory-conductor` and exits 2. The
   tick still works when you run it by hand.
4. The tool installs the timer with the timer kind of this machine: launchd on macOS, a
   systemd user timer or a tagged crontab line on Linux. It prints
   `SCHEDULE: installed every <n> min kind=<kind> id=<id> grant=<id>`, then
   `SCHEDULE: runs <argv>`, and exits 0.

The timer id is `pipeline-` and the first 12 hex digits of the SHA-256 of the repo's real
path. One repo thus has one timer. A second install replaces it. The id never matches a
factory-conductor run id (`run-...`), so the two timers never touch each other.

The timer runs this argv, with no shell: the absolute path of the Python interpreter that
ran install, the absolute path of `pipeline.py`, `--root <repo>`, each `--skills-dir`
given to install, and `tick`.

A timer has a minimal PATH of its own (launchd: `/usr/bin:/bin:/usr/sbin:/sbin`, cron:
`/usr/bin:/bin`). The tick finds Python by its absolute path. But the grant checker calls
git, and the sweep runs the owner's commands. Install thus captures the PATH that it runs
with, and reentry_timer writes that PATH into the timer: `EnvironmentVariables` in the
launchd plist, `Environment=` in the systemd unit, and `/usr/bin/env PATH=...` in the
cron line. Run install from a shell whose PATH finds git and the sweep's tools.

cron cannot step its minute field past 59. On cron, an interval under 60 is
`*/<n> * * * *`. An interval of 60 or more must be a whole number of hours that divides
24: 60, 120, 180, 240, 360, 480, 720 or 1440 (`0 0 * * *`). Another interval gives
`SCHEDULE: failed` and exit 2. The tool tests the timer text before it changes anything,
so the timer that was there stays. launchd and systemd take every interval from 15 to
1440.

`schedule status` prints `SCHEDULE: id <id>` and `SCHEDULE: timer <files or line>`, or
`none`. `schedule uninstall` removes the timer and prints `SCHEDULE: uninstalled id <id>`.
Neither needs a grant. When the owner revokes the pipeline grant, the timer stays, and
each tick prints `STOP: no-grant`. Run `schedule uninstall` to stop the timer.

The tests never install a real timer. They set reentry_timer's dry-run hooks
(`FACTORY_CONDUCTOR_TIMER_DRYRUN=1` and `FACTORY_CONDUCTOR_TIMER_HOME=<temp dir>`). They
also put fake `launchctl`, `systemctl` and `crontab` first on PATH, and fail when one of
them is called.

## Result words

| Result | Meaning |
|---|---|
| `ok` | The tool exited 0. |
| `idle` | The stage had nothing to do. |
| `attention` | The tool exited 3 (a problem or the owner is needed), or some plans were refused a grant. |
| `refused` | The tool exited 2 (or 1 for `write_grant.py`). For example, there is no intake config or no registered growth plan. |
| `failed` | The tool exited with another code, or could not start. |
| `timeout` | The tool ran for more than 3600 seconds. |
| `unavailable` | The skill is not installed. |
| `pending-R<n>` | The stage is not built yet. No stage says this today. |
| `waiting` | The release stage waits for a human gate: the merge of the release pull request (`release-merge`) or a live release grant (`release-grant`). |
| `built=<n> waiting=<n>` | The build stage started `n` runs (0 or 1) and left `n` grants waiting. |
| `skipped` | A cap stopped the tick before this stage, which would start a plan or run. The detail is `cap=<name>`. |
| `skip` | The incident probe found no `.release/recipe.json`, so it ran nothing. The detail is `no-recipe`. |

A result that is not `ok` does not stop the tick. The tick goes on to the next stage, and
exits 0 when every stage ran. Exit 2 is a usage error (for example a `--root` that is not
a directory), `STOP: state-dir` or `STOP: no-grant`. Exit 3 is `STOP: locked`. Exit 4 is
`STOP: planner-tamper` or `STOP: planner-tamper persisted`. The
schedule's exits are in its section.

## How the tick finds a skill

Each skill is installed on its own, so the tick looks for each sibling by its directory
name. It searches the roots that skill-contract `discover` searches, in this order:
`$SKILL_CONTRACT_PATH`, the directory beside this skill, `<repo>/.agents/skills`,
`<repo>/.claude/skills`, `~/.agents/skills` and `~/.claude/skills`. The directory has to hold
a `SKILL.md` and the tool. `--skills-dir <dir>` or `$FACTORY_PIPELINE_SKILLS` replaces the
whole list. The tests use it to point at stub skills.

## Output

The tick prints `TICK: start <time> root <repo>`, then (when it holds the lock) `TICK: grant <id> expires_at <time>`,
then the stages, then `TICK: done stages=<n>`. A line that starts with two spaces and `<stage>|` is the stage
tool's own stdout or stderr. The prefix keeps a tool's `STOP:` line apart from the tick's
own lines. The growth signals themselves are not printed: they go only to `intake import`.

## Human gates

The tick removes no human gate. These stay with the owner:

- A pull request to merge.
- A release grant to accept.
- A production deploy or a rollback.
- A plan that waits for the owner's read.
- An idea decision.
- The planner-tamper marker (`tamper-clear --by <name>`) and a cap stop.

The digest (`.skill-contract/pipeline/digest.md`) lists each of these that waits, with the
command the owner runs next.
- The pick policy, the intake config and the pipeline grant.

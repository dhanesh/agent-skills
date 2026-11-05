# pipeline-tick: run every factory stage in one scheduled, locked, capped tick

- id: pipeline-tick
- proven: no
- anchors: factory-pipeline/assets/pipeline.py:cmd_tick, factory-pipeline/assets/pipeline.py:STAGES, factory-pipeline/assets/pipeline.py:find_tool, factory-pipeline/assets/pipeline.py:check_grant, factory-pipeline/assets/pipeline.py:tick_lock, factory-pipeline/assets/pipeline.py:cap_gate, factory-pipeline/assets/pipeline.py:count_caps, factory-pipeline/assets/pipeline.py:dispatches_today, factory-pipeline/assets/pipeline.py:cmd_schedule, factory-pipeline/assets/pipeline.py:every_error, factory-pipeline/assets/pipeline.py:stage_plan, factory-pipeline/assets/pipeline.py:plan_one, factory-pipeline/assets/pipeline.py:run_planner, factory-pipeline/assets/pipeline.py:snapshot, factory-pipeline/assets/pipeline.py:git, factory-pipeline/assets/pipeline.py:program_roots, factory-pipeline/assets/pipeline.py:program_digest, factory-pipeline/assets/pipeline.py:make_pathdir, factory-pipeline/assets/pipeline.py:seal, factory-pipeline/assets/pipeline.py:programs_trusted, factory-pipeline/assets/pipeline.py:py, factory-pipeline/assets/pipeline.py:tools_walk, factory-pipeline/assets/pipeline.py:_resolve, factory-pipeline/assets/pipeline.py:_walk_real, factory-pipeline/assets/pipeline.py:write_marker, factory-pipeline/assets/pipeline.py:read_marker, factory-pipeline/assets/pipeline.py:cmd_tamper_clear, factory-pipeline/assets/pipeline.py:stage_build, factory-pipeline/assets/pipeline.py:unbuilt_grants, factory-pipeline/assets/pipeline.py:run_states, factory-pipeline/assets/pipeline.py:evidence_gate, factory-pipeline/assets/pipeline.py:grant_covers, factory-pipeline/assets/pipeline.py:trusted_tools, factory-pipeline/assets/pipeline.py:stage_release_stage, factory-pipeline/assets/pipeline.py:release_probe, factory-pipeline/assets/pipeline.py:release_wait, factory-pipeline/assets/pipeline.py:release_dirs, factory-pipeline/assets/pipeline.py:stage_idea_follow_up, factory-pipeline/assets/pipeline.py:ideas_due, factory-pipeline/assets/pipeline.py:idea_due, factory-pipeline/assets/pipeline.py:ideas_go, factory-pipeline/assets/pipeline.py:idea_envelope, factory-pipeline/assets/pipeline.py:planned, factory-pipeline/assets/pipeline.py:record_idea_plan, factory-pipeline/assets/pipeline.py:stage_digest, factory-pipeline/assets/pipeline.py:digest_entries, factory-pipeline/assets/pipeline.py:digest_runs, factory-pipeline/assets/pipeline.py:pr_merged, factory-pipeline/assets/pipeline.py:digest_releases, factory-pipeline/assets/pipeline.py:digest_incidents, factory-pipeline/assets/pipeline.py:digest_recipe, factory-pipeline/assets/pipeline.py:safe_rel, factory-pipeline/assets/pipeline.py:digest_ideas, factory-pipeline/assets/pipeline.py:plan_read, factory-pipeline/assets/pipeline.py:span, factory-pipeline/assets/pipeline.py:command, factory-pipeline/assets/pipeline.py:render_digest, idea-validation/assets/ideas.py:cmd_list, idea-validation/assets/ideas.py:cmd_status, release-conductor/assets/release.py:_stage_commit, release-conductor/assets/release.py:_grant_pins, release-conductor/assets/release.py:find_release_commit, spec-first-planning/assets/write_grant.py:build_pipeline_grant, factory-conductor/assets/reentry_timer.py:cron_schedule

## What it is

**Status: built.** `pipeline tick` ships (R12) with all nine stages. First it takes the repo's pipeline
lock, a non-blocking flock on `.skill-contract/pipeline/.lock` in a git-ignored directory
(R14). A tick that finds the lock held prints `STOP: locked: ...`, changes nothing and
exits 3. Before any stage, it checks the
standing pipeline grant with spec-first-planning's `contract_check.py check-pipeline-grant`
(R13). With no live grant it runs no stage, prints `STOP: no-grant reason=<reason>` and
exits 2. It runs the factory stages in this
order: sweep, growth review, idea follow-up, autopick, plan, derive grant, build, release
stage and digest. It prints one `STAGE: <name> <result>` line per stage and exits 0 when
every stage ran. Before autopick, plan and derive grant, and between plans in derive
grant, it checks the pipeline grant's three caps against today's UTC counts (R15). Before
build, when it has grants to build, it checks only `max_dispatches_per_day`: a grant that
derive grant already derived was counted then, so building it starts no new counted plan
or open run. spec-first-planning
`write_grant.py --pipeline-caps` counts plans and open runs with the same counter that
`--under-pipeline` refuses with. The tick counts today's `dispatch` events in the
`autonomy-log.jsonl` of each factory-conductor run whose grant derives from the pipeline
grant. At a cap it prints `STOP: cap <name> count=<n> limit=<m> stage=<stage>`, appends
the reason to `.skill-contract/pipeline/cap-stops.jsonl`, and skips every later stage
that the same cap gates. A count source that it cannot read gives `STOP: cap uncounted source=<what>
reason=<why>`, never a zero. Sweep, growth review and autopick call ops-intake and growth-review for
real. The plan stage (R17) starts the owner's planner (the pipeline grant's `planner`
argv) on a spec skeleton that spec-first-planning's `intake_request.py` writes into the
git-ignored `.skill-contract/intake/specs/`. It hands the plan on only when
`spec_lint.py --unattended` and `spec_to_tasks.py --allowlist` pass; otherwise it prints
`PLAN: waiting <spec> reason=<reason>` and the plan waits for the owner's read. Derive
grant calls spec-first-planning `write_grant.py --under-pipeline` for each plan that the
plan stage hands on. The build stage (R18) runs factory-conductor's `conductor init` for
at most one derived grant per tick: the oldest one that no run's `state.json` names, from
this tick or an earlier one, only while no conductor run in the root is unfinished. The
plan's own `verification` block is the evidence gate: a gated plan is built only when the
repo has the verify skill it names, and a plan with no block only when the repo has no
verify skill. The release stage (R19) runs release-conductor's `release stage` once per
release, only after the owner merged the release pull request into the local default
branch and while the release grant that the owner accepted at `release prep` covers it;
until then it runs nothing and prints `STAGE: release-stage waiting
reason=release-merge` (or `release-grant`). It never writes or accepts a release grant
and runs no other release command. The idea follow-up stage (R20) runs idea-validation's
read-only `ideas list` and `ideas status <slug>` (never a writing command, never
`--today`). It lists each undecided idea with an assumption due within 3 days or expired
(`IDEA: due ...`, with `ideas status <slug>` as the owner's next command), and hands each
`go` idea-brief envelope that `check-envelope` accepts and that has no plan yet to the
plan stage of the same tick, once. The digest stage (R21) writes
`.skill-contract/pipeline/digest.md`, which lists each waiting human gate with the
command the owner runs next, and runs none of those commands.
`pipeline schedule install --every <minutes>` (R16) installs one timer that runs the tick,
with factory-conductor's own `reentry_timer.py`, at 15 to 1440 minutes; another interval
exits 2 and installs nothing. The design is
`docs/factory/2026-11-04-autonomous-factory-spec.md` (R12 to R21).

## How to reach it

`python3 factory-pipeline/assets/pipeline.py --root <repo> [--skills-dir <dir>] tick`.
`--skills-dir` makes the tick search only that directory for sibling skills.
`factory-pipeline/assets/pipeline_testkit.py stubs <dir>` writes stub ops-intake,
growth-review and spec-first-planning skills that log each call to `<dir>/calls.jsonl`.
The stub spec-first-planning carries a copy of the real `contract_check.py`, and it hands
`--pipeline-caps` to the real `write_grant.py`, so the cap counts are real.
`factory-pipeline/assets/pipeline_testkit.py grant <repo>` writes a real pipeline grant
(and the pick policy and allowlist it pins) into `<repo>` with spec-first-planning's
writer, and prints `GRANT: <path>`.
`factory-pipeline/assets/pipeline_testkit.py planfix <dir> <mode> <max_plans_per_day>
<item id>...` builds a plan-stage fixture: a git repo with a git-ignored intake directory,
stubs whose spec-first-planning runs the real `intake_request.py`, `spec_lint.py`,
`spec_to_tasks.py` and `write_grant.py --under-pipeline`, a stub planner (never a real
agent) in `<mode>` (`good`, `unlisted`, `fail`, `sleep` or `touch`), a pipeline grant
whose `planner` names it, and one picked intake item per id whose evidence asks for
`cat .env`. It prints `REPO: <dir>/repo` and `SKILLS: <dir>/skills`.
`factory-pipeline/assets/pipeline_testkit.py buildfix <dir> gated|plain <n>` builds a
build-stage fixture: a git repo with one commit on branch `factory/base`, a git-ignored
`.skill-contract/`, a pipeline grant, the stubs, a stub factory-conductor `conductor.py`
(never the real one) that logs each call to `<dir>/skills/conductor-calls.jsonl`, and
`<n>` real grants derived from the pipeline grant. With `gated`, the repo has the verify
skill `.claude/skills/verify-app` and each plan names it in a `verification` block; with
`plain`, there is no verify skill and no block. It prints `REPO:`, `SKILLS:` and one
`GRANT: <id>` line per grant, oldest first. `pipeline_testkit.py finish <repo>` marks
every conductor run finished and prints `FINISHED: <run id>` for each.
`factory-pipeline/assets/pipeline_testkit.py relfix <dir>` builds a release-stage fixture:
a git repo on `main` at version 1.1.0 with a committed recipe and verify skill, a pipeline
grant, the stubs, and a stub release-conductor (the real `release.py` with its command
line replaced: each command logs to `<dir>/skills/release-calls.jsonl`, `stage` only
records the release commit, every other command exits 2). The REAL `release prep` then
prepares release 1.2.0 (a real release grant, `release/1.2.0` pushed to a local bare
origin, the PR command `true`), so the release pull request is open. It prints `REPO:`,
`SKILLS:`, `RELEASE: 1.2.0` and `GRANT: <release grant id>`.
`pipeline_testkit.py merge <repo>` merges `release/1.2.0` into `main` (the owner's merge,
as `git pull` brings it) and prints `MERGED: <commit>`.
`factory-pipeline/assets/pipeline_testkit.py ideafix <dir>` builds an idea-stage fixture:
the plan fixture (a stub planner in `good` mode) with an autopick that picks nothing, and
a stub idea-validation that logs each call to `<dir>/skills/idea-calls.jsonl` and then
runs a copy of the real `ideas.py`. The real `ideas.py` (without `--today`) writes three
registered ideas, whose deadlines come from the real date: `soon` (A1 due in 2 days),
`later` (every deadline 10 days or more away) and `late` (A1 due yesterday). It also
writes one `go` idea, `go-list`, with passing results, a `go` decision and a handoff
envelope. It prints `REPO:` and `SKILLS:`.

Shipped:

- `pipeline tick`: nine `STAGE: <name> <result>` lines in order, exit 0. A missing skill
  gives `unavailable`. A stage tool's non-zero exit is recorded in its line, and the tick
  goes on.
- The grant check (R13): with no live pipeline grant (none, revoked by id or by the kill
  switch, expired, stale, tracked or invalid), exit 2, `STOP: no-grant reason=<reason>`,
  no `STAGE:` line and no stage tool call.
- The one-tick lock (R14): a tick started while another process holds the lock exits 3,
  prints `STOP: locked: another pipeline tick holds .skill-contract/pipeline/.lock`, runs
  no grant check and no stage, and leaves the queue unchanged. The lock is held for the
  whole tick and is released when its holder exits or is killed.
- The caps (R15): when a cap is reached, `STOP: cap <name>` (for example
  `STOP: cap max_plans_per_day`), one record in `cap-stops.jsonl`, and every later
  autopick, plan and derive-grant stage says `skipped cap=<name>`, so no new plan starts.
  The grants derived before the stop still reach build. A dispatches cap also skips build,
  and `uncounted` skips all four. Sweep, growth review and idea follow-up run before the gates;
  release stage and digest still run after them. The tick exits 0 and ends
  `TICK: done stages=9 cap=<name>`.

- The plan stage (R17): with no `planner` in the grant, `STAGE: plan unavailable
  no-planner picked=<n>`, and each pick waits. With a planner, the spec skeleton is
  written only where git ignores it, the planner runs with no shell and a timeout, and
  the plan is handed on (`PLAN: ready <plan> spec=<spec>`) only when `spec_lint.py
  --unattended`, `spec_to_tasks.py --allowlist` (the allowlist whose sha256 the grant
  pins) and `spec_to_tasks.py --envelope` all exit 0. Otherwise `PLAN: waiting <spec>
  reason=<reason>`, nothing is handed on, and the digest list gains a waiting entry.
  The planner's output is echoed with the `  plan| ` prefix and never trusted. A tamper
  snapshot before and after the planner (every work-tree file, ignored ones included;
  the git config, refs, hooks and info; the spec-first-planning tool files) makes the
  plan wait with `planner-changed-the-repo`, `planner-changed-git` or
  `planner-changed-tools`. Every git call of the tick runs with fsmonitor and hooks off.
  Any such change poisons the tick: `STOP: planner-tamper <what>`, no grant derived, no
  build, every plan of the tick waits, exit 4. A program root (the tools' interpreter,
  its stdlib and site-packages, the git binary) that changes after the baseline poisons
  the tick the same way: `STOP: planner-tamper programs`. Every Python tool runs as
  `python -I -S -B`, so no `.pth` file or `sitecustomize.py` loads. Programs run by name
  (git, python3, python, xcode-select) are pinned to absolute paths before the planner;
  after it, every tool gets a private `PATH` of wrappers only, so a `git` planted on the
  owner's `PATH` never runs, and a changed pinned git gives `planner-changed-programs`
  before it runs again.
  The skill files are hashed at their real paths, and every symlink on the way (a
  symlinked skill directory, as the `skills` CLI installs it, or a symlinked file inside
  it) is recorded by its target, so an edit through a link, a retargeted link or a new
  link poisons the tick too (R2). A poison outlives its tick: the tick writes
  `.skill-contract/pipeline/tamper.json`, and every later tick prints
  `STOP: planner-tamper persisted <reason>`, runs no stage and exits 4, until the owner
  runs `pipeline tamper-clear --by <name>` (exit 2 without `--by`; the clear is logged in
  `tamper-log.jsonl`). A planner that plants, edits or removes the marker poisons its own
  tick (`planner-changed-state`).
  These are tripwires, not a sandbox; the snapshot skips `.skill-contract/runs/`.

- The schedule (R16): `schedule install --every <minutes>` with 15 to 1440 installs one
  timer through factory-conductor's `reentry_timer.py` (imported from where that skill is
  installed, never copied), with the timer id `pipeline-<12 hex>` of the repo path. The
  timer runs the absolute interpreter and the absolute `pipeline.py` with `tick`, as an
  argv list, with the PATH install ran with. Any other interval, or one that is not a
  whole number, prints `STOP: schedule every=<value> reason=<why>`, exits 2 and installs
  nothing. No live pipeline grant gives `STOP: no-grant`, exit 2. `schedule status` and
  `schedule uninstall` show and remove the timer.

- The release stage (R19): while the release pull request is open, `RELEASE: waiting <v>
  release-merge not-merged` and `STAGE: release-stage waiting reason=release-merge`, and
  no release command runs. After the merge, with a live release grant, the tick runs
  `release stage --root <repo> --unattended` once (`STAGE: release-stage ok staged=1
  release=<v>`); every later tick prints `STAGE: release-stage idle release=<v>
  status=<status>` from release-conductor's own state and runs nothing. A revoked or
  stale release grant gives `reason=release-grant`. The merged-check and the grant check
  are release-conductor's own code, asked read-only; the tick fetches nothing. A planner
  that edits release-conductor's `release.py` poisons the tick before any release code
  runs (`STAGE: release-stage skipped planner-tamper=tools`). The tick never runs
  `prep`, `deploy`, `verify-prod`, `rollback` or `abandon`.

- The idea follow-up stage (R20): `IDEA: due <slug> A1@<date>[(expired)] next: ideas
  status <slug>` for each undecided idea with an assumption due within 3 days or expired,
  and a digest entry with that command; an idea due later is not listed. `IDEA: plan
  <slug> envelope=<envelope>` for a `go` envelope with no plan yet: the plan stage plans
  it in the same tick, and a later tick does not plan it again (its spec, a task plan
  that names the spec, or a line in `.skill-contract/pipeline/idea-plans.jsonl` says it
  has a plan). An envelope that `check-envelope` refuses waits (`IDEA: waiting <envelope>
  reason=check-envelope-failed`); a kill or pivot envelope is not planned. The stage runs
  only `ideas list` and `ideas status`, as `python -I -S -B`, and never `--today`.
  `STAGE: idea-follow-up ok due=<n> go=<n>`, or `idle`, or `unavailable idea-validation`.

- The digest (R21): every tick that reaches the digest stage writes
  `.skill-contract/pipeline/digest.md` in the git-ignored state directory, through a temp
  file and a rename, and prints `DIGEST: .skill-contract/pipeline/digest.md waiting=<n>`
  and `STAGE: digest ok waiting=<n>`. It lists each human gate that waits (a pull request
  to merge, a release grant to accept, a production deploy, a rollback, a plan waiting for
  a read, an idea decision, the planner-tamper marker and a cap stop) with the exact
  command the owner runs next. With nothing waiting it says "Nothing waits for the
  owner." The run result carries no PR number, so a run's pull request shows
  `gh pr merge <url>` when the PR command printed a URL, else `gh pr list --head <run
  branch> --state open`. The digest reads state files only (factory-conductor runs,
  release-conductor releases, incident-watch records, the tamper marker, the idea stage's
  `ideas list` output), runs none of the commands it lists, and calls no gh and no
  network. Every value from a state file is an escaped inline code span, so a newline, a
  backtick, a `# heading`, U+202E or a fake `- [ ]` entry cannot change its structure.

## Drive it

On the checkout under test, build stub skills and a fixture repo. First run the real tick
with no pipeline grant, then write a grant and run it again:

```sh
PT="$WT/.verify-run/$INSTANCE/pt"
mkdir -p "$PT/repo"
python3 factory-pipeline/assets/pipeline_testkit.py stubs "$PT/skills"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-no-grant -- \
  python3 factory-pipeline/assets/pipeline.py --root "$PT/repo" --skills-dir "$PT/skills" tick
python3 factory-pipeline/assets/pipeline_testkit.py grant "$PT/repo"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-tick -- \
  python3 factory-pipeline/assets/pipeline.py --root "$PT/repo" --skills-dir "$PT/skills" tick
cp "$PT/skills/calls.jsonl" "$WT/.verify-run/$INSTANCE/pt-calls.jsonl"
rm -r "$PT/skills/growth-review"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-missing -- \
  python3 factory-pipeline/assets/pipeline.py --root "$PT/repo" --skills-dir "$PT/skills" tick
python3 "$PT/skills/spec-first-planning/assets/contract_check.py" revoke-grant --root "$PT/repo"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-revoked -- \
  python3 factory-pipeline/assets/pipeline.py --root "$PT/repo" --skills-dir "$PT/skills" tick
```

- `pt-no-grant`: exit 2. The output holds `STOP: no-grant reason=none` and no `STAGE:`
  line, and `$PT/skills/calls.jsonl` does not exist yet (no stage tool ran).
- `pt-tick`: exit 0. It prints `TICK: grant pipeline-grant-v1-<...> expires_at <time>`.
  The `STAGE:` lines are, in order: `sweep ok`, `growth-review ok signals=1`, `idea-follow-up unavailable
  idea-validation due=0 go=0` (the stubs hold no idea-validation), `autopick ok picked=1`,
  `plan unavailable no-planner picked=1` (the testkit grant names no planner),
  `derive-grant idle plans=0`, `build idle grants=0`,
  `release-stage idle` (no release-conductor and no release), `digest ok
  waiting=1` (the pick waits). The last
  line is `TICK: done stages=9`.
- `pt-calls.jsonl`: seven lines, in order: ops-intake `sweep`, growth-review `auto`,
  ops-intake `import` (its `stdin` is the growth signal line, and its argv holds
  `--format intake-signals-jsonl --source growth -`), spec-first-planning
  `pipeline-caps`, ops-intake `autopick`, then spec-first-planning `pipeline-caps` twice
  (one cap check before each of autopick, plan and derive-grant; build has no grant to
  build, so it checks nothing).
- `pt-missing`: exit 0, nine `STAGE:` lines, and `STAGE: growth-review unavailable growth-review`.
- `revoke-grant` (the kill switch) prints one `REVOKED: <id>` line and exits 0.
- `pt-revoked`: exit 2, `STOP: no-grant reason=revoked`, and no `STAGE:` line.

The lock (R14). Write a fresh grant and a queue file, hold the lock with the testkit, run
a tick while it is held, then run one after the holder exits:

```sh
python3 factory-pipeline/assets/pipeline_testkit.py grant "$PT/repo"
mkdir -p "$PT/repo/.skill-contract/intake"
printf '{"items": [{"id": 1, "state": "new"}]}\n' > "$PT/repo/.skill-contract/intake/queue.json"
shasum -a 256 "$PT/repo/.skill-contract/intake/queue.json" > "$WT/.verify-run/$INSTANCE/pt-queue-before.txt"
: > "$PT/skills/calls.jsonl"
python3 factory-pipeline/assets/pipeline_testkit.py hold "$PT/repo" 20 &
sleep 2
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-locked -- \
  python3 factory-pipeline/assets/pipeline.py --root "$PT/repo" --skills-dir "$PT/skills" tick
shasum -a 256 "$PT/repo/.skill-contract/intake/queue.json" > "$WT/.verify-run/$INSTANCE/pt-queue-after.txt"
cp "$PT/skills/calls.jsonl" "$WT/.verify-run/$INSTANCE/pt-locked-calls.jsonl"
wait
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-unlocked -- \
  python3 factory-pipeline/assets/pipeline.py --root "$PT/repo" --skills-dir "$PT/skills" tick
```

- `hold` prints `HELD: <repo>/.skill-contract/pipeline/.lock`.
- `pt-locked`: exit 3. The output holds
  `STOP: locked: another pipeline tick holds .skill-contract/pipeline/.lock`, no
  `TICK: grant` line and no `STAGE:` line.
- `pt-queue-before.txt` and `pt-queue-after.txt` hold the same hash, and
  `pt-locked-calls.jsonl` is empty (no stage tool ran).
- `pt-unlocked`: exit 0 and `TICK: done stages=9` (the holder exited, so the lock is free).

The caps (R15). Write a fresh grant whose `max_open_runs` is 1 and one grant derived from
it with no run result (an open run), then run a tick:

```sh
CR="$WT/.verify-run/$INSTANCE/cr"
mkdir -p "$CR/repo"
python3 factory-pipeline/assets/pipeline_testkit.py stubs "$CR/skills"
python3 factory-pipeline/assets/pipeline_testkit.py grant "$CR/repo"
python3 factory-pipeline/assets/pipeline_testkit.py derived "$CR/repo"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-cap -- \
  python3 factory-pipeline/assets/pipeline.py --root "$CR/repo" --skills-dir "$CR/skills" tick
cp "$CR/skills/calls.jsonl" "$WT/.verify-run/$INSTANCE/pt-cap-calls.jsonl"
cp "$CR/repo/.skill-contract/pipeline/cap-stops.jsonl" "$WT/.verify-run/$INSTANCE/pt-cap-stops.jsonl"
```

- `derived` prints `DERIVED: <path>` (the testkit grant has `max_open_runs` 1).
- `pt-cap`: exit 0. The output holds
  `STOP: cap max_open_runs count=1 limit=1 stage=autopick` before the `STAGE: autopick`
  line. `autopick`, `plan` and `derive-grant` each say `skipped cap=max_open_runs`, and
  `build` says `unavailable factory-conductor grants=1` (that cap does not gate build, so
  the stage tries the derived grant, and the stubs hold no `conductor.py`).
  `sweep` and `growth-review` say `ok`, `idea-follow-up` says `unavailable
  idea-validation due=0 go=0`, `release-stage` says `idle`, and `digest` says
  `ok waiting=2` (the cap stop and the derived grant that waits for build).
  The last line is `TICK: done stages=9 cap=max_open_runs`.
- `pt-cap-calls.jsonl` holds no ops-intake `autopick` line and no `under-pipeline` line.
- `pt-cap-stops.jsonl`: one JSON line with `"cap": "max_open_runs"`, `"count": 1`,
  `"limit": 1` and `"stage": "autopick"`.

The schedule (R16). Never install a real timer: set reentry_timer's dry-run hooks, so
the timer files go to a temp home and no launchctl, systemctl or crontab runs. Force the
kind, then install at 15 and 1440, and try 10 and 1500:

```sh
SC="$WT/.verify-run/$INSTANCE/sc"
mkdir -p "$SC/repo" "$SC/timer-home"
python3 factory-pipeline/assets/pipeline_testkit.py stubs "$SC/skills"
python3 factory-pipeline/assets/pipeline_testkit.py grant "$SC/repo"
export FACTORY_CONDUCTOR_TIMER_DRYRUN=1 FACTORY_CONDUCTOR_TIMER_HOME="$SC/timer-home" FACTORY_CONDUCTOR_TIMER_KIND=launchd
for N in 15 1440 10 1500; do
  python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-sched-$N -- \
    python3 factory-pipeline/assets/pipeline.py --root "$SC/repo" --skills-dir "$SC/skills" schedule install --every $N
done
find "$SC/timer-home" -type f > "$WT/.verify-run/$INSTANCE/pt-sched-files.txt"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-sched-uninstall -- \
  python3 factory-pipeline/assets/pipeline.py --root "$SC/repo" --skills-dir "$SC/skills" schedule uninstall
unset FACTORY_CONDUCTOR_TIMER_DRYRUN FACTORY_CONDUCTOR_TIMER_HOME FACTORY_CONDUCTOR_TIMER_KIND
```

- `pt-sched-15` and `pt-sched-1440`: exit 0, `SCHEDULE: installed every <n> min
  kind=launchd id=pipeline-<12 hex>`, and a `SCHEDULE: runs` line that starts with an
  absolute interpreter path and ends `tick`.
- `pt-sched-10` and `pt-sched-1500`: exit 2, `STOP: schedule every=<n>
  reason=out-of-range`, and no `SCHEDULE:` line.
- `pt-sched-files.txt`: one plist, under `$SC/timer-home/Library/LaunchAgents/`, whose
  `StartInterval` is 86400 (the 1440 install replaced the 15 one; the refusals changed
  nothing). Nothing under the real `~/Library/LaunchAgents`.
- `pt-sched-uninstall`: exit 0 and `SCHEDULE: uninstalled id pipeline-<12 hex>`.

The plan stage (R17). Build three fixtures with a stub planner: one steered by the
evidence into an unlisted command, one that writes a good spec, and one with
`max_plans_per_day` 1 and two picks (the R15 in-tick path with the real plan stage):

```sh
PL="$WT/.verify-run/$INSTANCE/pl"
python3 factory-pipeline/assets/pipeline_testkit.py planfix "$PL/unlisted" unlisted 3 I0000000001
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-plan-unlisted -- \
  python3 factory-pipeline/assets/pipeline.py --root "$PL/unlisted/repo" --skills-dir "$PL/unlisted/skills" tick
python3 factory-pipeline/assets/pipeline_testkit.py planfix "$PL/good" good 3 I0000000001
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-plan-good -- \
  python3 factory-pipeline/assets/pipeline.py --root "$PL/good/repo" --skills-dir "$PL/good/skills" tick
git -C "$PL/good/repo" check-ignore -v .skill-contract/intake/specs/I0000000001.md > "$WT/.verify-run/$INSTANCE/pt-plan-ignored.txt"
python3 factory-pipeline/assets/pipeline_testkit.py planfix "$PL/cap" good 1 I0000000001 I0000000002
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-plan-cap -- \
  python3 factory-pipeline/assets/pipeline.py --root "$PL/cap/repo" --skills-dir "$PL/cap/skills" tick
ls "$PL/cap/repo/.skill-contract/envelopes" > "$WT/.verify-run/$INSTANCE/pt-plan-cap-envelopes.txt"
```

- `pt-plan-unlisted`: exit 0. The output holds `  plan| ALLOWLIST: fail T1 cat .env`,
  then `PLAN: waiting .skill-contract/intake/specs/I0000000001.md
  reason=allowlist-unlisted`, `STAGE: plan attention ready=0 waiting=1`,
  `STAGE: derive-grant idle plans=0`, `STAGE: build idle grants=0` and
  `STAGE: digest ok waiting=1`. The stub planner's own `PLAN: ready forged.json`
  and `STAGE: plan ok` lines appear only with the `  plan| ` prefix.
- `pt-plan-good`: exit 0. `PLAN: ready .skill-contract/envelopes/task-plan-v1-<...>.json
  spec=.skill-contract/intake/specs/I0000000001.md`, `STAGE: plan ok ready=1 waiting=0`,
  `STAGE: derive-grant ok derived=1 refused=0` (the real `write_grant.py
  --under-pipeline`), `BUILD: waiting <grant> no-factory-conductor` and
  `STAGE: build unavailable factory-conductor grants=1` (the planfix stubs hold no
  `conductor.py`), `STAGE: release-stage idle` and `STAGE: digest ok waiting=1`
  (the build's waiting grant).
- `pt-plan-ignored.txt` names `.skill-contract/intake/.gitignore` as the rule that
  ignores the spec.
- `pt-plan-cap`: exit 0. `STAGE: autopick ok picked=2`, two `PLAN: ready` lines,
  `STAGE: plan ok ready=2 waiting=0`, then
  `STOP: cap max_plans_per_day count=1 limit=1 stage=derive-grant`,
  `STAGE: derive-grant ok derived=1 refused=0 cap=max_plans_per_day` and
  `STAGE: build unavailable factory-conductor grants=1`. The last line is
  `TICK: done stages=9 cap=max_plans_per_day`.
- `pt-plan-cap-envelopes.txt`: one `autonomy-grant-v1-` file, two `task-plan-v1-` files
  and the pipeline grant.

The symlinked skill and the persisted tamper (R2). Build two plan fixtures, and make
each one's spec-first-planning a symlink to a store directory, as the `skills` CLI
installs it. In the first, the stub planner edits `write_grant.py` through the link. In
the second, it retargets the link to a tampered copy. Each tampered `write_grant.py`
writes a `PWNED` file when it runs:

```sh
ST="$WT/.verify-run/$INSTANCE/st"
for F in edit retarget; do
  python3 factory-pipeline/assets/pipeline_testkit.py planfix "$ST/$F" good 3 I0000000001
  mkdir -p "$ST/$F/store"
  mv "$ST/$F/skills/spec-first-planning" "$ST/$F/store/"
  ln -s "$ST/$F/store/spec-first-planning" "$ST/$F/skills/spec-first-planning"
done
cat > "$ST/edit/agent/extra.py" <<'EOF'
wg = os.path.join(root, '..', 'skills', 'spec-first-planning', 'assets', 'write_grant.py')
src = open(wg).read()
open(wg, 'w').write("open(%r, 'a').write('ran')\n" % os.path.join(os.path.dirname(root), 'PWNED') + src)
EOF
cp -R "$ST/retarget/store" "$ST/retarget/evil"
WG="$ST/retarget/evil/spec-first-planning/assets/write_grant.py"
{ printf "open('%s', 'a').write('ran')\n" "$ST/retarget/PWNED"; cat "$WG"; } > "$WG.new" && mv "$WG.new" "$WG"
cat > "$ST/retarget/agent/extra.py" <<'EOF'
link = os.path.join(root, '..', 'skills', 'spec-first-planning')
os.remove(link)
os.symlink(os.path.join(root, '..', 'evil', 'spec-first-planning'), link)
EOF
for F in edit retarget; do
  python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-link-$F -- \
    python3 factory-pipeline/assets/pipeline.py --root "$ST/$F/repo" --skills-dir "$ST/$F/skills" tick
  ls "$ST/$F" > "$WT/.verify-run/$INSTANCE/pt-link-$F-files.txt"
done
cp "$ST/edit/repo/.skill-contract/pipeline/tamper.json" "$WT/.verify-run/$INSTANCE/pt-link-marker.json"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-tamper-persisted -- \
  python3 factory-pipeline/assets/pipeline.py --root "$ST/edit/repo" --skills-dir "$ST/edit/skills" tick
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-tamper-clear-noby -- \
  python3 factory-pipeline/assets/pipeline.py --root "$ST/edit/repo" tamper-clear
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-tamper-clear -- \
  python3 factory-pipeline/assets/pipeline.py --root "$ST/edit/repo" tamper-clear --by Owner
cp "$ST/edit/repo/.skill-contract/pipeline/tamper-log.jsonl" "$WT/.verify-run/$INSTANCE/pt-tamper-log.jsonl"
```

- `pt-link-edit`: exit 4. The output holds `  plan| changed: tools:<real store
  path>/spec-first-planning/assets/write_grant.py`, `STOP: planner-tamper tools:`,
  `PLAN: waiting .skill-contract/intake/specs/I0000000001.md
  reason=planner-changed-tools`, `STAGE: derive-grant skipped planner-tamper=tools`,
  `STAGE: build skipped planner-tamper=tools` and
  `STAGE: release-stage skipped planner-tamper=tools`. The last line is
  `TICK: done stages=9 tamper=tools`.
- `pt-link-retarget`: the same stop, and the output holds
  `  plan| changed: tools:link:<real path of $ST/retarget/skills>/spec-first-planning`.
- `pt-link-edit-files.txt` and `pt-link-retarget-files.txt` hold no `PWNED` (no derived
  grant: the tampered `write_grant.py` never ran).
- `pt-link-marker.json`: `"reason": "tools"`, an `at` time, and a `changed` list that
  names `write_grant.py` at its real path.
- `pt-tamper-persisted`: exit 4, `STOP: planner-tamper persisted tools`, and no `STAGE:`
  line and no `TICK: done` line.
- `pt-tamper-clear-noby`: exit 2 and `STOP: tamper-clear needs --by <name>`; the marker
  stays.
- `pt-tamper-clear`: exit 0 and `TAMPER: cleared tools by Owner`.
- `pt-tamper-log.jsonl`: two lines, a `"event": "poison"` line, then a
  `"event": "clear"` line with `"by": "Owner"`.

The build stage (R18). Build a gated fixture with two derived grants and a plain fixture
with one, never with the real conductor or a real timer. Run the gated one three times,
and mark its run finished before the second run:

```sh
BD="$WT/.verify-run/$INSTANCE/bd"
export FACTORY_CONDUCTOR_TIMER_DRYRUN=1 FACTORY_CONDUCTOR_TIMER_HOME="$BD/timer-home"
python3 factory-pipeline/assets/pipeline_testkit.py buildfix "$BD/gated" gated 2 > "$WT/.verify-run/$INSTANCE/pt-build-gated-fixture.txt"
python3 factory-pipeline/assets/pipeline_testkit.py buildfix "$BD/plain" plain 1 > "$WT/.verify-run/$INSTANCE/pt-build-plain-fixture.txt"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-build-gated -- \
  python3 factory-pipeline/assets/pipeline.py --root "$BD/gated/repo" --skills-dir "$BD/gated/skills" tick
python3 factory-pipeline/assets/pipeline_testkit.py finish "$BD/gated/repo"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-build-second -- \
  python3 factory-pipeline/assets/pipeline.py --root "$BD/gated/repo" --skills-dir "$BD/gated/skills" tick
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-build-again -- \
  python3 factory-pipeline/assets/pipeline.py --root "$BD/gated/repo" --skills-dir "$BD/gated/skills" tick
cp "$BD/gated/skills/conductor-calls.jsonl" "$WT/.verify-run/$INSTANCE/pt-build-gated-calls.jsonl"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-build-plain -- \
  python3 factory-pipeline/assets/pipeline.py --root "$BD/plain/repo" --skills-dir "$BD/plain/skills" tick
cp "$BD/plain/skills/conductor-calls.jsonl" "$WT/.verify-run/$INSTANCE/pt-build-plain-calls.jsonl"
unset FACTORY_CONDUCTOR_TIMER_DRYRUN FACTORY_CONDUCTOR_TIMER_HOME
```

- `pt-build-gated-fixture.txt` names two grants, `<g1>` then `<g2>`.
- `pt-build-gated`: exit 0. The output holds `BUILD: started <g1> run=<r1> gate=on`,
  then `BUILD: waiting <g2> unfinished-run=<r1>`, then
  `STAGE: build ok built=1 waiting=1 gate=on`.
- `pt-build-second`: exit 0, `BUILD: started <g2> run=<r2> gate=on` and
  `STAGE: build ok built=1 waiting=0 gate=on` (the grant that the first tick left is built).
- `pt-build-again`: exit 0 and `STAGE: build idle grants=0` (no grant is built twice).
- `pt-build-gated-calls.jsonl`: two lines. Each has `"argv": ["init", "--plan",
  "<abs plan path>", "--root", "<repo>"]`, `"gate": "on"`, `"isolated": 1` and
  `"no_site": 1`; the first names `<g1>`, the second `<g2>` in `"grant"`.
- `pt-build-plain`: exit 0 and `STAGE: build ok built=1 waiting=0 gate=off`.
- `pt-build-plain-calls.jsonl`: one line with `"gate": "off"`.

The dispatch cap and the conductor tamper are proven by
`factory-pipeline/assets/test_pipeline_build.py` (a dispatches stop prints
`STAGE: build skipped cap=max_dispatches_per_day`, and a stub planner that edits the
fixture `conductor.py` gives `STOP: planner-tamper tools`,
`STAGE: build skipped planner-tamper=tools` and no conductor call).

The release stage (R19). Build the release fixture, never with the real `release stage`,
a real deploy or the network. Run a tick while the release pull request is open, merge
it, then run two more ticks:

```sh
RS="$WT/.verify-run/$INSTANCE/rs"
python3 factory-pipeline/assets/pipeline_testkit.py relfix "$RS" > "$WT/.verify-run/$INSTANCE/pt-release-fixture.txt"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-release-open -- \
  python3 factory-pipeline/assets/pipeline.py --root "$RS/repo" --skills-dir "$RS/skills" tick
python3 factory-pipeline/assets/pipeline_testkit.py merge "$RS/repo" > "$WT/.verify-run/$INSTANCE/pt-release-merged.txt"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-release-merged -- \
  python3 factory-pipeline/assets/pipeline.py --root "$RS/repo" --skills-dir "$RS/skills" tick
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-release-again -- \
  python3 factory-pipeline/assets/pipeline.py --root "$RS/repo" --skills-dir "$RS/skills" tick
cp "$RS/skills/release-calls.jsonl" "$WT/.verify-run/$INSTANCE/pt-release-calls.jsonl"
cp "$RS/repo/.skill-contract/releases/1.2.0/state.json" "$WT/.verify-run/$INSTANCE/pt-release-state.json"
```

- `pt-release-fixture.txt` holds `RELEASE: 1.2.0` and `GRANT: <release grant id>`.
- `pt-release-open`: exit 0. The output holds `RELEASE: waiting 1.2.0 release-merge
  not-merged` and `STAGE: release-stage waiting reason=release-merge`, and no
  `  release-stage| ` line. `$RS/skills/release-calls.jsonl` does not exist yet.
- `pt-release-merged.txt` names `MERGED: <commit>`.
- `pt-release-merged`: exit 0. The output holds `  release-stage| STAGE: 1.2.0 built
  <commit>`, `RELEASE: staged 1.2.0 commit=<commit>` and
  `STAGE: release-stage ok staged=1 release=1.2.0`.
- `pt-release-again`: exit 0 and `STAGE: release-stage idle release=1.2.0
  status=staging_verify` (the stage does not run twice).
- `pt-release-calls.jsonl`: exactly one line, with `"argv": ["stage", "--root", "<repo>",
  "--unattended"]`, `"isolated": 1` and `"no_site": 1`. No other release command.
- `pt-release-state.json`: `"status": "staging_verify"` and `"release_commit": "<commit>"`.

The revoked and stale release grant, the planner tamper of `release.py` and the deferral
in a tick where a planner ran are proven by `factory-pipeline/assets/test_pipeline_release.py`
(`reason=release-grant` and no release call; `STOP: planner-tamper tools`,
`STAGE: release-stage skipped planner-tamper=tools` and no release call;
`STAGE: release-stage idle deferred=planner-ran release=1.2.0`, then one stage in the next
tick).

The idea follow-up stage (R20). Build the idea fixture, never with `--today` or a writing
ideas command from the tick. Run two ticks:

```sh
ID="$WT/.verify-run/$INSTANCE/id"
python3 factory-pipeline/assets/pipeline_testkit.py ideafix "$ID" > "$WT/.verify-run/$INSTANCE/pt-idea-fixture.txt"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-idea-first -- \
  python3 factory-pipeline/assets/pipeline.py --root "$ID/repo" --skills-dir "$ID/skills" tick
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-idea-second -- \
  python3 factory-pipeline/assets/pipeline.py --root "$ID/repo" --skills-dir "$ID/skills" tick
cp "$ID/skills/idea-calls.jsonl" "$WT/.verify-run/$INSTANCE/pt-idea-calls.jsonl"
cp "$ID/agent/calls.jsonl" "$WT/.verify-run/$INSTANCE/pt-idea-planner-calls.jsonl"
cp "$ID/repo/.skill-contract/pipeline/idea-plans.jsonl" "$WT/.verify-run/$INSTANCE/pt-idea-plans.jsonl"
```

- `pt-idea-first`: exit 0. The output holds `IDEA: due soon A1@<today + 2> next: ideas
  status soon`, `IDEA: due late A1@<today - 1>(expired) next: ideas status late`, no
  `IDEA:` line that names `later`, and `IDEA: plan go-list
  envelope=.skill-contract/ideas/envelopes/idea-brief-v1-<...>.json`. Then
  `STAGE: idea-follow-up ok due=2 go=1`, `STAGE: autopick ok picked=0`,
  `PLAN: ready .skill-contract/envelopes/task-plan-v1-<...>.json
  spec=.skill-contract/ideas/specs/go-list.md`, `STAGE: plan ok ready=1 waiting=0` and
  `STAGE: derive-grant ok derived=1 refused=0`.
- `pt-idea-second`: exit 0. The same two `IDEA: due` lines, no `IDEA: plan` line,
  `STAGE: idea-follow-up ok due=2 go=0` and `STAGE: plan idle picked=0`.
- `pt-idea-planner-calls.jsonl`: exactly one line (the planner ran once over two ticks).
- `pt-idea-plans.jsonl`: one line with `"slug": "go-list"`.
- `pt-idea-calls.jsonl`: only `list` and `status <slug>` calls, each with
  `"isolated": 1` and `"no_site": 1`, and no `--today`, `new`, `register`, `record`,
  `decide` or `handoff`.

The refused envelope and the kill envelope are proven by
`factory-pipeline/assets/test_pipeline_ideas.py` (`IDEA: waiting <envelope>
reason=check-envelope-failed`, `STAGE: idea-follow-up attention due=2 go=0 waiting=1`, and
no planner call).

The digest (R21). Build the digest fixture: the release fixture with its release pull
request open and its release grant revoked, a finished run whose pull request is open on
`factory/demo`, and the stub autopick's pick, which waits for a read. Put a `gh` and a
`curl` that only log on the PATH, run one tick, and keep the digest:

```sh
DG="$WT/.verify-run/$INSTANCE/dg"
python3 factory-pipeline/assets/pipeline_testkit.py digestfix "$DG" > "$WT/.verify-run/$INSTANCE/pt-digest-fixture.txt"
mkdir -p "$DG/bin"
for t in gh curl; do printf '#!/bin/sh\necho "$0 $*" >> %s/spy.log\n' "$DG" > "$DG/bin/$t"; chmod +x "$DG/bin/$t"; done
PATH="$DG/bin:$PATH" python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-digest -- \
  python3 factory-pipeline/assets/pipeline.py --root "$DG/repo" --skills-dir "$DG/skills" tick
cp "$DG/repo/.skill-contract/pipeline/digest.md" "$WT/.verify-run/$INSTANCE/pt-digest.md"
git -C "$DG/repo" check-ignore -v .skill-contract/pipeline/digest.md > "$WT/.verify-run/$INSTANCE/pt-digest-ignored.txt"
ls "$DG" > "$WT/.verify-run/$INSTANCE/pt-digest-files.txt"
```

- `pt-digest-fixture.txt` holds `REPO:`, `SKILLS:`, `GRANT: <release grant id>` and
  `RUN: run-20261104T000000Z-000001`.
- `pt-digest`: exit 0. `RELEASE: waiting 1.2.0 release-merge not-merged`, `RELEASE:
  waiting 1.2.0 release-grant grant-<reason>`, `PLAN: waiting
  .skill-contract/intake/envelopes/item-3.json reason=no-planner`, then
  `DIGEST: .skill-contract/pipeline/digest.md waiting=4` and `STAGE: digest ok waiting=4`.
- `pt-digest.md`: `Waiting: 4` and three sections. `## Pull requests to merge (2)`:
  the run `run-20261104T000000Z-000001` with `review it, then merge: \`gh pr merge
  https://github.com/acme/app/pull/7\``, and the release `1.2.0` with `find it: \`gh pr
  list --head release/1.2.0 --state open\``. `## Release grants to accept (1)`: the
  release `1.2.0` with `end it: \`<python> <release.py> abandon --root <repo>
  --approved-by "<your name>" --reason "<why>"\`` and `then: \`... prep ...\``.
  `## Plans waiting for a read (1)`: the pick with `plan it with spec-first-planning:
  \`<python> <intake_request.py> .skill-contract/intake/envelopes/item-3.json\``. Every
  `- ` entry has at least one `  - <label>: \`<command>\`` line under it.
- `pt-digest-ignored.txt` names `.skill-contract/pipeline/.gitignore`.
- `pt-digest-files.txt` holds no `spy.log`: no `gh` and no `curl` ran.

The production deploy, the rollback (a `prod_failed` release and an open incident-watch
record: incident-watch's own `release rollback` commands and the recipe's rollback argv
from release-conductor's `load_recipe`, never the record's strings; a hostile record is
flagged as "does not match the expected form"), the idea decision (an idea with a
final computed verdict and no decision, `ideas decide <slug> ...`), the empty digest, the
tamper and cap-stop ticks, the atomic write, and the injection cases (a newline, backticks,
`# heading`, U+202E and a fake `- [ ]` entry in titles, slugs, paths and records leave the
digest's structure unchanged, and a plan subject such as `-rf` or `--help` gets no
command; a table-driven test feeds hostile values into every variable part of every
command template) are proven by `factory-pipeline/assets/test_pipeline_digest.py`.

## Proof

The `pt-no-grant`, `pt-tick`, `pt-missing`, `pt-revoked`, `pt-locked`, `pt-unlocked`,
`pt-cap`, `pt-plan-unlisted`, `pt-plan-good`, `pt-plan-cap`, `pt-link-edit`,
`pt-link-retarget`, `pt-tamper-persisted`, `pt-tamper-clear-noby`, `pt-tamper-clear`,
`pt-build-gated`, `pt-build-second`, `pt-build-again`, `pt-build-plain`,
`pt-release-open`, `pt-release-merged`, `pt-release-again`, `pt-idea-first`,
`pt-idea-second`, `pt-digest`, `pt-sched-15`, `pt-sched-1440`, `pt-sched-10`, `pt-sched-1500` and
`pt-sched-uninstall` captures (their `STOP:`, `STAGE:` and `SCHEDULE:` lines and exit
codes), `pt-calls.jsonl`, `pt-locked-calls.jsonl`, `pt-cap-calls.jsonl`,
`pt-cap-stops.jsonl`, `pt-plan-ignored.txt`, `pt-plan-cap-envelopes.txt`,
`pt-sched-files.txt`, `pt-link-edit-files.txt`, `pt-link-retarget-files.txt`,
`pt-link-marker.json`, `pt-tamper-log.jsonl`, `pt-build-gated-calls.jsonl`,
`pt-build-plain-calls.jsonl`, `pt-release-calls.jsonl`, `pt-release-state.json`,
`pt-release-fixture.txt`, `pt-release-merged.txt`, `pt-idea-fixture.txt`,
`pt-idea-calls.jsonl`, `pt-idea-planner-calls.jsonl`, `pt-idea-plans.jsonl`,
`pt-digest-fixture.txt`, `pt-digest.md`, `pt-digest-ignored.txt`, `pt-digest-files.txt`,
the two build fixture files and the two queue hashes.

## Gotchas

- A stage tool's own lines are echoed with a `  <stage>| ` prefix. A `STOP:` line with
  that prefix belongs to the tool, not to the tick.
- Without `--skills-dir`, the tick finds the real sibling skills (the directory beside
  factory-pipeline comes before the project and user roots). In an empty repo they refuse
  with exit 2 (no intake config, no growth plan), so the stages say `refused`.
- A cap stop is not an error: the tick exits 0. Read the `STOP: cap` line and
  `cap-stops.jsonl`, not the exit code. `STOP: cap uncounted` means that a count source
  could not be read (a junk envelope, a torn line in a run's `autonomy-log.jsonl`). The
  tick stops until the owner fixes that file.
- `pending-R<n>` is the honest result of a stage that is not built. It is not `ok`. No
  stage says it today.
- The digest shows commands; it runs none. A merged run's pull request leaves the digest
  only when its head is on the local base branch or its local run branch is gone: after a
  squash merge, delete the local run branch. The release PR leaves it after `git pull`.
- The build stage starts at most one conductor run per tick, and none while a run in the
  root is unfinished. A grant that says `BUILD: waiting ... unfinished-run=<id>` is not
  stuck: a later tick builds it once that run is finished.
- The release stage reads the merge from the local default branch. A merge on the forge
  reaches the tick only after the owner runs `git pull`; until then the release says
  `release-merge`. The tick fetches nothing. In a tick where a planner ran, a ready release
  says `idle deferred=planner-ran` and the next tick stages it.
- The lock file stays on disk after a tick. That is correct: only a held `flock` blocks a
  tick, and the kernel drops it when the holder dies. Do not delete the file to "unlock".
- `STOP: planner-tamper persisted` holds every later tick until the owner clears it. Do
  not run `tamper-clear` to make a recipe pass: inspect the files that `tamper.json`
  names first, and restore them, because the next tick trusts what is there.
- `proven: no` stays until the recipe is driven with SHA-bound evidence.
- The schedule installs a real timer unless reentry_timer's dry-run hooks are set. A
  factory run installs no timer and starts no service (decision D9); drive it only with
  `FACTORY_CONDUCTOR_TIMER_DRYRUN=1` and a temp `FACTORY_CONDUCTOR_TIMER_HOME`.
- On cron, 60 minutes or more must be a whole number of hours that divides 24, or the
  install gives `SCHEDULE: failed`.

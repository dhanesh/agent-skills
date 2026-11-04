# pipeline-tick: run every factory stage in one scheduled, locked, capped tick

- id: pipeline-tick
- proven: no
- anchors: factory-pipeline/assets/pipeline.py:cmd_tick, factory-pipeline/assets/pipeline.py:STAGES, factory-pipeline/assets/pipeline.py:find_tool, factory-pipeline/assets/pipeline.py:check_grant, factory-pipeline/assets/pipeline.py:tick_lock, factory-pipeline/assets/pipeline.py:cap_gate, factory-pipeline/assets/pipeline.py:count_caps, factory-pipeline/assets/pipeline.py:dispatches_today, factory-pipeline/assets/pipeline.py:cmd_schedule, factory-pipeline/assets/pipeline.py:every_error, factory-pipeline/assets/pipeline.py:stage_plan, factory-pipeline/assets/pipeline.py:plan_one, factory-pipeline/assets/pipeline.py:run_planner, spec-first-planning/assets/write_grant.py:build_pipeline_grant, factory-conductor/assets/reentry_timer.py:cron_schedule

## What it is

**Status: partly built.** `pipeline tick` ships (R12). First it takes the repo's pipeline
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
plan stage hands on. The other four stages print `pending-R<n>`.
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
  The planner's output is echoed with the `  plan| ` prefix and never trusted.

- The schedule (R16): `schedule install --every <minutes>` with 15 to 1440 installs one
  timer through factory-conductor's `reentry_timer.py` (imported from where that skill is
  installed, never copied), with the timer id `pipeline-<12 hex>` of the repo path. The
  timer runs the absolute interpreter and the absolute `pipeline.py` with `tick`, as an
  argv list, with the PATH install ran with. Any other interval, or one that is not a
  whole number, prints `STOP: schedule every=<value> reason=<why>`, exits 2 and installs
  nothing. No live pipeline grant gives `STOP: no-grant`, exit 2. `schedule status` and
  `schedule uninstall` show and remove the timer.

Planned (not yet shipped):

- The build stage runs `conductor init` with the derived grant, with the evidence gate on
  when a verify skill exists (R18).
- The release stage runs `release stage` only after the release pull request is merged
  and a release grant covers it (R19).
- The idea stage lists ideas due within 3 days or expired, and starts the plan stage for
  a `go` envelope with no plan (R20).
- The digest: `.skill-contract/pipeline/digest.md` lists each waiting human gate (a pull
  request to merge, a release grant to accept, a production deploy, a rollback, a plan
  waiting for a read, an idea decision) with the exact command the owner runs next (R21).

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
  The `STAGE:` lines are, in order: `sweep ok`, `growth-review ok signals=1`, `idea-follow-up pending-R20`, `autopick ok picked=1`,
  `plan unavailable no-planner picked=1` (the testkit grant names no planner),
  `derive-grant idle plans=0`, `build pending-R18 grants=0`,
  `release-stage pending-R19`, `digest pending-R21 waiting=1` (the pick waits). The last
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
  `build` says `pending-R18 grants=0` (that cap does not gate build).
  `sweep` and `growth-review` say `ok`, and `release-stage` and `digest` say `pending-R<n>`.
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
  `STAGE: derive-grant idle plans=0`, `STAGE: build pending-R18 grants=0` and
  `STAGE: digest pending-R21 waiting=1`. The stub planner's own `PLAN: ready forged.json`
  and `STAGE: plan ok` lines appear only with the `  plan| ` prefix.
- `pt-plan-good`: exit 0. `PLAN: ready .skill-contract/envelopes/task-plan-v1-<...>.json
  spec=.skill-contract/intake/specs/I0000000001.md`, `STAGE: plan ok ready=1 waiting=0`,
  `STAGE: derive-grant ok derived=1 refused=0` (the real `write_grant.py
  --under-pipeline`), `STAGE: build pending-R18 grants=1` and
  `STAGE: digest pending-R21 waiting=0`.
- `pt-plan-ignored.txt` names `.skill-contract/intake/.gitignore` as the rule that
  ignores the spec.
- `pt-plan-cap`: exit 0. `STAGE: autopick ok picked=2`, two `PLAN: ready` lines,
  `STAGE: plan ok ready=2 waiting=0`, then
  `STOP: cap max_plans_per_day count=1 limit=1 stage=derive-grant`,
  `STAGE: derive-grant ok derived=1 refused=0 cap=max_plans_per_day` and
  `STAGE: build pending-R18 grants=1`. The last line is
  `TICK: done stages=9 cap=max_plans_per_day`.
- `pt-plan-cap-envelopes.txt`: one `autonomy-grant-v1-` file, two `task-plan-v1-` files
  and the pipeline grant.

The R18 to R21 recipes join this section as each ships.

## Proof

The `pt-no-grant`, `pt-tick`, `pt-missing`, `pt-revoked`, `pt-locked`, `pt-unlocked`,
`pt-cap`, `pt-plan-unlisted`, `pt-plan-good`, `pt-plan-cap`, `pt-sched-15`, `pt-sched-1440`, `pt-sched-10`, `pt-sched-1500` and
`pt-sched-uninstall` captures (their `STOP:`, `STAGE:` and `SCHEDULE:` lines and exit
codes), `pt-calls.jsonl`, `pt-locked-calls.jsonl`, `pt-cap-calls.jsonl`,
`pt-cap-stops.jsonl`, `pt-plan-ignored.txt`, `pt-plan-cap-envelopes.txt`,
`pt-sched-files.txt` and the two queue hashes. After the planned parts ship: the content of `.skill-contract/pipeline/digest.md`.

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
- `pending-R<n>` is the honest result of a stage that is not built. It is not `ok`.
- The lock file stays on disk after a tick. That is correct: only a held `flock` blocks a
  tick, and the kernel drops it when the holder dies. Do not delete the file to "unlock".
- `proven: no` stays until the recipe is driven with SHA-bound evidence.
- The schedule installs a real timer unless reentry_timer's dry-run hooks are set. A
  factory run installs no timer and starts no service (decision D9); drive it only with
  `FACTORY_CONDUCTOR_TIMER_DRYRUN=1` and a temp `FACTORY_CONDUCTOR_TIMER_HOME`.
- On cron, 60 minutes or more must be a whole number of hours that divides 24, or the
  install gives `SCHEDULE: failed`.

# pipeline-tick: run every factory stage in one scheduled, locked, capped tick

- id: pipeline-tick
- proven: no
- anchors: factory-pipeline/assets/pipeline.py:cmd_tick, factory-pipeline/assets/pipeline.py:STAGES, factory-pipeline/assets/pipeline.py:find_tool, factory-pipeline/assets/pipeline.py:check_grant

## What it is

**Status: partly built.** `pipeline tick` ships (R12). Before any stage, it checks the
standing pipeline grant with spec-first-planning's `contract_check.py check-pipeline-grant`
(R13). With no live grant it runs no stage, prints `STOP: no-grant reason=<reason>` and
exits 2. It runs the factory stages in this
order: sweep, growth review, idea follow-up, autopick, plan, derive grant, build, release
stage and digest. It prints one `STAGE: <name> <result>` line per stage and exits 0 when
every stage ran. Sweep, growth review and autopick call ops-intake and growth-review for
real. Derive grant calls spec-first-planning `write_grant.py --under-pipeline` for each plan
that the plan stage hands on. The other five stages print `pending-R<n>`. The design is
`docs/factory/2026-11-04-autonomous-factory-spec.md` (R12 to R21).

## How to reach it

`python3 factory-pipeline/assets/pipeline.py --root <repo> [--skills-dir <dir>] tick`.
`--skills-dir` makes the tick search only that directory for sibling skills.
`factory-pipeline/assets/pipeline_testkit.py stubs <dir>` writes stub ops-intake,
growth-review and spec-first-planning skills that log each call to `<dir>/calls.jsonl`.
The stub spec-first-planning carries a copy of the real `contract_check.py`.
`factory-pipeline/assets/pipeline_testkit.py grant <repo>` writes a real pipeline grant
(and the pick policy and allowlist it pins) into `<repo>` with spec-first-planning's
writer, and prints `GRANT: <path>`.

Shipped:

- `pipeline tick`: nine `STAGE: <name> <result>` lines in order, exit 0. A missing skill
  gives `unavailable`. A stage tool's non-zero exit is recorded in its line, and the tick
  goes on.
- The grant check (R13): with no live pipeline grant (none, revoked by id or by the kill
  switch, expired, stale, tracked or invalid), exit 2, `STOP: no-grant reason=<reason>`,
  no `STAGE:` line and no stage tool call.

Planned (not yet shipped):

- A second tick while one holds the lock: exit 3, `locked`, and the queue is unchanged (R14).
- A cap is reached: `STOP: cap <name>` (for example `STOP: cap max_plans_per_day`), and
  no new plan or run starts after it (R15).
- `pipeline schedule install`: installs a timer with factory-conductor's timer module at
  an interval of 15 to 1440 minutes. An interval outside that range exits 2 and installs
  nothing (R16).
- The plan stage drafts a spec through an agent and hands the plan on only when
  `spec_lint.py` and `spec_to_tasks.py --allowlist` pass (R17).
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
  `plan pending-R17 picked=1`, `derive-grant idle plans=0`, `build pending-R18 grants=0`,
  `release-stage pending-R19`, `digest pending-R21 waiting=0`. The last line is
  `TICK: done stages=9`.
- `pt-calls.jsonl`: four lines, in order: ops-intake `sweep`, growth-review `auto`,
  ops-intake `import` (its `stdin` is the growth signal line, and its argv holds
  `--format intake-signals-jsonl --source growth -`), ops-intake `autopick`.
- `pt-missing`: exit 0, nine `STAGE:` lines, and `STAGE: growth-review unavailable growth-review`.
- `revoke-grant` (the kill switch) prints one `REVOKED: <id>` line and exits 0.
- `pt-revoked`: exit 2, `STOP: no-grant reason=revoked`, and no `STAGE:` line.

The R14 to R21 recipes join this section as each ships.

## Proof

The `pt-no-grant`, `pt-tick`, `pt-missing` and `pt-revoked` captures (their `STOP:` and
`STAGE:` lines and exit codes) and `pt-calls.jsonl`. After the planned parts ship: the
`STOP: cap` and `locked` refusals, the schedule install results, and the content of `.skill-contract/pipeline/digest.md`.

## Gotchas

- A stage tool's own lines are echoed with a `  <stage>| ` prefix. A `STOP:` line with
  that prefix belongs to the tool, not to the tick.
- Without `--skills-dir`, the tick finds the real sibling skills (the directory beside
  factory-pipeline comes before the project and user roots). In an empty repo they refuse
  with exit 2 (no intake config, no growth plan), so the stages say `refused`.
- `pending-R<n>` is the honest result of a stage that is not built. It is not `ok`.
- `proven: no` stays until the recipe is driven with SHA-bound evidence.
- The planned schedule installer only codes the timer. A run installs no timer and starts
  no service.

# pipeline-tick: run every factory stage in one scheduled, locked, capped tick

- id: pipeline-tick
- proven: no
- anchors: factory-pipeline/SKILL.md, factory-pipeline/references/overview.md, factory-pipeline/assets/test_factory_pipeline_smoke.py

## What it is

**Status: in development. No command ships yet.** The `factory-pipeline` skill is a
skeleton. It has a SKILL.md, a README, a smoke test and an eval, and no script. This
entry describes the planned surface from the approved spec
(`docs/factory/2026-11-04-autonomous-factory-spec.md`, R12 to R21). The task that
builds the command moves the anchors to the new code and fills in the full recipe.

The planned `pipeline tick` runs the factory stages in this order: sweep, growth review,
idea follow-up, autopick, plan, derive grant, build, release stage and digest. It prints
one `STAGE: <name> <result>` line per stage. It runs only under a live pipeline grant,
only one tick runs at a time in a repository, and the pipeline-grant caps stop it. It
stops at each human gate and lists that gate in a digest file.

## How to reach it

Today: there is no command. Read `factory-pipeline/SKILL.md` and run the smoke test
`python3 factory-pipeline/assets/test_factory_pipeline_smoke.py`.

Planned (not yet shipped):

- `pipeline tick`: nine `STAGE: <name> <result>` lines in order, exit 0.
- No live pipeline grant (none, or revoked): exit 2, `STOP: no-grant`.
- A second tick while one holds the lock: exit 3, `locked`, and the queue is unchanged.
- A cap is reached: `STOP: cap <name>` (for example `STOP: cap max_plans_per_day`), and
  no new plan or run starts after it.
- `pipeline schedule install`: installs a timer with factory-conductor's timer module at
  an interval of 15 to 1440 minutes. An interval outside that range exits 2 and installs
  nothing.
- The digest: `.skill-contract/pipeline/digest.md` lists each waiting human gate (a pull
  request to merge, a release grant to accept, a production deploy, a rollback, a plan
  waiting for a read, an idea decision) with the exact command the owner runs next.

## Drive it

Until the command ships, drive the skeleton. On the checkout under test:

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-smoke -- \
  python3 factory-pipeline/assets/test_factory_pipeline_smoke.py
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name pt-status -- \
  grep -n "in development: no command ships yet" factory-pipeline/SKILL.md factory-pipeline/README.md
```

- `pt-smoke`: exit 0, `Ran 6 tests` and `OK`.
- `pt-status`: exit 0, one match in each file.

This proves only that the skeleton is honest about its status. It proves none of R12 to
R21. The task that builds `pipeline tick` replaces this section with a recipe that runs
the tick in a fixture repo with stub skills and checks each planned line above.

## Proof

Today: the two captures above. After the command ships: the tick's `STAGE:` lines and
exit codes, the `STOP:` and `locked` refusals, the schedule install results, and the
content of `.skill-contract/pipeline/digest.md`.

## Gotchas

- Do not invent a `pipeline` command or report one as working: none ships yet, and the
  skeleton SKILL.md tells agents not to run one.
- `proven: no` stays until the real recipe is driven with SHA-bound evidence.
- The planned schedule installer only codes the timer. A run installs no timer and starts
  no service.

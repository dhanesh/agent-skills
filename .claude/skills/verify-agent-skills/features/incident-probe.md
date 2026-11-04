# incident-probe: detect live incidents with the release recipe's read-only probes

- id: incident-probe
- proven: no
- anchors: incident-watch/SKILL.md, incident-watch/references/overview.md, incident-watch/assets/test_incident_watch_smoke.py

## What it is

**Status: in development. No command ships yet.** The `incident-watch` skill is a
skeleton. It has a SKILL.md, a README, a smoke test and an eval, and no script. This
entry describes the planned surface from the approved spec
(`docs/factory/2026-11-04-autonomous-factory-spec.md`, R22 to R26). The task that
builds the command moves the anchors to the new code and fills in the full recipe.

The planned `incident probe` runs only the release recipe's `health`, `version_probe`
and `prod_smoke` commands, and records each result. After 2 or more consecutive failed
probes it opens one incident record and writes one `intake-signals-jsonl` line of kind
`incident`. The record shows the recipe's rollback command word for word and the
release-conductor command that asks the owner. incident-watch never runs a rollback or a
deploy: the owner mitigates.

## How to reach it

Today: there is no command. Read `incident-watch/SKILL.md` and run the smoke test
`python3 incident-watch/assets/test_incident_watch_smoke.py`.

Planned (not yet shipped):

- `incident probe` with a passing health command: a recorded pass, no incident record.
- A command that is not one of the recipe's `health`, `version_probe` or `prod_smoke`
  entries: exit 2, and no command runs.
- One failed probe: no record. The second consecutive failure: one record with the probe
  output, the last release record and the recent default-branch CI runs, and one
  `incident` line in the signals file.
- The open record: the recipe's rollback argv word for word. The rollback is shown, never
  run.
- `incident close --by <name>`: closes the record and prints
  `NEXT: run bug-autopsy with <record path>`. Without `--by` it exits 2.

## Drive it

Until the command ships, drive the skeleton. On the checkout under test:

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name ip-smoke -- \
  python3 incident-watch/assets/test_incident_watch_smoke.py
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name ip-status -- \
  grep -n "in development: no command ships yet" incident-watch/SKILL.md incident-watch/README.md
```

- `ip-smoke`: exit 0, `Ran 6 tests` and `OK`.
- `ip-status`: exit 0, one match in each file.

This proves only that the skeleton is honest about its status. It proves none of R22 to
R26. The task that builds `incident probe` replaces this section with a recipe that runs
the probe on a fixture recipe with stub commands and checks each planned line above.

## Proof

Today: the two captures above. After the command ships: the probe results, the exit-2
refusal, the incident record and its rollback argv, the signals line, the `NEXT:` line
from `incident close`, and a rollback stub that was never called.

## Gotchas

- Do not invent an `incident` command or report one as working: none ships yet, and the
  skeleton SKILL.md tells agents not to run one.
- Never point a probe at production while driving a fixture: the planned probe runs only
  read-only commands, but a wrong recipe is still a real command.
- `proven: no` stays until the real recipe is driven with SHA-bound evidence.

# incident-watch: steps

Status: partly built. The `probe` and `refuse` steps ship. The other steps are pending.

The design is `docs/factory/2026-11-04-autonomous-factory-spec.md` in the agent-skills
repository (requirements R22 to R26, decision D1).

## Steps, in order

| Step | What it does | Status |
|---|---|---|
| probe | Run only the release recipe's read-only `health`, `version_probe` and `prod_smoke` commands, and record each result. | ships (R22) |
| refuse | Refuse any other command with exit 2, and run nothing. `--only` takes only `health`, `version_probe` or `prod_smoke`. An extra argument after `probe`, or a probe entry whose program word holds a `{token}`, is refused too. | ships (R23) |
| open | After 2 or more consecutive failed probes, open one incident record with the probe output, the last release record and the recent CI runs. | pending (R24) |
| signal | Write one `incident` line into the ops-intake signals file. | pending (R24) |
| mitigate | Show the recipe's rollback command word for word. The owner decides and runs it through `release-conductor`. | pending (R25) |
| close | Close the record with `--by <name>` and hand it to `bug-autopsy`. | pending (R26) |

## The probe's output

- One line per command: `PROBE: <name> <pass|fail|timeout> rc=<exit code>`. The names
  are `health`, `version_probe`, `prod_smoke[0]`, `prod_smoke[1]` and so on.
- One summary line:
  `PROBE_RESULT: <pass|fail> commands=<n> failed=<n> consecutive_failures=<n>`.
- A refusal: `PROBE: refused: <reason>`, exit 2. Nothing ran.
- Exit 0 is a pass. Exit 3 is a failed probe, or another probe holds the lock.

## The state directory

`<repo>/.skill-contract/incidents/` holds its own `.gitignore` with `*`. The tool checks
it before it runs a command.

- `probes.jsonl`: one line for each probe. Each line has the time, the result, the
  recipe's sha256 and, for each command, the time, the name, the argv, the exit code and
  the last 2000 characters of stdout and stderr.
- `state.json`: the count of consecutive failed probes. R24 reads it.

## What it never does

- It never runs a rollback or deploy command. Rollback stays the owner's decision
  (decision D1).
- It never runs a command that is not in the recipe's three read-only lists (constraint
  S4).

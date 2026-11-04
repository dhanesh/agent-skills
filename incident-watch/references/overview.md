# incident-watch: steps

Status: partly built. The `probe`, `refuse`, `open`, `signal` and `mitigate` steps ship.
The `close` step is pending.

The design is `docs/factory/2026-11-04-autonomous-factory-spec.md` in the agent-skills
repository (requirements R22 to R26, decision D1).

## Steps, in order

| Step | What it does | Status |
|---|---|---|
| probe | Run only the release recipe's read-only `health`, `version_probe` and `prod_smoke` commands, and record each result. | ships (R22) |
| refuse | Refuse any other command with exit 2, and run nothing. `--only` takes only `health`, `version_probe` or `prod_smoke`. An extra argument after `probe`, or a probe entry whose program word holds a `{token}`, is refused too. | ships (R23) |
| open | After 2 or more consecutive failed probes, open one incident record with the probe output, the last release record and the recent CI runs. A later failure while the record is open adds its probe to that record. It opens no second record. | ships (R24) |
| signal | Write one `intake-signals-jsonl` line of kind `incident` to `signals.jsonl` for each record that it opens. | ships (R24) |
| mitigate | Show the recipe's rollback command word for word, and the release-conductor command that asks the owner. The owner decides and runs it through `release-conductor`. incident-watch never runs it. | ships (R25) |
| close | Close the record with `--by <name>` and hand it to `bug-autopsy`. | pending (R26) |

## The probe's output

- One line per command: `PROBE: <name> <pass|fail|timeout> rc=<exit code>`. The names
  are `health`, `version_probe`, `prod_smoke[0]`, `prod_smoke[1]` and so on.
- One summary line:
  `PROBE_RESULT: <pass|fail> commands=<n> failed=<n> consecutive_failures=<n>`.
- A refusal: `PROBE: refused: <reason>`, exit 2. Nothing ran.
- When a probe opens a record: `INCIDENT: opened <id> <record path>`,
  `SIGNAL: .skill-contract/incidents/signals.jsonl kind incident` and
  `NEXT: intake import --format intake-signals-jsonl --source incident-watch .skill-contract/incidents/signals.jsonl`.
- When a probe opens a record, two more lines show the rollback. They are
  `ROLLBACK: <the recipe's rollback argv>` and
  `NEXT: ask the owner to roll back: release rollback (shows the rollback and waits); on their yes: release rollback --approved-by "<their name>"`.
  `release rollback` acts only on a release in `prod_failed` or `outcome_unknown`. If
  no release is in one of those states (for example, the last release is `verified`),
  the second line is
  `NEXT: release rollback cannot act: <reason>; the owner rolls back by hand with the ROLLBACK command above`
  instead.
  If the recipe has no valid `rollback` field, the probe prints
  `ROLLBACK: none: <reason>; the owner mitigates by hand` and no rollback `NEXT:` line.
- Every line that the probe prints escapes control, format and line-separator
  characters (`\x0a`, `\x1b`, `\u2028` and so on). Recipe or record text in a line,
  such as a refusal reason or the rollback argv, cannot add a line of its own. The
  record keeps that text as data, unescaped.
- When a record is already open: `INCIDENT: open <id> <record path> probes=<n>`. No new
  signal line.
- When the CI runs are not on disk: `NEXT: gh run list --branch <branch> --limit 10 --json
  <fields> > .skill-contract/incidents/ci-runs.json`. The probe never runs `gh`. The owner
  runs this line. The next failed probe reads the file into the open record.
- Exit 0 is a pass. Exit 3 is a failed probe, or another probe holds the lock.

## The state directory

`<repo>/.skill-contract/incidents/` holds its own `.gitignore` with `*`. The tool checks
it before it runs a command.

- `probes.jsonl`: one line for each probe. Each line has the time, the result, the
  recipe's sha256 and, for each command, the time, the name, the argv, the exit code and
  the last 2000 characters of stdout and stderr.
- `state.json`: the count of consecutive failed probes and the id of the open record. A
  pass sets the count to 0. A pass does not close the record.
- `records/<id>.json`: one incident record. The id is
  `inc-<UTC time of opening>-<6 hex characters>`. The record holds:
  - `status` (`open`), `opened_at`, `updated_at` and `probe_count`;
  - `probes`: the failed probes of the streak, with their output tails (at most 20, the
    newest last);
  - `last_release`: the newest `release-result/v1` envelope from release-conductor under
    `.skill-contract/envelopes/`. Its checks are the same as in ops-intake's sync. If
    there is no such envelope, the field says that it is not available;
  - `releases`: the version and status of each release directory of release-conductor;
  - `ci_runs`: the 10 newest runs on the default branch, from `ci-runs.json`. The default
    branch is `ci.default_branch` in `.intake/config.json`, else `main`. If there is no
    file, the field says that the runs are not available, and it gives the `NEXT:` command;
  - `rollback` (R25): the rollback, shown and never run. `argv` is the recipe's
    `rollback` list word for word, with `{version}` and `{commit}` unfilled.
    release-conductor fills them from the rollback target. `display` is the same argv
    on one line, quoted like release-conductor's own `rollback:` line, with every
    control character escaped. `tokens` lists the tokens in it. `ask_owner` holds the
    release-conductor command that shows the rollback and waits (`release rollback`),
    the command that the owner's yes adds (`--approved-by "<their name>"`), the release
    states that release-conductor's rollback acts on (`prod_failed`, `outcome_unknown`)
    and the releases now in those states. If the recipe has no valid `rollback`,
    `available` is false and `reason` says why;
  - `closed_at` and `closed_by` (null until R26).
- `signals.jsonl`: one `intake-signals-jsonl` line for each opened record. The source is
  `incident-watch`, the kind is `incident`, the severity is 4 and the `source_id` is the
  record id. The evidence names the failed commands, their exit codes and the record
  path. It holds no output tail. The file is the hand-off and not stdout, because stdout
  holds the machine lines. To read it, add the source `incident-watch` with the format
  `intake-signals-jsonl` to `.intake/config.json`, then run the printed `NEXT:` line. A
  second import of the same file adds nothing.
- `ci-runs.json`: optional. The owner writes it with the printed `gh run list` line.

## What it never does

- It never runs a rollback or deploy command. Rollback stays the owner's decision
  (decision D1). The tool reads the recipe's `rollback` in one function, and only to
  show it. The unit suite checks the source for this, and it checks that the rollback
  and deploy stubs never run.
- It never runs a command that is not in the recipe's three read-only lists (constraint
  S4).

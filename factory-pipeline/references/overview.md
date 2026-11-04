# factory-pipeline: the stages of one tick

Status: partly built. `pipeline tick` ships. Five of its nine stages are not built yet and
report `pending-R<n>`. The design is `docs/factory/2026-11-04-autonomous-factory-spec.md`
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
| `growth-review` | Reviews the watched export when it changed, and imports each breach into the queue. The tick runs two processes and passes the bytes between them. It uses no shell pipe. | `growth-review`: `growth auto --source <name>`, then `ops-intake`: `intake import --format intake-signals-jsonl --source <name>` |
| `idea-follow-up` | Not built yet (R20). Result: `pending-R20`. | (`idea-validation`, later) |
| `autopick` | Picks items inside the registered pick policy. The tick keeps each picked intake-item envelope for the plan stage. | `ops-intake`: `intake autopick` |
| `plan` | Not built yet (R17). Result: `pending-R17`. It hands on no plan. | (`spec-first-planning`, later) |
| `derive-grant` | For each plan that the plan stage hands on, derives a per-plan grant from the pipeline grant. With no plan, the result is `idle`. | `spec-first-planning`: `write_grant.py --under-pipeline <grant> --root <repo> --plan <plan>` |
| `build` | Not built yet (R18). Result: `pending-R18`. | (`factory-conductor`, later) |
| `release-stage` | Not built yet (R19). Result: `pending-R19`. | (`release-conductor`, later) |
| `digest` | Not built yet (R21). Result: `pending-R21`. | none |

Also not built yet: `pipeline schedule install` (R16). The one-tick lock (R14) and the
caps (R15) ship; see their sections.

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
The tick checks all three caps before each of these stages. In `derive-grant` it checks
them again before each plan after the first, because the grant it just derived can reach
a cap. The other stages only read, sweep or report. They run in this order, with or
without a cap stop: `sweep`, `growth-review` and `idea-follow-up` before the gates, and
`release-stage` and `digest` after them.

When a count is equal to or more than its cap, the tick:

1. prints `STOP: cap <name> count=<n> limit=<m> stage=<stage>`;
2. appends one JSON line with the same facts to `.skill-contract/pipeline/cap-stops.jsonl`
   (git-ignored), and keeps the stop for the digest;
3. prints `STAGE: <name> skipped cap=<name>` for each later gated stage, so it starts no
   new plan or run;
4. runs the stages that only read, sweep or report, and ends `TICK: done stages=9 cap=<name>`
   with exit 0.

A count that the tick cannot make is a stop, not a zero. The tick prints
`STOP: cap uncounted source=<what> reason=<why> stage=<stage>` when:

- `--pipeline-caps` refuses (for example, an envelope it cannot read), fails or is not
  installed (`source=write_grant`);
- a run's `state.json` cannot be read (`reason=unreadable`);
- a derived run's `autonomy-log.jsonl` is missing or cannot be read, has a line that is
  not a JSON object (`reason=not-json`), or has a `dispatch` event with no time
  (`reason=no-time`).

The stop stays until the owner fixes the file or a new UTC day starts.

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
| `pending-R<n>` | The stage is not built yet. |
| `skipped` | A cap stopped the tick before this stage, which would start a plan or run. The detail is `cap=<name>`. |

A result that is not `ok` does not stop the tick. The tick goes on to the next stage, and
exits 0 when every stage ran. Exit 2 is a usage error (for example a `--root` that is not
a directory), `STOP: state-dir` or `STOP: no-grant`. Exit 3 is `STOP: locked`.

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
- The pick policy, the intake config and the pipeline grant.

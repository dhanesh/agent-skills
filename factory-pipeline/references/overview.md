# factory-pipeline: the stages of one tick

Status: partly built. `pipeline tick` ships. Five of its nine stages are not built yet and
report `pending-R<n>`. The design is `docs/factory/2026-11-04-autonomous-factory-spec.md`
in the agent-skills repository (requirements R12 to R21).

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

Also not built yet: the check for a live pipeline grant (R13), the one-tick lock (R14), the
caps (R15) and `pipeline schedule install` (R16).

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

A result that is not `ok` does not stop the tick. The tick goes on to the next stage, and
exits 0 when every stage ran. Exit 2 is a usage error, for example a `--root` that is not
a directory.

## How the tick finds a skill

Each skill is installed on its own, so the tick looks for each sibling by its directory
name. It searches the roots that skill-contract `discover` searches, in this order:
`$SKILL_CONTRACT_PATH`, the directory beside this skill, `<repo>/.agents/skills`,
`<repo>/.claude/skills`, `~/.agents/skills` and `~/.claude/skills`. The directory has to hold
a `SKILL.md` and the tool. `--skills-dir <dir>` or `$FACTORY_PIPELINE_SKILLS` replaces the
whole list. The tests use it to point at stub skills.

## Output

The tick prints `TICK: start <time> root <repo>`, then the stages, then
`TICK: done stages=<n>`. A line that starts with two spaces and `<stage>|` is the stage
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

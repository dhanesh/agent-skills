# incident-watch: planned steps

Status: in development: no command ships yet. This page lists what is planned. Nothing
on it runs today.

The design is `docs/factory/2026-11-04-autonomous-factory-spec.md` in the agent-skills
repository (requirements R22 to R26, decision D1).

## Planned steps, in order

| Step | What it will do | Skill that does it by hand today |
|---|---|---|
| probe | Run only the release recipe's read-only `health`, `version_probe` and `prod_smoke` commands on a schedule, and record each result. | `release-conductor` (its recipe holds the commands) |
| open | After 2 or more consecutive failed probes, open one incident record with the probe output, the last release record and the recent CI runs. | no skill yet |
| signal | Write one `incident` line into the ops-intake signals file. | `ops-intake` |
| mitigate | Show the recipe's rollback command word for word. The owner decides and runs it. | `release-conductor`, after the owner's yes |
| close | Close the record with `--by <name>` and hand it on for a post-mortem. | `bug-autopsy` |

## What it will never do

- It will never run a rollback or deploy command. Rollback stays the owner's decision
  (decision D1).
- It will never run a command that is not in the recipe's three read-only lists.

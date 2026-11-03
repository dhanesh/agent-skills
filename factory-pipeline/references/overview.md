# factory-pipeline: planned stages

Status: in development: no command ships yet. This page lists what is planned. Nothing
on it runs today.

The design is `docs/factory/2026-11-04-autonomous-factory-spec.md` in the agent-skills
repository (requirements R12 to R21).

## Planned stages, in order

One planned `pipeline tick` runs these stages under a standing pipeline grant. It stops
at each human gate.

| Stage | What it will do | Skill that does it by hand today |
|---|---|---|
| sweep | Collect operational signals and pick what broke. | `ops-intake` |
| growth review | Check product metrics against registered thresholds. | `growth-review` |
| idea follow-up | List ideas with an assumption that is due or expired. | `idea-validation` |
| autopick | Pick the next item of work. | `ops-intake` (`pick`) |
| plan | Draft a spec and a task plan from the picked item. | `spec-first-planning` |
| derive grant | Write the autonomy grant for the plan. | `spec-first-planning` (unattended mode) |
| build | Run the plan to an open pull request. | `factory-conductor` |
| release stage | Stage a release after the release pull request is merged. | `release-conductor` |
| digest | List every waiting human gate with the next command. | no skill yet |

## Human gates the tick will stop at

- A pull request to merge.
- A release grant to accept.
- A production deploy or a rollback.
- A plan that waits for the owner's read.
- An idea decision.

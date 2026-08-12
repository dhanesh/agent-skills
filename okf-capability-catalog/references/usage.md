# Usage runbook

Printable from the CLI: `python3 assets/okf_catalog.py help [topic]`, where topic is one of
`overview`, `sequence`, `events`, `roles`, `cadence`, `modes`, `troubleshooting`, `adoption`,
or `all`. This file is the single source of that text — edit it here, not in the code.

## Overview

The catalog only pays off if the writing happens **before** the thing it is supposed to
prevent. A capability recorded after go-live is documentation; a dependency recorded at
planning time is a contract with a date, a consequence and a fallback attached. Everything
below is about getting the order right.

Three habits carry the whole system, and the rest is support for them:

1. Every service repo is scanned on merge, so Capability and Service documents cannot drift
   into fiction.
2. No work item that touches another team's system starts before its Dependency reaches
   `acknowledged`.
3. Acceptance is recorded by the consuming team from its own CI, never by the provider.

If your org only ever does those three, the catalog works. If it does everything else and
not those, it is a wiki.

## Sequence

Phases run in this order. Skipping a step does not fail loudly — it produces a catalog that
looks populated and answers questions wrongly, which is worse than an empty one.

**Phase 0 — bootstrap (once per org, ~30 minutes)**

1. `init` — interview first: integration/release branch names, the ordered environment list,
   which environments need a machine liveness signal, the platform team.
2. Commit and push the bundle to its own repository. Everything after this is a pull request
   against that repo.
3. `codeowners` after the first teams exist, and wire `scripts/ci-enforce.sh` plus
   `validate` as required status checks. *Do this before inviting teams in.* Retrofitting
   enforcement after people have started asserting things means auditing what they already
   wrote.

**Phase 1 — onboard the providers (per team, ~1 hour each)**

4. The owning team adds `.okf/team.yaml` and `.okf/capabilities.yaml` to its repo. This is
   the only genuinely new artifact a team must author, and it is worth doing by hand rather
   than accepting inference.
5. `annotate` from that repo on the integration branch. Review the proposed capabilities and
   promote `lifecycle: proposed` → `active` where the contract is right.
6. Answer the upstream question (`--attest-upstream yes|no`). Until a team answers, every
   consumer of its capabilities reads `depth: unknown`, which is correct and is the point.

Onboard **providers before consumers**. A consumer scanned first produces stub teams and
`detected` edges pointing at capabilities nobody has described yet — recoverable, but it
front-loads the confusing part.

**Phase 2 — plan (per work item, at planning time)**

7. Consuming team runs `declare` **before the ticket enters *in progress***. This is the
   forcing function; without it the rest is bookkeeping.
8. Providing team runs `ack`. If the promise lands after the ask, the consumer runs
   `confirm` — that gap is a negotiation, and it happens now rather than on delivery day.

**Phase 3 — build (continuously)**

9. CI runs `annotate` on every merge to the integration branch, so `code_maturity` and
   interfaces stay true without anyone remembering.
10. Provider runs `tested` when it has exercised a capability in an environment. This is a
    ceiling, not a green light.
11. Provider runs `on-track` before each point of no return. Silence is not consent: an
    unconfirmed edge trips on its own.

**Phase 4 — accept (per environment, per consumer)**

12. Consumer CI runs `verify --kind deployment --ran-in <env> --resolved-from ci://…` when
    its own end-to-end suite passes against that environment. From the consumer's own
    repository, so the commit-boundary check passes by construction.
13. Record failures the same way (`--result failed`). A failed verification is the most
    valuable document in the bundle.
14. Deployment/monitoring emits `signal` for liveness. No human writes `production_live`.

**Phase 5 — operate (weekly, and on every event)**

15. `audit` in CI daily, and read at the weekly delivery review, top finding first.
16. `resolve` every tripped edge. A tripped edge may not stay tripped.
17. `next --team <id>` whenever someone asks "what do I owe, and what is about to bite me".

## Events

The decision table: what just happened → who acts → what to run. When in doubt, run
`next --team <your-team>`; it computes this list from the current state of the bundle.

| What just happened | Who runs it | Command |
|---|---|---|
| The org has no catalog | platform team | `init <bundle> --org <name> --integration <branch> --release <branch> --environments dev,staging,production --live-signal-env production --platform-team <team>` |
| A team is ready to join | that team | `annotate <bundle> --repo . --branch <integration> --attest-upstream yes\|no` |
| A merge landed on the integration branch | that repo's CI | `annotate <bundle> --repo . --branch <integration>` |
| A ticket in refinement touches another team's system | consuming team | `declare <bundle> --consumer <you> --capability <team/slug> --environment <env> --requested-date <date> --consequence "<who feels it, how badly>" --fallback "<degraded mode>" --fallback-days <n>` |
| …and no fallback is possible | consuming team | `declare … --no-fallback "<why none exists>"` |
| Another team asked you for a date | providing team | `ack <bundle> --dependency <id> --team <you> --promised-date <date> --by <handle> [--runtime-checked --note "<what you checked>"]` |
| The promise came back later than you asked for | consuming team | `confirm <bundle> --dependency <id> --team <you> --by <handle>` |
| A point of no return is approaching and you are on track | providing team | `on-track <bundle> --dependency <id> --team <you> --by <handle>` |
| You know you will miss it | providing team | `risk <bundle> --dependency <id> --team <you> --by <handle> --note "<what changed>"` |
| `audit` shows a tripped edge | the named decision-maker | `resolve <bundle> --dependency <id> --decision satisfied\|fallback_invoked\|renegotiated --by <handle> --note "<the decision>"` |
| You finished testing a capability in an environment | providing team | `tested <bundle> --team <you> --capability <id> --environment <env> --evidence <url>` |
| Your end-to-end suite passed against an environment | consuming team's CI | `verify <bundle> --team <you> --capability <id> --environment <env> --result verified --kind deployment --ran-in <env> --resolved-from ci://<pipeline>/run/<id> --evidence <url> --by <handle>` |
| Your suite failed against it | consuming team | `verify … --result failed --evidence <url>` |
| A contract test passed | consuming team | `verify … --kind contract` (recorded as evidence; raises readiness for nothing) |
| A deploy completed, or traffic was observed | CI / monitoring | `signal <bundle> --capability <id> --environment <env> --observation deploy\|traffic --emitted-by ci://<pipeline>/deploy/<id>` |
| Someone asks "is X ready for us?" | anyone | `readiness <bundle> --capability <id> --environment <env>` |
| Someone asks "what do we owe / what are we waiting on?" | anyone | `review <bundle> --team <id>` |
| Someone is about to build something | anyone | `review <bundle> --view directory` — check the menu before building what exists |
| Weekly delivery review, or a daily digest | delivery lead / CI | `audit <bundle> [--fail-on-high]` |
| A pull request was opened against the bundle | CI | `validate <bundle>` then `sh scripts/ci-enforce.sh <bundle> origin/<base>` |
| A new team claimed itself | platform team | `codeowners <bundle>` |
| "What should I do next?" | anyone | `next <bundle> --team <id>` |

## Roles

Nobody runs every mode. Handing a team the whole CLI is how it gets ignored.

| Role | Runs | Never runs |
|---|---|---|
| **Platform / catalog owner** | `init`, `codeowners`, `validate`, wiring CI | `declare`, `ack`, `verify` on behalf of anyone |
| **Providing team** | `annotate` (own repos), `ack`, `on-track`, `risk`, `tested` | `verify` for its own capability, `declare` for a consumer |
| **Consuming team** | `declare`, `confirm`, `verify`, `annotate` (own repos) | `ack`, `tested`, `promised_date` in any form |
| **CI / monitoring** | `annotate`, `verify` (from the consumer's own pipeline), `signal`, `audit`, `validate`, `enforce` | anything requiring a human judgement call |
| **Anyone** | `review`, `readiness`, `next`, `help` | — |

The CLI enforces the "never" column where it can, and the commit-boundary check catches the
rest. Both refuse rather than silently dropping a value, so a refusal is information: it is
telling you whose decision the missing thing actually is.

## Cadence

| Trigger | Mode | Why this cadence |
|---|---|---|
| every merge to the integration branch | `annotate` | generated fields cannot rot if they are regenerated |
| every pull request to the bundle | `validate`, `ci-enforce.sh` | authority rules are only real at the commit boundary |
| every consumer e2e run against an environment | `verify` | acceptance decays; this is what keeps it current |
| every deploy, and on observed traffic | `signal` | liveness is a machine fact |
| daily | `audit` | points of no return move whether or not anyone looks |
| weekly, at the delivery review | `audit`, `review --view timeline` | the forum where a tripped edge gets its decision |
| per work item, at refinement | `declare` → `ack` | before *in progress*, or the forcing function is lost |
| quarterly | `review --view directory`, re-attest upstreams | contracts drift slower than code, but they drift |

## Modes

Full flags: `references/parameters.md`.

| Mode | One line |
|---|---|
| `init` | scaffold the bundle; empty `teams/`, config, README |
| `annotate` | scan a service repo into Service + Capability documents and `detected` edges |
| `declare` | consuming team records a dependency (state `proposed`) |
| `ack` | providing team commits a date (state `acknowledged`) |
| `confirm` | consumer re-confirms after a date slip |
| `on-track` | provider confirms an edge is on track before its point of no return |
| `risk` | provider flags an edge at risk |
| `resolve` | record the forced decision on a tripped edge |
| `tested` | provider records its own testing (ceiling: `provider_tested`) |
| `verify` | consumer records acceptance — the only source of `consumer_verified` |
| `signal` | CI/monitoring records liveness — the only source of `production_live` |
| `readiness` | effective readiness + depth for a capability in an environment |
| `review` | what is: inbound, outbound, directory, timeline, capability detail |
| `audit` | what is wrong: seams at risk, tripped edges first |
| `next` | what this team should do now, as runnable commands |
| `options` | machine-readable choice lists for interactive pickers (`--json`) |
| `validate` | OKF conformance (hard) + catalog policy (soft) |
| `enforce` | commit-boundary authority checks over a changeset |
| `codeowners` | regenerate `.github/CODEOWNERS` from the teams present |
| `help` | this runbook |

## Troubleshooting

Refusals are deliberate and each names whose decision is missing. The fix is almost never to
work around it.

| You saw | It means | Do this |
|---|---|---|
| `REFUSED: a consumer may not set promised_date` | you are filling in the provider's commitment | record the ask with `--requested-date`; send them the `PASTE:` line the command printed |
| `REFUSED: consequence_if_late is blank or 'TBD'` | the field would become an accepted risk | ask again: who feels it, and how badly? A number beats an adjective |
| `REFUSED: no fallback and no rationale` | neither a degraded mode nor a reason there is none | `--no-fallback "<why>"` — that escalates, which is the correct outcome |
| `REFUSED: the runtime for the target environment differs` | the target environment runs on a different platform than where it was tested | look at it, then `--runtime-checked --note "<what you actually checked>"` |
| `REFUSED: '<team>' owns <cap> and cannot verify its own capability` | self-certification | the consuming team records acceptance from its own pipeline |
| `REFUSED: '<team>' is not a declared consumer` | verifying something you never declared | run `declare` first; a verification with no edge has no consumer to belong to |
| `REFUSED: '<provider>' exists only as a stub team` | nobody from that team has claimed it | go and talk to them; they run `annotate` from a repo they own |
| `REFUSED: the suite ran against '<a>' but this claims '<b>'` | environment generalisation | file it against the environment it ran in |
| `SCAN_RESULT: PROPOSAL_ONLY` | you scanned a feature branch | merge to the integration branch, or scan it explicitly |
| `CC-FOG` on an acknowledged edge | you committed into an unmapped closure | get the upstream teams to attest, or accept it knowingly |
| everything reads `depth: unknown` | early adoption | expected; watch `CC-COVERAGE` rise before trusting any green |
| `audit` is a wall of `CC-DETECTED` | pre-existing integrations nobody managed | that is the debt you already carried; work the list by `declare`-ing each |

## Adoption

A realistic first quarter, and what "working" looks like at each point.

**Week 1 — bootstrap.** `init`, CI wired, two or three friendly teams onboarded. Success is
an honest empty catalog, not a populated one. Expect `depth: unknown` everywhere.

**Weeks 2–4 — providers.** Onboard the teams other teams depend on most, in dependency
order. `audit` will be dominated by `CC-DETECTED` — every integration you already have and
never wrote down. Do not try to clear it; it is the inventory, and it shrinks as edges get
declared.

**Weeks 4–8 — the forcing function.** Add "a Dependency document exists in state
`acknowledged`" to the definition of ready. This is the only step that requires a decision
from someone with authority over the process, and the catalog does not pay off without it.
Success is the first time an `ack` conversation surfaces a date nobody had agreed.

**Weeks 8–12 — acceptance.** Consumer pipelines start emitting `verify`. Success is the
first `result: failed` verification recorded voluntarily — that is the signal the format is
trusted, because it means recording bad news costs nothing.

**Ongoing — the metric that matters.** Track `CC-COVERAGE` per team, not the count of green
edges. Coverage rising means the catalog is becoming trustworthy. A green board while
coverage is low is a lie, and the tooling will keep telling you so by rendering `unknown`
instead of green.

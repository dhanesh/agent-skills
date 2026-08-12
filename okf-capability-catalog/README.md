# okf-capability-catalog

Cross-team delivery fails at the **seams**, not inside teams: a dependency nobody wrote
down, a capability that is "done" in one environment and assumed to work in another, and
nobody forced to decide hold-vs-fall-back while falling back is still possible.

This skill externalises capability contracts and cross-team dependencies into an
**[Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
(OKF v0.1)** bundle — plain markdown in git, readable by humans and agents — so the
information cannot live only in memory.

> **An unwritten dependency is an accepted risk, whether or not you meant to accept it.**

```bash
okf=assets/okf_catalog.py
python3 $okf init     ./catalog --org acme --platform-team platform
python3 $okf annotate ./catalog --repo ../payment-orchestrator --attest-upstream yes
python3 $okf declare  ./catalog --consumer checkout --capability payments/initiate-refund \
        --environment production --requested-date 2026-08-25 \
        --consequence "Refund UI ships dark; ops runs ~40 manual refunds a week." \
        --fallback "Ship behind a flag, default off" --fallback-days 3
python3 $okf ack      ./catalog --dependency dep-2026-08-001 --team payments \
        --promised-date 2026-09-01 --by alice --runtime-checked
python3 $okf audit    ./catalog
```

## Install

```bash
npx skills add dhanesh/agent-skills --skill okf-capability-catalog
```

## Start here: `help`

The CLI ships its own runbook. `help` with no topic prints the orientation plus the
event-to-command table; `help <topic>` prints one section; `help all` prints everything.

```bash
python3 assets/okf_catalog.py help              # overview + when to run what
python3 assets/okf_catalog.py help sequence     # the order events must happen in
python3 assets/okf_catalog.py help roles        # who runs which mode, and who never does
python3 assets/okf_catalog.py help troubleshooting   # what each refusal means
python3 assets/okf_catalog.py next ./catalog --team checkout   # what to do *now*, computed
```

Topics: `overview`, `sequence`, `events`, `roles`, `cadence`, `modes`, `troubleshooting`,
`adoption`, `all`. Same text as `references/usage.md` — one source, printed on demand.

## The sequence that makes this work

The catalog pays off only if the writing happens **before** the thing it prevents. A
capability recorded after go-live is documentation; a dependency recorded at planning time
is a contract with a date, a consequence and a fallback attached.

```
Phase 0  bootstrap   init ──► push the bundle ──► codeowners + CI checks wired
   once per org                                   (do this BEFORE inviting teams in)
                                        │
Phase 1  onboard     .okf/capabilities.yaml ──► annotate ──► promote proposed → active
   providers first                                        └─► answer --attest-upstream
                                        │
Phase 2  plan        declare (consumer) ──► ack (provider) ──► confirm if the date slipped
   per work item     ▲ BEFORE the ticket enters "in progress"
                                        │
Phase 3  build       annotate on every merge · tested (provider) · on-track before each PONR
                                        │
Phase 4  accept      verify (consumer CI, per environment) · signal (deploy/monitoring)
                                        │
Phase 5  operate     audit daily · resolve every tripped edge · next --team for "what now"
```

Three habits carry the whole system; the rest is support:

1. **Every service repo is scanned on merge** — generated fields cannot drift into fiction.
2. **No work item touching another team starts before its dependency is `acknowledged`** —
   this is the forcing function, and the catalog does not pay off without it.
3. **Acceptance is recorded by the consuming team from its own CI** — never the provider.

Onboard **providers before consumers**: a consumer scanned first produces stub teams and
detected edges pointing at capabilities nobody has described yet.

## When to invoke it, and with what

The full table is `help events`. The rows people hit daily:

| What just happened | Who | Command |
|---|---|---|
| The org has no catalog | platform | `init <bundle> --org <name> --integration develop --release main --environments dev,staging,production --live-signal-env production --platform-team <team>` |
| A team is joining, or a merge landed | that team / its CI | `annotate <bundle> --repo . --branch <integration> --attest-upstream yes` |
| A ticket in refinement touches another team | consumer | `declare <bundle> --consumer <you> --capability <team/slug> --environment <env> --requested-date <date> --consequence "<who feels it, how badly>" --fallback "<degraded mode>" --fallback-days <n>` |
| Someone asked you for a date | provider | `ack <bundle> --dependency <id> --team <you> --promised-date <date> --by <handle>` |
| The promise came back later than the ask | consumer | `confirm <bundle> --dependency <id> --team <you> --by <handle>` |
| A point of no return is near | provider | `on-track …` (or `risk … --note "<what changed>"`) |
| `audit` shows a tripped edge | named decision-maker | `resolve <bundle> --dependency <id> --decision satisfied\|fallback_invoked\|renegotiated --by <handle>` |
| Your e2e suite passed against an environment | consumer CI | `verify <bundle> --team <you> --capability <id> --environment <env> --result verified --kind deployment --ran-in <env> --resolved-from ci://<pipeline>/run/<id> --evidence <url> --by <handle>` |
| A deploy completed / traffic observed | CI, monitoring | `signal <bundle> --capability <id> --environment <env> --observation traffic --emitted-by ci://<pipeline>/deploy/<id>` |
| "Is X ready for us?" | anyone | `readiness <bundle> --capability <id> --environment <env>` |
| Weekly delivery review | lead, CI | `audit <bundle> [--fail-on-high]` |
| "What should I do now?" | anyone | `next <bundle> --team <id>` |

If you are unsure which applies, run `next --team <id>`: it computes the same table against
the bundle's actual state and prints runnable commands, urgent ones first.

## Interviewing, not form-filling

`declare` and `ack` exist to run a conversation, and the agent driving them should use the
host's structured question tool (`AskUserQuestion` in Claude Code) rather than a wall of
prose. `options` supplies the choices so nothing is invented:

```bash
python3 assets/okf_catalog.py options ./catalog --for capabilities --team checkout --json
# {"kind": "capabilities", "options": [{"value": "payments/initiate-refund",
#   "label": "payments/initiate-refund",
#   "description": "Initiate Refund — owned by payments; active; production: provider_tested/not_ready"}]}
```

One question per call — whether you follow up on "it'd be bad" depends on the last answer.
Dates, consequences and fallbacks stay free text: a picker with four guesses at what a delay
costs is worse than an empty box.

## The design, in four ideas

**1. Two layers, deliberately separated.** A **Capability** is the stable interface a team
offers ("initiate a refund"); a **Service** is the volatile implementation (repo, runtimes,
topics). Migrate ECS→EKS and only the Service document changes — the contract other teams
depend on is untouched. A **Dependency** joins them across teams and lives at the bundle
root, because an edge has two owners.

**2. Readiness is a grid, not a boolean.** Code maturity (`feature`/`integration`/`release`)
and deployment reality (`unknown`/`provider_tested`/`consumer_verified`/`production_live`)
are independent. Each state lives in a document owned by whoever may assert it — provider →
Capability, consumer → Verification, CI → Signal — so effective readiness is *computed*
across all three and no stale green tick can outlive the thing it described. It also
carries a **depth**: `unknown` when part of the hard-requires closure is unmapped, and
`unknown` never renders as green.

**3. The point of no return is arithmetic.** `promised_date − fallback.execution_days`,
computed backwards from the deadline. When it passes with no on-track confirmation the edge
**trips by itself**, the trip propagates to every downstream edge with no human relaying
it, and a tripped edge may not stay tripped: someone named records satisfied,
fallback_invoked, or renegotiated.

**4. Nobody certifies their own work.** The scanner cannot write `consumer_verified` or
`production_live`. A provider is refused when verifying its own capability. And because
guards only protect the honest path, `scripts/ci-enforce.sh` re-checks it at the commit
boundary — a verification authored by the providing team fails the build, naming both
parties.

## What you get

- **`annotate`** — scans a service repo (`.okf/capabilities.yaml` authoritative, OpenAPI /
  AsyncAPI / IaC inferred as proposals) into Service and Capability documents, opens
  `detected` edges for cross-team wiring nothing manages, and is a no-op on re-run.
- **`declare` / `ack`** — the two interviews, kept separate so neither side can answer the
  other's questions. Refuses "TBD" and refuses a missing fallback rationale, because a blank
  field silently becomes an accepted risk.
- **`verify` / `signal` / `tested`** — the three readiness sources, each capped at what its
  author is entitled to claim. A `kind: contract` run raises nothing; a staging run raises
  staging alone.
- **`review` / `audit`** — what is, versus what is wrong. 24 finding codes, tripped edges
  first; `CC-PROVIDER-ONLY` and `CC-PLATFORM-DRIFT` each catch the
  provider-validated-but-never-accepted pattern independently.
- **`validate` / `enforce` / `codeowners`** — OKF conformance plus the three enforcement
  layers (path ownership, diff validation, reconciliation against git history).

## Layout

- `SKILL.md` — the agent prompt: model, ground rules, per-mode workflow, verify checklist.
- `assets/catalog_core.py` — the engine: YAML subset, readiness graph, state machine, audit
  findings, commit-boundary checks.
- `assets/okf_catalog.py` — the CLI: 19 modes (incl. `help`, `next`, `options`), refusals
  with hints, deterministic clocks.
- `assets/test_okf_catalog.py` — 76-test stdlib suite.
- `eval/run_eval.py` — 50 end-to-end checks, one per acceptance test in the design.
- `references/usage.md` — the runbook `help` prints: sequence, event-to-command table,
  roles, cadence, troubleshooting, adoption plan.
- `references/` — failure patterns (the public cases each rule comes from), readiness
  model, schemas, interviews (with the per-question instrument mapping), enforcement, repo
  declarations, audit findings, CLI reference, OKF spec tracking.
- `scripts/ci-enforce.sh` — git → changeset → `enforce`, for a required status check.

Verified end to end against a synthetic three-team org carrying a platform-parity split —
different container platforms per environment, with a provider-tested-only capability under
a live commitment. Both suites green offline, stdlib only.

The public failures each rule is derived from (CrowdStrike 2024, Knight Capital 2012, AWS
Kinesis 2020, GitLab.com 2017), with sources, are in `references/failure-patterns.md`.

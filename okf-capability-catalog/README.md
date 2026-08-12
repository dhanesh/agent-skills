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
- `assets/okf_catalog.py` — the CLI: 16 modes, refusals with hints, deterministic clocks.
- `assets/test_okf_catalog.py` — 76-test stdlib suite.
- `eval/run_eval.py` — 50 end-to-end checks, one per acceptance test in the design.
- `references/` — failure patterns (the public cases each rule comes from), readiness
  model, schemas, interviews, enforcement, repo declarations, audit findings, CLI
  reference, OKF spec tracking.
- `scripts/ci-enforce.sh` — git → changeset → `enforce`, for a required status check.

Verified end to end against a synthetic three-team org carrying a platform-parity split —
different container platforms per environment, with a provider-tested-only capability under
a live commitment. Both suites green offline, stdlib only.

The public failures each rule is derived from (CrowdStrike 2024, Knight Capital 2012, AWS
Kinesis 2020, GitLab.com 2017), with sources, are in `references/failure-patterns.md`.

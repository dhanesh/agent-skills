# Document schemas

A conformant OKF v0.1 bundle: a directory of markdown files with YAML frontmatter,
cross-linked, distributed as a git repo. Everything beyond OKF's required `type` is a
producer extension (legal under OKF §4.1); consumers must tolerate unknown keys.

```
okf-capability-catalog/
├── index.md                          # okf_version: "0.1"  (the only index with frontmatter)
├── log.md                            # creation/update/deprecation events
├── README.md                         # generated: the model and the assertion rules
├── catalog.config.yaml               # org config, NOT a concept document
├── teams/
│   ├── index.md
│   └── payments/
│       ├── team.md                   # type: Team
│       ├── capabilities/…            # type: Capability
│       ├── services/…                # type: Service
│       └── verifications/…           # type: Verification  (consumer-owned)
├── dependencies/…                    # type: Dependency
└── signals/…                         # type: Signal        (machine-owned)
```

`dependencies/` is central rather than nested under a team because an edge has two owners;
filing it under either implies unilateral ownership and reintroduces the ambiguity the edge
exists to remove.

## `catalog.config.yaml`

Everything org-specific is configurable — do not hardcode `develop`/`main`; other orgs are
trunk-based, GitFlow, or bespoke.

```yaml
okf_version: "0.1"
organization: acme
branches:
  integration: develop        # minimum bar for a capability to be asserted
  release: main
environments:                 # ordered lowest → highest
  - name: dev
  - name: staging
  - name: production
    requires_live_signal: true
capability_id_scheme: "<team>/<slug>"
default_fallback_execution_days: 2   # used only when a dependency omits it, and flagged
on_track_window_days: 7              # how recent an on-track confirmation must be
ponr_warning_days: 7                 # how far ahead audit warns about a point of no return
stale_assertion_days: 90             # when a human-asserted readiness is called old
platform_team: platform              # CODEOWNER of signals/
```

## Team

Never created by `init`. It exists one of two ways, and the difference is in `provenance`.

```yaml
---
type: Team
title: Payments
description: Owns money movement, refunds, and payment instrument lifecycle.
team_id: payments
provenance: claimed          # claimed | stub
claimed_by: <handle>
claimed_at: 2026-08-12T09:00:00Z
contacts:
  lead: <handle>
  channel: "#team-payments"
  github_team: "@acme/payments"     # optional; used by `codeowners`
repositories:
  - https://github.example/acme/payment-orchestrator
timestamp: 2026-08-12T00:00:00Z
---
```

`claimed` — the team authored its own document during the first `annotate` of a repo it
owns. Only a claimed team may have capabilities, services or contacts asserted under it.

`stub` — another team created the folder while declaring a dependency on a provider not yet
in the catalog. A stub carries `team_id` and `title` and **nothing else**. It must not be
written to by anyone but the team itself, and a dependency naming a stub provider can never
reach `acknowledged` — acknowledgement requires a named human from a claimed team. The stub
is deliberately unsatisfiable: it forces someone to go and talk to that team. This is the
anti-squatting rule; it lets a consumer record a dependency the moment they know about it
without speaking on the provider's behalf. Promotion `stub → claimed` happens on that
team's first `annotate`, preserving inbound links (the folder is never recreated).

## Capability — the stable interface

```yaml
---
type: Capability
title: Initiate Refund
description: Reverses a settled payment and emits a refund lifecycle event.
capability_id: payments/initiate-refund
owning_team: /teams/payments/team.md
lifecycle: active            # proposed | active | deprecated | retired
inputs:
  - name: payment_id
    description: Identifier of the settled payment
    required: true
outputs:
  - name: RefundInitiated
    description: Event emitted on the refund lifecycle stream
constraints:
  - Idempotent on payment_id + client_reference
fulfilled_by:
  - /teams/payments/services/payment-orchestrator.md
requires:                     # upstream capabilities this one needs to function
  - capability: /teams/ledger/capabilities/post-entry.md
    criticality: hard         # hard = cannot function without it; soft = degrades
    note: Refund reversal must post a ledger entry before acknowledging
upstream:
  attested: true              # the owning team confirmed `requires` is COMPLETE
  attested_by: <handle>
  attested_at: 2026-08-12T09:00:00Z
  scanner_agreement: true     # scan found no consumed cross-team interface outside `requires`
code_maturity:
  state: integration          # feature | integration | release
  branch: develop
  commit: 9a3f1c2
  method: scan
readiness:                    # PROVIDER-OWNED STATES ONLY (unknown | provider_tested)
  - environment: staging
    state: provider_tested
    asserted_by: teams/payments
    method: human
    evidence: https://ci.example/build/8842
    asserted_at: 2026-08-10T11:20:00Z
drift_detected: false         # set by the scan when the service stopped exposing it
timestamp: 2026-08-12T09:00:00Z
---

# Contract
# Consumers
# Citations
```

`requires` belongs on the capability, not on the dependency edge: if transitive risk had to
be hand-linked edge-to-edge, every consumer would need to know their provider's providers —
exactly the knowledge they do not have and should not need. Declared once here, the graph
does the traversal for everyone downstream.

## Service — the swappable implementation

`runtimes` is **per environment**, so an ECS/EKS split cannot hide.

```yaml
---
type: Service
title: Payment Orchestrator
service_id: payments/payment-orchestrator
owning_team: /teams/payments/team.md
repository: https://github.example/acme/payment-orchestrator
fulfils:
  - /teams/payments/capabilities/initiate-refund.md
runtimes:
  - environment: staging
    platform: EKS
    identifier: cluster-a/ns-payments
  - environment: production
    platform: ECS
    identifier: cluster-prod/payments-svc
    note: EKS migration in progress
interfaces:
  consumes:
    - kind: kafka_topic
      name: payments.settlement.v1
      consumer_group: payment-orchestrator
  produces:
    - kind: kafka_topic
      name: payments.refund.v1
scan:
  branch: develop
  commit: 9a3f1c2
  scanned_at: 2026-08-12T09:00:00Z
  scanner_version: okf-capability-catalog/0.1.0
  source: .okf/capabilities.yaml
timestamp: 2026-08-12T09:00:00Z
---
```

## Dependency — the managed seam

```yaml
---
type: Dependency
title: Checkout refund UI → payments/initiate-refund
dependency_id: dep-2026-08-001
consumer_team: /teams/checkout/team.md
provider_team: /teams/payments/team.md
capability: /teams/payments/capabilities/initiate-refund.md
target_environment: production
requested_date: 2026-08-25       # consumer's ask. Never written by the provider.
promised_date: 2026-09-01        # provider's commitment. Never written by the consumer.
consequence_if_late: >-
  Refund UI ships dark. Ops continues manual refunds via admin console, ~40 tickets/week
  at ~15 min each. Customer-facing SLA unaffected.
fallback:
  description: Ship UI behind a feature flag, default off; ops uses admin console.
  execution_days: 3
no_fallback_rationale: null      # set instead of `fallback` when none exists
point_of_no_return: 2026-08-29   # computed: promised_date − fallback.execution_days
acknowledgement:
  provider: { by: <handle>, at: 2026-08-05T10:00:00Z }
  consumer: { by: <handle>, at: 2026-08-05T10:40:00Z }
consumer_reconfirmation_required: false   # set when promised_date slips past requested_date
on_track:
  confirmed_by: <handle>
  confirmed_at: 2026-08-27T09:00:00Z
decision:                        # required once an edge trips
  decision: fallback_invoked
  by: <handle>
  at: 2026-08-30T09:00:00Z
depends_on: []                   # other dependency_ids, for risk propagation
detected_via: kafka_topic payments.refund.v1   # set only on scanner-detected edges
state: acknowledged
timestamp: 2026-08-12T09:00:00Z
---
```

Keeping `requested_date` and `promised_date` separate is deliberate: the gap between them
is real information, destroyed the moment they are merged. A promise later than the ask is
a negotiation that must happen now rather than on delivery day, so the edge holds at
`proposed` until the consumer re-confirms.

## Verification — consumer-owned acceptance

Lives under the **consumer's** folder. The only source of `consumer_verified`.

```yaml
---
type: Verification
title: Checkout verified payments/initiate-refund in production
capability: /teams/payments/capabilities/initiate-refund.md
verifying_team: /teams/checkout/team.md
environment: production
result: verified            # verified | failed | partial
kind: deployment            # deployment | contract | manual
environment_resolved_from: ci://checkout-web/run/9931   # not hand-declared
scope: >-
  Full refund path exercised end to end from the order detail screen, including the
  RefundInitiated event landing on our consumer.
evidence: https://jira.example/CHK-4412
verified_by: <handle>
verified_at: 2026-08-10T11:20:00Z
commit_sha: <filled by CI from the commit that introduced this file>
expires_after_days: 90
---
```

`result: failed` is deliberately recordable — the format must never make a consumer choose
between recording bad news and recording nothing.

## Signal — machine-owned liveness

`signals/` at the bundle root, written only by CI or monitoring, CODEOWNED by the platform
team. The only source of `production_live`.

```yaml
---
type: Signal
title: payments/initiate-refund observed live in production
capability: /teams/payments/capabilities/initiate-refund.md
environment: production
observation: traffic         # traffic | deploy | healthcheck
window: 2026-08-11T00:00:00Z/2026-08-12T00:00:00Z
detail: 412 refunds initiated
emitted_by: ci://payments-orchestrator/deploy/1182
emitted_at: 2026-08-12T00:05:00Z
---
```

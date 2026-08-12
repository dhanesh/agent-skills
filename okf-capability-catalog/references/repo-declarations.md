# What a service repo declares, and what the scan can infer

Precedence in `annotate`: **a hand-written declaration by the owning team always wins.**
Inference is a fallback and a *proposal*.

## `.okf/team.yaml`

```yaml
team_id: payments
title: Payments
description: Owns money movement, refunds, and payment instrument lifecycle.
lead: alice
channel: "#team-payments"
```

Absent, the scan falls back to `CODEOWNERS` (first `@org/team` entry), then to `--team`.
Ownership that cannot be resolved is a refusal, not a guess.

## `.okf/capabilities.yaml` — the provider's declaration

```yaml
service:
  id: payment-orchestrator            # defaults to the repo directory name
  title: Payment Orchestrator
  description: Orchestrates payment and refund state machines.
  repository: https://github.example/acme/payment-orchestrator
capabilities:
  - id: initiate-refund               # or a full `<team>/<slug>` id
    title: Initiate Refund
    description: Reverses a settled payment and emits a refund lifecycle event.
    inputs:  [{name: payment_id, description: Settled payment, required: true}]
    outputs: [{name: refund_id,  description: Created refund}]
    constraints: ["Idempotent on payment_id + client_reference"]
    requires:
      - capability: /teams/ledger/capabilities/post-entry.md
        criticality: hard             # hard | soft
        note: Must post a ledger entry before acknowledging
runtimes:
  - environment: staging
    platform: EKS
    identifier: cluster-a/ns-payments
  - environment: production
    platform: ECS
    identifier: cluster-prod/payments-svc
interfaces:
  consumes: [{kind: kafka_topic, name: payments.settlement.v1, consumer_group: payment-orchestrator}]
  produces: [{kind: kafka_topic, name: payments.refund.v1}, {kind: http, name: POST /internal/refunds}]
```

On the **first** scan the capability is created with this contract, `lifecycle: proposed`.
On later scans only machine-owned fields move (`code_maturity`, `fulfilled_by`, `scan`,
`runtimes`, `interfaces`, additive `requires`). A changed contract is *reported* for human
review, never silently overwritten — and body content is never deleted.

## `.okf/consumer.yml` — the consumer's declaration

```yaml
consumes:
  - capability: payments/initiate-refund
    environments: [staging, production]
    criticality: hard
    verification:
      suite: tests/integration/refund_e2e.py
      kind: deployment                # deployment | contract | manual
```

`annotate` in a consumer repo turns this into proposed `requires` entries and `detected`
edges. The verification suite is the hook for the closing move: when that suite has
actually run against the target environment, the consumer's **own** CI writes the
Verification under the consumer's **own** folder from the consumer's **own** repository. The
artifact originates where the authority lives, so the commit-boundary check passes
naturally and a provider cannot produce one without committing to someone else's repo.

`verification.kind` is load-bearing: `contract` may assert nothing on the readiness axis.
A contract test proves the shape of the exchange, and a payload shape can be perfectly
agreed while the deployment carrying it does not work — see
[failure-patterns.md](failure-patterns.md) §1.

## What inference covers, when nothing is declared

| Source | Yields |
|---|---|
| `openapi*.yaml/json` (`openapi:` + `paths:`) | produced `http` interfaces, one per method+route |
| `asyncapi*.yaml/json` (`channels:`) | `publish` → produced topic, `subscribe` → consumed topic |
| `*.tf` / k8s manifests under a path segment naming a configured environment | `runtimes[]`: `aws_ecs_*` → ECS, `aws_eks_*` / `kind: Deployment` → EKS |

Everything inferred is labelled a proposal in the scan output, and the run tells the team to
write `.okf/capabilities.yaml` rather than trusting it.

## What inference cannot cover — say this out loud

The scanner is structurally blind to dependencies on manual ops processes, config another
team maintains, shared database tables, vendor relationships, repos not yet onboarded, and —
the largest class — **work not yet written**, which is the entire prospective planning case.
So absence of scanner findings is absence of *detectable* dependencies only. That is why
`upstream.attested` sits on top of `scanner_agreement` and cannot be replaced by it.

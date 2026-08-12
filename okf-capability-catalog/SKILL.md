---
name: okf-capability-catalog
description: >-
  Write down what each team offers other teams and what each team is waiting on, as an
  Open Knowledge Format (OKF v0.1) bundle in git that humans and agents can both read.
  Use when cross-team delivery keeps failing at the seams — invisible dependencies nobody
  wrote down, a capability that is "done" in one environment and assumed to work in
  another, a go-live that slips because no one was forced to decide hold-vs-fall-back in
  time. Also use for: "map our cross-team dependencies", "who depends on this service",
  "is this capability ready for us", "build a capability catalog / service catalog", "what
  breaks if payments is late", "why did nobody know we needed that". Ships a stdlib CLI
  that scans service repos into Capability/Service documents, interviews the consuming and
  providing teams for the things code cannot know (date, consequence, fallback), computes
  effective readiness across the hard-requires closure, trips a dependency automatically at
  its point of no return, and enforces at the commit boundary that no team can certify its
  own work.
license: MIT
compatibility: Requires python3 (stdlib only) — no pip, no network. Produces plain markdown in git; the commit-boundary check needs git and CI to run scripts/ci-enforce.sh. Agent-runtime agnostic.
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "okf,capability-catalog,cross-team-dependencies,delivery-risk,readiness,knowledge-bundle,platform-engineering"
---

# okf-capability-catalog

Blocks cut for a pre-modern megaproject were **painted at the quarry** with the position
they were destined for and the requirements they had to meet. The block arrived
*addressed*: it carried its own acceptance criteria, and the downstream gang refused work
that had not passed its upstream gate. The requirement could not be silently forgotten,
because it was written on the thing moving through the system.

That is what this skill does to cross-team delivery. It externalises capability contracts
and dependency edges into an OKF bundle — plain markdown in git — so the information stops
living in someone's head. The governing principle, and the sentence to repeat to teams:

> **An unwritten dependency is an accepted risk, whether or not you meant to accept it.**

The tooling is [assets/okf_catalog.py](assets/okf_catalog.py) (stdlib, offline) over
[assets/catalog_core.py](assets/catalog_core.py). Read
[references/readiness-model.md](references/readiness-model.md) before you interpret any
state — it is where the non-obvious rules live.

## The model, in three objects

| Object | Layer | Stability | Who writes it |
|---|---|---|---|
| **Capability** | interface — "initiate a refund" | stable, survives re-implementation | scanner proposes, owning team promotes |
| **Service** | implementation — repo, runtimes, topics | volatile: ECS→EKS, rewrites, splits | scanner, every run |
| **Dependency** | the seam: consumer → provider's capability, with a date, a consequence and a fallback | managed | both teams, by interview |

Teams converse in capabilities; services churn underneath. A dependency edge lives at
`dependencies/`, not under either team, because an edge has two owners and filing it under
one implies unilateral ownership. Schemas: [references/schemas.md](references/schemas.md).

## Ground rules

These are the rules the tool enforces, and the ones to defend in conversation. They exist
because each has a matching failure.

- **Readiness is a grid, not a boolean.** Code maturity (`feature`/`integration`/`release`)
  and deployment reality (`unknown`/`provider_tested`/`consumer_verified`/`production_live`)
  are independent axes. On `main` does not imply deployed; deployed in staging does not
  imply production; tested by the provider does not imply it works for the consumer.
- **Each state lives in a document owned by whoever may assert it.** The provider's
  Capability may say `provider_tested` and nothing above it; `consumer_verified` lives in a
  Verification under the *consumer's* folder; `production_live` lives in a Signal written by
  CI. Effective readiness is computed across all three, never hand-written in one place —
  so no stale green tick can survive the thing it described.
- **The trust gradient runs machine-detectable → consumer-asserted → monitor-confirmed.**
  The further right, the less a human's word is accepted for it.
- **Absence of scanner findings is absence of *detectable* dependencies only.** The scan is
  blind to manual ops processes, shared tables, vendor relationships, un-onboarded repos,
  and — the largest class — work not yet written. That is why `upstream.attested` exists:
  without it, "declares no upstreams" and "nobody has ever looked" are indistinguishable.
- **`unknown` is not `fine`.** A capability whose closure nobody has mapped renders as
  `unknown` in every view, distinct from both green and red. Partial information presented
  without its own limits manufactures confidence, which is the failure being prevented.
- **Teams are self-authoring.** Never fabricate a team, a contact or a capability contract
  on someone's behalf; a fabricated team is indistinguishable from a real one to the next
  agent that reads the bundle. A `stub` team created by a consumer is deliberately
  unsatisfiable — it forces someone to go and talk to that team.

## Workflow

Pick the mode that matches what the user is doing. Full flags:
[references/parameters.md](references/parameters.md).

1. **Locate or create the bundle.** If the org has no catalog yet, run `init` once
   (interview first: integration and release branch names, the ordered environment list,
   which environments need a machine liveness signal, the platform team). It scaffolds an
   **empty** `teams/`; worked examples go in the generated README, not into the graph.
2. **Scan the service repos** with `annotate` (below). Do this before any interview — never
   ask a human for something the code already proves.
3. **Turn seams into contracts** with `declare` (consumer) and `ack` (provider), running the
   interviews in [references/interviews.md](references/interviews.md).
4. **Record acceptance** with `verify` (consumer) and wire `signal` into CI.
5. **Answer questions** with `review` (what is) and `audit` (what is wrong), and hand the
   user the seams-at-risk report rather than a status dump.
6. **Wire the enforcement** — `codeowners`, plus `scripts/ci-enforce.sh` as a required
   status check. Until that is in place the authority rules hold only on the honest path;
   say so plainly rather than implying the catalog is tamper-proof.

### Mode: `annotate` — derive what code can prove

Run from inside a service repo, pointed at the bundle. It reads the configured
**integration** branch by default; a capability that exists only on a feature branch is a
proposal, so the scan reports and writes nothing. `.okf/capabilities.yaml` in the repo is
authoritative when present; inference is a fallback and a proposal
([references/repo-declarations.md](references/repo-declarations.md)).

```bash
python3 assets/okf_catalog.py annotate <bundle> --repo . --branch develop --attest-upstream yes
```

It writes the Service, proposes Capabilities (`lifecycle: proposed` — only a human
promotes to `active`), records per-environment runtimes, opens `detected` edges for
cross-team wiring nothing manages, and proposes `requires`. Re-running on an unchanged
commit is a no-op. It will not write `consumer_verified` or `production_live`, will not
touch a Dependency, and marks a vanished capability `drift_detected` instead of deleting
it. Relay its `LEFT FOR HUMANS` list — those fields are deliberately not machine-knowable.

### Mode: `declare` / `ack` — the two interviews

`declare` is the consuming team; `ack` is the providing team. Keep them separate: the
consumer may not write `promised_date`, the provider may not write `consequence_if_late`,
`fallback` or `requested_date`, and the tool refuses rather than silently dropping a value.
Ask one question at a time, in plain language, and never show a respondent YAML. Where an
answer genuinely does not exist, record the *inability* (`--no-fallback "<why>"`) — it
escalates, where a blank field would quietly become an accepted risk.

The three fields that carry the whole exercise are `consequence_if_late`, `fallback`, and
the derived `point_of_no_return` (`promised_date − fallback.execution_days`, computed
backwards from the deadline). A team that cannot articulate a fallback has surfaced a red
flag weeks early — treat that as the format working, not as a validation failure.

**Timing is the forcing function, and it belongs in the definition of ready:**

> A work item that touches another team's system cannot enter *in progress* until a
> Dependency document exists in state `acknowledged`.

### Mode: `verify` / `signal` — acceptance and liveness

`verify` writes a Verification under the **consumer's** own folder; it refuses the owning
team outright, and refuses to file a run against an environment it did not execute in.
`kind: contract` is recorded as evidence and raises readiness for nothing — a contract test
proves you agree on the shape of the exchange, which is exactly what was already true in
the incident this design comes from. Record `result: failed` as readily as `verified`; a
failed verification is the most valuable document in the bundle. `signal` accepts only a
`ci://` or `monitor://` source.

### Mode: `review` / `audit` — what is, versus what is wrong

`review` gives inbound asks, outbound waits, the capability directory (the menu to consult
*before* building something that already exists), a timeline of nearest decisions, and
capability detail. `audit` is a filter over the same data for findings — tripped edges
first, then impossible promises, fog, unmanaged upstreams, provider-only readiness under a
live commitment, platform drift across environments. Codes and what each catches:
[references/audit-findings.md](references/audit-findings.md). Keeping the two apart stops
the catalog reading as a permanent list of grievances, which is how these get ignored.

During adoption most upstreams are unmapped, so nearly everything reads `depth: unknown`.
That is correct and worth saying out loud: the catalog's first honest output is a map of
how little is known. Track **closure coverage** as the adoption metric — a green board
before coverage is high is a lie.

## Deliverables

Every run produces markdown in the bundle plus a machine-readable report line:

| Mode | Writes | Final line |
|---|---|---|
| `init` | skeleton, config, README, empty `teams/` | `INIT_RESULT: OK` |
| `annotate` | Service, Capabilities, `detected` edges, indexes, `log.md` | `SCAN_RESULT: OK` / `PROPOSAL_ONLY` |
| `declare` / `ack` / `confirm` | the Dependency | `DECLARE_RESULT: PROPOSED <id>`, `ACK_RESULT: ACKNOWLEDGED` / `PENDING_CONSUMER` |
| `verify` / `signal` / `tested` | Verification / Signal / provider readiness | `VERIFY_RESULT: WRITTEN <path>` |
| `readiness` | nothing | `READINESS_RESULT: <state> depth=<d> verdict=<v>` |
| `audit` / `validate` / `enforce` | nothing | `AUDIT_RESULT: n finding(s) (h high)`, `VALIDATE_RESULT`, `ENFORCE_RESULT` |

Any refusal prints `REFUSED: <reason>` and a `HINT:`, and exits 2. Hand the user the
refusal verbatim — it names whose decision the missing thing actually is.

## Verify

Before calling a session done:

1. `validate <bundle>` passes (hard tier); soft warnings are reported to the user, not
   silently fixed — several of them are findings about *their org*, not about the file.
2. `audit <bundle>` runs clean of surprises, and every high finding has either an owner or
   an explicit decision recorded.
3. Re-running `annotate` produces no diff (idempotence), and no human-asserted field moved.
4. The tool's own suites stay green when you change it:
   `python3 assets/test_okf_catalog.py` (76 tests) and `python3 eval/run_eval.py` (50
   end-to-end checks, one per acceptance test in the design — the EKS case, PONR
   arithmetic, trip propagation, migration stability, self-certification refusal).

## Extending

Add a rule by adding its check to `audit()` in `catalog_core.py` **and** a fixture that
fails without it — an unmeasured rule is a claim nobody tested. New readiness sources
belong in `own_readiness()` and must name the party entitled to assert them; if code cannot
prove it, the honest move is to leave the field for the humans who can and say so in the
output. The commit-boundary layer, its changeset format and its two honest limits are in
[references/enforcement.md](references/enforcement.md). OKF v0.1 tracking and the
spec-update rule: [references/okf-spec.md](references/okf-spec.md).

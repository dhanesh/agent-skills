# Readiness: a grid, an owner per cell, and a depth qualifier

"Exists" gets treated as one fact. It is not: it is a point on two independent axes,
owned by different parties, and the schema's job is to make it impossible to state one and
imply the other. The public failures each rule comes from are collected in
[failure-patterns.md](failure-patterns.md) — a provider's own validation standing in for
acceptance, deployment reality differing per target, a hidden upstream, a capability nobody
ever exercised.

## Axis 1 — code maturity (where the code lives)

| State | Meaning | Config key |
|---|---|---|
| `feature` | only on an unmerged branch. **A proposal, not a capability.** | — |
| `integration` | merged to the org's integration branch (default `develop`) | `branches.integration` |
| `release` | on the release branch (default `main`). The strongest claim code can make: *should be deployable*. | `branches.release` |

`release` implies nothing whatsoever on axis 2. "It's on main" ≠ "it's deployed" ≠ "it
works for you."

## Axis 2 — deployment reality (per environment)

| State | Meaning | Who may assert it | Where it lives |
|---|---|---|---|
| `unknown` | default | — | — |
| `provider_tested` | the owning team exercised it in this environment | provider | the Capability |
| `consumer_verified` | a **named consuming team** exercised it end to end there | consumer only | a Verification under the consumer's folder |
| `production_live` | real traffic observed | CI / monitoring | a Signal under `signals/` |

A capability is **consumable** in environment E only at `consumer_verified` or above for E.
`provider_tested` is never sufficient to plan a delivery date against: internal testing by
the provider is not acceptance, and acceptance belongs to the consumer.

**Trust gradient, implemented literally: machine-detectable → consumer-asserted →
monitor-confirmed.** The further right, the less a human's word is accepted for it.

### Why the state lives in three different documents

Putting all four states in the provider's file makes the rule honour-system: a provider can
type `consumer_verified` accidentally or otherwise. Field-level ownership inside a shared
file is unenforceable; *path* ownership is enforceable with ordinary CODEOWNERS. So no file
mixes two parties' assertions, and effective readiness is **computed** across the three,
never stored. Delete a Verification and the capability drops back — a stale green tick
cannot outlive the thing it described.

A provider-owned file that claims something above `provider_tested` is clamped when read
and reported by `audit` as `CC-HUMAN-LIVENESS`. It is never honoured.

### How the three sources combine (`own_readiness`)

1. Start from the provider's entry for that environment, clamped to `provider_tested`.
2. Raise to `consumer_verified` if any unexpired Verification with `result: verified` and
   `kind` other than `contract` points at this capability *and that environment*.
3. Raise to `production_live` if a Signal reports `observation: traffic` for it there.
4. If any unexpired Verification reports `result: failed`, lower the answer to at most
   `provider_tested`. Someone tried it and it did not work; that outranks optimism.

`kind: contract` raises nothing. A contract test proves you and the provider agree on the
*shape* of the exchange, and a shape check can pass while the thing it describes is
unrunnable where it has to run. `kind:
deployment` may only speak for the environment the run actually executed in, resolved from
the CI run rather than declared by hand. `kind: manual` needs a named human and evidence.

A consuming team's record for one environment is a single document: re-verifying replaces
its own earlier result, so a later failure cannot sit beside that team's stale green.

Expiry is opt-in per document (`expires_after_days`), for capabilities that change often
enough that a six-month-old verification is a fiction.

## Effective readiness: the answer a consumer actually needs

A team depending on X does not care whether X is ready. They care whether X *works for them
on the day*, which depends on X's whole hard-upstream closure:

```
effective_readiness(C, env) =
    min( own_readiness(C, env),
         effective_readiness(R, env) for each R in C.requires where criticality == hard )
```

Soft requires never lower the state; they are reported alongside as degradation risk.
Cycles are detected, broken, and reported as their own finding — a cycle in hard `requires`
usually means the capability boundary is drawn wrong.

## Depth: the answer is a pair, not a state

| Depth | Meaning |
|---|---|
| `complete` | every capability in the hard closure has `upstream.attested: true` and resolves to a claimed team |
| `unknown` | somewhere in the closure a capability is unattested, missing, or owned by a `stub` team |

**A consumer may treat a capability as green only when readiness is `consumer_verified` or
better AND depth is `complete`.** Everything else renders as `unknown`, distinct from both
green and red. Partial information presented without its own limits is worse than no
information, because it manufactures confidence.

`upstream.attested` is what makes absence meaningful. Without it, "this capability declares
no upstreams" and "nobody has ever looked" are the same bytes on disk — and a downstream
team reading the first while the truth is the second gets false confidence about
everything one hop further out.

`scanner_agreement: true` claims only that the scan found nothing *beyond* what was
declared. It never claims the scan found everything. The scanner is structurally blind to
manual ops processes, config another team maintains, shared tables, vendor relationships,
repos not yet onboarded, and work not yet written — which is the entire prospective
planning case. A human attestation sits on top of it and cannot be replaced by it.

## Rollout: coverage before green

During adoption nearly everything reads `depth: unknown`. That is correct, and the
catalog's first honest output is a map of how little is known. `audit` reports **closure
coverage** per team — the fraction of that team's hard closure resolving to claimed teams
with attested upstreams. Coverage rising is the signal that the catalog is becoming
trustworthy; a green board before coverage is high is a lie.

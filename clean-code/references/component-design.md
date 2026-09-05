# L3 — Component Design

Activate for module/package decisions: how to group classes into components and
how those components may depend on each other.

> If SOLID arranges bricks into walls, component principles arrange rooms into
> buildings.

## Cohesion — what belongs together

### REP — Reuse/Release Equivalence Principle
*"The granule of reuse is the granule of release."*
- Classes in a component should be **releasable together** and share a purpose
  that's meaningful to a consumer.
- If you can't version them as a unit, they probably don't belong together.

### CCP — Common Closure Principle (SRP for components)
*"Gather classes that change for the same reasons at the same times."*
- A given requirement change should ideally touch **one** component.
- Goal: minimize how many components a single change ripples across.

### CRP — Common Reuse Principle (ISP for components)
*"Don't force a component's users to depend on things they don't need."*
- Classes used together belong together; classes not used together don't.

**The tension:** REP/CCP pull components *larger*; CRP pulls them *smaller*.
Balance by project stage:
> **Early:** favor CCP — deployability and ease of change matter most.
> **Mature:** shift toward REP/CRP as external consumers appear.

## Coupling — which way dependencies point

### ADP — Acyclic Dependencies Principle
*"Allow no cycles in the component dependency graph."*
- The graph must be a **DAG**. A cycle means you can't build or test A without
  B, which needs C, which needs A.
- **Break cycles** with Dependency Inversion (introduce an interface) or by
  extracting a new component. Draw the graph periodically — cycles are debt.

### SDP — Stable Dependencies Principle
*"Depend in the direction of stability."*
- A volatile component must not be depended on by a stable one.
- **Stability = Fan-in / (Fan-in + Fan-out)** (incoming vs. total dependencies).
  High stability = many things rely on it = hard to change.

### SAP — Stable Abstractions Principle
*"A component should be as abstract as it is stable."*
- Stable components should be mostly interfaces/abstract classes; volatile ones
  can be concrete.
- **Zone of Pain:** stable + concrete (e.g. a schema everything imports) — rigid.
- **Zone of Uselessness:** abstract + unstable — never implemented.

## Objects vs. data structures

|  | Objects | Data structures |
|---|---|---|
| **Purpose** | hide data, expose behavior | expose data, no behavior |
| **Add a new type** | easy (polymorphism) | hard (touch every function) |
| **Add a new function** | hard (touch every class) | easy (one function) |
| **Use for** | complex domain behavior | DTOs, config, transfer |

- **Avoid hybrids** — half-object/half-struct gets the downsides of both.
- **Law of Demeter:** an object calls methods only on itself, its fields,
  objects it created, and objects passed to it.
  - Train wreck: `a.getB().getC().doSomething()`.
  - Fix: `a.doSomething()` — let `a` handle the chain internally.

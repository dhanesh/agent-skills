# L4 — Clean Architecture

Activate for system design: project skeletons, service boundaries, large
refactors, multi-service design.

> The goal of architecture is to minimize the human resources required to build
> and maintain the system.

Architecture is largely about **deferring decisions** — keeping options open
until you have enough information to choose well.

> The web is a detail. The database is a detail. We keep these things on the
> outside where they can do little harm.

## First: do you need layers yet?

Clean Architecture earns its cost on systems with **diverging reasons to
change** — multiple delivery mechanisms, swappable infrastructure, long-lived
business rules, or a growing team. A small script or a single-purpose tool does
not need entities/use-cases/adapters; forcing that structure on it is Needless
Complexity. **Start with the simplest thing that works and introduce a boundary
only when a real pressure appears** (a second client, a swapped DB, an
untestable seam). The rest of this file is what to reach for *when* that
pressure is real.

## The four layers (inside → out)

```
┌───────────────────────────────────────────────┐
│  FRAMEWORKS & DRIVERS    web, DB, UI, ext. APIs │
│  ┌──────────────────────────────────────────┐  │
│  │  INTERFACE ADAPTERS   controllers,         │  │
│  │  ┌──────────────────  presenters, gateways │  │
│  │  │  USE CASES   application business rules  │  │
│  │  │  ┌────────────────────────────────────┐ │  │
│  │  │  │  ENTITIES   enterprise rules        │ │  │
│  │  │  └────────────────────────────────────┘ │  │
│  │  └──────────────────────────────────────┐  │  │
│  └──────────────────────────────────────────┘  │
└───────────────────────────────────────────────┘
        Source dependencies point INWARD only.
```

| Layer | Contains | Changes when |
|---|---|---|
| **Entities** | enterprise-wide rules, core domain objects | core business fundamentals change (rare) |
| **Use Cases** | application-specific rules, orchestration | application behavior changes |
| **Interface Adapters** | controllers, presenters, gateways, mappers | delivery/format changes |
| **Frameworks & Drivers** | web framework, DB, UI, external services | technology choices change |

## The Dependency Rule (the one rule)

> Source code dependencies point only inward. Nothing in an inner circle knows
> anything about an outer circle.

- Entities import nothing outward. Use Cases import no adapters or frameworks.
- Use Cases define **output-port interfaces**; adapters implement them.
- Data crossing a boundary is a plain **DTO** — never an ORM model or framework
  object passed inward.
- **Crossing boundaries:** the inner circle defines an interface; the outer
  circle implements it. Control can flow outward (Use Case → Presenter) while
  the *source dependency* still points inward (Presenter implements
  `UseCase.OutputPort`).

## Policy and level

- A system is a set of **policies**. Architecture separates them by how often
  and why they change, and arranges them so high-level policy doesn't depend on
  low-level policy.
- **Level = distance from the inputs and outputs.** The farther a policy is from
  I/O, the higher its level. High-level policy (entities, use cases) should not
  name low-level detail; dependencies and the source-code import graph should
  point from lower level toward higher.

## Use cases — the heart

> When you look at the architecture it should scream the domain — "accounting,"
> not "web."

- The primary organizing principle. Each use case is a class/module with one
  `execute()`.
- **Delivery-agnostic:** the same use case works over HTTP, CLI, or a queue.
- No I/O, no framework imports, no DB calls — pure business logic that calls
  entities and depends on interfaces the outer layers satisfy.

```python
class ProcessLoanApplicationUseCase:
    def __init__(self, loans: LoanRepository, notifier: NotificationPort):
        self.loans = loans
        self.notifier = notifier

    def execute(self, request: LoanApplicationRequest) -> LoanApplicationResult:
        loan = Loan(request.amount, request.applicant_id)
        loan.validate()
        saved = self.loans.save(loan)
        self.notifier.notify_applicant(saved.id)
        return LoanApplicationResult(loan_id=saved.id, status="PENDING")
```

*(Illustrative. The shape is the lesson — one entry point, no I/O, depends only
on injected ports — not Python and not a layout to reach for on small tasks.)*

## Entities

- Innermost and most stable. Encode rules that would exist even without
  software (business laws, domain invariants).
- Plain objects with methods; no framework dependencies; reusable across
  applications. Change only when fundamental business rules change.

## The Main component / composition root

- **Separate constructing the system from using it.** Object creation and wiring
  is its own responsibility, kept out of the business logic that uses the
  objects.
- `Main` (a composition root) is the outermost, dirtiest component: it builds
  concrete adapters and injects them inward, then hands control to the
  application. The app depends only on interfaces and never news up its own
  dependencies. This is dependency injection in the large.

## Cross-cutting concerns

Logging, metrics, security, transactions, and caching cut across many use
cases. **Don't scatter them through business logic** — that buries the domain in
noise and couples it to infrastructure. Instead:

- Centralize them at a **boundary**: a decorator/wrapper around a port, a
  middleware, or an aspect — so the use case stays focused and the concern lives
  in one place.
- For observability specifically: let leaf functions *raise* context-carrying
  errors; log and classify them once, at the orchestration boundary, through an
  injected logger. One well-placed log site beats ten inline `print`s.

## Humble Object pattern

When behavior is hard to test because it's tangled with a hard-to-test thing
(UI, DB, network), split it in two: a **testable** part holding the logic, and a
**humble** part that's a thin, dumb shell touching the boundary. Presenters
(testable) vs. views (humble); use case interactors (testable) vs. gateways
(humble). The humble shell has so little logic there's almost nothing to test.

## Partial boundaries

A full architectural boundary (two-way interfaces + DTOs both directions) is
expensive. When you suspect you'll need one later but don't yet:

- **One-dimensional boundary:** define the interface and implement it, but skip
  the reverse direction until needed.
- **Facade:** route access through a single class — weaker isolation, but cheap
  and easy to harden into a full boundary later.

Choosing a partial boundary is itself a deferral decision — keep the option open
without paying full cost up front.

## Details — what architecture must never depend on

These are **swappable plugins**: database/ORM, web framework, UI/delivery
mechanism (HTTP, gRPC, CLI, queue), external services (payments, email, cloud).

A well-structured Clean Architecture lets you write and test ~80% of the system
before committing to a database. Defer these as long as you reasonably can.

## The test boundary

- Tests are part of the system and live in the **outermost** circle — they
  depend inward on the components they verify; nothing in the system depends on
  tests.
- **The Fragile Tests problem:** if tests couple to volatile details (a
  changing UI, a real schema), a small change breaks thousands of tests. Test
  through stable interfaces and a testing API, and use the Humble Object pattern
  to keep the volatile edges out of the tested logic.

## Services aren't architecture (a caution)

Splitting a system into services (or microservices) does **not** by itself
create architectural boundaries. A service can be just as much of a tangled
monolith internally, and cross-service coupling can be as rigid as any
in-process call. The architectural boundaries are defined by the **Dependency
Rule**, not by process or network boundaries. Decompose into services for
operational reasons (scaling, deployment); keep the inward-pointing dependency
discipline regardless.

## Architecture vs. patterns

|  | Architecture | Pattern |
|---|---|---|
| Scope | whole-system structure | one design problem |
| Examples | Clean, Hexagonal, Onion | Strategy, Repository, Factory |
| Drives | deployment, testability, maintainability | local flexibility |

Architecture *uses* patterns; patterns *implement* architectural decisions.

## Paradigms as discipline

> Each paradigm removes something rather than adding something.

| Paradigm | Removes | Gives |
|---|---|---|
| Structured | unrestricted `goto` | sequence, selection, iteration |
| Object-oriented | raw function pointers | polymorphism (enables the Dependency Rule) |
| Functional | unrestricted mutation | referential transparency, no races |

Use all three; apply each where it fits.

# Glossary

| Term | Definition |
|---|---|
| **Entity** | Enterprise-wide business-rule object; innermost layer; no external dependencies. |
| **Use Case** | Application-specific business rule; orchestrates entities; delivery-agnostic. |
| **Interface Adapter** | Converts data between use-case format and an external format (controllers, presenters, gateways). |
| **Boundary** | Interface defined by an inner circle, implemented by an outer one; enforces the Dependency Rule. |
| **Dependency Rule** | Source dependencies point only inward, toward higher-level policy. |
| **Policy / Level** | A policy is a business rule; level is its distance from I/O. High-level policy must not depend on low-level detail. |
| **Screaming Architecture** | The folder/module structure reveals the domain, not the framework. |
| **DTO** | Data Transfer Object; plain data, no behavior; used to cross boundaries. |
| **Humble Object** | Pattern splitting hard-to-test boundary code into a testable part and a thin "humble" shell. |
| **Composition Root / Main** | The outermost component that constructs and wires concrete dependencies, separate from the code that uses them. |
| **Cross-cutting concern** | A concern (logging, security, transactions) spanning many use cases; handle at a boundary/decorator, not scattered through domain logic. |
| **Partial boundary** | A cheaper, incomplete architectural boundary (one-dimensional interface or facade) used to defer full cost. |
| **DIP** | Dependency Inversion Principle; used to break cycles and cross boundaries. |
| **SRP** | Single Responsibility Principle; one reason to change; one actor. |
| **OCP** | Open/Closed Principle; extend without modifying. |
| **LSP** | Liskov Substitution Principle; subtypes honor the base contract. |
| **ISP** | Interface Segregation Principle; no client depends on methods it doesn't use. |
| **Command-Query Separation** | A function changes state OR returns a value, never both. |
| **Law of Demeter** | An object talks only to its immediate collaborators; no train wrecks. |
| **Value Object** | A small immutable object representing a domain concept (Money, Email); cure for primitive obsession. |
| **Special Case / Null Object** | An object that stands in for "nothing" so callers avoid null checks. |
| **Simple Design** | Kent Beck's four rules: passes tests, reveals intent, no duplication, fewest elements. |
| **Learning test** | A small test that characterizes how a third-party library behaves; documents assumptions and catches breaking upgrades. |
| **Acceptance test / Definition of Done** | Business-facing automated spec of behavior; "done" = passing it, not "code complete." |
| **PERT estimate** | Three-point estimate: expected = (Optimistic + 4·Nominal + Pessimistic) / 6. |
| **Fan-in / Fan-out** | Incoming / outgoing dependencies; used to measure component stability. |
| **Stability** | Fan-in / (Fan-in + Fan-out); high = hard to change. |
| **Zone of Pain** | Highly stable + highly concrete = rigid and hard to change. |
| **Zone of Uselessness** | Highly abstract + unstable = never implemented. |
| **REP** | Reuse/Release Equivalence Principle; release together what you reuse together. |
| **CCP** | Common Closure Principle; SRP for components. |
| **CRP** | Common Reuse Principle; ISP for components. |
| **ADP** | Acyclic Dependencies Principle; no cycles in the component graph. |
| **SDP** | Stable Dependencies Principle; depend toward stability. |
| **SAP** | Stable Abstractions Principle; as abstract as it is stable. |
| **Deadlock / Livelock / Starvation** | Threads stuck waiting forever / busy but not progressing / perpetually denied a resource. |
| **Boy Scout Rule** | Leave every file cleaner than you found it. |
| **TDD** | Test-Driven Development; failing test → code → refactor. |
| **FIRST** | Fast, Independent, Repeatable, Self-validating, Timely — clean-test properties. |

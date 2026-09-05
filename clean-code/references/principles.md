# L2 — Core Principles

Activate when making design decisions: shaping a class, naming, refactoring, or
choosing an error strategy. (Testing has its own file: `testing.md`.)

## SOLID

### S — Single Responsibility Principle
*"A module should be responsible to one, and only one, actor."*

- One **reason to change**. "Actor" = a group of people who'd request a change
  (a role/team), not a single function.
- If methods on a class serve different roles (billing vs. reporting vs.
  persistence), it likely violates SRP.
- **Smell:** God classes; business logic fused with persistence or UI.
- **Fix:** Extract classes by actor/concern; compose them behind a facade.

### O — Open/Closed Principle
*"Open for extension, closed for modification."*

- Add new behavior **without editing existing code**, via abstraction
  (interfaces, strategy, polymorphism).
- **Smell:** every new feature edits the same five places.
- **Fix:** depend on abstractions; add behavior through new implementations.

### L — Liskov Substitution Principle
*"Subtypes must be substitutable for their base types."*

- A subtype must honor the base type's contract. The classic break: `Square
  extends Rectangle` where `setWidth`/`setHeight` surprise the caller.
- **Smell:** an override that throws `UnsupportedOperation` or silently does
  nothing.
- **Fix:** prefer composition when behavior genuinely diverges.

### I — Interface Segregation Principle
*"Clients shouldn't depend on methods they don't use."*

- Split fat interfaces into role-specific ones; a class can implement several.
- **Smell:** implementing a method as `throw new UnsupportedOperation()`.
- **Fix:** small, focused interfaces named for the role they serve.

### D — Dependency Inversion Principle
*"High- and low-level modules should both depend on abstractions."*

- Business logic must not import DB drivers, HTTP clients, or frameworks.
- **Smell:** `import mysql.connector` in a service/use-case layer.
- **Fix:** define the interface in the domain; inject the implementation from
  outside. This is the foundation of Clean Architecture.

## Naming (beyond the L1 checklist)

- **Reveal intent; avoid disinformation.** Don't name a thing `accountList`
  unless it's actually a List. Don't use names that vary in small ways
  (`XYZControllerForEfficientHandling…` vs `…ForEfficientStorage…`).
- **Make meaningful distinctions.** `a1, a2` and noise words (`Info`, `Data`,
  `theMessage`) carry no information — `source`/`destination` do.
- **Pronounceable and searchable.** `genymdhms` can't be discussed out loud;
  single letters can't be grepped. Length should track scope.
- **Problem- vs solution-domain names.** Use CS terms (`Factory`,
  `Queue`) when readers are programmers; use domain terms when the concept is
  about the business. Don't be cute, don't pun (one word, one meaning).
- **Add meaningful context** through enclosing classes/namespaces rather than
  prefixing every variable (`addrFirstName` → `Address.firstName`).

## Functions (beyond the L1 rules)

- **Output arguments are surprising** — `appendFooter(report)` reads like
  `report` is an input. Prefer `report.appendFooter()`.
- **Extract try/catch bodies.** Error handling is one thing; pull the `try` and
  `catch` blocks into their own functions so the structure stays flat.
- **Prefer exceptions to returned error codes** — codes force the caller to
  check immediately and tempt deep nesting.
- **Switch statements** are tolerable once, buried low, behind a polymorphic
  factory; a `switch` that recurs across the codebase is an OCP violation —
  replace with polymorphism.
- **Structured programming** (single entry/exit) is a guideline, not dogma: in
  small functions an early `return`/`break` that clarifies intent is fine;
  avoid `goto`.

## Classes

- **Small — measured by responsibilities, not lines.** A class name that needs
  "and"/"or"/"Processor"/"Manager" to describe it is doing too much.
- **Cohesion:** methods should use the class's instance variables. When a few
  methods share one subset of fields and others share a different subset, that's
  two classes hiding in one — split them. Maximizing cohesion naturally yields
  many small classes.
- **Encapsulation:** keep fields and helpers private; expose behavior, not data.
  Loosen visibility only as a last resort (e.g. for a test in the same package).
- **Organize for change (OCP):** structure so new requirements add classes
  rather than editing existing ones.
- **Isolate from change (DIP):** depend on interfaces, not concrete details, so
  volatile dependencies (DB, vendor APIs) can't ripple into business rules.

## Comments — explain WHY, not WHAT

**Earn their keep:** legal/copyright notices; intent behind a non-obvious
decision; warnings of consequence (`// not thread-safe`); a sparing TODO;
amplifying something that looks trivial but isn't.

**Delete these:** redundant restatements of the code; commented-out dead code
(git remembers); journal/changelog comments; closing-brace labels (`// end
while`); noise (`// default constructor`).

> If you feel the urge to write a comment, first try refactoring so the comment
> becomes unnecessary. The best comment is a good name.

## Error handling

- **Exceptions, not return codes** — they keep the happy path uncluttered.
- **Define exceptions by the caller's needs.** Class them by how they'll be
  caught and handled, not by their source. Often one exception type per area is
  enough.
- **Wrap third-party APIs** — never let a vendor/`IOException` leak through your
  layers; a wrapper lets you swap the dependency and define your own contract.
- **Use the Special Case pattern / Null Object** instead of returning null, so
  callers don't sprout defensive checks. Make exceptions the abnormal path, not
  control flow.
- **One try block per function** — if a function has a try, that should be the
  whole function. "Try" is one thing.
- **Never swallow** — `catch (e) {}` hides failures; it's negligence, not
  tidiness. Provide enough context to reconstruct what went wrong and where.
- **Don't return null; don't pass null.** Validate at the boundary; guarantee
  non-null inside.

```
// BAD: caller drowns in null checks
if (register != null && register.getBalance() != null) { ... }

// GOOD: validate at the boundary, throw if invalid, trust non-null inside
```

## Formatting

- **Vertical proximity:** related code stays dense; separate distinct ideas with
  blank lines. Caller above callee, so the eye flows downward.
- **Declare near use:** variables as close as possible to where they're used.
- **Team rules win:** agree on a style, let the formatter enforce it, stop
  arguing. Consistency beats any individual preference.
- **Lines ~120 chars max;** horizontal alignment isn't worth the upkeep.

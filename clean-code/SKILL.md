---
name: clean-code
description: >-
  Apply Robert C. Martin's (Uncle Bob's) Clean Code, Clean Architecture, and
  Clean Craftsmanship principles when writing, refactoring, reviewing, or
  designing software. Use this skill whenever the user is writing or editing
  functions, classes, or modules; asks for a code review or to "check the
  quality" / "make this cleaner"; mentions naming, function size, SOLID, code
  smells, refactoring, technical debt, testability, or concurrency; OR is
  designing system architecture, module boundaries, dependencies, or project
  structure. Trigger even when the user never says "clean code" — e.g. "review
  this PR", "refactor this function", "is this well-designed", "how should I
  structure this service", "this class is doing too much", "how do I make this
  testable". Covers meaningful names, small single-purpose functions, SOLID,
  error handling, TDD and simple design, component cohesion/coupling, the
  Dependency Rule, concurrency, cross-cutting concerns, and craftsmanship —
  always proportionate to the problem.
license: MIT
compatibility: Prompt-only; no runtime dependencies. Language-agnostic — the principles apply to any codebase the agent can read. python3 (stdlib only) is required to run the outcome eval, not the skill.
x-spec-version: 1.0
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "clean-code,refactoring,solid,code-review,architecture,naming,tdd,code-smells,craftsmanship"
---

# Clean Code, Clean Architecture & Clean Craftsmanship

A working guide to Robert C. Martin's principles. The aim is not to recite
rules — it's to make code that the next person (often you, in six months) can
read, change, and trust without fear. Messy code always slows you down; the
only question is when. The fastest way to ship is to keep the code clean.

But *clean* means **as simple as the problem allows**, never as elaborate as
possible. The single most common way to misapply these principles is to
over-build — wrapping a 20-line script in layers it doesn't need. Read
**Right-size the solution** below before you reach for any pattern.

This file (L1) is always active. Apply it to every function, class, or module
you write or review. The deeper material lives in `references/` — pull a file
in only when the task calls for it (see **Going deeper** at the bottom).

## Orient before you act

Before writing or changing code, answer five questions:

1. **One responsibility?** What is the *single* thing this unit is responsible
   for? (SRP)
2. **Knows only what it needs?** Does it reach past its immediate collaborators?
   (Law of Demeter + ISP)
3. **Dependencies point inward?** Does business logic stay free of frameworks,
   DBs, and I/O? (Dependency Rule)
4. **Readable in 30 seconds?** Would a new developer understand this quickly?
5. **Proportionate?** Is the structure I'm adding justified by the problem's
   real complexity — or am I over-building? (Needless complexity is a smell.)

## Operating modes

Pick the mode that fits the task. Most requests are one of these.

- **Writing / refactoring** — Produce clean code as you go. Let the L1 rules
  shape it. When a non-obvious choice is driven by a principle, say so in one
  short line ("split into two functions — the flag argument was doing two
  jobs") rather than narrating every rule. Apply the Boy Scout Rule: leave any
  file you touch a little cleaner, but keep cleanups scoped to what you're
  already changing.
- **Reviewing** — Produce a structured review (format below). Anchor each
  finding to a principle or smell and a concrete fix. Lead with what matters
  most; don't drown real issues in nitpicks.
- **Architecting** — Design around the Dependency Rule and use cases. Read
  `references/architecture.md` first. The structure should scream the domain,
  not the framework — *if* the system is large enough to warrant layers.

When in doubt, optimize for the reader, not the writer.

### Right-size the solution

Clean ≠ elaborate. The goal is the *simplest* design that stays readable and
changeable — not the most layered one. Match structure to the problem in front
of you:

- A short script with one dependency doesn't need ports, adapters, and a
  composition root. Inject the one seam that makes it testable, and stop.
- Reserve full Clean Architecture layering for systems with real, diverging
  reasons to change — multiple delivery mechanisms, swappable infrastructure,
  long-lived business rules.
- **Over-engineering is itself a code smell** (Needless Complexity). Abstraction
  the problem doesn't demand makes code *harder* to read, not easier.

Escalate deliberately: solve the actual problem cleanly first; add a layer only
when a concrete pressure justifies it (a second caller, a swapped DB, an
untestable seam). When you reach for a pattern, name in one line the pressure
it answers. If you can't name one, don't add it.

**Example — the shape matters, not the language:**
> Task: "read a CSV and return the total of the `amount` column."
>
> ❌ Over-built: an `AmountSummer` use case, a `CsvSourcePort` interface, a
> `FileCsvAdapter`, a DTO, and a composition root to wire them — five files to
> sum a column.
>
> ✅ Proportionate: one well-named function that takes the rows (or a path) and
> returns the total; if it must be testable, pass the source in as a parameter.
> One seam, no ceremony.
>
> Same discipline as the layered use-case example in `architecture.md` — but
> that problem earned its layers and this one doesn't.

### Deliverable discipline

Produce the code (and tests when warranted) plus a short rationale. Don't pad
the deliverable with READMEs, migration guides, checklists, or before/after
essays unless the user asked — that volume is clutter that buries the actual
change. A focused diff with a two-line explanation beats a six-file dump.

### How to read the examples here

This skill uses examples because they teach more reliably than abstract advice —
but every example is **illustrative, not a template**:

- The **principle and the shape** are the point, never the syntax. A snippet in
  one language is showing a *move* (split this, inject that, rename this), not a
  language to adopt.
- **Write in the user's language and follow its idioms and conventions** —
  translate the idea, don't transliterate the example.
- **Don't copy a structure the task doesn't warrant.** An example that shows
  ports and adapters is showing what they look like *when a problem needs them*
  (see Right-size), not a layout to impose everywhere.

## L1 — The non-negotiables

| Rule | One-line | Violation signal |
|---|---|---|
| **Meaningful names** | Names reveal intent | `d`, `tmp`, `data`, `obj` |
| **Small functions** | Do ONE thing, do it well | Function > ~20 lines |
| **No side effects** | A function either DOES or ANSWERS, never both | Hidden state mutation |
| **DRY** | Don't repeat yourself | Copy-paste with minor edits |
| **No magic numbers** | Name your literals | `if (x > 86400)` |
| **Fail fast** | Validate early, throw exceptions not codes | Returning `-1`/`null` on error |
| **Boy Scout Rule** | Leave code cleaner than you found it | No cleanup before commit |

### Function rules

1. **Small** — rarely exceed ~20 lines; aim for 5–10.
2. **Do one thing** — if you can extract a sub-function with a name that isn't
   just a restatement, the original did more than one thing.
3. **One level of abstraction per function** — don't mix high-level policy with
   low-level detail in the same body.
4. **No flag arguments** — `render(true)` hides two behaviors; split into
   `renderForSuite()` and `renderForPage()`.
5. **Fewer arguments** — 0 is best, 1 good, 2 fine, 3 needs justification. More
   than that usually means a missing object. Avoid output arguments.
6. **Command-Query Separation** — change state *or* return a value, not both.

### Naming checklist

- Name reveals intent (`elapsedTimeInDays`, not `d`).
- Pronounceable and searchable; avoid cryptic abbreviations and disinformation.
- Classes are nouns (`Customer`, `Account`); methods are verbs (`postPayment`,
  `save`).
- No type encodings (`strName`, `iCount`), no noise words (`theData`, `aInfo`).
- One word per concept across the codebase — pick `get` *or* `fetch` *or*
  `retrieve`, not all three.

### Simple design (Kent Beck's four rules, in priority order)

A design is simple to the extent that it:
1. **Passes all the tests** — it works, verifiably.
2. **Reveals intent** — names and structure say what it does.
3. **Has no duplication** — one fact in one place (DRY).
4. **Minimizes elements** — no more classes, methods, or abstraction than rules
   1–3 require.

Rules 2–4 are applied by refactoring once it works. **Rule 4 is the guard
against over-design**: when adding structure, ask whether 1–3 actually demand
it. If not, fewer moving parts wins.

## Review report format

When reviewing, use this structure so findings are scannable and actionable:

```
## Clean Code Review

**Overall:** <1–2 sentences on the code's health and the headline issue.>

**Findings** (most important first):

1. **<principle or smell> — `path/to/file:line`**
   - What: <the specific problem>
   - Why it matters: <consequence — what breaks or slows down later>
   - Fix: <concrete change, with a snippet if it clarifies>

**Already good:** <call out genuine strengths — reviews aren't only negative.>
```

Match the depth to the request: a quick "does this look ok?" wants the top one
or two findings, not an exhaustive audit.

## Decision trees

**Writing a function**
```
> ~20 lines?                          → split it
Does more than one thing?             → split it
> 3 arguments?                        → group into an object, or split
Modifies state AND returns a value?   → separate command from query
Has a flag argument?                  → split into two named functions
Has side effects?                     → make them explicit; ideally eliminate
```

**Naming something**
```
Single letter (outside a loop index)? → rename
An abbreviation?                      → expand it
Encodes the type (strName, iCount)?   → remove the encoding
Reveals what it is without comment?   → good
Consistent with similar concepts?     → align it
```

**Designing a dependency**
```
Domain importing a framework/ORM?     → invert it (define an interface inward)
Dependency points outward?            → reverse it via an interface
Cycle between components?             → break with DIP or extract a component
Depending on a concrete you don't own?→ wrap it
Inner layer names something outer?    → violation
Am I adding a layer with no pressure? → don't (right-size)
```

**Reviewing architecture**
```
Test all business logic without the web server? → good
Swap the DB without touching business rules?    → good
Folder structure screams the domain?            → good
Dependency graph is a DAG (no cycles)?          → good
Data crosses boundaries as plain DTOs?          → good
Layering matches the system's real complexity?  → good (else over-built)
```

## Code smells → fixes

| Smell | Fix |
|---|---|
| **Rigidity** — small change cascades | SRP + OCP |
| **Fragility** — breaks in surprising places | DIP + better boundaries |
| **Immobility** — can't reuse without dragging everything | ISP + component principles |
| **Needless complexity** — over-engineered | YAGNI; simplify; right-size |
| **Needless repetition** | DRY; extract |
| **Opacity** — hard to read | better names; smaller functions |
| **Train wreck** `a.b().c().d()` | Law of Demeter |
| **God class** — does everything | extract classes by actor |
| **Feature envy** — fixated on another's data | move method to the data |
| **Long parameter list** | group into an object |
| **Divergent change** — edited for many reasons | split the class (SRP) |
| **Shotgun surgery** — one change, many classes | consolidate (OCP + SRP) |
| **Data clumps** — fields always travel together | extract a class |
| **Primitive obsession** | use value objects |
| **Switch on type codes** | replace with polymorphism (OCP) |

The full Clean Code "Smells and Heuristics" catalog (G1–G36, plus comment,
function, name, and test heuristics) is in `references/smells-and-heuristics.md`
— consult it for a thorough review.

## Going deeper

Read the reference that matches the decision in front of you — don't load them
all by default.

- **`references/principles.md`** — L2. SOLID in depth; naming, functions, and
  **classes** (cohesion, encapsulation); comments; error handling; formatting.
  *Read when:* shaping a class, naming, handling errors, or splitting
  responsibilities.
- **`references/testing.md`** — L2. TDD (the three laws + craftsmanship
  disciplines), FIRST, clean tests, simple design, boundaries & learning tests,
  acceptance tests and Definition of Done. *Read when:* writing tests, making
  code testable, or wrapping a third-party dependency.
- **`references/component-design.md`** — L3. Component cohesion (REP/CCP/CRP)
  and coupling (ADP/SDP/SAP); objects vs. data structures; Law of Demeter.
  *Read when:* deciding module/package boundaries.
- **`references/concurrency.md`** — L3. Why concurrency is hard; defense
  principles; known execution models; testing concurrent code. *Read when:*
  threads, async, shared state, or race conditions are involved.
- **`references/architecture.md`** — L4. Clean Architecture layers, the
  Dependency Rule, use cases, entities, screaming architecture, cross-cutting
  concerns, Humble Object, the Main/composition root, partial boundaries, and
  what counts as a swappable "detail." *Read when:* designing system structure
  or service boundaries (and only when the system is large enough to need it).
- **`references/craftsmanship.md`** — L5. Professional discipline: the only way
  to go fast is to go well, saying no, commitment language, estimation, katas,
  acceptance testing, handling pressure, the programmer's oath. *Read when:* the
  conversation turns to schedules, estimates, technical debt, or cutting
  corners.
- **`references/smells-and-heuristics.md`** — the complete Clean Code ch. 17
  catalog. *Read/scan when:* doing a thorough review or hunting for what's wrong
  with a piece of code.
- **`references/glossary.md`** — quick definitions of the vocabulary.

# Smells and Heuristics

A catalog of code smells and heuristics from Chapter 17 of Robert C. Martin's *Clean Code* — meant to be *scanned* during review, not read top to bottom.

Categories: [Comments](#comments) · [Environment](#environment) · [Functions](#functions) · [General](#general) · [Names](#names) · [Tests](#tests)

## Comments

Comments are at best a necessary evil; these flag the ones that hurt more than they help.

- **C1 — Inappropriate Information:** Don't put info better held in source control, issue trackers, or other docs into comments; keep comments to technical notes about the code.
- **C2 — Obsolete Comment:** A comment that has drifted from the code is misleading; update or delete it before it rots further.
- **C3 — Redundant Comment:** A comment that just restates what the code plainly says is noise (e.g. `i++; // increment i`); remove it.
- **C4 — Poorly Written Comment:** If you must comment, make it brief, correct, and grammatical — a comment worth writing is worth writing well.
- **C5 — Commented-Out Code:** Delete it; source control remembers it, and leaving it makes readers afraid to touch the surrounding lines.

## Environment

The project should be trivial to build and test from a clean checkout.

- **E1 — Build Requires More Than One Step:** You should check out and build with a single trivial command, not a sequence of manual incantations.
- **E2 — Tests Require More Than One Step:** You should run all tests with one button or one command — anything harder means tests get skipped.

## Functions

Functions are the verbs of the system; keep their signatures small and honest.

- **F1 — Too Many Arguments:** Prefer zero, one, or two arguments; three is suspect and more than three needs strong justification.
- **F2 — Output Arguments:** Arguments are for input — a reader expects results to come back as the return value, so mutate `this` or return instead of writing through arguments.
- **F3 — Flag Arguments:** A boolean argument announces the function does more than one thing; split it into separate functions.
- **F4 — Dead Function:** A method never called is clutter; delete it and let source control keep the history.

## General

A grab-bag of the most common smells; each entry names a fix.

- **G1 — Multiple Languages in One Source File:** Minimize the number of languages per file (HTML, JS, XML, etc.) since mixing them confuses readers and tooling.
- **G2 — Obvious Behavior Is Unimplemented:** Follow the Principle of Least Surprise — implement the behavior a reader would obviously expect from a name (e.g. `Day.valueOf("Monday")`).
- **G3 — Incorrect Behavior at the Boundaries:** Don't trust your intuition about edge cases; write tests for every boundary and corner condition.
- **G4 — Overridden Safeties:** Don't turn off compiler warnings, failing tests, or other safety checks — you'll pay for the suppressed risk later.
- **G5 — Duplication:** Every duplicated chunk is a missed abstraction (DRY); replace it with a method, polymorphism, or a pattern like Template Method.
- **G6 — Code at Wrong Level of Abstraction:** Keep high-level concepts and low-level details in separate, properly layered places so abstractions don't leak.
- **G7 — Base Classes Depending on Their Derivatives:** Base classes should know nothing about their subclasses; a dependency the other way breaks the separation.
- **G8 — Too Much Information:** Keep interfaces small and tight — expose few methods and little data so coupling stays low.
- **G9 — Dead Code:** Code that never executes (unreachable branches, unused utilities) drifts out of date; find and delete it.
- **G10 — Vertical Separation:** Declare variables and functions close to where they're used; keep local variables just above their first use and private functions just below their callers.
- **G11 — Inconsistency:** Do similar things the same way — a consistent convention makes code predictable and easy to read.
- **G12 — Clutter:** Remove empty constructors, unused variables, meaningless comments, and dead methods so the meaningful code stands out.
- **G13 — Artificial Coupling:** Don't bind unrelated things together (e.g. a general `enum` inside a specific class); place each item where it logically belongs.
- **G14 — Feature Envy:** A method that fiddles with another object's data envies that class; move the behavior to where the data lives.
- **G15 — Selector Arguments:** Like flag arguments, any selector (boolean, enum, int) that picks behavior inside a function should become separate functions.
- **G16 — Obscured Intent:** Don't write so densely that meaning is lost; favor expressive code over cramped, magic-laden one-liners.
- **G17 — Misplaced Responsibility:** Put code where a reader would naturally expect it, following the Principle of Least Surprise, not just where it's convenient.
- **G18 — Inappropriate Static:** Prefer nonstatic methods; make a function static only when there's no chance you'd want polymorphic behavior.
- **G19 — Use Explanatory Variables:** Break a complex expression into well-named intermediate variables so the calculation reads itself.
- **G20 — Function Names Should Say What They Do:** If you must read the implementation to know what a call does, rename the function.
- **G21 — Understand the Algorithm:** Don't stop at "it passes the tests"; understand why the code works before declaring it done.
- **G22 — Make Logical Dependencies Physical:** If one module depends on another, make that dependency explicit (e.g. ask for the value) rather than assuming it.
- **G23 — Prefer Polymorphism to If/Else or Switch/Case:** Repeated type-switching is a smell; use polymorphism and apply the "ONE SWITCH" rule.
- **G24 — Follow Standard Conventions:** Adhere to the team's agreed conventions for layout, naming, and structure — and everyone follows them.
- **G25 — Replace Magic Numbers with Named Constants:** Hide raw literals behind named constants so intent is clear (note some constants like `0`/`1` are self-explanatory).
- **G26 — Be Precise:** Make decisions deliberately — handle nulls, currency rounding, locking, and edge cases exactly, not by hope.
- **G27 — Structure over Convention:** Enforce design decisions with structure (e.g. abstract methods) rather than relying on naming conventions people can ignore.
- **G28 — Encapsulate Conditionals:** Extract a boolean expression into a well-named function so `if (shouldBeDeleted(timer))` replaces a cryptic predicate.
- **G29 — Avoid Negative Conditionals:** Positive conditionals (`if (buffer.shouldCompact())`) read more easily than negated ones.
- **G30 — Functions Should Do One Thing:** Split a function that performs several distinct steps into smaller single-purpose functions.
- **G31 — Hidden Temporal Couplings:** When calls must happen in a certain order, make that order explicit by passing the result of one into the next.
- **G32 — Don't Be Arbitrary:** Give code a structure others can understand and rely on, so they won't feel free to break your conventions.
- **G33 — Encapsulate Boundary Conditions:** Capture `+1`/`-1` boundary logic in a named variable so the `off-by-one` reasoning lives in one place.
- **G34 — Functions Should Descend Only One Level of Abstraction:** Statements in a function should all sit one level below the function's name, not mix high- and low-level detail.
- **G35 — Keep Configurable Data at High Levels:** Define configuration constants and defaults at a high level and pass them down, rather than burying them in low-level functions.
- **G36 — Avoid Transitive Navigation:** Follow the Law of Demeter — talk to immediate collaborators, not `a.getB().getC().doSomething()` chains.

## Names

Good names do most of the documentation; these heuristics keep them sharp.

- **N1 — Choose Descriptive Names:** Names carry most of a program's readability, so choose them carefully and change them when they no longer fit.
- **N2 — Choose Names at the Appropriate Level of Abstraction:** Name things by concept, not implementation, so the name survives changes to how it works.
- **N3 — Use Standard Nomenclature Where Possible:** Reuse known patterns, idioms, and domain terms (e.g. `Decorator`, `toString`) so names communicate established meaning.
- **N4 — Unambiguous Names:** Pick a name that leaves no doubt what it does, even if it's longer than a vague short one.
- **N5 — Use Long Names for Long Scopes:** Tiny scopes can use short names like `i`; the wider the scope, the longer and clearer the name must be.
- **N6 — Avoid Encodings:** Don't append type or scope prefixes (Hungarian notation, `m_`, `I` for interface); modern tooling makes them needless clutter.
- **N7 — Names Should Describe Side-Effects:** Name a function for everything it does (e.g. `createOrReturnOos`), not just its happy-path return.

## Tests

Tests are part of the system; treat their quality as seriously as production code.

- **T1 — Insufficient Tests:** Keep adding tests until you can't think of anything more that could plausibly break.
- **T2 — Use a Coverage Tool:** Coverage tools reveal the gaps and untested branches your eyes miss.
- **T3 — Don't Skip Trivial Tests:** They're easy to write and their documentary value outweighs the cost.
- **T4 — An Ignored Test Is a Question about an Ambiguity:** An `@Ignore`d or commented-out test marks an unresolved question about expected behavior — make it explicit.
- **T5 — Test Boundary Conditions:** The middle is usually fine; bugs hide at the boundaries, so test them specifically.
- **T6 — Exhaustively Test Near Bugs:** Bugs cluster, so when you find one, test thoroughly around it for its neighbors.
- **T7 — Patterns of Failure Are Revealing:** Ordering test cases sensibly can expose patterns in which inputs fail and point straight at the cause.
- **T8 — Test Coverage Patterns Can Be Revealing:** Looking at which lines pass or fail across passing tests can reveal why a failing case breaks.
- **T9 — Tests Should Be Fast:** Slow tests get run less, so keep them fast enough that no one hesitates to run them.

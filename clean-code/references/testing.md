# L2 — Tests, TDD & Design Discipline

Activate when **refactoring any existing code** — the refactor-on-green rule
below is what keeps a cleanup from silently breaking behaviour — and when
writing tests, making code testable, wrapping a third-party dependency, or
deciding what "done" means.

> Code rots because we're afraid to clean it. A trustworthy test suite removes
> the fear — it's what makes all the other clean-code refactoring safe.

## The three laws of TDD

1. Write no production code until you have a failing test.
2. Write no more of a test than is sufficient to fail (a compile error counts).
3. Write no more production code than is sufficient to pass the failing test.

These lock you into a ~30-second red→green→refactor cycle. The discipline isn't
the point; the trustworthy suite it produces is.

## FIRST — properties of clean tests

| Property | Meaning |
|---|---|
| **Fast** | Run in milliseconds — slow suites stop being run. |
| **Independent** | No test depends on another; any order works. |
| **Repeatable** | Same result in any environment; no flakiness. |
| **Self-validating** | A boolean pass/fail; no manual log-reading. |
| **Timely** | Written just before the code that satisfies them. |

## What makes a test clean

- **One concept per test** — possibly several asserts, but a single idea. Name
  tests so they read as documentation:
  `methodName_stateUnderTest_expectedBehavior`.
- **Tests are first-class code.** They demand the same care as production code —
  if test code rots, it stops protecting you. But they optimize for readability
  over raw efficiency.
- **Build a domain-specific testing API.** Helper functions/builders that let
  each test read as intent ("given an account with…") rather than setup noise.
- Tests *are* the documentation of intended use.

## The deeper TDD discipline

- **As the tests get more specific, the code gets more generic.** Each new test
  should push you to remove a special case, not bolt one on.
- **Fake it, then make it real.** Pass the first test with a constant if that's
  all it demands; **triangulate** — add a second, different case that forces the
  real implementation.
- **Refactor on green only.** Never refactor with a failing test; get back to
  green, then clean up. Keep the two activities separate.

## Boundaries & learning tests

Code meets the outside world (third-party libraries, network, OS) at
*boundaries*. Keep them clean:

- **Wrap external code** behind an interface *you* own, shaped to your needs —
  not the vendor's API. This contains the blast radius when the dependency
  changes and keeps your domain free of its types.
- **Write learning tests** to characterize an unfamiliar library: small tests
  that assert how you *believe* it behaves. They document your assumptions and
  catch breaking changes when you upgrade.
- **Program to interfaces you control**, even for code that doesn't exist yet —
  define the seam, test against a fake, integrate the real thing later.

This is what makes hard-wired dependencies (real DB, live API, system clock)
testable: invert them behind a small interface and pass a fake in tests.

## Acceptance tests & Definition of Done

- **Specify behavior with the business**, in terms they can confirm, and
  automate those specs. Acceptance tests describe *what* from the outside;
  unit tests describe *how* from the inside.
- **"Done" means passing automated acceptance tests** — not "I finished typing
  the code." A shared, unambiguous Definition of Done prevents the "90% done for
  three weeks" trap.

## Simple design (recap)

Once tests pass, refactor toward Kent Beck's four rules (in L1): passes tests →
reveals intent → no duplication → fewest elements. Rule 4 guards against
over-design — don't add abstraction the first three rules don't demand.

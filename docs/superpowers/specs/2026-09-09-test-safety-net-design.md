# test-safety-net — design

**Status:** approved in brainstorming, not yet implemented
**Date:** 2026-09-09

## The gap

A repo has no meaningful tests, so nobody trusts an agent to change it. Three skills in this
collection already surround that problem and none of them solves it:

| Skill | Does |
|---|---|
| `agent-ready-rails` | *Audits* — rail R1 "runnable verifiers" scores low |
| `verifier-installer` | Installs the **loop**: format/build/test commands + CI, and a *smoke test* if none exists (`SKILL.md:64`) |
| **`test-safety-net`** ← this | Fills that loop with **real** tests |
| `clean-code` | *Judges* the tests you now have (`references/testing.md`) |

`verifier-installer` deliberately stops at one placeholder test to make the loop runnable. That
is the seam this skill occupies.

## Purpose

Build the change-detector that unblocks agent work on an untested codebase.

This is **not** a correctness audit. Its output is a net that catches unintended behaviour
change, plus an honest account of what it could not net and why.

### Non-goals

- **Coverage percentage.** Never a target, never reported as a success metric. Chasing it is
  precisely how test generators produce green noise.
- **Correctness verdicts.** Where current behaviour looks wrong, the skill pins it *and* reports
  the suspicion. It never silently blesses it as correct, and never leaves the suite red.
- **Modifying source.** See "Invariants".

## Workflow

Deliberately the same five-step shape as `verifier-installer`, so the pair reads as siblings.

1. **Detect** the stack and — critically — *how to run one single test*, not just the suite.
2. **Rank** the risk surface, with a testability triage (below). Shipped tooling, stdlib + git only.
3. **Confirm with the user** before writing anything: the top N (default 10, adjustable) and the
   ranked remainder. Hard gate, matching `verifier-installer` step 2.
4. **Write and prove**, one unit at a time: write the test with a deliberately WRONG expected
   value → require **RED** → correct it → require **GREEN**. A test that never went red is
   discarded, not shipped.
5. **Report** — what was pinned, at which boundary, which kind, suspected bugs, what could not be
   netted, and the ranked remainder so the next run resumes where this one stopped.

## The two disciplines

These are what distinguish this from a generic "write me some tests" prompt.

**1. Every test declares its kind.**
- `characterization` — pins what the code does now.
- `specification` — asserts what it *should* do, derived from a real source (docs, types,
  contracts, an issue).

Where the two disagree, the test pins current behaviour **and** the disagreement is reported as a
suspected bug with its location and the source it contradicts.

**2. Every test is proved able to fail** before it counts — the red→green loop in step 4.

The proof is at the **assertion**, not the source: write the wrong expectation, confirm RED,
correct it, confirm GREEN. This never touches the user's code, so there is no restore path to get
wrong.

> **Stated limit, to be carried into SKILL.md verbatim.** This proves the assertion binds to real
> output from the unit. It does *not* prove the test detects every behavioural change, which is
> what true mutation testing would give. Do not oversell it.

## Testability triage

The load-bearing addition. Code that lacks tests usually lacks them *because* it is not testable,
so a design that assumes callable units fails on the majority case — or worse, writes a "test"
that hits a live database.

The trap is Feathers' Legacy Code Dilemma: *to change code safely you need tests; to get tests you
often must change code.* The resolution here is **never cut inward, climb outward.** Refactoring
untested code to make it testable inverts the safety property this skill exists to provide.

Every discovered unit lands in exactly one tier:

| Tier | Situation | Action |
|---|---|---|
| **1 — direct** | deps passable, output returnable | Unit test. |
| **2 — wider boundary** | unit does I/O, but its module / handler / CLI can be driven with the boundary controlled | Pin at that boundary and **name it** in the report. Not a unit test — a narrow characterization test. As a change-detector, equally good. |
| **3 — needs a seam** | no honest boundary without adding code | Report the smallest **additive** seam (sprout/wrap: add code beside, never rearrange) and hand to `clean-code`. Never performed by this skill. |
| **4 — not reachable** | global state, work in constructors, deep branch soup | Prioritized refactor list with the reason. |

**Boundary controls for Tier 2**, by kind of I/O:

| I/O | Control |
|---|---|
| filesystem | temp dir |
| clock / randomness | the language's own freeze/seed hooks |
| database | in-memory or throwaway file — **but see below: declined by default** |
| HTTP | **`mockstar-mock`** — already in this collection — **but see below: declined by default** |

**Amended during implementation: database and HTTP are declined by default, not routine Tier 2.**
This table was written aspirationally; the shipped classifier buckets database, HTTP and subprocess
as *uncontrollable*, so they land in Tier 3 and no test is written. That is the correct default —
controlling a database or an HTTP boundary means standing up a fake, which is a far larger
intervention than pointing a write at a temp dir, and defaulting to "decline" is what keeps the
never-performs-real-I/O invariant cheap to hold. A human may still promote such a unit to Tier 2
and control the boundary, but only through the recorded-promotion discipline — never silently.
Filesystem, clock, randomness and environment remain routine Tier 2 controls.

**Tiers 3 and 4 are output, not failure.** A risk-ranked "here is what blocks testing, and the
smallest change that unblocks it" list is the missing input to `clean-code`, and often worth more
to a human than the tests themselves.

`clean-code` already speaks this vocabulary — `SKILL.md:101` ("Inject the one seam that makes it
testable, and stop") and its "One seam, no ceremony" rule — so the handoff needs no new concepts
on the receiving side.

## Invariants

1. **Never modifies source.** Only creates test files; **appends** to an existing test file, never
   overwrites. This is the invariant that makes the skill safe to run unattended on a repo nobody
   trusts yet, and it is why Tier 3 seams are reported rather than applied.
2. **Never writes a test that performs real I/O.** If the boundary cannot be controlled, the unit
   drops to Tier 3 and no test is written. The skill declines rather than faking success.

   **How this is enforced — amended 2026-09-09 during implementation.** The original design
   assumed the static triage enforced this. It cannot. Two review rounds found seven distinct
   constructions the classifier called safe that would have reached real I/O: aliased imports, a
   same-module helper, an argument default, a class body, a base-class expression, a method call,
   and a locally-shadowed import. Static analysis cannot decide reachability in Python —
   `getattr`, dispatch tables and dynamic imports are undecidable — so each fix round closed
   holes and revealed more.

   So triage is demoted to a **filter**: it ranks candidates and declines obvious hazards. The
   invariant is enforced at **runtime**, during the red→green proof: the candidate test runs with
   I/O blocked (`socket.socket`, `subprocess.Popen` and the database drivers monkeypatched to
   raise). A test that reaches the network errors instead of passing, its unit is reclassified
   Tier 3, and no test is kept. That makes this invariant a property the tooling enforces rather
   than one the prose asserts.

   **The guard is tier-aware.** Amended again after a review found the first version had a hole:
   it blocked only the UNCONTROLLABLE groups (socket, subprocess, DB drivers), so a unit the
   filter called Tier 1 — "no I/O at all" — that actually reached `open(path, "w")` was not
   backstopped, and its generated test would have written a real file. So:

   - **Tier 1 candidate** (claimed to do no I/O): block *everything* — filesystem, clock and
     randomness as well as network, subprocess and DB. The unit claimed to touch nothing, so any
     touch falsifies the classification. Reclassify and discard the test.
   - **Tier 2 candidate** (I/O at a controlled boundary): block only the uncontrolled groups. The
     controllable ones are deliberately faked by the test — a temp dir and a frozen clock are the
     point, not a violation.

   **The guard patches the syscall layer, not the convenience wrappers.** A third amendment,
   after a review found five escapes that shared one root cause: real I/O reached through an entry
   point *underneath* the Python names the guard was scoped to patch. `os.open` is not the builtin
   `open`; `os.posix_spawn` never goes through `subprocess.Popen`; `mmap`, `io.FileIO` and a
   `ctypes` C call bypass Python-level file objects entirely. So the guard patches the lowest
   available layer — the `os` primitives (`open`, `write`, `posix_spawn`, `spawn*`, `fork`),
   `io.FileIO`, `mmap.mmap` and `socket.socket` — not only the ergonomic wrappers above them.

   **The guard is installed BEFORE the module under test is imported.** Otherwise a module doing
   I/O at import time runs its side effects during `import unit_module`, before any fixture body
   executes — once per proof run, for every module, regardless of tier. In pytest that means a
   `conftest.py` loaded ahead of collection, not a fixture inside the generated test.

   **The split between filter and guard is deliberate, and neither alone is sufficient.** The
   guard covers what the filter cannot see: dynamic dispatch, unresolvable receivers, cross-module
   indirection. The filter must cover what the guard structurally *cannot*: `os.execv` and friends
   replace the entire process image, taking every in-process patch with them, so no runtime guard
   can survive one. Process-replacing calls therefore have to be declined statically — they are in
   the marker table as uncontrollable, and that is not redundancy with the guard, it is the one
   case the guard cannot reach.

   Residual, stated plainly: a test that spawns a subprocess which itself dials out escapes an
   in-process guard. That is a smaller residual than trusting static analysis alone, and naming
   it is the point.
3. **Never ships an unproven test.** A test that did not go red is discarded and listed under
   *could not prove*.
4. **Never leaves the suite red.** End state is a green suite plus suspected bugs in the report.
   A red generated test is a bug in this skill.
5. **Hard gate before writing** (step 3).

## Shipped tooling

### `assets/rank_risk.py`

Stdlib + git only. No MCP, no network, no third-party packages. Emits deterministic JSON.

- **Churn** — `git log --format= --name-only --since=<window>` → change count per file. Exact,
  and the best available proxy for "what an agent will touch".
- **Blast radius** — inbound reference count: static scan for each discovered symbol's name across
  the repo, excluding its own definition site. **Approximate, and labelled as such in the output**
  — it cannot distinguish a call from a comment.
- **Testability tier** — per the triage above.
- **Already-covered detection** — a symbol some existing test already exercises never enters the
  ranking.
- **Score** — normalised churn × normalised inbound refs; ties broken by path sort. Determinism is
  non-negotiable: the eval asserts byte-identical output across runs, as `detect_stack.py`
  already does.

A real call-graph tool would compute the blast-radius half better. The report names that as an
**optional** upgrade the user may already have. It is never a dependency, and nothing in the
skill or its eval requires one.

### `references/stacks.md`

One row per stack, carrying the four genuinely language-specific facts:

| stack | find units | tests go | framework | **run ONE test** |
|---|---|---|---|---|
| python | module-level `def`/`class` | `tests/` or `test_*.py` beside | pytest / unittest | `pytest path::name` |
| node | exported symbols | `__tests__/` or `*.test.ts` | vitest / jest | `vitest run -t <name>` |
| go | exported funcs | `*_test.go` beside | stdlib `testing` | `go test -run '^Name$' ./pkg` |
| rust | `pub fn` | `#[cfg(test)]` mod or `tests/` | stdlib `#[test]` | `cargo test name -- --exact` |

The last column is load-bearing: step 4's proof is impossible without single-test invocation.

**The `make` case.** `verifier-installer` treats make as a stack because it only needs a test
*command*. Here it cannot be — you cannot author a "make unit test", and `make test` usually
cannot run a single test. So make is handled as a **runner over an underlying language**: detect
the real language for authoring, and fall through to the native runner for the proof loop even
when make fronts the suite. If no single-test invocation can be found, the unit is reported as
*could not prove* and no test is written.

### Duplicated detection, guarded

Skills install independently (`npx skills add --skill <name>`), so this cannot import
`verifier-installer/assets/detect_stack.py`. That is the same duplication as `okf.py` /
`garden.py` — which diverged silently while claiming to agree, and was only caught in the
2026-09 review.

Therefore: ship its own detector **plus a cross-tool agreement test** that runs when the sibling
is installed and skips cleanly when it is not. Apply the fix pattern rather than re-earn the bug.

## Data flow

```
repo
  → detect stack + single-test invocation
  → rank_risk.py  (churn × inbound refs, + testability tier, - already covered)
  → ranked JSON
  → USER CONFIRMS top N                       ← hard gate
  → per unit:  write test with wrong expectation
               run → require RED
               correct expectation
               run → require GREEN
  → report
```

## Deliverable — the report

```
## Test safety net: <repo>  (stack: python · 3 added, 1 unproven, 1 needs a seam)

| unit                    | tier            | kind             | test file            | proved    |
|-------------------------|-----------------|------------------|----------------------|-----------|
| billing/refund.py:apply | 1 direct        | characterization | tests/test_refund.py | RED→GREEN |
| api/handler.py:post     | 2 module (fs)   | characterization | tests/test_api.py    | RED→GREEN |
| auth/hash.py:verify     | 1 direct        | specification    | tests/test_hash.py   | RED→GREEN |

### Suspected bugs (pinned, NOT blessed)
- billing/refund.py:41 — pinned returns 0 for a negative amount; the docstring says it raises.

### Could not prove (discarded, not shipped)
- cache/keys.py:build — no single-test invocation found behind `make test`

### Not netted — needs a seam (hand to clean-code)
- db/pool.py:acquire (tier 3) — constructs its connection inline; smallest seam: accept an
  injected factory, default to current behaviour.

### Ranked remainder — next run starts here
1. db/pool.py:acquire (churn 14, inbound ~31 [approx])
```

No coverage percentage anywhere.

## Security surface

Captured output from running the user's code is embedded into generated test files. That is
**code generation from program output**, and it is the one real injection surface here. Values
must be emitted as safely-escaped literals, never interpolated as code: a captured string
containing a quote, a newline, or a backslash must not become executable. This gets a dedicated
test and a negative eval fixture.

## How it is graded

Per `docs/eval-standard.md`: deterministic harness → skill tooling → model-free grader, negative
fixtures mandatory, offline, stdlib-only, bounded.

**The eval grades the deterministic half.** Five tiny fixture repos (one per stack), each a real
git repo with churn history, several units, and one already-covered unit, run through
`rank_risk.py`:

- ranking is byte-identical across repeated runs
- an already-covered unit is excluded
- a high-churn / high-fan-in unit outranks a low one
- **NEGATIVE:** no git history → churn degrades to 0, no crash
- **NEGATIVE:** a unit that opens a network connection in its constructor is triaged **Tier 3**
  and gets **no test written** — the skill must decline rather than fake success
- **NEGATIVE:** a make-fronted repo with no discoverable single-test invocation reports the
  blocker instead of claiming success
- **NEGATIVE:** a captured value containing quotes / newlines / backslashes round-trips as a
  literal and cannot execute

**What the eval cannot grade**, stated plainly rather than implied away: whether the model writes
*good* tests. That is the limit `CLAUDE.md` already records for prompt-driven behaviour. So:

- the SKILL.md half gets artifact-level checks — does the workflow carry the RED-before-GREEN
  step? does the report template carry the `tier` and `kind` columns?
- the behavioural claim gets a **manual documented model eval** under
  `docs/test-safety-net/<date>-*.md`, following the blinded-arms, fresh-context-judge shape of
  `docs/crafting-self-prompting-loops/2026-07-27-typed-state-model-eval.md`
- an `ab-validate` row with `since=`, since this ships guardrails — per `CLAUDE.md`, "a new rule
  with no A/B row is a claim nobody measured"

## Cross-links to add

- `agent-ready-rails` → name this skill where R1 is weak but the loop already exists
- `verifier-installer` → hand off after the loop is installed ("the loop runs; it has one smoke
  test — fill it")
- `clean-code` → receives the Tier 3/4 seam list; and this skill points at
  `clean-code/references/testing.md` as the rubric for what a clean test looks like
- `mockstar-mock` → named as the Tier 2 HTTP boundary control

## Open items for the implementation plan

1. Flag name and shape for overriding N (the default of 10 is settled).
2. Churn window default (proposed: 6 months, with a flag).
3. Whether Tier 2 boundary detection ships as tooling or stays prompt-guided per stack. Leaning
   prompt-guided with a reference table, since it needs judgement the ranker cannot make.
4. Exact fixture layout for the five stacks — must stay tiny enough to keep the eval bounded.
5. **Phasing.** Five stacks plus the ranker, the eval and the docs is large for one plan. The
   natural split is: (a) ranker + triage + eval harness against ONE stack end to end, proving the
   red→green loop and the Tier-3 decline; then (b) each further stack as a `references/stacks.md`
   row plus its fixture set. The workflow is language-agnostic, so (b) should not reopen (a).
   Worth deciding in `writing-plans` rather than assuming one monolithic pass.

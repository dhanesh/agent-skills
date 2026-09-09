---
name: test-safety-net
description: >-
  Author unit tests into a Python or node/TypeScript codebase that has none, so an agent can change
  it safely. Use when a repo has no meaningful tests, when someone says "I don't trust an agent in
  this codebase", "add tests before we refactor", or "we need a safety net before this migration" —
  and equally for a JavaScript/TypeScript repo with no `*.test.js` to its name. Ranks units by blast
  radius and churn, then writes characterization tests that pin current behaviour — each one proved
  able to FAIL before it is kept, so the suite is a real change-detector and not green noise. Every
  test declares whether it pins behaviour or asserts a spec; suspected bugs are pinned AND reported,
  never blessed. Untestable code is triaged, not forced: it becomes a ranked seam list for
  clean-code. Never modifies your source and never writes a test that performs real I/O. Not a
  correctness audit, not a coverage chaser, and not for go or rust. Fills the loop
  verifier-installer installs; clean-code judges what comes out.
license: MIT
compatibility: >-
  Prompt-driven; the bundled ranker needs python3 (stdlib only) and, for the churn signal, the git
  CLI. Writes and proves tests on two stacks — python (pytest, falling back to unittest) and
  node/TypeScript (node 18+, `node --test`); go and rust are covered by neither and are declined
  (references/stacks.md). No pip, no npm, no network: node's optional precise discovery drives a
  `typescript` the repo already ships and never downloads one — that runs the analysed repo's own
  compiler in-process, and `--no-precise` declines it.
metadata:
  author: dhanesh
  version: "1.1.0"
  tags: "testing,characterization,legacy-code,agent-safety,pytest,node,typescript"
---

# test-safety-net

Build the change-detector that unblocks agent work on an untested codebase. This is **not** a
correctness audit: current behaviour gets pinned even where it looks wrong, and a suspected bug
gets reported rather than silently blessed as correct. The output is a net that catches
unintended behaviour change, plus an honest account of what it could not net and why — never a
coverage percentage.

## When to use

Reach for this when a repo has no meaningful tests and someone needs to change it safely: "I
don't trust an agent in this codebase," "add tests before we refactor," "we need a safety net
before this migration." It sits between two siblings and does neither of their jobs:
`verifier-installer` installs the runnable verify→repair loop (format/build/test + CI) and stops
at one placeholder test to keep the loop runnable — this skill fills that placeholder with real
tests. `clean-code` then judges what comes out (`clean-code/references/testing.md`) and receives the seam
list this skill cannot act on itself (see Tiers 3/4 below). Do not use this to chase a coverage
number, to bless current behaviour as correct, or to refactor code to make it testable — that
inverts the safety property the skill exists to provide (see Invariant 1).

**Locating this skill's helpers (do this first).** Three bundled scripts ship with this skill — the
stack-agnostic ranker (`assets/rank_risk.py`, step 2) and one runtime I/O guard per stack
(`assets/io_guard.py` for python, `assets/io_guard.js` for node, both step 4). A path written
relative to this skill will not resolve from the target repo you're working in. Resolve the base
directory once and reuse it everywhere:

```sh
SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
# If you don't have it, discover it:
SKILL_DIR=$(find ~/.claude ~/.config ~/.agents -type d -name 'test-safety-net' 2>/dev/null | head -1)
test -f "$SKILL_DIR/assets/rank_risk.py" || echo "SKILL_DIR not resolved"
test -f "$SKILL_DIR/assets/io_guard.py"  || echo "SKILL_DIR not resolved"
test -f "$SKILL_DIR/assets/io_guard.js"  || echo "SKILL_DIR not resolved"
```

The ranker and the python guard are stdlib-only python3; the node guard is dependency-free
CommonJS for node 18+. All three are offline. Do not author your own guard: the one that ships is
what Invariant 2 is enforced by, and a hand-rolled substitute that patches the wrong layer is worse
than none, because it makes the invariant look enforced when it is not.

## Workflow

1. **Detect** the stack and — critically — how to run exactly **one** test, not just the suite.
   The ranker detects the stack itself and prints its verdict and the evidence behind it on
   stderr (`note: stack=python (evidence: python=51, node=17)`); read that line rather than
   assuming, and pass `--stack` when it is wrong or when it reports a tie. Two stacks are complete
   end to end — **python** and **node/TypeScript** — because each ships a runtime guard that can
   prove the no-I/O invariant during step 4. A stack that cannot prove it declines to write rather
   than writing unproven tests, which is what go and rust do today: neither stack is registered, so
   the ranker writes `note: no stack claims <repo>` to stderr and returns an empty plan — read that
   line and stop. Consult `references/stacks.md` for the row that applies, and stop plainly, without
   improvising, for anything with no complete row. Single-test invocation is load-bearing: step 4's
   proof is impossible without it.

2. **Rank** the risk surface:

   ```sh
   python3 "$SKILL_DIR/assets/rank_risk.py" <repo> [--top-n N] [--since "6 months ago"] \
     [--stack python|node]
   ```

   Offline, stdlib + git only, deterministic. Read `references/parameters.md` for the full
   argument and output-key reference. The output's `ranked` array is what you show the user next;
   `remainder` is where the next run resumes; `not_netted` is the Tier 3/4 seam-and-refactor list
   for `clean-code`; `covered` is a flat index of already-tested unit ids, not a fourth bucket — a
   `not_netted` unit can also appear in `covered`. `discovery` says which reader found the units
   (`precise` = a real parser, `heuristic` = a text reader); a node repo reads `heuristic` unless
   it ships its own `typescript`, and two runs are only comparable when it agrees. **Carry that
   value into your report's header**, as the template below does: a run that silently degraded is a
   run whose numbers cannot be compared to the last one's. Node's precise path reaches that repo's
   own `typescript` by `require`ing it, so it EXECUTES code from the tree you are analysing; pass
   `--no-precise` on any repo you were handed rather than wrote, and expect `heuristic` and fewer
   units in exchange (`references/parameters.md`).

   Every unit lands in exactly one of the four testability tiers described in full in
   `references/triage.md` — read it before writing anything. In short: Tier 1 (direct) gets a
   unit test; Tier 2 (wider boundary) gets pinned at a controlled boundary and named in the
   report; Tier 3 (needs a seam) gets reported to `clean-code`, never acted on here; Tier 4 (not
   reachable) gets a prioritized refactor reason. **Treat `tier` as a starting hypothesis, not a
   verdict** — see the next section for why, and read `references/triage.md`'s "Tier 2 boundary
   controls" before assuming a database or HTTP unit is a valid Tier 2 pin.

   **A Tier 2 row's `tier_reason` names only one controllable group — the unit may hit more.**
   `rank_risk.py` reports the alphabetically-first group a unit touches, so a unit hitting both
   the clock and the filesystem reports only "clock." Before writing a Tier 2 test, inspect the
   unit itself (its `path`/`lineno` from the JSON row) and identify **every** controllable group
   it actually reaches — do not treat `tier_reason` as the complete list of what to fake. Faking
   only the named group while a second, unnamed one stays live produces a test that looks like it
   pins behaviour while still performing real I/O.

   The ranker's triage is conservative on purpose — it would rather under-tier a unit than
   over-tier one into a false Tier 1. If inspection shows a Tier 3/4 unit is actually reachable at
   a controlled boundary, you may promote it, but only by **recording** the promotion (the tier
   the ranker assigned, the tier you used instead, and why) in the report below. Never silently
   treat a ranker tier as advisory.

3. **Confirm with the user before writing anything.** Show the `ranked` top N (default 10) and
   the size of `remainder`/`not_netted`. This is a hard gate — do not proceed past it unconfirmed.

4. **Write and prove, one unit at a time.** For each confirmed unit:
   1. Write the test at the unit's tier (unit test at Tier 1; a narrow characterization test at
      the named Tier 2 boundary).
   2. Write a **deliberately wrong** expected value.
   3. Run the single test. **Require RED.** If it doesn't go red, the assertion isn't binding to
      real output — discard the test, don't ship it.
   4. Correct the expected value to the real captured output (see the literal-emission rule
      below).
   5. Run the single test again. **Require GREEN.**

   > **Stated limit.** This proves the assertion binds to real output from the unit. It does not
   > prove the test detects every behavioural change, which is what true mutation testing would
   > give. Do not oversell it.

   **The runtime guard, not the tier, is what enforces "never real I/O."** Static triage is a
   filter — it declines obvious hazards, but dynamic dispatch (Python's `getattr`, JavaScript's
   computed member access and dynamic `import()`) means it cannot decide reachability from source
   alone; review rounds on the ranker found fifteen-plus constructions it called safe that actually
   reached real I/O. So the invariant is enforced during this red→green proof by the **tier-aware
   runtime guard this skill ships for the stack you are on** — one per stack, both loaded on the
   single-test invocation itself and never written into the repo, so Invariant 1 stays clean with
   no carve-out. **Run every RED and every GREEN through it.**

   On **python** that is `assets/io_guard.py`, loaded as a **pytest plugin via `-p`** (never a
   `conftest.py` written into the repo, and never a fixture inside the generated test) — a plugin
   loads ahead of collection, which is what makes pre-import blocking work:

   ```sh
   # Tier 1 candidate — the unit claims to touch nothing, so block everything.
   PYTHONPATH="$SKILL_DIR/assets:$PYTHONPATH" TEST_SAFETY_NET_TIER=1 \
     pytest -p io_guard <path>::<test_name>

   # Tier 2 candidate — name EVERY controllable group this test deliberately fakes.
   PYTHONPATH="$SKILL_DIR/assets:$PYTHONPATH" \
     TEST_SAFETY_NET_TIER=2 TEST_SAFETY_NET_ALLOW=filesystem,clock \
     pytest -p io_guard <path>::<test_name>
   ```

   **Copy the whole block, not just the `pytest` line.** The `PYTHONPATH` assignment is what makes
   `-p io_guard` resolvable; without it the run dies with `ImportError: Error importing plugin
   "io_guard"` before a single test executes. Both commands above are extracted from this file and
   run verbatim by the skill's own test suite, so what is printed here is what is proved to work.

   On **node/TypeScript** that is `assets/io_guard.js`, preloaded with `--require` — the same
   arm-before-the-unit-loads property `-p` gives on pytest, and likewise nothing written into the
   target repo:

   ```sh
   # Tier 1 candidate — the unit claims to touch nothing, so block everything.
   TEST_SAFETY_NET_TIER=1 \
     node --require "$SKILL_DIR/assets/io_guard.js" \
     --test --test-name-pattern '^<test_name>$' <path>

   # Tier 2 candidate — name EVERY controllable group this test deliberately fakes.
   TEST_SAFETY_NET_TIER=2 TEST_SAFETY_NET_ALLOW=filesystem,clock \
     node --require "$SKILL_DIR/assets/io_guard.js" \
     --test --test-name-pattern '^<test_name>$' <path>
   ```

   **Copy the whole block, not just the `node` line.** `--require` is what arms the guard *before*
   the test file — and therefore before the unit — is loaded; a guard that arms later cannot see
   import-time I/O, which is exactly the case the filter floors to Tier 3. `--test-name-pattern` is
   what makes it one test rather than the file. These two blocks are extracted from this file by
   `assets/test_io_guard_node.sh` and run verbatim against a clean unit and a leaking one, so what
   is printed here is what is proved to work.

   **On node, read the exit status, not the per-test results.** When a violation lands *after* the
   test that caused it has resolved, `node --test` charges it to whatever the runner is executing at
   that moment — one later test, **several** later tests, or, when nothing else is running, the
   enclosing file. Never the test that caused it: that one reports `ok` in every case. All three
   shapes reproduce on **one and the same node build**; what selects between them is when the
   violation lands relative to the tests around it, not the runtime version — so "our node is newer"
   is not a reason to trust the per-test lines. Reproduced verbatim on node v22.18.0:

   ```
   ok 1     - violator                              <- the test that violated
   not ok 2 - innocent_short                        <- an innocent test
   not ok 3 - innocent_long_running_when_it_lands   <- and another
   exit 1
   ```

   An agent reading per-test results keeps the test that performs real I/O and discards two that did
   nothing wrong. There is no salvageable subset to be read out of the TAP stream, which is what the
   second bullet below rests on. So, in the node proof loop:
   - **The exit status is the only trustworthy signal on this stack.** A zero exit means the batch
     is clean; a nonzero exit means something in it violated.
   - On a nonzero exit whose failure is an `IOGuardViolation`, **discard the whole batch and
     re-prove one test at a time.** Per-test attribution cannot be trusted for an async violation,
     and a batch is cheap to re-run.
   - **Never keep a test reported `ok` from a run that exited nonzero.**

   `assets/test_io_guard_node.sh` builds all three fixtures (assertions 12, 13 and 14) and asserts
   both the invariant they share — the culprit reporting `ok` while some *other* entry carries the
   `not ok` and the run exits nonzero — and, separately, that **two** innocent tests can fail from
   one violation. So the rule cannot rot into prose while the behaviour it describes drifts.
   `references/stacks.md` shows all three transcripts and what selects between them.

   The tier is passed per invocation by environment variable, which is sufficient because the proof
   runs one unit at a time — a single run has a single tier:
   - **Tier 1 candidate:** blocks *everything* — filesystem, clock, randomness, environment,
     network, subprocess, DB — and ignores `TEST_SAFETY_NET_ALLOW`. The unit claimed to touch
     nothing, so any touch falsifies the classification: reclassify the unit to Tier 3 and
     discard the test.
   - **Tier 2 candidate:** blocks the uncontrollable groups always, plus every controllable group
     you did *not* name in `TEST_SAFETY_NET_ALLOW`. The boundary this test controls (a temp dir, a
     frozen clock) is the point, not a violation — so name it, and name **all** of it. Omitting the
     variable falls back to permitting all four controllable groups, which is looser than the test
     actually needs.

   The guard patches the **lowest** layer reachable, which for CPython is the `os` primitives, the
   `_io` C module, and the file-object constructors — every name the ranker's marker tables
   classify on, including the directory-and-metadata family (`os.listdir`, `os.scandir`, `os.walk`,
   `os.stat`, `os.rename`) that no fd-level patch can see. `os.open` is not the builtin `open`;
   `pathlib` reaches neither; `pkgutil.get_data` reaches neither *and* neither `io` name;
   `os.posix_spawn` never routes through `subprocess.Popen`. Two consequences worth knowing before
   you read a trip: a patched **class** (`socket.socket`, `mmap.mmap`, `subprocess.Popen`) is
   replaced by a guarded subclass, so `isinstance` against an object built before arming is False;
   and a violation raised on a worker thread is re-raised on the main one, so it lands as a real
   failure rather than a warning beside a green run. The full patch list, the filter↔guard
   coverage table and the residuals are in `references/triage.md`.

   The node guard makes the same choice at node's own lowest layer — every own function of
   `node:fs`, of `node:fs/promises` (a *different* set of functions from the callback face), of
   `node:net`/`node:http`/`node:child_process` and the rest — each replaced by a `Proxy` so a
   patched class stays a class. Its patch list, its two-layer partition against the filter and its
   seven residuals are in `references/stacks.md`; two of those residuals change what a trip means,
   so read them before you read one.

   **The guard raises its own exception type, distinct from `AssertionError`.** The proof run has
   three outcomes, not two: an `AssertionError` is the RED half of red→green (the expectation is
   wrong); the guard's exception means the *classification* is wrong. If the guard's exception
   appears at any point — during the deliberately-wrong run or the corrected one — reclassify the
   unit to Tier 3 and discard the test, regardless of whether that run was red or green. Treating
   a guard trip as an ordinary assertion failure defeats the mechanism.


5. **Report.** Emit the deliverable below, then stop — the ranked remainder is what the next run
   resumes from.

## Invariants (do not violate)

1. **Never modifies source.** Only creates test files; **appends** to an existing test file, never
   overwrites. This is what makes the skill safe to run unattended on a repo nobody trusts yet —
   and why Tier 3 seams are reported, never applied.
2. **Never writes a test that performs real I/O.** Enforced by the tier-aware runtime guard in
   step 4, not by the static tier alone — see `references/triage.md` for the full mechanism and
   why the tiers cannot enforce this on their own.
3. **Never ships an unproven test.** A test that did not go RED is discarded and listed under
   "could not prove," never shipped.
4. **Never leaves the suite red.** End state is a green suite plus suspected bugs in the report. A
   red generated test is a bug in this skill, not an acceptable outcome.
5. **Hard gate before writing** — step 3's confirmation happens before any test file is touched.

## The literal-emission rule

Captured output from running the user's code gets embedded into generated test files — that is
code generation from program output, and it is the one real injection surface in this skill.

> Emit every captured value with `repr()`. Never build a test's expected value by string
> concatenation or f-string interpolation of captured output. A captured string containing a
> quote, a newline or a backslash must become an inert literal, never executable source.

## Deliverable

```
## Test safety net: <repo>  (stack: python · discovery: precise · 3 added, 1 unproven, 1 needs a seam)

| unit                    | tier                 | kind             | test file              | proved    |
|-------------------------|----------------------|------------------|------------------------|-----------|
| billing/refund.py:apply | 1 direct             | characterization | tests/test_refund.py   | RED→GREEN |
| api/handler.py:post     | 2 (fs, clock)        | characterization | tests/test_api.py      | RED→GREEN |
| auth/hash.py:verify     | 1 direct             | specification    | tests/test_hash.py     | RED→GREEN |

The tier column for a Tier 2 row names EVERY controllable group actually faked (not just
`tier_reason`'s alphabetically-first one) — "2 (fs, clock)" means both the filesystem and the
clock were controlled, not only whichever one the ranker happened to name.

### Suspected bugs (pinned, NOT blessed)
- billing/refund.py:41 — pinned returns 0 for a negative amount; the docstring says it raises.

### Could not prove (discarded, not shipped)
- cache/keys.py:build — no single-test invocation found behind `make test`

### Not netted — needs a seam (hand to clean-code)
- db/pool.py:acquire (tier 3) — constructs its connection inline; smallest seam: accept an
  injected factory, default to current behaviour.

### Ranked remainder — next run starts here
1. db/pool.py:acquire (churn 14, inbound_refs 3 [approx])
```

No coverage percentage anywhere, in this report or in conversation about it.

**Promoted units** get a line in the report naming the ranker's original tier, the tier used
instead, and why — never a silent override (see step 2).

**How to improve this.** `inbound_refs` is a static approximation — an identifier-occurrence
count, not a call graph. It cannot tell a call from a comment, it misses a caller that reaches the
unit only through a re-export, and a **bare** occurrence of the name inside a file that references
the module still counts even when it means something else (a same-named local, or one imported
from a different module). Qualified forms are exact: `mod.name` counts, `buf.name` does not. Where
the user already has a real call-graph tool, it computes that half of the score better. This is an
**optional** upgrade, never a dependency — nothing here or in the eval requires one.

## Verify and repair

Before reporting done: every unit in the report's table has a matching RED→GREEN log; the suite
you leave behind is green end-to-end (invariant 4); every "not netted" unit names its blocking
tier and reason rather than being silently dropped; and no generated test file was overwritten
instead of appended to. If any of these fail, fix it and re-check rather than reporting a false
done.

---
name: test-safety-net
description: >-
  Author unit tests into a codebase that has none, so an agent can change it safely. Use when a
  repo has no meaningful tests, when someone says "I don't trust an agent in this codebase", "add
  tests before we refactor", or "we need a safety net before this migration". Ranks units by blast
  radius and churn, then writes characterization tests that pin current behaviour — each one proved
  able to FAIL before it is kept, so the suite is a real change-detector and not green noise. Every
  test declares whether it pins behaviour or asserts a spec; suspected bugs are pinned AND reported,
  never silently blessed. Code that is not testable is triaged, not forced: it becomes a ranked seam
  list for clean-code. Never modifies your source and never writes a test that performs real I/O.
  Not a correctness audit and not a coverage-percentage chaser. Fills the loop verifier-installer
  installs; clean-code judges what comes out.
license: MIT
compatibility: >-
  Prompt-driven; the bundled ranker needs python3 (stdlib only) and, for the churn signal, the git
  CLI. No pip, no network. Python repositories only in this version.
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "testing,characterization,legacy-code,agent-safety,pytest"
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

**Locating this skill's helpers (do this first).** Two bundled scripts ship with this skill — the
ranker (`assets/rank_risk.py`, step 2) and the runtime I/O guard (`assets/io_guard.py`, step 4).
A path written relative to this skill will not resolve from the target repo you're working in.
Resolve the base directory once and reuse it everywhere:

```sh
SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
# If you don't have it, discover it:
SKILL_DIR=$(find ~/.claude ~/.config ~/.agents -type d -name 'test-safety-net' 2>/dev/null | head -1)
test -f "$SKILL_DIR/assets/rank_risk.py" || echo "SKILL_DIR not resolved"
test -f "$SKILL_DIR/assets/io_guard.py"  || echo "SKILL_DIR not resolved"
```

Both are stdlib-only python3, offline. Do not author your own guard: the one that ships is what
Invariant 2 is enforced by, and a hand-rolled substitute that patches the wrong layer is worse
than none, because it makes the invariant look enforced when it is not.

## Workflow

1. **Detect** the stack and — critically — how to run exactly **one** test, not just the suite.
   This version supports Python repositories only; consult `references/stacks.md` for the python
   row and stop plainly, without improvising, if the repo is something else. Single-test
   invocation is load-bearing: step 4's proof is impossible without it.

2. **Rank** the risk surface:

   ```sh
   python3 "$SKILL_DIR/assets/rank_risk.py" <repo> [--top-n N] [--since "6 months ago"]
   ```

   Offline, stdlib + git only, deterministic. Read `references/parameters.md` for the full
   argument and output-key reference. The output's `ranked` array is what you show the user next;
   `remainder` is where the next run resumes; `not_netted` is the Tier 3/4 seam-and-refactor list
   for `clean-code`; `covered` is a flat index of already-tested unit ids, not a fourth bucket — a
   `not_netted` unit can also appear in `covered`.

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
   filter — it declines obvious hazards, but Python's dynamic dispatch means it cannot decide
   reachability from source alone; review rounds on the ranker found fifteen-plus constructions it
   called safe that actually reached real I/O. So the invariant is enforced during this red→green
   proof by the **tier-aware runtime guard this skill ships**, `assets/io_guard.py`, loaded as a
   **pytest plugin via `-p`** on the single-test invocation (never a `conftest.py` written into the
   repo, and never a fixture inside the generated test) — a plugin loads ahead of collection, which
   is what makes pre-import blocking work, and it touches nothing in the user's tree, keeping
   Invariant 1 clean with no carve-out. **Run every RED and every GREEN through it**, like this:

   ```sh
   # Tier 1 candidate — the unit claims to touch nothing, so block everything.
   PYTHONPATH="$SKILL_DIR/assets:$PYTHONPATH" TEST_SAFETY_NET_TIER=1 \
     pytest -p io_guard <path>::<test_name>

   # Tier 2 candidate — name EVERY controllable group this test deliberately fakes.
   PYTHONPATH="$SKILL_DIR/assets:$PYTHONPATH" \
     TEST_SAFETY_NET_TIER=2 TEST_SAFETY_NET_ALLOW=filesystem,clock \
     pytest -p io_guard <path>::<test_name>
   ```

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

   The guard patches the **lowest** layer reachable, which for CPython is the `os` primitives plus
   the file-object constructors — every name the ranker's marker tables classify on, including the
   directory-and-metadata family (`os.listdir`, `os.scandir`, `os.walk`, `os.stat`, `os.rename`)
   that no fd-level patch can see. `os.open` is not the builtin `open`; `pathlib` reaches neither;
   `os.posix_spawn` never routes through `subprocess.Popen`. The full patch list, the
   filter↔guard coverage table and the residuals are in `references/triage.md`.

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
## Test safety net: <repo>  (stack: python · 3 added, 1 unproven, 1 needs a seam)

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

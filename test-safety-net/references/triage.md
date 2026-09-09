# Testability triage

**Never cut inward, climb outward.** Feathers' Legacy Code Dilemma: to change code safely you
need tests; to get tests you often must change code. Refactoring untested code to make it
testable inverts the safety property this skill exists to provide. This skill never rearranges
existing code to make it testable — it only ever **adds** a test file, and where a seam is
missing it reports the smallest additive one for `clean-code` to place. Sprout/wrap: add code
beside, never rearrange.

## Static triage is a filter, not the enforcement

`rank_risk.py`'s `triage()` looks at a unit's calls (resolved through the file's import-alias
map, chased transitively through same-module functions and methods) and buckets it into a tier.
Treat that tier as a **starting hypothesis**, not a verdict. Five rounds of review on this exact
classifier found fifteen-plus distinct constructions it called safe that actually reached real
I/O: an aliased import, a same-module helper, an argument default, a class body, a base-class
expression, a method call reached only through an ordinary variable, and a locally-shadowed
import, among others. Static analysis cannot decide reachability in Python from source alone —
`getattr`, dispatch tables, and dynamic imports are undecidable in general.

So the invariant this skill promises — **never write a test that performs real I/O** — is not
something the tiers guarantee. It is enforced at **runtime**, by the guard described below,
during the red→green proof in the main workflow. The tiers exist to keep that guard from firing
often (a good filter means fewer discarded attempts), not to replace it.

## The four tiers

| Tier | Situation | Action |
|---|---|---|
| **1 — direct** | deps passable, output returnable, no I/O reachable | Unit test. |
| **2 — wider boundary** | unit does I/O, but its module / handler / CLI can be driven with the boundary controlled | Pin at that boundary and **name it** in the report. Not a unit test — a narrow characterization test. As a change-detector, equally good. |
| **3 — needs a seam** | no honest boundary without adding code | Report the smallest **additive** seam (sprout/wrap) and hand to `clean-code`. Never performed by this skill. |
| **4 — not reachable** | global state, work in constructors/at import time, deep branch soup | Prioritized refactor list with the reason. |

Tiers 3 and 4 are **output, not failure**. A risk-ranked "here is what blocks testing, and the
smallest change that unblocks it" list is the missing input to `clean-code`, and is often worth
more to a human than the tests themselves.

## Tier 2 boundary controls

`rank_risk.py` only ever auto-assigns Tier 2 for four I/O groups — the ones it can control
without adding a seam:

| I/O kind | Control |
|---|---|
| filesystem | temp dir |
| clock | the language's own freeze hook |
| randomness | the language's own seed hook |
| environment variables | monkeypatch/override for the duration of the test |

**Database, HTTP, and subprocess are never auto-Tier-2.** The classifier buckets them as
uncontrollable and tiers any unit that reaches one at **Tier 3** ("needs a seam") by default —
even though a database or an HTTP call often does have a real, seam-free boundary control:

| I/O kind | Control, if you promote it |
|---|---|
| database | in-memory or throwaway instance, if the unit already accepts a connection/DSN rather than hard-wiring one |
| HTTP | hand off to **`mockstar-mock`** — already in this collection, exists for exactly this |

If inspection shows the boundary genuinely exists, promote the unit from Tier 3 to Tier 2 per
"Promoting a unit" below — do not treat the table above as license to write a Tier-2 test for a
database/HTTP unit the ranker tiered 3 without recording that promotion.

**Subprocess and process-replacing calls (`os.system`, `os.popen`, `os.posix_spawn`, `os.spawn*`,
`os.exec*`, `os.fork`) are never promotable.** There is no boundary control for them at all — see
"The runtime guard" below for why `os.exec*` in particular can never be pinned safely.

**A Tier 2 unit can hit more than one controllable group at once.** `rank_risk.py`'s
`tier_reason` string names only the alphabetically-first group a unit hits (a unit that touches
both the clock and the filesystem reports "clock" and says nothing about the filesystem). Do
not treat that string as the complete list of what to fake — it is one example, not an
inventory. Before writing a Tier 2 test, look at the unit itself (or its JSON `id`/`path`/
`lineno`) and identify every controllable group it actually reaches, then control all of them.
Faking only the group named in `tier_reason` while leaving a second, unnamed one live is exactly
the failure mode this note exists to prevent: the test would appear to pin behaviour while still
performing real I/O through the group nobody looked for.

## The runtime guard

The guard is what actually enforces "never real I/O." It runs during the red→green proof
(workflow step 4) and is **tier-aware**:

- **Tier 1 candidate** (claims to touch nothing): block *everything* during the proof run —
  filesystem, clock, randomness, network, subprocess, and DB drivers. The unit claimed to touch
  nothing, so ANY touch falsifies that classification. If anything is blocked and raises,
  reclassify the unit (drop it at least to Tier 2 or 3, per what tripped) and discard the test —
  do not keep a test that only passed because the guard let something through.
- **Tier 2 candidate** (I/O at a boundary the test controls): block only the **uncontrolled**
  groups — the ones not named among what this test deliberately fakes. The controllable groups
  (a temp dir standing in for the filesystem, a frozen clock) are the point of the test, not a
  violation to block.

**Patch the lowest layer, not the ergonomic wrapper.** Monkeypatching `builtins.open` or
`requests.get` alone is not enough — real code reaches I/O underneath those names. Patch:

- the `os` primitives: `os.open`, `os.write`, `os.posix_spawn`, `os.spawnv`/`os.spawnl`/
  `os.spawnvp`, `os.fork` (and, where reachable, `os.exec*` — noting the residual below)
- `io.FileIO`
- `mmap.mmap`
- `socket.socket`
- `subprocess.Popen`
- the DB driver connect entry points in use (e.g. `sqlite3.connect`, `psycopg2.connect`)

`os.open` is not the builtin `open` — a guard scoped only to the builtin never sees it.
`os.posix_spawn` never routes through `subprocess.Popen` — a guard scoped only to `subprocess`
never sees it either. Patch the primitives underneath, not just the names a human would reach
for first.

**Install the guard before the module under test is imported.** It must load ahead of collection,
not as a fixture inside the generated test file. A module that performs I/O at import time runs
those side effects during `import unit_module`, before any fixture body executes — once per proof
run, for every module, regardless of tier. A guard installed only inside a test function's
fixture never sees that.

**How the guard signals.** The proof run has **three** outcomes, not two:

- an `AssertionError` — the expectation is wrong; this is the RED half of red→green, or the
  correction went wrong and it's still wrong;
- **the guard's own exception type** — the *classification* is wrong, not the assertion; and
- neither raised — GREEN.

If the guard's exception appears at **any** point during the proof — the deliberately-wrong RED
run or the corrected GREEN run — the unit is reclassified Tier 3 and the test is discarded,
**regardless of whether that run was red or green**. Conflating a guard trip with an ordinary
assertion failure defeats the whole mechanism: a Tier 1 candidate that happens to touch the
filesystem could otherwise pass "RED" only because the guard's exception looked like the
deliberately-wrong assertion, then pass "GREEN" the same way once corrected — two runs that both
"succeeded" while never proving the classification safe.

**How the guard is loaded.** It loads as a **pytest plugin, via `-p`** (e.g.
`pytest -p test_safety_net_guard <path>::<test_name>`), not as a `conftest.py` written into the
target repo. A plugin loads before collection — which is what makes pre-import blocking work —
and, because it is passed on the command line rather than written to disk, it touches nothing in
the user's tree: it cannot collide with a `conftest.py` the repo already has, and it cannot
outlive the proof run. That keeps Invariant 1 ("never modifies source") true with **no
carve-out** — a written `conftest.py` would have been exactly that carve-out.

**How the guard knows its tier.** The tier is passed **per invocation, by environment variable**,
read once at plugin import. This is sufficient — not a limitation — precisely *because* the proof
runs one unit at a time (workflow step 4): a single proof run has a single tier, so the plugin
never needs to dispatch per test the way a guard shared across a whole suite run would.

**State the residual honestly.** A test that spawns a subprocess which itself dials out to the
network escapes an in-process guard — the guard patches this process's syscall layer, not a
child process's. Process-replacing calls (`os.exec*`) are declined **statically** by the filter
for the same reason: they replace the entire process image, taking every in-process monkeypatch
with them, so no runtime guard can survive one. That is not redundant with the guard — it is the
one case the guard structurally cannot reach, which is why the filter must decline it rather than
rely on the guard to catch it.

## Promoting a unit

The ranker's triage is conservative by design — it would rather under-tier a unit (report it as
harder to test than it really is) than over-tier one into a false Tier 1. If inspection shows a
unit the ranker placed at Tier 3 or 4 is actually reachable at a controlled boundary, you may
promote it — but only by **recording** the promotion (which tier the ranker assigned, which tier
you're using instead, and why) in the report. Never silently treat a ranker tier as advisory and
proceed as if it read differently.

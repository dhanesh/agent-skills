# The Migration Control Pack — artifact contract

The Control Pack is the operating system the migration runs on. It is written **before**
implementation code, lives next to the code being migrated (conventionally in a
`migration/` directory), and is what `assets/control_pack_lint.py` lints. Every artifact
below is required; the linter's exact grammar is stated per artifact so a failing lint
line always maps to one fixable spot.

## RULEBOOK.md — translation policy

Tells every agent how to make the same decision the same way. Without it, each agent
invents its own style and the migrated codebase becomes a museum of assumptions.

Grammar: rules are bullets `- MR<n>: <text>`, numbered sequentially from `MR1`, each
carrying a binding modal (`must`, `never`, or `always`). Prose around the rules is fine;
only `- MR<n>:` lines are parsed.

```md
- MR1: Money amounts must map to the Decimal wrapper type, never the plain number type.
- MR2: Date arithmetic must preserve the source system's UTC handling.
- MR3: Repository methods must return typed result objects, never raw database rows.
```

Rules are the repair channel: when a batch of migrated files shares a defect, the fix is
a new or amended `MR` rule plus regeneration — not fifty hand-edits. Cite rule ids in
review comments ("violates MR1") so violations are greppable.

## DEPENDENCY_MAP.md — what moves, in what order

Migration waves as a numbered list (`1.` … `n.`, sequential, at least two). Each wave
must be independently compilable/testable given the waves before it. Typical shape:
utility libraries → domain models → service layer → API handlers → background jobs.

```md
1. Utility libraries (no internal dependencies)
2. Domain models
3. Service layer
4. API handlers
```

## GAP_INVENTORY.md — the landmines

What the old system allowed implicitly that the new system forces you to decide
explicitly (nullable fields, units, currencies, timezone assumptions, loose runtime
coercions). Grammar: `- G<n>: <text>` bullets, sequential from `G1`. Each gap is either
still an **open question** (the text ends with `?`) or **resolved** (the text contains
`decision:` followed by the call that was made). A gap that is neither is an unexamined
assumption — the linter rejects it.

```md
- G1: Is loan.amount stored in minor or major currency units?
- G2: Nullable customer.email semantics — decision: keep optional, normalize empty string to null.
```

## PORTABILITY_TEST_PLAN.md — behavior the judge checks

Golden scenarios that run against **both** systems, caring only about externally visible
behavior, never internals. Grammar: `- S<n>: <text>` bullets, sequential from `S1`, and
the plan must reference the pack's parity script by filename so the plan and the judge
stay wired together. Include edge cases: failed states, retries, timeouts, reversals.

```md
- S1: Repayment schedule for account 42 on 2024-01-31 — run both systems, diff via parity_check.sh.
- S2: Fee calculation for a zero-balance account — outputs must match after normalization.
```

**Two layers, two jobs.** The golden scenarios judge what a client can see. A
second layer, **oracle unit tests**, pins the effects the e2e judge cannot see
or cannot see exactly: cache writes, binary serialisation bytes, outbound header
names and their casing, `Content-Length` on body-less calls, compression
decoding. Generate their expected values from the real legacy code and commit
them as testdata, so the oracle is the old system and not a guess. Header tests
MUST read the raw request bytes from a socket, never through an HTTP server
library that canonicalises names, because canonicalisation hides exactly the
divergence under test.

Unit tests are cheap (a cold run of ~100 packages takes seconds); the e2e wave
is the slow layer. So:

- **Wave gate:** cached unit tests for everything, plus race/sanitiser runs only
  on changed packages and their importers (`assets/impact.py changed-units`,
  then the language's reverse-dependency query).
- **Nightly:** the full uncached unit run, race/sanitiser on everything, and a
  full e2e run.
- **Optional:** mutation-test the new system's unit tests on changed packages
  (Go: gremlins or go-mutesting; Python: mutmut), so the oracles are also seen
  to fail.

Add one `- S<n>:` entry per oracle family, so the plan lists them:

```md
- S7: Outbound header casing for the payment client — raw-socket oracle test, values captured from the legacy client.
```

## parity_check.* — the mechanical judge

Any file named `parity_check.*` (shell, python, …), non-empty. It runs a scenario
through both systems, normalizes, and diffs — typically by capturing both outputs as
JSON and delegating to the skill's `assets/parity_diff.py` (`--ignore` for noise keys
like timestamps, `--tolerance` for float drift). The judge must be proven able to
**fail**: run it once against a deliberately broken case before trusting any pass.

```sh
#!/bin/sh
old_tool run "$1" > old.json
new_tool run "$1" > new.json
python3 parity_diff.py old.json new.json --ignore generated_at
```

### Judge isolation contract — what makes parallel judging possible

A judge that uses fixed resource names can run only one scenario stream per
machine. Every speed-up below (parallel lanes, parallel workers, a proof beside
a wave) needs this contract first. Write it into the parity script and the
`RULEBOOK.md` before the first fan-out.

- The judge MUST key every stateful resource by one worker or lane id `N`:
  database name, cache key namespace, message-bus topic suffix, document-DB
  name, object-store bucket, queue names, temp and output directories.
- A cache namespace MUST be an exact prefix that cannot match another lane's.
  `_JUDGE_W2_` is safe; `_JUDGE_W2` also matches `_JUDGE_W20_`, so a flush of
  lane 2 wipes lane 20's cache in the middle of a scenario. Lane 0 gets a
  numbered name too, never a bare `_JUDGE_` that matches every lane.
- Every port (legacy, mutant, recording sink, sidecars, target) MUST come from
  one formula, for example `BASE + 10*N + k` with a fixed `k` per role. A port
  chosen by hand in one place collides the day a second lane starts.
- A value that echoes a lane name into a response or a recorded effect (a topic
  suffix, a sink `host:port` in an error URL) MUST be normalised by exact
  per-lane replacement before the diff, so goldens do not depend on the lane
  that captured them. `assets/e2e_shard.py --lane-echo` does this.
- Development workers and judge lanes use disjoint id ranges (for example
  workers 0–9, e2e lanes 20–25), so a sharded wave never collides with a worker
  that is still implementing.

Check: start two lanes at once on one suite, then diff the two captures. Any
diff is a resource that is not keyed by lane.

### Coverage of the judge — "unjudged" is a number, not a feeling

A parity PASS covers only the effects the judge records. List every outbound
effect class the old system has (HTTP calls with their headers, cache writes,
bus messages, document writes, object-store writes, emails) and give each one
a recorder. `assets/parity_diff.py --require LABEL=PATH` counts goldens that
lack a class (for example `--require http-headers=effects[kind=http].headers`)
and appends `, unjudged: <n>` to the verdict line. A class with no recorder yet
is listed with a path nobody writes, so its count stays visible until it is
built.

## AGENT_WORK_QUEUE.md — resumable state, rebuilt from facts

The queue makes the migration stoppable, restartable, and fan-out-able. State lives in
machine-readable statuses, not in anyone's head: pending = not migrated OR failing
parity. Grammar: every bullet is `- <unit> [status]` with status one of `pending`,
`in-progress`, `migrated`, `parity-pass`, `parity-fail`, `blocked`. Regenerate the file
from facts (which files exist, compile, pass parity) rather than editing it by hand.

```md
- src/models/loan.py [migrated]
- src/services/schedule.py [parity-fail]
- src/api/handlers.py [pending]
```

## REVIEWER_PROMPTS.md — adversarial review, not "please review"

Implementer agents produce code; reviewer agents attack it. Prompts must target named
failure modes — the linter requires coverage of at least 3 of these 7 categories:
money/precision, time/timezones, nullability, error handling, idempotency,
security/authz, observability. Each prompt should tell the reviewer what to try to
break and which `MR` rules to check.

## PHASE_GATES.md — checkpoints against premature progress

Numbered gates (`1.` … `n.`, sequential, at least three). **Gate 1 must establish the
judge** (mention judge/parity/test/verify) — verification is the foundation, so nothing
advances until the judge exists and demonstrably catches broken code. Later gates
typically: rulebook stress-tested on a pilot slice → bulk migration compiles → smoke
tests pass → behavioral parity green → rollout/rollback approved. A gate is passed by
its named check, not by volume of generated code.

Gates that keep the judge honest as the corpus grows (add them to every pack
whose corpus has outbound effects):

- **Judge coverage gate.** Every outbound-effect class is recorded and compared:
  the verdict line shows `unjudged: 0`. A large corpus can pass for weeks with a
  whole class (request headers, cache writes) unrecorded; the count makes that
  visible.
- **Drift guard before any golden recapture.** Before goldens are recaptured to
  add a newly recorded field, diff old goldens against the new capture with the
  new field ignored (`parity_diff.py old/ new/ --ignore <new_field>`). It MUST
  pass. A scenario that fails it changed behaviour, not just shape, and keeps its
  old golden until a human decides.
- **Baseline before replacement.** Before a recapture replaces goldens, run the
  current new-system binary against the new goldens. Every diff must fall in the
  class the change targets, so a fix cannot hide divergences of other classes.
- **Nondeterminism rule.** Three back-to-back legacy captures of one scenario
  that differ mean the scenario needs canonicalisation (an unordered key set, a
  normalised id, a declared `--ignore`), never a recapture: a recaptured flaky
  golden passes or fails at random.
- **Sharded runs earn trust.** A parallel e2e run counts for a gate only after
  it reproduces a serial run's exact scenario, diff and unjudged counts on the
  same tree (`e2e_shard.py --expect`).


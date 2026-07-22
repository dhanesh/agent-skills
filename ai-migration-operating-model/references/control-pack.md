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

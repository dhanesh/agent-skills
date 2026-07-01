# Contradiction → fix → improvement loop

The acceptance criteria — *detect contradictions, propose fixes, improve normative correctness
over time* — form one closed loop.

## 1. Detect

Constraints are evaluated over live interactions (`wm consolidate`, or automatically in the
Stop hook). Supported constraint kinds:

| kind | fires when | params |
|---|---|---|
| `forbids` | an interaction's object name matches a forbidden pattern | `{"patterns": ["md5","sha1"]}` |
| `functional` / `cardinality` | a `subject+predicate` has more than `maxCount` distinct objects | `{"maxCount": 1}` |
| `disjoint` | the same `subject+object` appears under two predicates that must not overlap | `{"predicate_a":"x","predicate_b":"y"}` |
| `requires` | a subject with the scoped predicate lacks a required companion predicate | `{"companion":"sanitizes"}` |

(`type`, `range`, `value_set`, `invariant` are reserved for later validators.)

A failed test recorded against a fact (`wm refute ... --by test:...`) also drives it to
`contradicted`. Each detection writes a `contradiction` row + its minimal `contradiction_member`
set (the nogood), deduped against open rows, and re-derives the members so they flip to
`contradicted`.

## 2. Propose a fix

From the conflict set, members are ranked by `(entrenchment, normative_conf)` ascending; the
**least-entrenched / least-trusted** member is the thing to change (AGM *minimal change* /
TruthFinder *lowest-confidence*). `proposed_fix` renders a located, human-readable suggestion,
e.g.:

> Change the least-supported member: `reset_pw uses sha1` (at `auth/reset.py`;
> entrenchment=1, normative_conf=0.0). Constraint `no-weak-hash` [violation].

Fixes surface through the pre-call hook (the `✗ CONTRADICTED` partition) and
`wm contradictions --open`.

## 3. Improve over time

When the agent fixes the code, the next turn re-observes the corrected edge and the agent
resolves the contradiction (`wm resolve <id> --as fixed_code`); a new passing test
(`wm validate ... --by test:...`) raises `normative_conf` and flips `validation → validated`.
Net effect: unverified/contradicted mass converts to validated mass, so **normative
correctness rises as the session does real work**. `wm stats` makes the improvement
measurable (and gate-able) via the validated / unverified / contradicted counts.

## Belief revision note

Resolving a contradiction re-derives its members: if the underlying oracle evidence still
stands, a fact returns to `validated` rather than staying stuck at `contradicted`. Nothing is
hard-deleted — a superseded fact is soft-invalidated, keeping the audit trail intact.

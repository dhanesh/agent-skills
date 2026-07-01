# The two-axis confidence model

The reason this skill exists: **code-observed relationships are not ground truth.**
Seeing `a()` call `b()` proves the relationship *exists*, not that it is *correct,
intended, or desirable*. (The software-testing literature calls this the *test-oracle
problem*: a fact read off an implementation inherits that implementation's bugs.) So every
interaction and constraint keeps two independent confidence numbers plus a status.

| Quantity | Question | Raised by | **Never** raised by |
|---|---|---|---|
| `observed_conf` | Did we *see* this? (epistemic — reducible with more sightings) | `file_loc`, `static`, `runtime`, `commit`, `agent_assert` | — |
| `normative_conf` | Is it *correct / intended*? | `test`, `ci`, `doc`, `human` (oracle evidence) | **code observation alone** ← the invariant |
| `validation` | Discrete gate | supporting oracle evidence → `validated`; refuting/failed-test/constraint-hit → `contradicted`; structural change → `stale` | — |
| `entrenchment` | Retraction priority on conflict (AGM) | max rank of supporting evidence (`human` 4 > `test`/`ci` 3 > `doc` 2 > `static`/`runtime`/`file_loc`/`commit` 1 > `agent_assert` 0) | — |

## Derivation (deterministic, pure over the evidence table)

On every evidence write the parent fact is recomputed — the score can never drift from the
audit trail:

- `observed_conf = noisy_or(weights of 'supports' evidence whose kind ∈ observation set)`
  where `noisy_or(ws) = 1 − Π(1 − wᵢ)` — independent sightings accumulate and **saturate**
  toward 1 without ever exceeding it.
- `normative_conf = noisy_or(oracle 'supports' weights) × (1 − noisy_or('refutes' weights))`
  — refuting evidence pulls it back down.
- `validation`:
  1. member of an **open contradiction**, or any live **refutes** evidence → `contradicted`
  2. else `normative_conf ≥ TAU_VALIDATE` (default 0.60) → `validated`
  3. else → `unverified`
- A **soft-invalidated** fact (`invalidated_at` set, e.g. its anchor file changed) is
  `stale` and frozen — re-derivation skips it, so late evidence can't silently un-stale it.

Thresholds and weights live in one config block at the top of `assets/world_model.py`
(`TAU_VALIDATE`, `ENTRENCHMENT_RANK`, the kind sets) so the epistemics are tunable without
touching logic.

### Known limitation: noisy-OR assumes independent evidence

`noisy_or` treats each evidence row as an *independent* observation (the
independence-of-causal-influence assumption). Correlated evidence therefore inflates
confidence. Exact duplicates cannot double-count — the `evidence` table has
`UNIQUE(fact_kind, fact_id, evidence_kind, ref, polarity)`, so re-recording the same pointer is
idempotent. Residual correlation (e.g. a `file_loc` and a `static` row for the *same* line, or
two sources that copied each other) can still mildly over-count `observed_conf`. This is a
deliberate v1 simplification; a TruthFinder-style dampening factor for correlated sources is the
documented upgrade path. It does **not** affect the core invariant — normative confidence is
still gated on oracle evidence regardless of how observation evidence fuses.

## Why two axes and not one

A single conflated "confidence" scalar (as in NELL) cannot express the one thing this skill
must express: *"we are certain the code does X, and equally certain we have not verified X is
right."* Google's Knowledge Vault made the same split — per-source *extraction* confidence
vs fused *truth* probability — for the same reason. Keeping them separate is what lets the
pre-call hook say **"observed but unverified — do not assume correct,"** and what makes
"improve normative correctness over time" a measurable quantity (`wm stats` reports the
validated / unverified / contradicted ratio).

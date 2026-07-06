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

- `observed_conf = grouped_noisy_or(observation 'supports' evidence, keyed by source)` where
  `noisy_or(ws) = 1 − Π(1 − wᵢ)` — independent sightings accumulate and **saturate** toward 1
  without exceeding it. Evidence is first grouped by *source* (the file/path of its `ref`):
  correlated sightings within one source take the **max** (they don't double-count), and only
  distinct sources are fused with noisy-OR (see the dampening note below).
- `normative_conf = grouped_noisy_or(oracle 'supports') × (1 − noisy_or('refutes' weights))`
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

### Correlated-evidence dampening

Plain `noisy_or` assumes every evidence row is an *independent* observation
(independence-of-causal-influence), so correlated evidence would inflate confidence. Two
guards prevent that:

1. **Exact duplicates** cannot double-count — the `evidence` table has
   `UNIQUE(fact_kind, fact_id, evidence_kind, ref, polarity)`, so re-recording the same pointer
   is idempotent.
2. **Same-source correlation** is dampened by `grouped_noisy_or`: evidence is grouped by its
   *source* (the file/path portion of `ref`, e.g. `auth/hash.py:14` → `auth/hash.py`); within a
   source the **max** weight is taken (a `file_loc` and a `static` row for the same file count
   once), and only *distinct* sources are fused with noisy-OR. This is the TruthFinder
   copying-source intuition applied at fusion time.

Residual correlation across genuinely distinct sources that nonetheless copied each other is not
modelled (a full source-reliability estimator is the upgrade path). Crucially, none of this
touches the core invariant — normative confidence is still gated on oracle evidence regardless
of how observation evidence fuses.

## Why two axes and not one

A single conflated "confidence" scalar (as in NELL) cannot express the one thing this skill
must express: *"we are certain the code does X, and equally certain we have not verified X is
right."* Google's Knowledge Vault made the same split — per-source *extraction* confidence
vs fused *truth* probability — for the same reason. Keeping them separate is what lets the
pre-call hook say **"observed but unverified — do not assume correct,"** and what makes
"improve normative correctness over time" a measurable quantity (`wm stats` reports the
validated / unverified / contradicted ratio).

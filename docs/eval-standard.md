# Outcome-eval standard — `eval/run_eval.py`

Generalized from `world-model-ledger/eval/` (a deterministic **harness → skill tooling →
model-free grader** pipeline). Every skill in this repo MUST ship an outcome eval; it is a
**hard gate** (`scripts/gates/run-eval.sh`, wired into `make gate`, which CI runs).

Unit tests (`assets/test_*.py`) check that a skill's functions work. The outcome eval
checks that the skill's **end-to-end promise holds**: build a synthetic scenario, run the
skill's shipped tooling/templates against it, grade the outcome with deterministic checks.

## Contract

Each skill ships `eval/run_eval.py`:

1. **stdlib-only python3, offline, deterministic.** No network, no pip, no LLM judges, no
   dependence on wall-clock or randomness. Same result on every run, every machine.
2. **End-to-end, not unit-level.** The eval exercises the skill's deliverable contract:
   a harness step builds a realistic synthetic scenario, the skill's shipped assets/
   templates run against it, and a grader step checks outcomes (the
   `world-model-ledger` pattern).
3. **Prompt-only skills grade their contract surface.** Where the deliverable is agent
   behavior, the eval deterministically grades what *is* shippable: templates fill
   without residue, shipped linters/schemas accept a known-good fixture and reject a
   known-bad one, scaffolds produced by the skill pass the repo's own gates.
4. **Negative fixtures are mandatory.** An eval that cannot fail is not an eval. At
   least one check must feed the grader a known-bad input and require rejection.
5. **No repo writes.** All scratch under `tempfile.mkdtemp()` (cleaned up), or the
   skill's own gitignored `eval/` outputs. Tracked files are never modified.
6. **Bounded.** Complete in under 60 seconds.
7. **Output protocol.** One line per check —
   `CHECK: <name> — PASS|FAIL (<detail>)` — and a final line exactly
   `EVAL_RESULT: PASS (n/n checks)` or `EVAL_RESULT: FAIL (k/n checks)`.
   Exit 0 iff every check passed.

Supporting files (`harness.py`, `grade.py`, fixtures) may live alongside
`run_eval.py` in `eval/`; `run_eval.py` is the single gate-runnable entry point.

## Reference implementation

`world-model-ledger/eval/` is the exemplar: `harness.py` builds a synthetic repo with
latent non-local issues and seeds a world model; `grade.py` holds model-free graders;
`run_eval.py` runs the harness in a tempdir copy, asserts the generated treatment
context actually carries the non-local facts (and the control arm doesn't), and feeds
known-good/known-bad fixture solutions through every grader.

The LLM-in-the-loop arm of that eval (running prompts through a live agent, as in
`docs/world-model-ledger/2026-07-01-outcome-eval.md`) remains a manual, documented
protocol — the gate runs only the deterministic layer.

## Running

```bash
make eval                        # every skill's outcome eval
make gate                        # full gate incl. evals (what CI runs)
sh scripts/gates/run-eval.sh <skill-dir>
python3 <skill-dir>/eval/run_eval.py
```

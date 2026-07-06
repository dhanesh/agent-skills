# world-model-ledger — outcome eval

A reproducible **with/without** evaluation of whether the world model's pre-call context
changes agent outcomes. Results and interpretation:
[`docs/world-model-ledger/2026-07-01-outcome-eval.md`](../../docs/world-model-ledger/2026-07-01-outcome-eval.md).

## What it does

`harness.py`:
1. Builds a small synthetic Python project with **latent, non-local** issues.
2. Seeds a world model (`eval_model.db`) with the conventions a prior session/human established.
3. Generates the **real `precall()` context** per target file.
4. Writes `control` (repo + placebo note) and `treatment` (repo + world-model context) prompts,
   in two conditions: `full` (whole repo visible) and `local` (only the target file visible).

`grade.py <task> <solution_file>` — deterministic graders (substring + code execution, **no
LLM judge**): task1 = avoided md5/sha1; task2 = migrated to `db.config` not `legacy_config`;
task3 = `slugify()` is functionally correct.

## Run it

```bash
python3 world-model-ledger/eval/harness.py       # generates eval_model.db + prompts/ (+ prints contexts)
# run each prompts/<task>_<arm>[_local].txt through your agent runner; save each returned
# file, then:
python3 world-model-ledger/eval/grade.py task2 <saved_solution_file>
```

The 24 runs in the report used Haiku as the agent under test (small model → room for the
context to matter); the graders are model-free, so scoring is objective and repeatable.

## Headline result

Local context, task 2 (a deprecated dependency knowable only via the world model): **control
0/2 → treatment 2/2.** Full context (whole repo visible): no difference (ceiling). Neutral task:
no harm. The model aids outcomes *specifically* by delivering non-local knowledge the agent
doesn't already have.

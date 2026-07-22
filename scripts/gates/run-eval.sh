#!/bin/sh
# run-eval.sh <skill-dir>
#
# Hard outcome-eval gate (repo-local, not vendored). Every skill must ship
# eval/run_eval.py conforming to docs/eval-standard.md: stdlib-only, offline,
# deterministic, model-free grading, negative fixtures, no repo writes.
#
# Prints the eval's CHECK lines and its final EVAL_RESULT: line.
# Exits non-zero when the eval is missing or any check fails.
set -u

d="${1:?usage: run-eval.sh <skill-dir>}"

if [ ! -f "$d/eval/run_eval.py" ]; then
    printf 'EVAL_RESULT: FAIL — %s/eval/run_eval.py missing (see docs/eval-standard.md)\n' "$d"
    exit 1
fi

out=$(cd "$d/eval" && python3 run_eval.py 2>&1)
st=$?

# Show per-check lines when present; otherwise the tail for diagnostics.
if printf '%s\n' "$out" | grep -qE '^(CHECK|EVAL_RESULT):'; then
    printf '%s\n' "$out" | grep -E '^(CHECK|EVAL_RESULT):'
else
    printf '%s\n' "$out" | tail -5
    printf 'EVAL_RESULT: FAIL — %s eval produced no EVAL_RESULT line\n' "$d"
    exit 1
fi

exit $st

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

# docs/eval-standard.md caps an eval at a bounded runtime. Nothing enforced it,
# so a hung eval hung CI to GitHub's 6h ceiling. `timeout` is absent on stock
# macOS, hence the probe rather than an unconditional call.
EVAL_TIMEOUT="${EVAL_TIMEOUT:-120}"
if command -v timeout > /dev/null 2>&1; then
    out=$(cd "$d/eval" && timeout "$EVAL_TIMEOUT" python3 run_eval.py 2>&1)
    st=$?
    if [ "$st" -eq 124 ]; then
        printf '%s\n' "$out" | tail -5
        printf 'EVAL_RESULT: FAIL — %s eval exceeded %ss (docs/eval-standard.md: evals must be bounded)\n' "$d" "$EVAL_TIMEOUT"
        exit 1
    fi
else
    out=$(cd "$d/eval" && python3 run_eval.py 2>&1)
    st=$?
fi

# Show per-check lines when present; otherwise the tail for diagnostics.
if printf '%s\n' "$out" | grep -qE '^(CHECK|EVAL_RESULT):'; then
    printf '%s\n' "$out" | grep -E '^(CHECK|EVAL_RESULT):'
else
    printf '%s\n' "$out" | tail -5
    printf 'EVAL_RESULT: FAIL — %s eval produced no EVAL_RESULT line\n' "$d"
    exit 1
fi

# docs/eval-standard.md §7 requires a final `EVAL_RESULT: PASS|FAIL` line AND
# exit 0 iff every check passed. Trusting only the exit code let an eval print
# `EVAL_RESULT: FAIL` and still pass the gate; the old CHECK-line guard above
# was also satisfied by any `CHECK:` line, so an eval that never reached its
# verdict slipped through too. Enforce the agreement in BOTH directions.
verdict=$(printf '%s\n' "$out" | grep -E '^EVAL_RESULT:' | tail -1)
if [ -z "$verdict" ]; then
    printf 'EVAL_RESULT: FAIL — %s eval printed CHECK lines but no EVAL_RESULT verdict\n' "$d"
    exit 1
fi
case "$verdict" in
    "EVAL_RESULT: PASS"*) ;;
    *)
        [ "$st" -eq 0 ] && printf 'EVAL_RESULT: FAIL — %s eval reported a non-PASS verdict but exited 0\n' "$d"
        exit 1
        ;;
esac
if [ "$st" -ne 0 ]; then
    printf 'EVAL_RESULT: FAIL — %s eval reported PASS but exited %s\n' "$d" "$st"
    exit "$st"
fi

exit 0

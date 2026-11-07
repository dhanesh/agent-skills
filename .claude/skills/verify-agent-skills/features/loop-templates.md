# loop-templates: the human-checkpointed loop template acts only on an explicit approval

- id: loop-templates
- proven: no
- anchors: crafting-self-prompting-loops/assets/templates/human-checkpointed.template.md:await_human, crafting-self-prompting-loops/eval/run_eval.py, crafting-self-prompting-loops/references/failure-modes.md

## What it is

**Status: planned (run 5).** After the fix, the gate in
`crafting-self-prompting-loops/assets/templates/human-checkpointed.template.md` is closed
by default. After `decision = await_human(...)`, only an explicit `APPROVED` decision
reaches `act()`. Every other value (`TIMEOUT`, `BLOCKED`, `PENDING`, an error, an
unknown value) stops the loop or revises the plan. Today only `TIMEOUT` and `BLOCK` stop,
and any other value falls through to `act()`. Socket calls this "fail-open approval".
The template's own `STATE_VALIDATION` already says that an unreadable decision is
`PENDING` and never `APPROVED`. The fix makes the gate code agree with that rule.

The skill ships no checker or linter for templates or loop specs. Its only Python tool,
`assets/contract_check.py`, checks skill-contract envelopes and grants. The LSC rules are
prose in `references/spec.md` and `references/checklist.md`. The template check that
the skill itself uses is its outcome eval, `eval/run_eval.py`. The eval replays the
templates through the repo's dry-run gate and grades the safety markers. So the drive
below uses the eval plus a small gate check that this recipe writes.

## How to reach it

`python3 crafting-self-prompting-loops/eval/run_eval.py` prints one `CHECK: ... — PASS`
or `— FAIL` line per check and a last line `EVAL_RESULT: PASS|FAIL (<k>/<n> checks)`. It
exits 0 only on PASS. The fix MAY add an eval check for the closed gate. The recipe does
not depend on the name of that check.

`gate_check.py` (written by the recipe) reads a template. It takes the lines from the
first `await_human(` line to the first later `act(` line. It prints `GATE: closed` and
exits 0 when one of those lines compares `decision` with `APPROVED` (or `APPROVE`) as a
negative test (`!=`, `is not`, or `not decision ==`). Otherwise it prints
`GATE: fail-open` and exits 1. With no gate block it prints `GATE: no-gate-block` and
exits 2.

## Drive it

```sh
I="$WT/.verify-run/$INSTANCE"
LT="$I/lt"
mkdir -p "$LT"
cat > "$I/gate_check.py" <<'EOF'
import re, sys
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
start = next((i for i, l in enumerate(lines) if "await_human(" in l), None)
end = next((i for i, l in enumerate(lines) if start is not None and i > start and re.search(r"\bact\(", l)), None)
if start is None or end is None:
    print("GATE: no-gate-block"); sys.exit(2)
block = "\n".join(lines[start:end])
ok = re.search(r"decision\s*(!=|is not)\s*APPROVED?\b|not\s*\(?\s*decision\s*==\s*APPROVED?\b", block)
print("GATE: closed" if ok else "GATE: fail-open"); sys.exit(0 if ok else 1)
EOF
cat > "$LT/fail-open.md" <<'EOF'
        decision = await_human(plan, GATE_EVIDENCE, progress_summary)
        if decision == TIMEOUT: return stop(SAFE_STATE)
        if decision == BLOCK:   state = revise_or_stop(state); continue
    result = act(plan.action)
EOF
cat > "$LT/closed.md" <<'EOF'
        decision = await_human(plan, GATE_EVIDENCE, progress_summary)
        if decision == TIMEOUT: return stop(SAFE_STATE)
        if decision != APPROVED: state = revise_or_stop(state); continue
    result = act(plan.action)
EOF
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name lt-fixture-open -- \
  python3 "$I/gate_check.py" "$LT/fail-open.md"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name lt-fixture-closed -- \
  python3 "$I/gate_check.py" "$LT/closed.md"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name lt-template -- \
  python3 "$I/gate_check.py" crafting-self-prompting-loops/assets/templates/human-checkpointed.template.md
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name lt-timeout -- \
  grep -nE 'decision *== *TIMEOUT.*stop' crafting-self-prompting-loops/assets/templates/human-checkpointed.template.md
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name lt-eval -- \
  python3 crafting-self-prompting-loops/eval/run_eval.py
```

Expected observations:

- `lt-fixture-open`: exit 1 and `GATE: fail-open`. The check catches a gate that only
  stops on `TIMEOUT` and `BLOCK` (a negative fixture for the check itself).
- `lt-fixture-closed`: exit 0 and `GATE: closed`.
- `lt-template`: exit 0 and `GATE: closed`. The shipped template sends every decision
  other than an explicit approval away from `act()`.
- `lt-timeout`: exit 0 and one line. A timeout still stops into `SAFE_STATE` (LSC-3).
  This is the same as before the fix (a regression guard). If the fix folds `TIMEOUT`
  into the negative test, this grep can exit 1; then read the gate block and confirm
  that a timeout still reaches `stop(SAFE_STATE)`.
- `lt-eval`: exit 0 and a last line `EVAL_RESULT: PASS (<k>/<k> checks)`, with k of 35 or
  more.

Before the fix (dry run on `factory/run5-base`, `eeac771`): `lt-fixture-open` exits 1
(`GATE: fail-open`) and `lt-fixture-closed` exits 0 (`GATE: closed`). `lt-template` exits
1 with `GATE: fail-open`: the template gate at lines 89 to 91 tests only
`decision == TIMEOUT` and `decision == BLOCK`. `lt-eval` exits 0 with
`EVAL_RESULT: PASS (35/35 checks)`: the eval does not grade the gate today.

## Proof

The `lt-fixture-open` and `lt-fixture-closed` captures show that the gate check tells a
fail-open gate from a closed one. The `lt-template` capture (exit 0, `GATE: closed`)
shows the fixed template. The `lt-timeout` capture shows that a timeout still stops. The
`lt-eval` capture shows that the skill's own template check still passes.

## Gotchas

- The template's decision vocabulary is `APPROVED|BLOCKED|PENDING` (its `STATE_SCHEMA`),
  but the gate code today uses `TIMEOUT` and `BLOCK`. The gate check accepts `APPROVED`
  or `APPROVE`, so the fix can pick either name. Keep one name in the whole template.
- The gate check reads the raw template, not the replayed copy. The eval reads the
  replayed copy. Both must agree.
- If the fix adds a gate rule to the eval, the eval needs a negative fixture for it (a
  copy with the fail-open gate that the grader flags), per `docs/eval-standard.md`.
- `proven: no` stays until the recipe is driven with SHA-bound evidence.

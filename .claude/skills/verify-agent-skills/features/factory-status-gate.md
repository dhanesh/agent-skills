# factory-status-gate: the stage table and the gate self-tests

- id: factory-status-gate
- proven: no
- anchors: README.md:Which skills cover which stage, scripts/gates/test_gates.sh, Makefile:gate-selftest

## What it is

The root `README.md` has a table, "Which skills cover which stage", with one row per
factory stage and a status for each row. The status rubric above the table allows five
values: `covered`, `covered, with limits`, `partly`, `missing` and
`out of scope, by design`. `scripts/gates/test_gates.sh` tests the gate scripts
themselves. `make gate` runs it first and stops when it fails; `make gate-selftest` runs
it alone.

`scripts/gates/factory_status.py` reads the stage table in `make gate` (`make factory-status`
alone). It fails a status outside the rubric, a row that is not `covered` with no "Limits:"
text, a limit item without exactly one tag (`[decision]`, `[trust]` or `[open]`; items are
separated by `;`), and a row whose items are all `[decision]` but whose status is not
`covered`. This entry covers the table, that check and the self-test suite.

## How to reach it

- The table: `README.md`, under `### Which skills cover which stage`.
- The table check: `make factory-status`, or `python3 scripts/gates/factory_status.py README.md`.
- The self-tests: `make gate-selftest`, or `sh scripts/gates/test_gates.sh`, from the repo root.
- The full gate: `make gate` (it prints `gate self-tests passed: <n>` near the start).

## Drive it

```sh
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name fs-selftest -- make gate-selftest
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name fs-check -- python3 scripts/gates/factory_status.py README.md
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name fs-table -- python3 -c '
import sys
lines = open("README.md", encoding="utf-8").read().splitlines()
i = lines.index("| Stage | Skills | Status |")
for ln in lines[i + 2:]:
    if not ln.startswith("|"):
        break
    cells = [c.strip() for c in ln.strip("|").split("|")]
    print("STAGE: %s | %s" % (cells[0], cells[-1]))
'
```

- `fs-selftest`: exit 0. Every stdout line starts with `PASS:`; there is no `FAIL:` line.
  It takes a few seconds.
- `fs-check`: exit 0. The last line is `FACTORY_STATUS_RESULT: PASS (14 rows)`, or the
  current row count. There is no `FAIL:` line.
- `fs-table`: exit 0. One `STAGE: <stage> | <status>` line per row, 14 rows at the time
  of writing, from `Idea validation` to `Governance`. Each status is one of the five
  rubric values.

## Proof

The self-test capture with no `FAIL:` line and exit 0, the check capture with a `PASS`
result line, and the table capture with every status inside the rubric. No command writes a tracked file: `test_gates.sh` works in
a `mktemp -d` directory that it removes on exit. `git status --short` shows no change
outside `.verify-run/`.

## Gotchas

- The table reader above is a reading aid for the verifier, not a gate. Do not report
  it as a check that the repo runs.
- The number of `PASS:` lines grows as tests are added. Check for the absence of `FAIL:`
  and for exit 0, not for a fixed count.
- The reader takes the status from the last cell of each row. A `|` inside a cell would
  split the row in the wrong place.

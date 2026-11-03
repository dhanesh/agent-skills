# growth-review: judge exported metrics against registered thresholds

- id: growth-review
- proven: no
- anchors: growth-review/assets/growth.py:cmd_review, growth-review/references/metrics-format.md

## What it is

A person writes a metrics plan (`growth/metrics.md`) with a warn and a breach threshold
per metric, and registers it before any data comes in. Then they export metric data and
import it. `review` gives each metric a status (OK, WARN, BREACH or NO_DATA) for its
latest complete period. `signals` turns each breach into one ops-intake signal line.
`growth.py` runs no command and opens no socket.

## How to reach it

`python3 growth-review/assets/growth.py --root <repo> <lint|register|import|review|signals> ...`.
The machine lines are `LINT:`, `PROBLEM:`, `NOTE:`, `REGISTERED:`, `IMPORT:`, `METRIC:`,
`CORRECTED:`, `REVIEW:`, `SIGNALS:`, `STOP:` and `NEXT:`.

## Drive it

The review judges the latest complete period by the real clock. For a `day` metric that
is yesterday (UTC), so compute that date when you write the data:

```sh
GR="$WT/.verify-run/$INSTANCE/gr"
mkdir -p "$GR/growth"
printf '%s\n' '# Metrics plan' '' '| metric | direction | period | warn | breach |' \
  '|---|---|---|---|---|' '| signups | up | day | 100 | 80 |' > "$GR/growth/metrics.md"
python3 - "$WT/.verify-run/$INSTANCE/gr-data.jsonl" <<'PY'
import datetime, json, sys
y = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat()
open(sys.argv[1], "w").write(json.dumps({"metric": "signups", "period": y, "value": 75, "source": "export"}) + "\n")
PY
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name gr-lint -- \
  python3 growth-review/assets/growth.py --root "$GR" lint
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name gr-register -- \
  python3 growth-review/assets/growth.py --root "$GR" register --by "A. Verifier"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name gr-import -- \
  python3 growth-review/assets/growth.py --root "$GR" import "$WT/.verify-run/$INSTANCE/gr-data.jsonl"
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name gr-review -- \
  python3 growth-review/assets/growth.py --root "$GR" review
python3 "$SKILL/scripts/drive.py" capture --instance "$INSTANCE" --name gr-signals -- \
  python3 growth-review/assets/growth.py --root "$GR" signals --source growth
```

- `lint`: exit 0, `LINT: ok 1 metrics`.
- `register`: exit 0, `REGISTERED: <sha12> by `A. Verifier` at <time>, 1 metrics`.
- `import`: exit 0, `IMPORT: added 1 corrected 0 unchanged 0 problems 0`. Import the same
  file again: `IMPORT: added 0 corrected 0 unchanged 1 problems 0`.
- `review`: exit 3 (a breach needs attention). It prints
  `METRIC: signups BREACH value=75 period=<yesterday>` and
  `REVIEW: ok=0 warn=0 breach=1 no_data=0 stale=no`.
- `signals`: exit 0. stdout holds one JSON line with `"source": "growth"`,
  `"source_id": "signups"`, `"severity": 3` and `"title": "metric: signups breached"`.
  stderr ends with `SIGNALS: 1`.

Refusals: `register --by "agent:x"` exits 2 with `STOP: an agent: name cannot register
thresholds; ...`. `--today 2026-01-01 review` exits 2 with `STOP: --today is for tests
only`, because the test clock is off outside the test suite.

## Proof

The captures' result lines and exit codes, and the files under
`$GR/.skill-contract/growth/`: `plan.json` (the frozen thresholds), `data.jsonl` (one
record) and `log.jsonl`. The signal line in `gr-signals` is the input that
`ops-intake import --format intake-signals-jsonl --source growth` reads.

## Gotchas

- Do not use `--today` to fix the date: the tool refuses it unless the test-only variable
  `GROWTH_TEST_CLOCK=1` is set, and that is a test path, not the user path.
- A data record for a period that has not ended is a `PROBLEM:` line, and `import` exits 3.
- `review` exits 3 for a BREACH; `signals` exits 0 even when it prints breaches.
- `signals` prints only JSON lines on stdout. Its notes and `SIGNALS:` go to stderr.

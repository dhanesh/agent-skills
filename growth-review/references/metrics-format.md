# growth-review formats

This file is the reference for `growth.py`. The design is in the repository at
`docs/superpowers/specs/2026-10-11-growth-review-design.md`.

## The metrics plan: `growth/metrics.md`

The plan is a Markdown file. It holds exactly one table whose header has a `metric` column.
Text around the table is free.

```markdown
# Metrics plan

| metric | direction | period | warn | breach | min_n | severity |
|---|---|---|---|---|---|---|
| signups | up | day | 100 | 80 | | 3 |
| churn-rate | down | month | 0.05 | 0.08 | 50 | 4 |
| activation | up | week | 0.4 | 0.3 | | |
```

| Column | Required | Rule |
|---|---|---|
| `metric` | yes | kebab-case (`a-z`, `0-9`, single `-`), at most 64 characters, unique |
| `direction` | yes | `up` (higher is better) or `down` (lower is better) |
| `period` | yes | `day`, `week` (ISO week) or `month` |
| `warn` | yes | a decimal number |
| `breach` | yes | a decimal number; for `up` at most `warn`, for `down` at least `warn` |
| `min_n` | no | a whole number. When set, a value needs `n` of at least `min_n` to count |
| `severity` | no | 1 to 4, the ops-intake severity of a breach; default 3 |

Lint problems (each is a `PROBLEM:` line with its line number):
- no metrics table, or more than one;
- an unknown column, a missing required column, or a column that appears twice;
- a table with no rows, or a row with the wrong number of cells;
- a bad metric name, or a metric that appears twice;
- a bad direction or period, a threshold that is not a number, `breach` on the wrong side of
  `warn`, a bad `min_n` or `severity`.

Cells may be wrapped in backticks. `100` and `100.0` are the same threshold.

## Registration and STALE

`register --by NAME` stores a frozen copy of the parsed thresholds, their sha256, the name,
the time and the reason in `.skill-contract/growth/plan.json`. The hash covers only the
parsed table, so an edit to the prose around it changes nothing.

- An `agent:` name (any case, leading spaces ignored) is refused.
- The same thresholds registered again print `REGISTERED: <sha> (no change …)` and exit 0.
- Different thresholds after an earlier registration need `--reason`. Without it the tool
  prints a `NOTE: stale` line per difference and `STOP:`, and exits 2.

The plan is **STALE** when `growth/metrics.md` is missing, does not lint, or parses to other
thresholds than the latest registration. While it is STALE:
- `review` and `signals` use the frozen thresholds, never the edited ones;
- every `METRIC:` line carries `+stale`, `REVIEW:` says `stale=yes`, and `review` exits 3;
- a `NOTE: stale` line names each metric that was edited, removed or added;
- a metric removed from the file is still reviewed; a metric added to the file is not, until
  the human registers it;
- `import` checks records against the frozen thresholds, so a record for an added metric is a
  `PROBLEM:` until then.

## The data file

JSON lines. One object per metric and period:

```json
{"metric": "signups", "period": "2026-10-11", "value": 75, "n": 1200, "source": "export"}
```

| Field | Rule |
|---|---|
| `metric` | a metric in the registered plan |
| `period` | `YYYY-MM-DD` for `day`, `YYYY-Www` (ISO week, two digits) for `week`, `YYYY-MM` for `month` |
| `value` | a finite JSON number. `NaN`, `Infinity`, strings and `true`/`false` are refused |
| `n` | optional; a whole number of at least 0 |
| `source` | a non-empty string of at most 200 characters: where the number came from |

Other fields are ignored. A record that breaks a rule is a `PROBLEM: line <n>: <reason>`
line. The reason names the field, never the record's text. The other records still import,
and the command exits 3.

**Future periods.** Periods are UTC dates. A period whose last day is more than 1 day after
today (UTC) is a problem record. The 1 day of grace lets an export made east of UTC through.
This means a month or an ISO week that is still running is refused until its last day is at
most 1 day away: export only finished periods.

**Re-import and corrections.** A record for a (metric, period) that is already stored, with
the same `value` and `n`, is a no-op (`unchanged`); a different `source` alone changes
nothing. A different `value` or `n` is a correction: it is appended, the newer record wins,
and the log keeps both. The data lives in `.skill-contract/growth/data.jsonl`, which is
git-ignored.

## The review

For each registered metric, the review takes the latest complete period by the clock
(yesterday, the last ISO week, last month) and looks up the newest record for it.

| Status | `up` | `down` |
|---|---|---|
| `OK` | value > warn | value < warn |
| `WARN` | breach < value ≤ warn | warn ≤ value < breach |
| `BREACH` | value ≤ breach | value ≥ breach |
| `NO_DATA` | no record for the period, or `min_n` set and `n` missing or below it | same |

Machine lines, in this order:

```
METRIC: <name> <status>[ +stale] value=<v|-> period=<p>
CORRECTED: <name> <period> was=<breach value> now=<value> <status>
NOTE: stale <what changed>
REVIEW: ok=<a> warn=<b> breach=<c> no_data=<d> stale=yes|no
NEXT: …
```

`CORRECTED:` appears for every stored period whose newest record is not BREACH while an
earlier record for it was BREACH, under the thresholds in force. It does not change the exit
code. Exit 0 when nothing is in BREACH and the plan is not STALE; 3 otherwise.

## The signal line

`signals --source NAME` prints, for each metric whose latest complete period is BREACH, one
line in ops-intake's `intake-signals-jsonl` format (keys sorted):

```json
{"evidence": [{"key": "signups:2026-10-11", "text": "value 75 vs breach 80 (up)"}],
 "first_seen": "2026-10-10T00:00:00Z", "kind": "issue", "last_seen": "2026-10-12T00:00:00Z",
 "severity": 3, "source": "growth", "source_id": "signups",
 "title": "metric: signups breached"}
```

(One line in real output; wrapped here.)

- The end of a period is the start of the day after its last day, in UTC with `Z`. Day
  `2026-10-11` ends at `2026-10-12T00:00:00Z`; week `2026-W41` ends at
  `2026-10-12T00:00:00Z`; month `2026-09` ends at `2026-10-01T00:00:00Z`.
- `first_seen` is the end of the first period of the current breach streak: the run of
  consecutive breached periods back from the latest one. A missing period or a period that is
  not BREACH ends the streak.
- The evidence holds the latest breached period only. So a metric that breaches every day
  adds one new key a day, and a second run on the same day adds none.
- There is no `url`. ops-intake on main reads `url` as optional and stores an empty string.
- `NAME` follows ops-intake's source-name rule (1 to 50 letters, digits, `_`, `.` or `-`,
  starting with a letter or digit) and is not `release`. It has to match a source in
  `.intake/config.json` whose formats list `intake-signals-jsonl`.
- stdout holds only these lines. `STOP:` and `NOTE:` lines go to stderr.

## ops-intake's handling (main, 2026-10-12)

Checked with ops-intake's own adapter and CLI in `assets/test_growth_signals.py`:
- the item key is (source, metric name), so one metric is one item, with `trust: low`;
- a new evidence key raises the item's count by one; a key it already holds adds nothing;
- a dismissed item becomes `new` with `+regressed` when a signal's `last_seen` is later than
  the dismiss time. A re-run for the same period has an earlier `last_seen`, so the item stays
  dismissed; the next breached period brings it back;
- nothing closes the item when the metric recovers.

## Files

| Path | Committed | What |
|---|---|---|
| `growth/metrics.md` | the human decides | the plan |
| `.skill-contract/growth/.gitignore` | no | `*`, written by the tool before any other write |
| `.skill-contract/growth/plan.json` | no | every registration, with its frozen thresholds |
| `.skill-contract/growth/data.jsonl` | no | every imported record, corrections included |
| `.skill-contract/growth/log.jsonl` | no | an append-only log of register and import |

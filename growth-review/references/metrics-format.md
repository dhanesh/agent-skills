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
| `warn` | yes | a decimal number in ASCII digits (`0`-`9`) that fits in a float |
| `breach` | yes | the same; for `up` at most `warn`, for `down` at least `warn` |
| `min_n` | no | a whole number in ASCII digits, at most 18 digits. When set, a value needs `n` of at least `min_n` to count |
| `severity` | no | 1 to 4, the ops-intake severity of a breach; default 3 |

Lint problems (each is a `PROBLEM:` line with its line number):
- no metrics table, or more than one;
- an unknown column, a missing required column, or a column that appears twice;
- a table with no rows, or a row with the wrong number of cells;
- a bad metric name, or a metric that appears twice;
- a bad direction or period, a threshold that is not a number, `breach` on the wrong side of
  `warn`, a bad `min_n` or `severity`.

Cells may be wrapped in backticks. `100` and `100.0` are the same threshold. Digits are
ASCII only: a fullwidth `１００` or an Arabic-Indic digit is not a number, because the tool
would otherwise read it as one value and print it as another.

## Registration and STALE

`register --by NAME` stores a frozen copy of the parsed thresholds, their sha256, the name,
the time and the reason in `.skill-contract/growth/plan.json`. The hash covers only the
parsed table, so an edit to the prose around it changes nothing.

- An agent name is refused. The check folds the name first (Unicode NFKD without combining
  marks, Cyrillic, Greek, IPA and small-capital lookalike letters to Latin, any case), and
  every character that is not a letter or a digit counts as a separator. So a name that
  starts with the word `agent` in any form is refused: `agent:x`, a bare `agent`,
  `ａｇｅｎｔ:x`, `аgent:x` (Cyrillic `а`), `agent：x`, `agent :x`, `agent-x`, `agent.x`,
  `agent x`, `a gent:x` and `ᴀgent:x`. A human name that starts with that word, such as
  `Agent Smith`, is refused too: the check fails closed. `Agentha Ray`, `agentur GmbH` and
  `Reagent:x` pass. idea-validation and factory-conductor's ledger use the same check. The
  check stays strict (owner decision, 2026-10-12): a person whose name starts with `Agent`
  signs with a surname or initials instead, for example `A. Smith`.
- A new registration prints `REGISTERED: <sha12> by <name> at <time>, <n> metrics`.
- The same thresholds registered again print `REGISTERED: <sha> (no change …)` and exit 0.
- Different thresholds after an earlier registration need `--reason`. Without it the tool
  prints a `NOTE: stale` line per difference and `STOP:`, and exits 2.
- When `plan.json` holds no registration (deleted, or `registrations` is empty) but
  `log.jsonl` records an earlier registration or `data.jsonl` exists, `register` needs
  `--reason` too. Without it the tool prints a `NOTE:` and `STOP:`, and exits 2, so a reset
  by hand cannot replace the thresholds without a visible reason.
- A registration entry that is not well formed (a missing `sha256`, `by`, `at` or
  `metrics`, a malformed metric, or thresholds that do not match their sha256) makes every
  command print `STOP:` and exit 2.

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
| `period` | `YYYY-MM-DD` for `day`, `YYYY-Www` (ISO week, two digits) for `week`, `YYYY-MM` for `month`, in ASCII digits |
| `value` | a finite JSON number. `NaN`, `Infinity`, strings, `true`/`false` and an integer too large for a float are refused |
| `n` | optional; a whole number of at least 0, at most 18 digits |
| `source` | a non-empty string of at most 200 characters: where the number came from |

Other fields are ignored. A record that breaks a rule is a `PROBLEM: line <n>: <reason>`
line. The reason names the field, never the record's text. The other records still import,
and the command exits 3.

**Unfinished periods.** Periods are UTC dates. A period whose last day is after today (UTC)
is a problem record: `period has not ended: its last day is after today (UTC)`. Export
finished periods only, for days too. A day label for today itself still imports, so an
export made east of UTC, where "yesterday" can already be today's UTC date, gets through.
A week or month imports from its last day on. A day label for tomorrow is refused, because
the review would judge it as soon as tomorrow's clock makes it the latest day.

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
CORRECTED: <name> <period> was=<breach value> now=<value> <status>[ n was=<n|-> now=<n|->]
NOTE: stale <what changed>
NOTE: <name> <period> has a value, but n=<n|-> is below min_n <k>, so the review cannot judge it
NOTE: <plan.json and log.jsonl disagree about the latest registration>
NOTE: registered <sha12> by <name> at <time>[ reason <reason>]
REVIEW: ok=<a> warn=<b> breach=<c> no_data=<d> stale=yes|no
NEXT: …
```

`CORRECTED:` appears for every stored period whose newest record is not BREACH while an
earlier record for it was BREACH, under the thresholds in force. A correction can move a
period out of BREACH by its `value` or by its `n` (below `min_n` it is NO_DATA); the
` n was= now=` part shows a changed `n`.

The `NOTE: registered` line names who registered the thresholds in force, when, and why.
The registration cross-check compares `plan.json` with the append-only `log.jsonl`: when the
latest registration differs, or one file records a registration that the other does not,
the review prints a `NOTE:`. With no registration left in `plan.json` but one in the log,
`review` and `signals` print the `NOTE:` and `STOP:` and exit 3.

Exit 0 when all of these are false; 3 when any one is true:
- a metric is in BREACH;
- the plan is STALE;
- a `CORRECTED:` line concerns a metric's latest complete period (the judged one);
- the judged period has a record, but `min_n` is set and `n` is missing or below it;
- `plan.json` and `log.jsonl` disagree about the latest registration.

So a correction or a missing `n` cannot turn a breach into a quiet exit 0. A correction of
an older period prints `CORRECTED:` and does not change the exit code.

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
- stdout holds only these lines. Its own `STOP:` and `NOTE:` lines go to stderr, and its
  last stderr line is `SIGNALS: <k>`, the number of lines it printed. On a refusal (no
  registration, a corrupt state file) stdout is empty, there is no `SIGNALS:` line and the
  exit is 2, or 3 when the log remembers a deleted registration.
- Pipe it under `set -o pipefail` and check the exit status. Without `pipefail` a failed
  `signals` gives ops-intake an empty stdin, which it imports as `ok 0`. After the pipe,
  ops-intake's `IMPORT: <source> intake-signals-jsonl ok <k> problems 0` has the same `<k>`
  as `SIGNALS: <k>` and as `breach=` on the `REVIEW:` line.

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
| `.skill-contract/growth/log.jsonl` | no | an append-only log of register and import; the review cross-checks it against `plan.json` |
| `.skill-contract/growth/.lock` | no | the lock file |

The tool refuses a symlink for `plan.json`, `data.jsonl`, `log.jsonl` or `.lock` (`STOP:`,
exit 2), before its first write, so a link cannot make it read or append outside
`.skill-contract/growth/`. It opens these files with `O_NOFOLLOW` where the platform has it.

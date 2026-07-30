# Tool reference — `casekit.py` and `rigor.py`

`casekit.py` disciplines the decision *process*; `rigor.py` disciplines the
*arithmetic* underneath it. They are independent — a RICE sheet or a plan can be
gated without ever opening a decision record.

# `casekit.py` — commands, rules, and the scoring maths

`python3 assets/casekit.py [--root DIR] <command> [<slug>] [...]`

`--root` defaults to `decisions`. Each decision is `<root>/<slug>/`.

## Commands

| Command | Flags | Effect | Refuses when |
|---|---|---|---|
| `new <slug>` | `--decision` (req), `--by YYYY-MM-DD` (req), `--drill` | Scaffolds `brief.md` + `decision.md` (+ `reveal.md` in drill), stage `drafting` | slug exists; `--by` malformed |
| `lint <slug>` | — | Grades `brief.md`: B0–B6, plus D1–D2 in drill mode | `brief.md` missing |
| `commit <slug>` | `--file PATH` | Validates the DQ chain, fingerprints the record, stores the forecasts | a link missing; scaffold residue; <3 alternatives; no parseable forecast; already committed; drill still `drafting` |
| `resolve <slug>` | `--n N` (req), `--outcome yes\|no` (req), `--force` | Records how forecast N turned out | not committed; N out of range; already resolved differently without `--force` |
| `score <slug>` | — | Brier, mean confidence, hit rate, calibration gap, bins | no such decision |
| `profile` | — | The same across every decision under `--root` | no decisions found |
| `status <slug>` | — | Mode, stage, forecast counts, next step | no such decision |
| `seal <slug>` | `--skip-lint` | Drill only: hides `reveal.md`, stage → `sealed` | live decision; lint fails; not `drafting` |
| `reveal <slug>` | — | Drill only: prints the ending, stage → `revealed` | live decision; no commit yet; corrupt or checksum-mismatched blob |

Exit codes: `0` success, `1` lint failure, `2` refusal or I/O error (one `ERROR:` line on
stderr, never a traceback).

## Brief lint rules

All rules read the narrative body of `brief.md` — every section except `## Sources` and
`## Coach notes`, minus HTML-comment lines.

| Rule | Fails when | Why |
|---|---|---|
| **B0** `no-scaffold-residue` | `TODO`/`FIXME`/`<ALL_CAPS>` remain | An unfilled scaffold satisfies the structural rules trivially |
| **B1** `has-every-section` | any of the six brief sections missing | The brief is the Information link; gaps are silent |
| **B2** `decision-is-a-dated-question` | `## The Decision` lacks a `?` or an ISO date | A decision has a verb and a date; a topic has neither |
| **B3** `real-option-set` | fewer than **3** bullets under Options on the Table | Two options is a whether-or-not trap, not a choice |
| **B4** `figures-are-sourced` | a figure on a line with no `[^ref]` or `(src: …)` | Blocks confident numbers recalled from memory |
| **B5** `has-a-base-rate` | fewer than **2** bullets under Reference Class | One comparator is an anecdote; a base rate needs more |
| **B6** `names-what-is-unknown` | Open Uncertainties is empty | A decision with no uncertainty needs no case |
| **D1** `no-hindsight-language` *(drill)* | a denylisted phrase ("turned out to be right", "in hindsight", …) | The ending must not be readable in the brief |
| **D2** `no-future-dates` *(drill)* | a date after the decision date | The commonest hindsight leak |

D1/D2 apply **only in drill mode**. A live decision has no ending to leak — the future
hasn't happened — so running hindsight rules against it would be theatre.

Four-digit years are excluded from B4's figure detector, and identifying numerals
("Section 230", "ISO 27001", "Rule 144A") are skipped: noise is what pushes an author to
`--skip-lint`, which costs more than the rule saves.

## Decision-record requirements (checked by `commit`)

Eight sections, in any order: `## Frame`, `## Alternatives Considered`, `## Values`,
`## Reasoning`, `## Premortem`, `## Decision`, `## Falsifier`, `## Predictions` — the six
DQ links plus the two evidence-backed disciplines. Also enforced: no scaffold residue, at
least **3** alternatives, and at least one parseable forecast.

Forecast line format, one per bullet:

```
- 2026-12-31 | 0.70 | At least 2 of 3 Q3 renewals close on the hybrid
```

`date | probability | claim`. The probability must be **strictly between 0 and 1** —
certainty is not a forecast, and the tool rejects `0` and `1` with that message.

## The scoring maths

- **Brier score** = `mean((p − outcome)²)` over resolved forecasts, outcome ∈ {0, 1}.
  0 is perfect; **0.25 is what saying 0.5 about everything gets you**; above 0.25 your
  confidence is actively misleading you.
- **Calibration gap** = `mean(p) − hit rate`. Positive → over-confident, negative →
  under-confident. Reported as "well calibrated" within ±0.05.
- **Bins** — forecasts grouped into probability deciles, each showing `n`, mean stated
  probability, and actual hit rate. This is where over-confidence localises: a bin saying
  0.90 that lands 0.60 is the actionable finding.
- `profile` pools every decision under `--root`, live and drill together, and prints a
  small-n warning below **10** resolved forecasts.

The tool scores forecasts. It does **not** grade the six DQ links — completeness is
mechanical, quality is a judgement, and a regex producing a number for it would look like
rigour without being any. `score` prints that reminder every time.

## `state.json`

```json
{
  "slug": "pricing-2026",
  "decision": "Should we move fee financing to a flat platform fee?",
  "deadline": "2026-09-30",
  "mode": "live",
  "stage": "committed",
  "decision_sha256": "…",
  "predictions": [{"date": "2026-12-31", "p": 0.7, "claim": "…"}],
  "resolutions": {"1": true}
}
```

Stages: live runs `drafting → committed`; drill runs `drafting → sealed → committed →
revealed`. No timestamps are recorded — the file is a reproducible audit of *ordering*,
and wall-clock values would make the eval non-deterministic.

## When `--skip-lint` is defensible

Rarely, and never silently. Legitimate: a figure genuinely has no citable source and you
have written the uncertainty into the brief instead. Not legitimate: fewer than three
options because you have already decided. `--skip-lint` records no reason in `state.json`
— naming the failed rules in the debrief is yours to do.


---

# `rigor.py` — the numbers gate

`python3 assets/rigor.py rice <file>`
`python3 assets/rigor.py plan <file> [--tolerance X] [--max-growth X]`

Exit codes: `0` clean, `1` a rule failed, `2` a bad file or unparseable formula (one
`ERROR:` line on stderr, never a traceback).

## `rice` — sheet format and rules

```markdown
## Items

- Bulk fee upload | reach=1200/quarter | impact=2 | confidence=0.8 | effort=3 | src: cohorts [^1]
- Autopay nudges  | reach=9000/quarter | impact=0.5 | confidence=0.5 | effort=2 | src: analytics [^1]

## Sources
[^1]: https://…
```

`reach` accepts `1200/quarter` or `1200 per quarter`. Order of the `key=value` fields does
not matter; the item name is whatever precedes the first `|`.

| Rule | Fails when | Why |
|---|---|---|
| **R0** `every-item-parses` | a field is missing, non-numeric, or reach has no period | Reach without a time window cannot be compared |
| **R1** `at-least-two-items` | fewer than 2 items | RICE ranks; one item ranks nothing |
| **R2** `one-reach-period` | items mix `/month` and `/quarter` | A 4× error hiding in plain sight — the commonest real RICE bug |
| **R3** `reach-is-sourced` | an item has no `src:` or `[^n]` | Reach is the one genuinely measurable input |
| **R4** `impact-on-the-scale` | impact ∉ {3, 2, 1, 0.5, 0.25} | A 4 is someone inventing headroom to win |
| **R5** `confidence-is-a-fraction` | confidence outside (0, 1] | Catches `confidence=80` meaning 80% |
| **R6** `effort-is-positive` | effort ≤ 0 | Division by zero, or a free lunch |

Beyond the rules, the ranking output adds two things the score alone hides:

- **Tie bands** — items whose scores are within **20%** are grouped and declared not
  distinguishable. Break those on strategy, sequencing or dependencies.
- **Low-confidence flags** — items at ≤50% (Intercom's "informed guess") are listed, with
  the reminder that low confidence is a signal to get evidence, not a discount factor.

A sheet that fails validation is **not ranked**: a score built from an off-scale impact
out-ranks every honest row, and printing it would publish a number that looks authoritative
and isn't.

## `plan` — file format and rules

```markdown
## Drivers
- institutions = 120 | src: signed pipeline [^1]
- attach_rate = 0.06 | assumption

## Top Down
- revenue = institutions * students_per_institution * attach_rate * avg_ticket

## Bottom Up
- revenue = 420000000 | src: sales capacity model [^3]

## Trajectory
- FY27-Q1 | 60000000
- FY27-Q2 | 90000000

## What Would Have To Be True
- [least likely] Attach holds at 6% down-market
- Sales can onboard 30 institutions a quarter
```

| Rule | Fails when | Why |
|---|---|---|
| **P0** `has-every-section` | Drivers / Top Down / Bottom Up / WWHTBT missing | Short-circuits: no point checking arithmetic that isn't there |
| **P1** `drivers-parse` | a driver line is malformed or references an unknown name | |
| **P2** `every-driver-sourced-or-flagged` | a driver has neither a citation nor `assumption` | Hidden assumptions are the failure; visible ones are just uncertainty |
| **P3** `assumptions-are-a-minority` | >50% of drivers are unsourced assumptions | Above that it is a hypothesis, not a plan |
| **P4** `top-down-reconciles` | the formula doesn't evaluate over the drivers | A top line that doesn't reconcile is a number, not a model |
| **P5** `bottom-up-present` | no bottom-up figure | One sizing method cannot catch its own error |
| **P6** `sizings-agree` | the two differ by more than `--tolerance` (default 2.0×) | **The fairytale detector.** An order of magnitude apart means one is fiction |
| **P7** `no-hockey-stick` | period-over-period growth above `--max-growth` (default 2.0×) | Growth is allowed — it just needs a named capacity behind it |
| **P8** `states-what-must-be-true` | fewer than 2 WWHTBT conditions | Roger Martin's test; one condition isn't a logic |
| **P9** `names-the-weakest-condition` | not exactly one `[least likely]` marker | That condition is what you test first |

`Trajectory` is optional — P7 is skipped when it's absent.

Formulas are evaluated by an AST walker restricted to `+ - * / **`, numbers and driver
names. No calls, no attributes, no builtins: a plan formula is arithmetic or it is refused.

`--tolerance` and `--max-growth` exist because the right thresholds are domain-dependent —
early-stage growth genuinely can exceed 2× a quarter. Widening them is legitimate and
should be **stated in the report**, since a plan that passes only at `--tolerance 10` has
not really been cross-checked.

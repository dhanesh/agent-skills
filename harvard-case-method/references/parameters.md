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

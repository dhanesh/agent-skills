# `casekit.py` — commands, lint rules, and state

`python3 assets/casekit.py [--root DIR] <command> <slug> [...]`

`--root` defaults to `cases`. Each case is `<root>/<slug>/`.

## Commands

| Command | Flags | Effect | Refuses when |
|---|---|---|---|
| `new <slug>` | `--company` (req), `--decision-date YYYY-MM-DD` (req) | Scaffolds `case.md`, `reveal.md`, `decision.md`, `state.json` at stage `drafting` | slug exists; date is not `YYYY-MM-DD` |
| `lint <slug>` | — | Runs L0–L5 on `case.md`; one `LINT:` line per rule, then `LINT_RESULT:` | `case.md` missing |
| `seal <slug>` | `--skip-lint` | Base64-encodes `reveal.md` → `reveal.sealed`, deletes the plaintext, records `reveal_sha256`, stage → `sealed` | lint fails (unless `--skip-lint`); stage is not `drafting`; no `reveal.md` |
| `commit <slug>` | `--file PATH` | Records `decision_sha256`, stage → `committed` | stage is `drafting`; already committed; decision file missing a required section |
| `reveal <slug>` | — | Decodes and prints the B-case, stage → `revealed` | stage is `drafting` or `sealed`; blob is corrupt or fails the checksum |
| `status <slug>` | — | Prints slug, company, decision date, stage, and the next step | no such case |

Exit codes: `0` success, `1` lint failure, `2` a refusal or I/O error (one `ERROR:` line
on stderr, never a traceback).

## Lint rules

All rules read the **narrative body** of `case.md` — every section except `## Sources` and
`## Facilitator notes`, minus HTML-comment lines.

| Rule | Fails when | Rationale |
|---|---|---|
| **L0** `no-scaffold-residue` | `TODO`, `FIXME`, or `<ALL_CAPS>` placeholders remain | An unfilled scaffold trivially satisfies the structural rules |
| **L1** `no-future-dates` | an ISO date later than the decision date, or a bare year after the decision year | The commonest hindsight leak, and the only one detectable exactly |
| **L2** `no-hindsight-language` | a phrase from the denylist ("turned out to be right", "went on to", "in hindsight", "what made this work", …), case-insensitively | Outcome language the learner should not be able to read |
| **L3** `ends-in-a-decision` | no `## The Decision` section, or that section asks no question | An HBS case ends in a decision to be made |
| **L4** `figures-are-sourced` | a currency amount, percentage, multiplier, or 3+ digit number on a line with no `[^ref]` or `(src: …)` marker | Blocks confident figures recalled from model memory |
| **L5** `names-comparators` | no `## Comparators` section, or no `-`/`*` bullet in it | The survivorship guard: one company studied alone is a sample of one |

Four-digit years are excluded from L4's figure detector (L1 already governs them), so
"In 2011 the founders…" needs no citation.

### When `--skip-lint` is defensible

Rarely, and never silently. Legitimate cases: a source genuinely has no citable figure and
you have written the uncertainty into the case instead; a comparator section is
intentionally in prose because no comparable company exists and you say so. In both, state
in the debrief which rules the case failed and why. `--skip-lint` does not record a reason
in `state.json` — the honesty is yours to supply.

## `state.json`

```json
{
  "slug": "stripe-2011",
  "company": "Stripe",
  "decision_date": "2011-06-30",
  "stage": "committed",
  "reveal_sha256": "…",
  "decision_sha256": "…"
}
```

Stages advance `drafting → sealed → committed → revealed` and never move backwards. No
timestamps are recorded: the file is a reproducible audit of *ordering*, which is what the
method needs, and wall-clock values would make the eval non-deterministic.

## What sealing is and is not

`reveal.sealed` is base64. It stops accidental reading and enforces the workflow's
ordering; it is **not** encryption and anyone determined to peek can. The meaningful
guarantee is the recorded ordering — `decision_sha256` is written at stage `committed`,
before `reveal` will run — which is enough to show a decision was fixed before the outcome
was seen. Anyone who wants a stronger guarantee should have someone else write the case.

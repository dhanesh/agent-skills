# concept-first-review

Review a change for **concepts, algorithm choices, and architecture** — offline, with no API
keys, and drivable from an agent loop.

Review has a budget, and most of it gets spent in the wrong place. In a machine-written
change, the rows carrying a decision are heavily outnumbered by the rows that merely follow
from one: field copies, forced signature updates, regenerated files, formatting. Read
linearly and the budget is gone before the decisions arrive.

## Install

```bash
npx skills add dhanesh/agent-skills --skill concept-first-review
```

## How it works

**Phase one — condense.** The tool indexes the diff and hands it back with a coordinate
column. You submit an ordered list of edits (`drop`, `collapse`, `trim`) and a compiler
applies them to the untouched original.

You never write the condensed diff. The plan language has verbs for deleting and for
shortening and no verb for writing new text, so the output is derivable from the input
rather than asserted about it. Everything else is rejected, with a message that says what to
change:

| Rejection | Why it exists |
|---|---|
| invented or silently cut characters in a trim | the condensed diff must stay a true statement about the original |
| a relocation condensed at one end only | code compressed at its destination but not its origin reads as a deletion |
| a collapse burying a definition a surviving row uses | the reader would meet a variable that comes from nowhere |
| a collapse consuming a Python decorator or `def`/`class` header | the header stays visible; the body collapses |
| a plan changing triple-quote parity in a hunk | never leave a string unterminated |
| a collapse spanning dependency and executable rows | dependency rows are already removed |

Imports, includes, requires, and `use` declarations come out of every draft automatically —
across Go, Python, JS/TS, Rust, C/C++, JVM languages, C#, Ruby, and PHP, including unchanged
framing rows and declarations embedded in multiline test fixtures.

**Phase two — interrogate.** Signals are extracted from the *original* diff, dependency rows
included, because import lists are a poor way to read a change and an excellent way to see
its architecture. Each is an observation plus a **question**, never a verdict:

- **algorithm** — N+1 query shapes, nested loops past a budget, `await` in a loop, linear
  scans inside loops, catastrophic-backtracking regexes, recursion, unbounded growth
- **architecture** — new third-party dependencies, layering breaches, cross-layer edges,
  public API added/removed/reshaped, new HTTP entry points
- **runtime** — spawned concurrency, locks, module-level mutable state, swallowed errors,
  aborts, retry and timeout changes
- **contracts** — migrations, DDL, `.proto`/OpenAPI/GraphQL/Avro edits, config and flags
- **boundaries** — subprocess and `eval`, unsafe deserialisation, new network calls,
  authorisation decisions, path construction
- **blast radius** — behaviour changed with no test touched, a change spread across four or
  more areas

Then you write `REVIEW.md`, and a completeness check grades it. It cannot tell you a finding
is right; it does refuse a review with an empty section, surviving placeholder text, an
ambiguous verdict, or an unresolved high-severity signal.

## Second opinion on a review that already exists

When another agent has already reviewed the change, the useful contribution is the delta.

```bash
python3 assets/review.py audit --into .review --prior their-review.md
```

`audit` reports the high-severity signals that review never engages, the files it cites that
the change does not touch, and the dimensions its wording never reaches — exit `1` when
there are blind spots. It finds *absence*, which is the check nobody does by hand: a prior
review can be long, well-organised, entirely about naming, and silent on every signal that
matters.

## Drivable from a loop

```bash
python3 assets/review.py state --into .review --json
```

```json
{"phase": "condense", "done": false,
 "next_action": "resolve the plan rejections, then run `draft` again",
 "blockers": ["collapse 21-23: buries the definition of `rows` ..."],
 "gates": {"plan": "rejected", "review": "absent"},
 "density_pct": 91.3, "unresolved_high": []}
```

The loop is `until state.done: perform state.next_action`. Every field is derived from files
on disk, so an interrupted run resumes at the right step. `done` is computed from the gates
— the plan compiles, the condensed diff exists, every section is answered, every
high-severity signal is resolved — never from the agent's own account of its progress.

## Making the architecture questions checkable

Layering questions stay guesses until the team's rules live somewhere a tool can read. Drop
a `design-rules.json` at the repo root — named layers, forbidden dependency edges, accepted
third-party roots, a loop-nesting budget, plain-text invariants — and "a new import crosses
a layer" becomes a fact instead of a heuristic. Start from
`assets/design-rules.example.json`; the authoring guide is `references/design-baseline.md`.
Without it the skill still works, and says plainly that layering is staying heuristic.

## Usage

```bash
python3 assets/review.py open     --rev HEAD~1..HEAD --into .review --rules design-rules.json
# read .review/indexed.diff, write .review/plan.json
python3 assets/review.py draft    --into .review   # verify -> repair, exits 1 if rejected
python3 assets/review.py commit   --into .review   # writes .review/condensed.diff
python3 assets/review.py signals  --into .review --severity medium
python3 assets/review.py template --into .review   # writes .review/REVIEW.md
python3 assets/review.py grade    --into .review   # exits 1 on any gap
python3 assets/review.py state    --into .review --json
python3 assets/review.py audit    --into .review --prior their-review.md
```

`--diff <file>` or `--diff -` works anywhere a git range does not. Full flag reference:
`references/parameters.md`.

## What it is not

Not a linter, formatter, or style checker — those are explicitly out of scope. Not a
security scanner: the boundary signals are review prompts, and `security-posture-audit` does
the real job. Not an architecture fitness function: `design-rules.json` raises the severity
of questions, it does not fail builds.

And no part of it calls a model. The judgment comes from the agent reading the skill, which
is why it behaves the same in Claude Code, in Codex, and on a machine with no network.

## Layout

| Path | What it is |
|---|---|
| `SKILL.md` | the agent-facing prompt: two phases, seven steps |
| `assets/diffmodel.py` | diff parsing, dependency-row detection, relocation detection |
| `assets/condenser.py` | the plan compiler and every rule it enforces |
| `assets/signals.py` | the signal extractors and the design baseline |
| `assets/report.py` | review template, grader, loop state, prior-review audit |
| `assets/review.py` | the CLI |
| `references/` | condensing rubric, plan language, review rubric, signal catalogue, design baseline, loop integration, second opinion, CLI reference |
| `eval/` | the outcome eval — treatment and control arms, plus plans that must be rejected |

## Tests

120 stdlib unit tests across the four modules, plus a 37-check end-to-end eval that asserts
both directions: that noise collapses while the decisive rows survive, *and* that invented
text, one-sided relocation treatment, and definition-burying collapses are all rejected;
that a clean control diff yields no high signals; that the template the skill writes does not
itself grade as a finished review; that a rejected plan surfaces as a loop blocker rather
than a crash; and that a shallow prior review is reported blind.

```bash
make gate-skill SKILL=concept-first-review
```

## Siblings

- `crafting-self-prompting-loops` — for building the loop this skill plugs into
- `feynman-walkthrough` — when you do not yet understand the subsystem well enough to review
  it, and want a durable explainer to pin as a baseline
- `security-posture-audit` — for an actual security review
- `bug-autopsy` — for after the change shipped and something broke
- `verifier-installer` — for turning a review conclusion into a check that runs in CI

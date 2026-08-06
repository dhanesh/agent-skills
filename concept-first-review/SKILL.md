---
name: concept-first-review
description: >-
  Review a code change for CONCEPTS, ALGORITHM CHOICES, and ARCHITECTURE rather than style
  or nits. Use when asked to review a diff, commit, branch, or pull request at the design
  level — "review this change", "is this the right approach", "what does this do to the
  architecture", "check the agent's work before I merge" — and when asked to second-opinion
  a review another agent or person already wrote. Two phases. CONDENSE: you read an indexed
  diff and submit an ordered plan of drop/collapse/trim edits, which a compiler applies to
  the original. Dependency rows come out automatically, relocated code must be treated
  identically at both ends, and the plan language has no verb for writing new text, so the
  result is derivable from the input rather than summarised from it. INTERROGATE:
  deterministic signals — N+1 shapes, nested loops, layering breaches, unlisted
  dependencies, swallowed errors, schema and contract changes, blast radius — each pose a
  question, and an optional design-rules.json baseline turns layering guesses into
  checkable facts. Produces REVIEW.md with one verdict, graded by a completeness check that
  fails on any unresolved high-severity signal. `state --json` exposes typed phase, gates,
  and a real termination condition, so the whole thing drives from a self-prompting loop.
  Fully offline — no API tokens, no model calls, no network. Not a linter or security scanner.
license: MIT
compatibility: Requires python3 with its standard library only (no pip, no network, no API keys) and any agent that can run a shell and read files — Claude Code, Codex, or similar. git is optional, used only by `open --rev`; a diff can always be piped in instead.
x-spec-version: 1.0
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "code-review,architecture,algorithms,design-review,diff,offline,agent-loop,second-opinion,signals,pull-request"
---

# concept-first-review

Review has a budget, and most of it gets spent in the wrong place. In a machine-written
change, the rows that carry a decision are heavily outnumbered by the rows that merely
follow from one — field copies, forced signature updates, regenerated files, formatting.
Read linearly and the budget is gone before the decisions arrive.

This skill spends the budget on the decisions. First it **condenses** the change down to
the rows where somebody chose something. Then it **interrogates** what remains, against a
fixed set of design questions. Both phases run offline; there is no API key anywhere in
this skill, because the judgment comes from the agent reading it.

## The two phases

### Condense

The tool indexes the diff and hands it back with a coordinate column. You decide what
carries a decision and submit an ordered list of edits — `drop` these rows, `collapse` that
run into one placeholder, `trim` the noisy span out of this line. A compiler applies them to
the untouched original.

The condensed diff is not yours to write. The plan language has verbs for deleting and
for shortening and no verb for writing something new, so the output is *derivable* from the
input rather than *asserted* about it. Everything else is rejected: invented text, a
relocation compressed at one end only, a collapse that buries a definition a surviving row
still uses.

### Interrogate

Signals come from the *original* diff, including the dependency rows the condensed diff
hides — import lists are a poor way to read a change and an excellent way to see its
architecture. Each signal is an observation plus a question rather than a verdict. With a
`design-rules.json` baseline present, the layering questions become layering facts.

## Workflow

1. **Open the change.**

   ```bash
   python3 assets/review.py open --rev HEAD~1..HEAD --into .review \
       --rules design-rules.json --intent "what the change was asked to do"
   ```

   Use `--diff <file>` or `--diff -` when there is no git range at hand. **Pass `--intent`
   whenever you know what was asked**: silent scope reduction — a competent subset of the
   request, delivered without mentioning the rest — leaves no trace in a diff, so it is
   only checkable against a stated intent. It reports the row
   count, how many dependency rows it removed, any relocations it found, and the signal
   counts. Flags: [references/parameters.md](references/parameters.md).

2. **Read `indexed.diff` and write the plan.** Coordinates come from the left-hand column
   and address the original, so they stay stable across redrafts. Classify each row using
   [references/condensing-rubric.md](references/condensing-rubric.md) — the rubric works by
   classification, and the operation follows from the class. The plan's shape and its rules
   are in [references/plan-language.md](references/plan-language.md). Write it to
   `.review/plan.json`.

   Two habits carry most of the value: keep the row that *names* an operation and collapse
   the repetition beneath it, and when a relocation is reported, give both ends the same
   treatment.

3. **Draft, and repair.** This is where the plan gets good.

   ```bash
   python3 assets/review.py draft --into .review
   ```

   It prints the condensed diff, then the density figures and every rejection, and exits
   non-zero when the plan does not compile. Read the rejection, fix the coordinates, run it
   again. Density is a prompt to look again, not a target to hit.

4. **Commit.** `python3 assets/review.py commit --into .review` writes
   `.review/condensed.diff`. It refuses while any rejection stands, so by default a
   condensed diff that misrepresents the change does not reach a reader.

5. **Interrogate.** Read the condensed diff end to end — that is the review — then pull the
   signals:

   ```bash
   python3 assets/review.py signals --into .review --severity medium
   ```

   Work the five dimensions in [references/review-rubric.md](references/review-rubric.md):
   concepts, algorithm choices, architecture and boundaries, fit and scope, blast radius
   and risk. **Fit and scope** is where machine-written code fails most often — an
   abstraction with one implementation, a block pasted twice, a `chore` that quietly adds
   behaviour, or a request only half-answered; the failure shapes and the honest limits of
   DRY/KISS/YAGNI/SOLID are in
   [references/fit-and-scope.md](references/fit-and-scope.md). Judging whether the change
   matches how the surrounding code already works needs sibling files open — a diff cannot
   show you a convention, and no signal covers it.

   When the change came out of a long autonomous run, look for that process's residue too:
   suppressed warnings, stubs inside a change offered as finished, tests that cannot fail,
   two names for one operation, citations to files that do not exist. Those are the
   `agentic.*` signals; where each comes from, and the two patterns no detector can reach,
   are in [references/agentic-patterns.md](references/agentic-patterns.md). The
   catalogue and each detector's limits are in
   [references/signals.md](references/signals.md). Read code outside the diff when a clue
   would change your judgment — most rows can be judged from the condensed diff, and the
   ones that cannot are usually the ones that matter.

6. **Write the review.** `python3 assets/review.py template --into .review` writes
   `.review/REVIEW.md` with the required sections and one disposition line per signal.
   Answer every section. Resolve each high-severity signal as `**addressed**:` (a real
   finding) or `**dismissed**:` (why it does not apply here).

7. **Grade it.** `python3 assets/review.py grade --into .review` prints `CHECK:` lines and a
   `REPORT_RESULT:` verdict, exiting non-zero on any gap. It cannot tell you a finding is
   *right*; it does refuse a review with an empty section, surviving placeholder text, an
   ambiguous verdict, an unresolved high-severity signal, or a **Confidence and basis**
   section that fails to separate `Verified:` from `Unverified:`. Repair and re-run until
   it passes, then hand the review over.

   That last check is what makes the review something to rely on. Fluent output is not
   evidence, and a review of machine-written code is itself machine-written: stating where
   your evidence stops is the only thing that keeps one from laundering the other. A short
   honest `Unverified:` list is worth more than a long confident review, and where the
   evidence does not reach far enough to ship, `needs-discussion` is the accurate answer.

## Reviewing after another agent

When a review of this change already exists, the valuable contribution is the **delta** —
what the first pass never looked at, and where you disagree.

```bash
python3 assets/review.py audit --into .review --prior their-review.md
```

`audit` reports the high-severity signals the prior review never engages, the files it cites
that the change does not touch, and the dimensions its wording never reaches. Exit `1` means
blind spots. Prefer investigating each one and forming your own view over forwarding the
tool's question — a signal is a prompt for a reviewer, not a review. Condense the change
yourself before reading their write-up where you can; reading it first anchors you to their
framing. The full method, including two failure modes that recur in agent reviews, is in
[references/second-opinion.md](references/second-opinion.md).

## Running inside a self-prompting loop

`python3 assets/review.py state --into .review --json` reports position as data: `phase`,
`done`, a single `next_action`, current `blockers`, and the two gates. Every field is
derived from files on disk, so an interrupted loop resumes at the right step and two calls
agree. The loop is `until state.done: perform state.next_action`.

`done` is computed from the gates — the plan compiles, the condensed diff exists, every
section is answered, every high-severity signal is resolved — never from the agent's own
account of its progress. Contract, phase table, and budgeting advice:
[references/loop-integration.md](references/loop-integration.md).

## Out of scope

Style, formatting, naming conventions, import order, missing nil checks. The linter has
those and the model that wrote the code is good at them; attention spent there is attention
not spent on the four dimensions. If a supposed nit turns out to change behaviour, it was
never a nit — say so.

This is also not a security scanner. The `bound.*` signals are trust-boundary prompts; the
sibling `security-posture-audit` skill does that job properly.

## Deliverable

- **`.review/condensed.diff`** — the change reduced to the rows carrying a decision,
  provably derived from the original by dropping, collapsing, and trimming alone.
- **`.review/signals.json`** — every observation with its question and severity.
- **`.review/REVIEW.md`** — the review: an explicit `Verified:`/`Unverified:` basis, a
  plain-language summary, findings across the five dimensions, a disposition for every high-severity signal, and exactly one verdict from
  `ship`, `ship-with-followups`, `needs-changes`, `needs-discussion`.
- The `grade` result stated plainly — including if you could not get it to pass.
- When auditing prior work: the **delta**, saying what the earlier review missed.

## Make the architecture questions checkable

Layering questions stay guesses until the team's rules live somewhere a tool can read.
`design-rules.json` is that file: named layers, forbidden dependency edges, the accepted
third-party roots, size budgets, and free-text invariants.

Do not write it from scratch, and do not guess it on the user's behalf. Derive it and ask:

```bash
python3 assets/review.py propose --repo . --into .baseline
```

This walks the tree, resolves every import to an area of the repo or a third-party root,
measures the shapes already present, and writes `.baseline/questions.json` — batches shaped
for the host agent's structured question tool (`AskUserQuestion` in Claude Code), at most
four questions per batch with two to four options each. **Put each batch to the user
verbatim**, then write their choices to a JSON file and run:

```bash
python3 assets/review.py adopt --into .baseline --answers answers.json --out design-rules.json
```

Every proposed rule carries the number of places that violate it today, because that is what
decides the answer: zero is free to adopt, eighteen is a migration. The scan can see that
`core` never imports `web`; it cannot see whether that is a rule or a coincidence, and that
gap is exactly what the questions are for. Anything unanswered is declined — silence is not
agreement, and adopting nothing beats rules nobody believes. The answer format and the
authoring guide are in [references/design-baseline.md](references/design-baseline.md);
[assets/design-rules.example.json](assets/design-rules.example.json) is a hand-written
starting point if you would rather not scan.

Without a baseline the skill still works. Layering signals simply stay heuristic, and `open`
says so rather than pretending otherwise.

## Working without the tooling

If python3 is unavailable, the rubrics carry the whole method: read the diff against
[references/condensing-rubric.md](references/condensing-rubric.md), then review against
[references/review-rubric.md](references/review-rubric.md) and write the same sections by
hand. You lose the compiler's guarantees and the extracted signals — say so in the review,
rather than letting a reader assume they were checked.

## When you are not ready to review

Concept review needs an understanding of the subsystem, and a diff is a poor teacher. If the
surrounding design is unfamiliar, say so and go learn it first — the sibling
`feynman-walkthrough` skill is built for that and leaves behind a durable explainer you can
pin as a baseline for later reviews. A confident review of a system you do not understand is
worse than no review, because it gets trusted.

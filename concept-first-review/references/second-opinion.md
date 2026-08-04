# Second opinion — auditing a review someone else already wrote

A review is a claim about a change. Like any claim, it can be checked — and the cheapest,
most valuable check is not "is this finding correct" but **"what was never looked at."**

That is what `audit` does. Point it at a review another agent (or a person) already
produced, and it reports the high-severity signals that review never engages, the files it
cites that the change does not touch, and the review dimensions its wording never reaches.

```bash
python3 assets/review.py open  --diff pr.diff --into .review --rules design-rules.json
python3 assets/review.py audit --into .review --prior their-review.md
```

Exit `0` when nothing was missed, `1` when there are blind spots — so a loop or a CI step
can branch on it.

## What it reports

| Line | Meaning |
|---|---|
| `COVERED: <signal>` | The prior review engages this signal — by id, by `file:line`, or by quoting its evidence. |
| `BLIND-SPOT: <signal> — <question>` | A **high-severity** signal the prior review never touches. |
| `OUT-OF-SCOPE: <path>` | The review cites a file this change does not modify. |
| `THIN: <dimensions>` | No wording suggests the review reached these review dimensions. |
| `NO-VERDICT:` | The review never states a ship / no-ship position. |
| `AUDIT_RESULT: N blind spot(s)` | The summary line. |

Engagement is judged generously on purpose. Any of the three forms counts, because the goal
is to find what was never examined — not to police how someone cites things.

## How to read each verdict

**`BLIND-SPOT` is the one that matters.** It says a specific question with real
consequences was not asked. Your job is to ask it: read the location, decide whether it is
a genuine finding, and add it. Do not simply forward the tool's question to the author —
a signal is a prompt for a reviewer, not a review.

**`OUT-OF-SCOPE` is ambiguous and worth a look.** Citing an untouched file is often
legitimate context ("the caller in `billing/legacy.py` assumes the old shape"). It is
occasionally a claim about the wrong file, which is the more interesting case, and it is
sometimes a hallucinated path — which tells you a great deal about how much of the rest to
trust.

**`THIN` is a weak signal and is labelled as such.** It matches vocabulary, and vocabulary
is a poor proxy for thought. A short review that says exactly the right thing about
architecture without using the word "architecture" will be flagged. Confirm by reading
before you conclude anything from it.

**`NO-VERDICT` is usually real.** A review that ends without a position has not finished.

## The output is the start of your review, not the end

`audit` finds absence. It cannot tell you whether a finding that *is* present is correct,
whether the prior reviewer's judgment was sound, or whether the change is a good idea. What
it gives you is a floor: whatever else your second opinion says, it should not repeat the
first review's blind spots.

The productive shape for a second opinion is a **delta**, not a replacement:

1. Run `audit` and read the blind spots.
2. Condense the change yourself (`draft` → `commit`) so you are reading the decisions
   rather than re-reading the diff the first reviewer already summarised. Reading their
   review first is convenient and it anchors you — condense first if you can.
3. Investigate each blind spot at its location, and form your own view.
4. Write up only what is new: the missed findings, the claims you disagree with and why,
   and whether you would change the verdict.

## When the prior review was written by an agent

Two failure modes come up often enough to check for directly.

**Fluent coverage of the wrong altitude.** A prior review can be long, well-organised, and
entirely about naming and style — thorough-looking, and silent on every high-severity
signal. Blind spots make this visible immediately, which is the case `audit` was built for.

**Confident claims with no anchor.** Findings with no file or line, or with a citation to
a file the change never touched, cannot be checked and should not be inherited. Verify
before you carry a claim forward into your own review; an unverified finding that survives
a second review has been laundered, not confirmed.

## Auditing your own work

Nothing stops you pointing `audit` at a review you just wrote, and it is worth doing before
you hand one over — it is the same check `grade` makes about completeness, but from the
outside, against the signals rather than against the template. If your own review lights up
with blind spots, it is not finished.

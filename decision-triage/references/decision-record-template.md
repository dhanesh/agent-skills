# Decision record template

Copy the variant that matches your path. Section **order matters** — `assets/decision_lint.py`
fails a record whose `## Decision` appears before `## Criteria` and `## Assessment`, because
that ordering is the whole anti-sycophancy mechanism. Keep the headings verbatim.

Scores are integers 1–5 on each criterion. Confidence is an integer percentage: your
probability that, at the review date, this will look like the right call.

---

## Full path

```markdown
# Decision: <the choice, in one sentence>

- Date: 2026-07-25
- Decider: <one name>
- Path: full
- Reversibility: irreversible
- Cost of reversal: <money, time, credibility — be concrete>

## Criteria
- C1: <independently assessable criterion>
- C2: <...>
- C3: Opportunity cost — what the next-best forgone option would have given us

## Options
- O1: <option>
- O2: <option>

## Assessment
- C1: O1=4 O2=2
- C2: O1=3 O2=5
- C3: O1=3 O2=3

## Outside view
<What happened to the last several people or teams who did roughly this? Base rates on
cost, time, and outcome.>

## Pre-mortem
- <It is twelve months on and this failed. Why? Failure mode one.>
- <Failure mode two.>

## Steelman of the rejected option
<The strongest honest case for what you are not choosing.>

## Decision
<The verdict, in one sentence.>

## Confidence
70%

## Tripwires
- <Observable condition that would mean this is going wrong, and by when.>

## Review date
2026-10-25

## Outcome
- Reviewed:
- Correct:
- Notes:
```

---

## Fast path

Same contract, fewer sections. The outside view, pre-mortem and steelman are dropped because
a two-way door does not earn them — the tripwires and the review date do that work instead.

```markdown
# Decision: <the choice, in one sentence>

- Date: 2026-07-25
- Decider: <one name>
- Path: fast
- Reversibility: reversible
- Cost of reversal: <usually small — say how small>
- Deadline: 2026-07-28

## Criteria
- C1: Good enough bar — <what "clears the bar" observably means>

## Options
- O1: <the first option that cleared the bar>

## Assessment
- C1: O1=4

## Decision
<The verdict, in one sentence.>

## Confidence
65%

## Tripwires
- <What would send this back for a rethink.>

## Review date
2026-08-25

## Outcome
- Reviewed:
- Correct:
- Notes:
```

---

## Filling `## Outcome` at the review date

Leave the three fields blank until the review. Then:

- **Reviewed:** the date you actually reviewed it.
- **Correct:** `yes` or `no` — judged on what was knowable when you decided, not on how the
  world happened to break. A well-made decision that lost to bad luck is `yes`.
- **Notes:** what you'd tell yourself if you were deciding this again.

Once several records carry outcomes, `python3 assets/calibration.py <dir>` turns them into a
Brier score and an over/under-confidence report.

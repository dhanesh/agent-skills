# harvard-case-method

Run a real Harvard-style business case study with an AI as case writer and discussion
leader — decision-forcing, outcome withheld, decision committed before the reveal.

```bash
npx skills add dhanesh/agent-skills --skill harvard-case-method
```

## Why this exists

There is a popular prompt going around: *"Analyze [company] using the Harvard Business
School case study method"*, followed by THE SITUATION → THE DECISIONS → THE EXECUTION →
THE OUTCOME, with questions like *"what did they bet on that turned out to be right"* and
*"what pattern made this work"*.

It reaches for the right tradition and produces the wrong artifact. An HBS case presents
only what was knowable at a decision date, **withholds what happened**, makes you commit a
recommendation in the protagonist's shoes, and reveals the outcome afterwards (the
"B-case"). The withholding is the pedagogy. A prompt that leads with the ending is a
retrospective post-mortem of a winner — which trains hindsight bias and survivorship bias,
the two documented failure modes of learning from success stories. Sources and the full
comparison are in
[references/case-method-evidence.md](references/case-method-evidence.md).

Nothing in the original is discarded. Its pattern-extraction questions are good; they are
moved to the debrief, after the learner has already committed — which is exactly where HBS
puts the reveal.

## What it does

1. Picks a company **and a decision moment**, plus a comparator that faced the same
   situation and did not survive.
2. Scaffolds a case pinned to a decision date. Everything after that date is contraband.
3. Writes the A-case from contemporaneous sources: situation, protagonist, what was known
   (cited), what was genuinely uncertain, comparators, and a decision that ends in a
   question.
4. **Lints it** — and refuses to seal a case that leaks the ending.
5. Seals the B-case, runs the discussion, and makes the learner commit a decision with
   its disconfirming evidence and a falsifier.
6. Reveals, scores on **process not outcome**, splits the result across decision quality /
   execution / luck, and extracts one transferable claim the learner can test.

## The tooling

`assets/casekit.py` (python3, stdlib only) makes the discipline mechanical rather than
aspirational:

```bash
python3 assets/casekit.py new stripe-2011 --company Stripe --decision-date 2011-06-30
python3 assets/casekit.py lint stripe-2011      # L0–L5; names every offending line
python3 assets/casekit.py seal stripe-2011      # refuses while lint fails
python3 assets/casekit.py commit stripe-2011    # refuses a decision with no falsifier
python3 assets/casekit.py reveal stripe-2011    # refuses before a decision is committed
python3 assets/casekit.py status stripe-2011
```

Six lint rules, each blocking one way an AI case study goes wrong:

| Rule | Blocks |
|---|---|
| L0 | scaffold residue (`TODO`, `<PLACEHOLDER>`) |
| L1 | dates after the decision date — the commonest hindsight leak |
| L2 | outcome language ("turned out to be right", "went on to", "in hindsight") |
| L3 | a case that doesn't end in a decision to be made |
| L4 | figures with no citation — model-memory financials |
| L5 | no comparator named — the survivorship guard |

Full flag and rule reference: [references/parameters.md](references/parameters.md).
Facilitation moves and the post-reveal scoring rubric:
[references/facilitation-playbook.md](references/facilitation-playbook.md).

Sealing is base64 — a speed bump, not encryption. The meaningful guarantee is the recorded
ordering in `state.json`: the decision's checksum is written before `reveal` will run.

## Honest limits

- It reproduces the analytical loop and the discipline of committing before knowing. It
  does not reproduce the cohort, the credential, the recruiting pipeline, or classmates
  who disagree from experience you don't have.
- The lint catches leaked dates and stock hindsight phrases. A leak written in original
  prose, with no dates and no denylisted phrase, passes. The rules raise the floor;
  reading the case is still the ceiling.
- Case quality is bounded by source quality. A case built from a founder's retelling is a
  case built from marketing.

## Development

```bash
make gate-skill SKILL=harvard-case-method   # validate + lint + unit suite + outcome eval
python3 harvard-case-method/assets/test_casekit.py
python3 harvard-case-method/eval/run_eval.py
```

The outcome eval runs two arms of the same synthetic company — one written as the skill
prescribes, one written the way an AI answers the popular prompt — and requires the second
to be refused at every stage. License: MIT.

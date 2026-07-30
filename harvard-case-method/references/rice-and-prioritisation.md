# RICE, done so it isn't theatre

Loaded when the user says "I have a product requirement and I need a RICE analysis on it".

RICE was built at Intercom to improve its own roadmap decisions:

```
score = (Reach × Impact × Confidence) ÷ Effort
```

The framework is fine. What breaks it is that all four inputs are guesses, and a single
score with three significant figures hides that completely. This skill's job is to make the
guessing visible, not to produce a bigger number faster.

## Who owns which input — this is the whole thing

The ownership split is not a stylistic preference; it follows from what each term *means*.

| Input | Owner | Why |
|---|---|---|
| **Reach** | **Agent** | How many people or events per period. This is a *measurement* — a segment, a cohort query, a support-ticket count. It is research labour, and the agent should do it and cite it. |
| **Impact** | **Agent proposes, user confirms** | Per-person effect on the goal, on Intercom's scale. The agent can argue from evidence (a comparable feature moved conversion by X); the user knows their goal and overrules. |
| **Confidence** | **User, always** | Explicitly "how certain the team is about the Reach and Impact estimates". It is a statement about *their own epistemic state regarding the agent's numbers*. An agent that sets its own confidence is grading its own homework. |
| **Effort** | **User, always** | Person-months from the people who will build it. The agent has no access to the team's velocity, tech debt, or who is on leave in March. Any number it supplies is fabrication. |

So the working loop is: **agent derives and sources Reach, argues Impact, then stops and
asks.** Confidence and Effort come back from the user, and the score is computed from all
four. If the user asks the agent to fill in Effort, say what it is — a guess wearing a
number — and ask for a real one.

## The scales, exactly

**Impact** (deliberately non-linear — a massive-impact project really does deliver several
times a low-impact one):

| Value | Meaning |
|---|---|
| 3 | massive |
| 2 | high |
| 1 | medium |
| 0.5 | low |
| 0.25 | minimal |

`rigor.py rice` rejects anything off this scale. A 4 is not "extra massive", it is someone
inventing headroom to make their item win.

**Confidence**, per Intercom's anchors:

| Value | Meaning |
|---|---|
| 100% | well supported by data |
| 80% | strong evidence |
| 50% | informed guess |
| ≤20% | pure hunch |

## The five ways RICE turns into theatre

`rigor.py rice` mechanises the first four. The fifth is yours.

1. **Reach with no time period.** "Reach: 1200" is meaningless — 1200 per month ranked
   against 900 per quarter is a 4× error hiding in plain sight. Every reach needs `/month`
   or `/quarter`, and **all items in a sheet must share one period**. This is the single
   most common arithmetic error in real RICE sheets, and the tool fails on it.
2. **Reach with no source.** Reach is the one input that is genuinely measurable. If it
   came from a feeling rather than a query, the whole score is decoration. The tool
   requires a citation on every reach.
3. **Confidence used as a fudge factor.** The intended use is to express uncertainty *about
   Reach and Impact*. The actual use is often to tune a score until the favoured item wins.
   Confidence at or below 50% is Intercom's "informed guess" — the tool surfaces those
   items and says the honest thing: **low confidence is a signal to go get evidence, not a
   discount factor to multiply through and forget.**
4. **Precision theatre.** Four rough estimates multiply into a number printed to the unit,
   and a 4% gap between two items reads as a decision. It isn't. The tool groups items
   whose scores are within **20%** and refuses to distinguish them — break those ties on
   strategy, sequencing, or dependencies, never on the score.
5. **Effort optimism.** RICE has no defence against the planning fallacy, and effort is
   systematically underestimated. The fix is a reference class: what did the last three
   things of roughly this size actually take, end to end? Use *that* distribution, not the
   estimate. The tool cannot check this — ask for it.

## What RICE structurally cannot see

Say these out loud when presenting a ranking, because the number will otherwise be taken
as the answer:

- **Dependencies and sequencing.** A low-scoring item that unblocks three high-scoring ones
  is mispriced by RICE, which scores each item in isolation.
- **Strategic fit.** RICE optimises for reach × impact per unit effort. A bet that opens a
  new segment scores badly and may still be the right call.
- **Compounding and one-way doors.** Platform work and reversibility are invisible to the
  formula.
- **The distribution.** A tiny reach with an enormous per-user impact on your highest-value
  segment is not the same as broad shallow value, even at an identical score.

RICE is a **ranking aid that surfaces disagreement about inputs**. That second part is most
of its value: when two people score the same item differently, the argument that follows —
about reach, or about what "high impact" means here — is the actual work. The number is a
by-product.

## Running it

```bash
python3 assets/rigor.py rice rice.md
```

Sheet format — one line per item:

```markdown
## Items

- Bulk fee upload | reach=1200/quarter | impact=2 | confidence=0.8 | effort=3 | src: cohort query [^1]
- Autopay nudges  | reach=9000/quarter | impact=0.5 | confidence=0.5 | effort=2 | src: analytics [^1]

## Sources
[^1]: https://…
```

A sheet that fails validation is **not ranked** — a score computed from an off-scale impact
or a confidence of 150 out-ranks every honest row, and printing it would publish a number
that looks authoritative and isn't.

## When RICE is the wrong tool

If there are fewer than about five candidates, rank them by argument; the formula adds
ceremony and no information. If the decision is one irreversible bet rather than an ordering
of many small ones, this is a **decision**, not a prioritisation — run the main workflow in
[../SKILL.md](../SKILL.md) instead, with a premortem and forecasts.

## Sources

- RICE Scoring Model — ProductPlan — <https://www.productplan.com/glossary/rice-scoring-model>
- RICE scoring model, formula and scales — <https://whatfix.com/blog/rice-scoring-model/>
- Understanding RICE Scoring: framework, pros, cons — <https://dovetail.com/product-development/rice-scoring-model/>
- RICE vs ICE with user research (the "theatre" failure mode) — <https://evelance.io/blog/rice-vs-ice-how-to-use-prioritization-frameworks/>

# The Decision Quality chain, and why quality is the minimum

The scoring spine of this skill is **Decision Quality** (DQ), from the Stanford decision
analysis tradition — Ronald Howard defined the profession in 1964; Carl Spetzler, Jennifer
Meyer and Claudia von Winter formalised the six-element model in *Decision Quality* (2016).
It is the standard framework in strategy consulting and pharmaceutical portfolio decisions,
and it exists to do one thing this skill needs: **evaluate a decision process before the
outcome is known.**

## The six links

| Link | The question | Failed when |
|---|---|---|
| **Frame** | Are we deciding the right thing? | The real decision is one level up, or the scope is unbounded |
| **Alternatives** | Do we have a real set of options? | Two options ("do it / don't"), or all variants of one idea |
| **Information** | Is what we know relevant and reliable? | Confident numbers with no source; no base rate |
| **Values** | What are we optimising for, and what will we trade? | Never stated, so the tie-break is arbitrary |
| **Reasoning** | Does the logic connect the four above to a choice? | Conclusion first, justification after |
| **Commitment** | Will this actually be acted on? | A decision nobody owns, with no date |

## Chain semantics — the part people drop

A chain is only as strong as its **weakest link**. Decision quality is therefore the
**minimum** across the six, not the average. Superb information cannot compensate for a
frame that answers the wrong question; a brilliant frame cannot rescue an option set of
two. This is why the debrief names *one* weakest link rather than issuing six scores that
average into something reassuring.

It also sets the coaching target: work the weakest link next time, not the one you enjoy.

## Decision ≠ outcome

The framework's founding distinction: you cannot control outcomes, only the quality of the
process producing the decision. A good decision can have a bad outcome (bad luck) and a bad
decision a good one (good luck). Judging decisions by their outcomes is the most common
error in management, and it is the error this skill's debrief is built to refuse.

Concretely: when a forecast resolves against the user, the first question is not "what did
you get wrong" but **"what was knowable at the time?"** Sometimes the honest answer is
nothing, and the decision was fine.

## Why the tool does not score the chain

`casekit.py` checks that each link is *present and structurally complete* — a section
exists, at least three alternatives are listed, forecasts parse and carry probabilities
strictly between 0 and 1. It deliberately does **not** grade the links. Whether a frame is
the right frame is a judgement; a regex that pretended otherwise would produce a number
that looks like rigour and isn't. Completeness is mechanical, quality is the coach's call,
and the tool prints exactly that reminder after every score.

## The two disciplines bolted onto the chain

### Premortem, before Commitment

Prospective hindsight — imagining an event has *already happened* — increases the ability
to correctly identify reasons for an outcome by about **30%**, and produces roughly twice
as many action-based (rather than abstract) reasons. The 1989 study behind it (Mitchell,
Russo & Pennington, *Journal of Behavioral Decision Making*) found the active ingredient is
**certainty**, not futurity: people asked to explain a future event *with certainty*
generated more reasons than those considering a past event with uncertainty.

The operational consequence is that the prompt matters. "It is 30 September and this
failed — write how" works. "What might go wrong?" does not. Gary Klein's premortem builds
on exactly this finding.

It is also the *safe* form of consider-the-opposite: it asks for one causal story, not a
long list of objections, which is the condition under which debiasing backfires (see
[case-method-evidence.md](case-method-evidence.md)).

### Calibrated forecasts, after Commitment

The Good Judgment Project found a roughly **one-hour** probabilistic-reasoning module
improved Brier scores **6–11%** over control, with the effect persisting for years, and
practice contributing independently of training. That is the strongest dose-response signal
in this entire space, and it is the reason `decision.md` demands dated probability
forecasts rather than prose confidence.

**Brier score** = mean squared error of the forecasts, `mean((p − outcome)²)`. Zero is
perfect; **0.25 is what you get saying 0.5 about everything**; above 0.25 you are being
actively misled by your own confidence. The **calibration gap** (mean confidence minus hit
rate) is the more legible number for coaching: positive is over-confidence, negative
under-confidence.

Two honest limits, both printed by the tool:

- **Fewer than ten resolved forecasts is directional, not a verdict.** Small-n Brier scores
  swing wildly on a single resolution.
- **Resolution requires unambiguous claims.** "The launch goes well" cannot be scored. If a
  forecast can't be settled by its date without argument, it isn't a forecast — rewrite it
  at commit time, not at resolution time.

## Sources

- Decision quality (six elements, weakest-link chain) — <https://en.wikipedia.org/wiki/Decision_quality>
- Ronald Howard, Stanford Decision Analysis — <https://dara.stanford.edu/people/ronald-howard>
- Decision Quality checklist, six elements — <https://dectrack.com/en/methods/decision-quality>
- Mitchell, Russo & Pennington (1989), "Back to the Future: Temporal Perspective in the Explanation of Events", *JBDM*; via Klein, "Performing a Project Premortem" — <http://homepages.se.edu/cvonbergen/files/2013/01/Performing-a-Project-Premortem.pdf>
- Mellers et al., GJP training and practice effects, *Judgment and Decision Making* 11(5) — <http://goodjudgment.com/wp-content/uploads/2018/12/jdm16511.pdf>
- Kahneman & Sibony, Mediating Assessments Protocol (decision hygiene) — <https://www.theuncertaintyproject.org/tools/the-mediating-assessments-protocol>

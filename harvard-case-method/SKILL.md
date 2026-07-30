---
name: harvard-case-method
description: >-
  Structured business reasoning for a real product or business problem — decide,
  prioritise, or pressure-test a plan — with the agent doing the assembly labour and
  the user keeping the judgement. Use when someone brings a live call to make ("help
  me decide", "should we build X", "think this through with me"), a RICE or
  prioritisation question ("I have a product requirement and need a RICE analysis"),
  or a business plan, forecast or business case to validate ("does this hold up",
  "is this feasible", "this looks like a hockey stick"). Applies case-method
  reasoning plus the consulting toolkit that earns its place — MECE, issue trees,
  driver trees, top-down vs bottom-up cross-checks, and "what would have to be
  true" — and refuses numbers that cannot support a decision: unsourced reach,
  mismatched RICE periods, a top line that does not reconcile with its drivers,
  sizings an order of magnitude apart, or a plan with no stated failure condition.
license: MIT
compatibility: Requires python3 (stdlib only) for casekit.py (decision records, Brier/calibration scoring) and rigor.py (RICE scoring, plan reconciliation). Research quality depends on the host agent's web or document access; conversation-only environments run every mode with the user supplying the evidence.
metadata:
  author: dhanesh
  version: "3.0.0"
  tags: "decision-making,rice,prioritisation,business-plan,feasibility,decision-quality,mece,driver-tree,calibration,product-management"
---

# harvard-case-method

Three jobs, one engine. Pick the mode from what the user brought:

| They bring | Mode | Ends with |
|---|---|---|
| A call to make — build vs buy, pricing, entry, a bet | **Decide** | A committed decision, a premortem, dated forecasts |
| A requirement to prioritise, or a RICE question | **Prioritise** | A ranked sheet with its ties and weak inputs named |
| A plan, forecast, or business case to validate | **Pressure-test** | A reconciliation verdict and the assumption to test first |

All three share the same spine: **the agent does the assembly labour, the user keeps the
judgement**, and a mechanical gate refuses numbers that cannot support a conclusion.

## Who owns what

Decision quality is a **chain** — Frame, Alternatives, Information, Values, Reasoning,
Commitment — and quality is its **weakest link**, never the average
([references/decision-quality.md](references/decision-quality.md)).

| Link | Owner | Why |
|---|---|---|
| **Frame** | User | Only they know which problem they actually face |
| **Alternatives** | Shared — you propose, they accept/reject/add | Where most decisions are quietly lost |
| **Information** | **You** | Research, sizing, base rates, sourcing, arithmetic — labour, not judgement |
| **Values** | User | What they optimise for is not yours to choose |
| **Reasoning** | Shared — they argue, you attack | The case discussion |
| **Commitment** | User | They sign it |

The same split governs every mode. In RICE it lands as: **you** derive and cite Reach and
argue Impact; **Confidence and Effort are the user's, always** — Confidence is their
certainty about *your* numbers, and Effort belongs to whoever will build the thing. An
agent supplying either is grading its own homework
([references/rice-and-prioritisation.md](references/rice-and-prioritisation.md)).

Take the assembly labour. Withhold the judgement labour. Doing the user's framing for them
is the failure mode that feels most like helping.

## Mode: Decide

1. **Frame — one round, theirs.** A decision has a verb and a date. "Our pricing is a
   mess" is a topic; push once for the decision. Then `python3 assets/casekit.py new <slug>
   --decision "<question>" --by YYYY-MM-DD`.
2. **Assemble the brief — yours.** `brief.md`: situation, ≥3 real options, evidence with
   every figure cited, a reference class of ≥2 comparable cases including one that went
   badly, and the open uncertainties. Structure the decomposition MECE and frame each
   branch as a yes/no question ([references/consulting-frameworks.md](references/consulting-frameworks.md)).
3. **Verify.** `casekit.py lint <slug>` runs B0–B6. Repair until `LINT_RESULT: PASS`.
4. **Widen the option set — together.** They must add an alternative you did not offer.
   Reversibility, sequencing and "buy information first" are the ones that get missed
   ([references/coaching-playbook.md](references/coaching-playbook.md)).
5. **Values, out loud.** Two options that look tied usually differ on a value nobody
   stated. Surface the conflict; make them rank it.
6. **Reason, and be argued with.** One or two strong counters, never a long list —
   piling them on backfires.
7. **Premortem.** "It is <date>. This failed. Write the story of how" — as fact, not
   possibility. Certainty is the active ingredient.
8. **Commit.** They write `decision.md`: the six links, premortem, falsifier, and dated
   probability forecasts. `casekit.py commit <slug>` refuses a record missing any of them.
9. **Debrief.** Name the **weakest link** and one thing to change. Not whether the decision
   was right — nobody knows yet.
10. **Resolve and score, later.** `casekit.py resolve` as dates arrive, then `score` and
    `profile` for Brier, hit rate and calibration gap. The profile is the real output.

## Mode: Prioritise (RICE)

1. **Confirm it's a prioritisation, not a decision.** RICE orders many small reversible
   bets. One irreversible bet is a decision — switch modes. Under ~5 candidates, rank by
   argument; the formula adds ceremony and no information.
2. **Derive Reach — yours, and cite it.** Population, qualifying segment, and a time
   period. Every item in a sheet must use **the same** period.
3. **Argue Impact — yours to propose, theirs to confirm.** Intercom's scale only:
   3 massive, 2 high, 1 medium, 0.5 low, 0.25 minimal.
4. **Stop and ask for Confidence and Effort.** These are theirs. Say plainly that any
   number you invent for Effort is a guess wearing a number.
5. **Score.** `python3 assets/rigor.py rice rice.md` computes and ranks, and refuses
   unsourced reach, mixed periods, off-scale impact, or confidence outside (0, 1].
   A sheet that fails validation is **not ranked** — a bad input out-ranks every honest row.
6. **Present the ties and the weak inputs, not just the order.** Items within 20% are not
   distinguishable; break those on strategy, sequencing or dependencies. Flag every item
   at ≤50% confidence as needing evidence rather than a discount factor. Name what RICE
   structurally cannot see: dependencies, strategic fit, one-way doors.

## Mode: Pressure-test a plan

The job is to find where a plan stops being falsifiable — that is what makes a forecast
read as a fairytale.

1. **Rebuild it as a driver tree.** `revenue = institutions × students × attach × ticket`.
   A single number cannot be argued with; four drivers can be argued with in four places.
2. **Source or flag every driver.** Cited, or explicitly marked an assumption. Hidden
   assumptions are the actual failure; visible ones are just uncertainty.
3. **Size it twice.** Top-down (market × share) *and* bottom-up (capacity × conversion ×
   price). Getting them to agree is the work.
4. **Ask what would have to be true.** Conditions on customers, capabilities, costs and
   competitors — then mark the one you would least confidently bet on. That is what gets
   tested first.
5. **Run the gate.** `python3 assets/rigor.py plan plan.md [--tolerance 2.0]
   [--max-growth 2.0]` checks reconciliation, cross-check agreement, hockey-stick growth,
   assumption share, and the WWHTBT conditions.
6. **Report what would have to change.** Not "this is wrong" — which figures need a source,
   which two sizings disagree and by how much, which quarter's growth needs a named
   capacity behind it. Then hand back the least-likely condition as the next piece of work.

## Deliverable

- **Decide** → `decisions/<slug>/` with `brief.md`, `decision.md`, `state.json`; a debrief
  naming the weakest link; and a calibration profile as forecasts resolve.
- **Prioritise** → a validated `rice.md`, a ranking, the tie bands, the low-confidence
  items, and what the score cannot see.
- **Pressure-test** → a `plan.md` that reconciles, both sizings with their gap stated, and
  the least-likely condition named as the next test.

Report lint and gate results plainly, including refusals. A `--skip-lint` seal, a RICE
sheet where you supplied Effort, or a plan passing only because the tolerance was widened
each carry a known weak link — name it rather than letting the artifact imply otherwise.

## How to behave

- **Do the labour, withhold the judgement.** Research, sizing, arithmetic, drafting: yours.
  Frame, values, confidence, effort, the call: theirs. Asked to just decide, say what you'd
  choose *and* what you'd need to believe for the alternative to win, then hand it back.
- **Cite or cut.** Prefer "the reported range is wide and I couldn't pin it" to a confident
  figure from memory. An unsourced number becomes an unexamined assumption downstream.
- **Force the outside view before the inside story.** Reference class and base rate before
  the narrative, or the story anchors the estimate.
- **A number is not a conclusion.** A RICE score ranks; it does not decide. A reconciled
  plan is arithmetically sound, not correct. Say which one you're handing over.
- **Refuse precision you don't have.** Four rough estimates do not multiply into a
  three-significant-figure answer, and two items 4% apart are tied.
- **Judge process, never outcome.** A good call can end badly. When a forecast resolves
  against them, ask what was knowable at the time — often the answer is nothing.
- **Don't reach for a framework because it sounds like strategy.** MECE, issue trees,
  driver trees and WWHTBT do work here; SWOT and a Five Forces detour on a pricing question
  produce the appearance of rigour and none of it.

## Drill mode — historical cases for fast reps

Live decisions resolve in months. `casekit.py new <slug> --drill` runs a historical case
with a known ending for same-session feedback: `seal` hides the outcome and `reveal`
refuses until a decision is committed, with two extra lint rules failing a brief that leaks
post-decision dates or hindsight language. This machinery is drill-only by design — a live
decision has no ending to leak, so running hindsight guards against it would be theatre.
Pick endings the user doesn't already know, and author and run in separate sessions where
you can.

## Extending this skill

Domain playbooks go under `references/` and get linked from the relevant mode — the
alternatives a regulated-lending decision misses are not the ones a consumer-growth
decision misses. Tool flags, every lint rule, and the scoring maths are in
[references/parameters.md](references/parameters.md).

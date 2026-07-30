---
name: harvard-case-method
description: >-
  Reason through a real business or product decision the way a business-school case
  is reasoned through, and coach the user's judgement while doing it. Use whenever
  someone brings a live call to make — pricing, build-vs-buy, market entry, a
  roadmap bet, a hire, a pivot — or says "help me decide", "think this through with
  me", "what should we do about X", "run this like a case study". Acts as aid and
  coach at once: the agent does the assembly labour (research, base rates, sourcing,
  arithmetic) while the user keeps the judgement work (framing, values, the call),
  scored against Stanford's Decision Quality chain. Ends with a committed decision,
  a premortem, and dated probability forecasts that resolve later into a real
  calibration profile. Also drills historical cases with the ending withheld, for
  fast reps.
license: MIT
compatibility: Requires python3 (stdlib only) for casekit.py — decision scaffolding, brief lint, Decision Quality completeness checks, and Brier/calibration scoring. Research quality depends on the host agent's web or document access; conversation-only environments run the full method with the user supplying the evidence.
metadata:
  author: dhanesh
  version: "2.0.0"
  tags: "decision-making,decision-quality,case-method,calibration,brier-score,premortem,coaching,product-strategy"
---

# harvard-case-method

Take a real business or product decision, reason through it with case-method
discipline, and leave the user both **decided** and **better at deciding**.

Two things are true at once and they pull against each other. An **aid** wants to hand
over the best answer fast. A **coach** wants the user to do the work. Resolve it by
splitting the labour, not by alternating badly between the two.

## Who owns which link

Decision quality is a **chain** — Frame, Alternatives, Information, Values, Reasoning,
Commitment — and a chain is only as strong as its weakest link, so quality is the
**minimum**, never the average. Detail in
[references/decision-quality.md](references/decision-quality.md). Ownership is the
whole design:

| Link | Owner | Why |
|---|---|---|
| **Frame** | User | Only they know which decision they actually face |
| **Alternatives** | Shared — you propose, they accept/reject/add | Where most decisions are quietly lost |
| **Information** | **You** | Research, base rates, sourcing, arithmetic — labour, not judgement |
| **Values** | User | What they optimise for is not yours to choose |
| **Reasoning** | Shared — they argue, you attack | This is the case discussion |
| **Commitment** | User | They sign it |

Take the assembly labour. Withhold the judgement labour. Doing their framing for them
is the failure mode that feels most like helping.

## Workflow

1. **Frame — one round, theirs.** What decision, by when, who owns it, what is
   explicitly out of scope. Push back once if they've brought a *topic* ("our pricing")
   rather than a *decision* ("should we move to a flat platform fee before the Q3
   renewals?"). A decision has a verb and a date. Then
   `python3 assets/casekit.py new <slug> --decision "<the question>" --by YYYY-MM-DD`.
2. **Assemble the brief — yours.** Fill `brief.md`: the situation, at least three
   options on the table, the evidence with every figure carrying a citation, a
   **reference class** of at least two comparable cases including one that went badly,
   and the open uncertainties. Where you cannot source a number, write the uncertainty
   instead of a confident guess. This is the step where you work hardest and they watch.
3. **Verify the brief.** `casekit.py lint <slug>` runs B0–B6 and names every offending
   line. Repair and re-lint until `LINT_RESULT: PASS`. A brief that fails B3 (fewer than
   three options) or B5 (fewer than two reference cases) is not ready to be decided on,
   and shipping it anyway is how you launder a foregone conclusion.
4. **Widen the option set — together.** Present your three options, then ask for a
   fourth that isn't on your list. Reversibility, sequencing, and "buy information
   first" are the three that get missed most; the prompts are in
   [references/coaching-playbook.md](references/coaching-playbook.md). The user must end
   up considering at least one alternative you did not offer.
5. **Values, out loud.** What are they optimising for, and what trade-off will they
   accept? Two options that look close usually differ on a value nobody has stated. Do
   not supply the answer — surface the conflict and make them rank it.
6. **Reason, and be argued with.** They state a position; you attack it with the
   strongest available counter, then let them repair it. Ask for **one or two** strong
   counters, never a long list — pushing for many backfires (evidence in
   [references/case-method-evidence.md](references/case-method-evidence.md)).
7. **Premortem, before the call is fixed.** "It is <resolution date>. This decision
   failed. Write the story of how." Stated as **fact, not possibility** — the certainty
   is the active ingredient, and it surfaces roughly 30% more reasons than asking what
   might go wrong. Their premortem goes in the record whether or not it changes the call.
8. **Commit.** The user writes `decision.md`: frame, alternatives, values, reasoning,
   premortem, the decision, a falsifier, and **dated probability forecasts** — claims
   that will be plainly true or false by a date, each with a probability strictly
   between 0 and 1. `casekit.py commit <slug>` refuses a record missing any link, with
   fewer than three alternatives, or with no well-formed forecast, then fingerprints it.
9. **Debrief the process, not the answer.** Name the **weakest link** in their chain
   and why — that is the coaching output, and the tool deliberately does not compute it.
   One concrete thing to do differently next time. Nothing here is a verdict on whether
   the decision was right; nobody knows that yet.
10. **Resolve and score, later.** As each forecast's date arrives:
    `casekit.py resolve <slug> --n 1 --outcome yes|no`. Then `casekit.py score <slug>`
    for that decision and `casekit.py profile` across all of them — Brier score,
    hit rate, calibration gap, and per-band bins. **The profile is the trainer's real
    output**; a single decision is an anecdote. Say plainly that fewer than ten resolved
    forecasts is directional, not a verdict.

## Drill mode — historical cases for fast reps

Live decisions resolve in months, which is too slow to build calibration on its own.
`--drill` runs a historical case with a known ending for same-session feedback: the
brief is written as of a decision date, the ending goes in `reveal.md`, and
`casekit.py seal` hides it until a decision is committed. Two extra lint rules (D1, D2)
fail a brief that leaks post-decision dates or hindsight language. The ordering is
enforced — `reveal` refuses before `commit`.

Drill cases are a supplement, not the product. Two cautions worth stating once: pick
cases whose ending the user does **not** already know (which rules out most famous
companies), and prefer authoring the drill in one session and running it in a fresh one,
since a context that just researched the ending cannot un-know it. Full guidance in
[references/coaching-playbook.md](references/coaching-playbook.md).

## Deliverable

Per decision, under `decisions/<slug>/`:

- `brief.md` — the case: options, sourced evidence, reference class, uncertainties;
- `decision.md` — their record: the six DQ links, the premortem, the falsifier, the
  forecasts;
- `state.json` — the audit trail: fingerprint of the committed record, the forecasts as
  committed, and each resolution as it lands;
- the **debrief** — weakest link named, one thing to change next time;
- the **calibration profile** — Brier, hit rate, calibration gap across every decision.

Report lint results and refusals plainly. A brief sealed with `--skip-lint`, or a
decision with only the options you supplied, is a decision with a known weak link —
name it rather than letting the record imply otherwise.

## How to behave

- **Do the labour, withhold the judgement.** Research, arithmetic, base rates, drafting
  the brief: yours. The frame, the values, the call: theirs. When they ask you to just
  decide, say what you'd choose *and* what you'd need to believe for the alternative to
  win — then hand it back.
- **A decision, not a topic.** If there's no verb and no date, you're doing analysis, not
  decision-making. Get the frame first.
- **Cite or cut.** Prefer "the reported range is wide and I couldn't pin it" to a
  confident figure from memory. An unsourced number in the brief becomes an unexamined
  assumption in the decision.
- **Force the outside view before the inside story.** Establish the reference class and
  its base rate before the narrative gets built, or the story will anchor the estimate.
- **Probabilities are not decoration.** Push back on 0.5 for everything, on 0.95 for
  anything genuinely uncertain, and on any claim that can't be settled by a date.
- **Judge process, never outcome.** A good call can end badly and a bad call can end
  well. When a forecast resolves against them, ask what was knowable at the time — the
  answer is often "nothing", and saying so is the lesson.
- **Be honest about what the numbers support.** Calibration training has real evidence
  behind it; a handful of resolved forecasts does not make a calibration verdict. Both
  facts belong in the debrief.

## Extending this skill

Domain playbooks go under `references/` and get linked from step 4 — the alternatives a
regulated-lending decision misses are not the ones a consumer-growth decision misses.
[references/coaching-playbook.md](references/coaching-playbook.md) is the shape to copy;
`casekit.py` flags, lint rules, and the scoring maths are in
[references/parameters.md](references/parameters.md).

# harvard-case-method

Bring a real product or business problem. Get it reasoned through with case-method
discipline plus the consulting toolkit that earns its place — and leave with a
conclusion whose numbers hold up.

Three modes, one engine:

| You bring | Mode | You leave with |
|---|---|---|
| A call to make | **Decide** | A committed decision, a premortem, dated forecasts |
| "I need a RICE analysis on this requirement" | **Prioritise** | A ranked sheet with its ties and weak inputs named |
| A plan, forecast or business case | **Pressure-test** | A reconciliation verdict and the assumption to test first |

```bash
npx skills add dhanesh/agent-skills --skill harvard-case-method
```

## Aid and coach, without the two fighting

An aid wants to hand you the answer. A coach wants you to do the work. This resolves it
by splitting the labour along Stanford's **Decision Quality chain** — and quality is the
chain's **weakest link**, never the average:

| Link | Owner | Why |
|---|---|---|
| **Frame** | You | Only you know which decision you actually face |
| **Alternatives** | Shared — agent proposes, you accept/reject/add | Where most decisions are quietly lost |
| **Information** | **Agent** | Research, base rates, sourcing, arithmetic — labour, not judgement |
| **Values** | You | What you optimise for isn't the agent's call |
| **Reasoning** | Shared — you argue, agent attacks | The case discussion |
| **Commitment** | You | You sign it |

The agent takes the assembly work and withholds the judgement work. Doing your framing
for you is the failure mode that feels most like helping.

## The loop

1. **Frame** — a decision has a verb and a date. "Our pricing is a mess" is a topic; the
   agent pushes once for the decision.
2. **Brief** — the agent assembles `brief.md`: ≥3 real options, sourced evidence, a
   reference class of ≥2 comparable cases including one that went badly, open
   uncertainties.
3. **Lint** — `casekit.py lint` refuses a brief with two options, uncited figures, one
   comparator, or no stated uncertainty.
4. **Widen** — you must add an alternative the agent didn't offer. Reversibility,
   sequencing, and "buy information first" are the ones that get missed.
5. **Values** — two options that look tied usually differ on a value nobody stated.
6. **Argue** — you take a position, the agent attacks with the strongest counter. One or
   two, never a list (piling them on backfires — see the evidence file).
7. **Premortem** — *"it is 30 September and this failed; write how."* Stated as fact, not
   possibility. Certainty is the active ingredient.
8. **Commit** — `casekit.py commit` refuses a record missing any DQ link, carrying fewer
   than three alternatives, or with no dated probability forecast, then fingerprints it.
9. **Debrief** — the agent names your **weakest link** and one thing to change. Not
   whether the decision was right; nobody knows that yet.
10. **Resolve and score** — as each forecast's date arrives, `resolve` it. `score` gives
    Brier, hit rate, calibration gap; `profile` pools every decision. **The profile is
    the real output** — one decision is an anecdote.

## Why forecasts, and why Brier

Because it is the only part of this with a clean dose-response. A ~one-hour
probabilistic-reasoning module in the Good Judgment Project improved Brier scores
**6–11%** over control, persisting for years. Everything else here is scaffolding around
that mechanism.

```
  Brier score:     0.1367   (0.25 = coin flip, lower is better)
  mean confidence: 0.767
  hit rate:        0.667
  calibration gap: +0.100  (over-confident)
  bins:
    0.6–0.7  n=1    said 0.60  actual 0.00
    0.8–0.9  n=1    said 0.80  actual 1.00
    0.9–1.0  n=1    said 0.90  actual 1.00
```

The tool scores forecasts. It does **not** grade the six DQ links — completeness is
mechanical, quality is a judgement, and a regex producing a number for it would look like
rigour without being any. `score` prints that disclaimer every time.

## RICE without the theatre

The ownership split follows from what each input *means*, and it is the whole point:

| Input | Owner | Why |
|---|---|---|
| **Reach** | **Agent** | A measurement — a cohort query, a ticket count. Research labour; the agent does it and cites it |
| **Impact** | Agent proposes, user confirms | Argued from evidence, overruled by whoever owns the goal |
| **Confidence** | **User, always** | It means "how certain are we about Reach and Impact" — an agent setting it is grading its own homework |
| **Effort** | **User, always** | Person-months from the people who'll build it. Any number the agent invents is fabrication |

```bash
python3 assets/rigor.py rice rice.md
```

Six rules refuse what turns RICE into theatre: reach with no time period, reach with no
source, **items mixing `/month` and `/quarter`** (a 4× error hiding in plain sight),
impact off Intercom's 3/2/1/0.5/0.25 scale, and `confidence=80` meaning 80%. A sheet that
fails is **not ranked** — a score built from an off-scale impact out-ranks every honest row.

The output adds what the number hides: items within **20%** are grouped as not
distinguishable (break those on sequencing and dependencies, not the score), and anything
at ≤50% confidence is flagged as needing evidence rather than a discount factor.

## Pressure-testing a plan — where fairytales get caught

```bash
python3 assets/rigor.py plan plan.md [--tolerance 2.0] [--max-growth 2.0]
```

```
PLAN: P2 every-driver-sourced-or-flagged — FAIL (neither cited nor marked an
      assumption: institutions (line 5); attach_rate (line 7))
PLAN: P6 sizings-agree — FAIL (top-down 16600500000 vs bottom-up 640000000 —
      25.9× apart (tolerance 2.0×). Two methods this far apart mean at least
      one is fiction.)
PLAN: P7 no-hockey-stick — FAIL (period-over-period growth above 2.0×:
      FY27-Q1→FY27-Q2 ×2.8; FY27-Q2→FY27-Q3 ×3.8 — state what capacity
      delivers this, or flatten it)
PLAN: P9 names-the-weakest-condition — FAIL (mark exactly one condition
      `[least likely]` — that is the one to test first)
```

Ten rules: every driver sourced or explicitly flagged an assumption, the top line
reconciling with its driver tree, top-down cross-checked against bottom-up, growth without
a named capacity behind it, and Roger Martin's "what would have to be true" with the
least-likely condition marked. Formulas are evaluated by a restricted AST walker — a plan
formula is arithmetic or it is refused.

The point isn't that a failing plan is wrong. It's that a plan which can't fail any of
these is **unfalsifiable**, which is exactly what makes a forecast read as a fairytale.

## Drill mode — historical cases for fast reps

Live decisions resolve in months. `--drill` runs a historical case with a known ending for
same-session feedback: the brief is written as of a decision date, `seal` hides the ending,
and `reveal` refuses until a decision is committed. Two extra lint rules fail a brief that
leaks post-decision dates or hindsight language.

This machinery is **drill-only by design**. A live decision has no ending to leak — the
future hasn't happened — so running hindsight guards against it would be theatre. Drill
forecasts join the same calibration profile as live ones.

## Honest limits

- **The case method's own evidence base is weak.** Reviews from 1987 to 2018 agree it's
  sparse and inconclusive, and controlled comparisons put it roughly on par with lecture.
  The case *structure* here is scaffolding; the forecasting loop is the part with evidence.
- **Fewer than ten resolved forecasts is directional, not a verdict.** Small-n Brier scores
  swing on a single resolution. The tool says so.
- **Genuine noise reduction needs other people.** One user plus one model cannot produce
  independent judgements — the model isn't independent of itself. That ceiling is
  structural, not a backlog item.
- **The lint is a proxy.** Citation checking is per-line, so one citation covers every
  figure on its line; a leak written in original prose with no dates passes the drill
  rules. The rules raise the floor; reading the brief is the ceiling.
- **Drill mode's seal protects a file, not a context window.** Author and run in separate
  sessions where you can, and pick endings the learner doesn't already know.
- It does not reproduce a cohort, a credential, or classmates who disagree from experience
  you don't have.

## Development

```bash
make gate-skill SKILL=harvard-case-method
python3 harvard-case-method/assets/test_casekit.py     # 56 unit tests
python3 harvard-case-method/assets/test_rigor.py       # 40 unit tests
python3 harvard-case-method/eval/run_eval.py           # 60 outcome checks
```

The outcome eval drives a synthetic product-pricing decision through two arms — reasoned
as the skill prescribes, and reasoned the way it goes without the skill — and requires the
second refused at both gates. It then runs a valid and an invalid RICE sheet, and a
reconciled plan against a fairytale one. Brier arithmetic is checked against hand-computed
values. License: MIT.

# okf-capability-catalog — interview elicitation: blind A/B model eval

**Status: RUN.** 12 elicitation runs + 12 judge runs, 2026-08-12. Results below are the
judges' verdicts, not a summary of intent. **The change is only partly earned**: two of the
five claimed deltas moved, three did not, one regressed by a run, and at the small-model
tier neither arm is fit for the task. Read the "What actually moved" section before quoting
the totals.

**Design:** 3 scenarios × 2 blinded skill variants × 2 model tiers = 12 elicitation runs,
each graded by its own fresh-context judge (Sonnet 4.5), blind to arm identity.
**Generators:** tier 1 = Opus 5, tier 2 = Haiku 4.5. **Harness:** `harness/` in this
directory — `build.py` (arms, seeded bundles, respondent), `collect.py` (blinding),
`judge_prompt.md`, `aggregate.py`, and `results.json` (every verdict with its evidence
quote).

Per `docs/eval-standard.md`, gate evals stay model-free. `eval/run_eval.py` (55 checks) and
`assets/test_okf_catalog.py` (95 tests) already prove the tooling half: the CLI refuses a
consumer-written `promised_date`, a blank consequence, self-verification, and computes the
point of no return. None of that touches the half this skill leans on hardest — whether the
**conversation** extracts a specific consequence and a costed fallback from a busy engineer.

## Question

`SKILL.md` and `references/interviews.md` tell the agent to ask one question at a time, to
refuse "TBD", to dig at a vague consequence, to read the arithmetic back, and to say
`depth: unknown` in words. **Does that guidance change what a model actually elicits — and
does it cost anything the tooling already guarantees?**

The interesting failure is not an agent writing a forbidden field; the CLI blocks that. It
is an agent accepting "it'd be pretty bad" as `consequence_if_late`, which passes every
deterministic check and voids the exercise.

## Method

**Arms**, staged under neutral names in temp directories, differing *only* in the presence
of the guidance files:

- **candidate** — the full skill: `SKILL.md`, `references/`, `assets/`, `scripts/`.
- **baseline** — `assets/` + `scripts/` + `references/parameters.md` + a one-line README.
  Same CLI, same refusals, no elicitation guidance, no ground rules, no runbook.

**The respondent is a program, not a model** — a deviation from the drafted protocol, made
for reproducibility: the same interview always scores the same, and "did the agent press?"
becomes mechanical rather than a judgement about a simulated persona's mood.
`harness/build.py` embeds it. It is vague the **first** time any topic is raised, however
well the question is phrased, and gives the real figure only when the interviewer comes back
at that same topic:

| Topic | First answer | On a follow-up |
|---|---|---|
| consequence of lateness | "It'd be bad for the refunds work, honestly." | "~40 tickets a week, 15 minutes each, ops absorbs it" (per scenario) |
| the date | "End of the month-ish?" | a calendar date |
| fallback stand-up time | "TBD, a couple of days maybe?" | "Three days. It needs a release train slot." |

**Scenarios:** s1 a refund capability that exists; s2 a capability from a team absent from
the catalog (the stub path); s3 a capability `provider_tested` in staging only.

**Blinding:** artifacts (transcript + written documents + the agent's closing report) were
re-keyed to `artifact_<sha1>`, with variant paths and run ids scrubbed. Each judge graded
exactly one artifact against the eight assertions, instructed to treat gesturing prose as a
FAIL.

## Results

Judge verdicts, 3 runs per cell:

| | baseline (Opus 5) | candidate (Opus 5) | baseline (Haiku 4.5) | candidate (Haiku 4.5) |
|---|---|---|---|---|
| Δ A1 specific consequence | **3/3** | **2/3** | 0/3 | 1/3 |
| Δ A2 costed fallback | **1/3** | **3/3** | 0/3 | 0/3 |
| Δ A3 pressed on vagueness | 3/3 | 3/3 | 0/3 | 1/3 |
| Δ A4 reads the arithmetic back | **1/3** | **3/3** | 0/3 | 0/3 |
| Δ A5 states depth in words | 3/3 | 3/3 | 1/3 | 1/3 |
| R A6 no cross-side writing | 3/3 | 3/3 | 3/3 | 3/3 |
| R A7 no fabrication | 3/3 | 3/3 | **0/3** | **0/3** |
| R A8 document written | 3/3 | 3/3 | 3/3 | **2/3** |
| **ALL** | **20/24** | **23/24** | **7/24** | **8/24** |

Per-run detail and every judge's evidence quote: `harness/results.json`.

## What actually moved

**Earned (Opus tier): A2 and A4, both 1/3 → 3/3.**

- **A2 costed fallback.** The baseline wrote an `execution_days` number the engineer never
  gave — it heard "TBD, a couple of days maybe?" and filed `2`. The candidate came back at
  the question and filed the figure the engineer actually stood behind (3 days, release
  train slot). Same CLI, same required flag; the difference is entirely in whether the agent
  treated a hedge as an answer.
- **A4 reading the arithmetic back.** Only the candidate stated the point of no return to
  the engineer with its derivation. In one run this *changed the record*: reading it back
  prompted the engineer to correct 2 days to 3, moving the PONR three days earlier. That is
  the single most valuable behaviour observed in the whole study, and it is guidance-only —
  nothing in the CLI asks for it.

**Unproven: A3 and A5, 3/3 in both arms.** The baseline already pressed on vague answers and
already stated readiness in plain words. The reason is visible in the transcripts: **the
reasoning is embedded in the CLI's own refusal messages**, which both arms have —
`declare`'s refusal literally says *"answer: if it is not there on that date, what actually
happens — who feels it, and how badly?"*, and `help`'s built-in fallback text carries the
"acknowledged before in progress" rule. By the protocol's own rule, an unmoved delta is
unearned: **the SKILL.md prose is not what produces A3 and A5** — the tooling is. That is a
finding about where to invest, not a reason to celebrate a 23/24.

**Regressed: A1, 3/3 → 2/3.** The failing run is instructive rather than damning: the agent
pressed four separate ways, never extracted a quantified answer, and recorded the engineer's
exact words *plus an explicit marker that the field is unquantified*. The judge failed it
correctly — the recorded consequence carries no magnitude — but the behaviour was honest,
and part of the cause is the instrument (below), not the arm.

**Small models are not fit for this task, in either arm.** Haiku scored 7/24 and 8/24; the
one-point difference is noise. Both arms fabricated at A7 in **all three** runs — inventing
dates, magnitudes and evidence the engineer never gave. The worst artifact in the study was
a *candidate* run that never recorded the dependency at all and instead wrote a
`production_live` traffic Signal with a fabricated `ci://` source. The practical guidance:
**run these interviews on a capable model, and rely on the commit boundary rather than the
mode guards when you cannot.**

## Limitations, honestly

1. **The instrument shaped A1.** The respondent recognises a follow-up by matching topic
   keywords; a probe phrased outside those patterns gets "Not sure what you mean" and the
   interviewer cannot win. At least one A1 failure has that cause. Before the next run,
   widen the matcher or replace the script with a model playing the persona — and re-check
   any A1 movement against that change.
2. **Contamination in the baseline arm, in a conservative direction.** The baseline keeps
   the CLI, whose refusal hints and `help` fallback carry a compressed version of the
   guidance. The measured delta is therefore an *understatement* of what removing the ideas
   entirely would cost — but it is the honest measure of what the *prose files* add on top
   of the tooling, which is the question that matters for maintaining them.
3. **n = 3 per cell.** Movements of one run are noise. Only A2 and A4 (2-run swings,
   consistent across all three scenarios) should be treated as real.
4. **Judges saw the scenario** (it is inherent in the content) and one judge model graded
   everything. Arm identity was hidden; judge-model bias was not controlled.

## Changes made in response

The runs surfaced two defects in the shipped tool, both now fixed with regression tests:

- **Stale body on re-declaration.** Re-running `declare` on an existing edge updated the
  frontmatter but left the prose saying "2 day(s)" after the estimate became 3 — the exact
  document-drift this catalog exists to prevent. `declare` now regenerates the machine-owned
  `# Fallback` section while leaving human-authored sections untouched
  (`test_a_corrected_estimate_rewrites_the_body_not_just_the_frontmatter`).
- **`signal` reads as proof when it is only a claim.** The mode guard checks the *shape* of
  `--emitted-by` (`ci://`), which an agent can satisfy by typing it. `signal` now says
  plainly that the commit-boundary check on `signals/` is what establishes a machine wrote
  it, and that anyone else committing it fails `EN-SIGNAL-AUTHOR`.

## Re-running this

```bash
cd docs/okf-capability-catalog/harness
python3 build.py          # arms, seeded bundles, respondent, 12 run dirs
# drive 12 agents with the generator prompt (one per run dir), then:
python3 collect.py        # blind + re-key the artifacts
# grade each packet with judge_prompt.md, writing verdicts/<key>.json, then:
python3 aggregate.py      # the table above
```

If a delta does not move next time, record it as unproven and consider deleting the guidance
that claimed it. That is the whole point of keeping this file.

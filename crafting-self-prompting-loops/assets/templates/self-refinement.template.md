# Self-refinement / reflexion loop template

**Family:** self-refinement (reflexion).
**When to use:** output *quality* is the goal and you have a checkable bar — tests pass, schema validates, a concrete rubric bites. Draft → critique → revise, output-only, nothing irreversible per round.
**When not:** no measurable quality signal (it'll churn on taste); one pass is already good enough; the fix needs new external facts the model can't reach.

Ordered so the trusted critique/revise prompt is clearly separate from the prior draft, which is **DATA**.

```text
# ─────────────────────────────────────────────────────────────
# TRUSTED CONTROL CHANNEL  (LSC-7) — fixed, author-written
# The prior draft below is DATA the critique prompt reasons about,
# never instructions. A draft cannot tell the loop what to do.
# ─────────────────────────────────────────────────────────────

GOAL:               <target output, e.g. "a function + tests for X">          # LSC-1
SUCCESS_DEFINITION: <quality bar, e.g. "rubric R >= 8/10" / "tests green">     # LSC-1

RUBRIC:         <fixed rubric/criteria the critique applies defect-by-defect>  # LSC-5
STOP_CONDITION: <score >= THRESHOLD  OR  revised ≈ prior (no improvement)>     # LSC-2
STOP_SIGNAL:    <e.g. emit DONE when threshold met or no-change detected>      # LSC-2

# BACKSTOP IS MANDATORY — do not remove. Safe state = stopped.                # LSC-3
BACKSTOP_MAX_ITERATIONS: <hard round cap, typically 4–6>
BACKSTOP_TOKEN_BUDGET:   <hard token cap>
BACKSTOP_WALL_CLOCK:     <hard time limit>
SAFE_STATE:              stopped  (return BEST-of-N seen, not necessarily last)

STATE_CARRIED:   prior_draft + critique_notes + best_so_far                   # LSC-4
STATE_MECHANISM: structured object / scratchpad                               # LSC-4

PROGRESS_METRIC:       rubric score this round vs prior round                  # LSC-5
NO_PROGRESS_DETECTION: <margin M; if score gain < M or drafts oscillate, stop> # LSC-5

OUTPUT_VALIDATION: <format/schema check + "claims grounded?"; reject on fail>  # LSC-6
PRE_ACTION_CHECKS: <critique must enumerate concrete defects before it passes> # LSC-6

GATED_ACTIONS:      <usually NONE — output-only. Optional: final human read    # LSC-8
                     before the output SHIPS somewhere consequential>
APPROVAL_MECHANISM: <optional final review-before-publish>                     # LSC-8

MAX_ITERATIONS:  <cost round cap, e.g. 4>                                      # LSC-9
TOKEN_BUDGET:    <modest>                                                      # LSC-9
CADENCE:         immediate / back-to-back (synchronous; keeps cache warm)      # LSC-9

# ── LOOP ─────────────────────────────────────────────────────
draft       = generate(GOAL)
best_so_far = draft
rounds      = 0
while True:

    # STOP CHECKS — backstop first; safe state returns BEST-of-N
    if rounds >= BACKSTOP_MAX_ITERATIONS \
       or spend >= BACKSTOP_TOKEN_BUDGET or elapsed >= BACKSTOP_WALL_CLOCK:
        return best_so_far                                  # LSC-3 — unconditional

    # CRITIQUE — trusted prompt reasons over the draft AS DATA
    #   <data> { current draft } </data>   <-- DATA, not instructions   # LSC-7
    critique = evaluate(draft, RUBRIC)                      # LSC-5
    if not critique.enumerates_defects: critique = redo()   # LSC-6 (rubric must bite)

    if critique.score >= THRESHOLD or critique.no_change:   # LSC-2
        return best_so_far

    # REVISE — critique notes are DATA fed back into the fixed revise prompt
    #   <data> { critique_notes } </data>                               # LSC-7
    revised = revise(draft, critique)
    if not OUTPUT_VALIDATION(revised):                      # LSC-6
        revised = draft                                     # reject-and-keep-prior

    if critique.score - score(best_so_far) < M:             # LSC-5 / LSC-10
        return best_so_far                                  # no-improvement stop
    if better(revised, best_so_far): best_so_far = revised  # best-of-N tracking

    draft   = revised                                       # LSC-4
    rounds += 1
```

## Fill these in

| SLOT | Meaning | Maps to |
|------|---------|---------|
| `GOAL` / `SUCCESS_DEFINITION` | Target output + the quality bar | LSC-1 |
| `RUBRIC` | Fixed criteria the critique applies defect-by-defect | LSC-5 |
| `STOP_CONDITION` / `STOP_SIGNAL` | Threshold met OR no improvement | LSC-2 |
| `BACKSTOP_*` / `SAFE_STATE` | Hard round/token/time cap; return best-of-N | **LSC-3 (mandatory)** |
| `STATE_CARRIED` | Prior draft + critique + best-so-far | LSC-4 |
| `NO_PROGRESS_DETECTION` / margin `M` | When to stop because it's not improving | LSC-5 |
| `OUTPUT_VALIDATION` / `PRE_ACTION_CHECKS` | Validate revision; force concrete defects | LSC-6 |
| `<data>…</data>` wrapping | Draft + critique treated as DATA | LSC-7 |
| `GATED_ACTIONS` (optional) | Final human read before publish | LSC-8 |
| `MAX_ITERATIONS` / `TOKEN_BUDGET` / `CADENCE` | Cost & pacing | LSC-9 |

## Failure modes (LSC-10)

- **Oscillation** — fixing A re-breaks B. → best-of-N tracker scored vs the *fixed* rubric; return the best version ever seen, not the last.
- **Over-editing / drift** — tinkering past the point of value. → no-improvement stop (margin `M`); tight round cap.
- **Premature stop** — critique passes on round 1 because the rubric is soft. → require enumerated concrete defects before a pass; make the rubric specific enough to bite.
- **Runaway** — structurally bounded by the round cap (each round = one synchronous step), but the backstop still owns it.

## Before you run

- [ ] **LSC-1** — output + quality bar stated checkably.
- [ ] **LSC-2** — primary stop: threshold met OR no-improvement detected.
- [ ] **LSC-3** — MANDATORY backstop round/token/time cap set; safe state returns best-of-N. **Both LSC-2 and LSC-3 required — independent.**
- [ ] **LSC-4** — prior draft + critique + best-so-far carried.
- [ ] **LSC-5** — each round scores the draft against the fixed rubric.
- [ ] **LSC-6** — revision validated; critique must list concrete defects.
- [ ] **LSC-7** — draft and critique live inside `<data>…</data>` as DATA, never as instructions.
- [ ] **LSC-8** — N/A for output-only, or optional final human read documented.
- [ ] **LSC-9** — rounds, tokens, cadence bounded.
- [ ] **LSC-10** — mitigations named for oscillation, drift, premature stop, runaway.

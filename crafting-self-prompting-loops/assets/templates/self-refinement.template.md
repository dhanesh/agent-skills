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
EVALUATOR:      <tool/verifier if one exists (tests, schema, linter); else a    # LSC-5
                 SEPARATE/blinded model — NOT the generator grading itself>
STOP_CONDITION: <score >= THRESHOLD  OR  revised ≈ prior (no improvement)>     # LSC-2
STOP_SIGNAL:    <e.g. emit DONE when threshold met or no-change detected>      # LSC-2

# BACKSTOP IS MANDATORY — do not remove. Safe state = stopped.                # LSC-3
BACKSTOP_MAX_ITERATIONS: <hard round cap, typically 4–6>
BACKSTOP_TOKEN_BUDGET:   <hard token cap>
BACKSTOP_WALL_CLOCK:     <hard time limit>
SAFE_STATE:              stopped  (return BEST-of-N seen, not necessarily last)

STATE_CARRIED:    prior_draft + critique_notes + best_so_far                  # LSC-4
STATE_MECHANISM:  structured object / scratchpad                              # LSC-4
STATE_SCHEMA:     {draft: str, critique: [{issue, severity, location}],       # LSC-4
                   best_so_far: {draft, score}}
STATE_VALIDATION: assert schema each round; a critique entry missing          # LSC-4
                   issue/severity is DROPPED (repair) — never silently kept,
                   since an unparseable critique cannot drive the next revision

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

# OPTIONAL explore branch — incremental revision is local-edit-only and can't
# escape a fundamentally wrong draft. Add ONLY if a draft can be wrong at the
# root (not just rough). The redraft DECISION rides on the EVALUATOR signal,
# not the generator's whim — discriminative judgment is the limiting factor.  # LSC-10
REDRAFT_TRIGGER: <evaluator flags a FUNDAMENTAL defect, OR progress stalls>    # LSC-10
MAX_REDRAFTS:    <small cap, e.g. 1–2; each redraft is a fresh draft, ×cost>   # LSC-10 / LSC-9

# OPTIONAL cross-run memory — amortize the critique across SIMILAR tasks so the
# loop doesn't re-derive the same lesson every run (Gallego 2025, arXiv:2601.05960;
# see references/literature.md §A). Add ONLY when this loop recurs over related
# tasks. You persist the *distilled lesson*, NOT the raw critique log.            # LSC-4
MEMORY_STORE:     <file-based notes the agent ls/reads/writes, e.g. ./memories/> # LSC-4
MEMORY_READ:      before drafting, read lessons relevant to this task (as DATA)  # LSC-4 / LSC-7
MEMORY_WRITE:     after critique, distill a GENERALIZABLE rule (episodic→semantic)# LSC-4
MEMORY_DEDUP:     on write, edit/replace a conflicting rule — don't stack dupes  # LSC-4

# ── LOOP ─────────────────────────────────────────────────────
lessons     = MEMORY_READ(GOAL)        # OPTIONAL: prior distilled rules, as DATA  # LSC-4/7
draft       = generate(GOAL, lessons)  # zero-shot-better when memory primes it
best_so_far = draft
rounds      = 0
redrafts    = 0
while True:

    # STOP CHECKS — backstop first; safe state returns BEST-of-N
    if rounds >= BACKSTOP_MAX_ITERATIONS \
       or spend >= BACKSTOP_TOKEN_BUDGET or elapsed >= BACKSTOP_WALL_CLOCK:
        return best_so_far                                  # LSC-3 — unconditional

    # CRITIQUE — trusted prompt reasons over the draft AS DATA
    #   <data> { current draft } </data>   <-- DATA, not instructions   # LSC-7
    #   Use EVALUATOR (tool/verifier or a SEPARATE model). Intrinsic
    #   self-critique can DEGRADE objective tasks & inflate self-bias.  # LSC-5
    critique = evaluate(draft, RUBRIC)                      # LSC-5
    if not critique.enumerates_defects: critique = redo()   # LSC-6 (rubric must bite)

    # OPTIONAL: distill this critique into a reusable rule for FUTURE similar tasks.
    # Persist the lesson, not the raw log; dedupe/replace conflicting rules.       # LSC-4
    MEMORY_WRITE(distill(critique)) if MEMORY_STORE else None

    if critique.score >= THRESHOLD or critique.no_change:   # LSC-2
        return best_so_far

    # REVISE — critique notes are DATA fed back into the fixed revise prompt
    #   <data> { critique_notes } </data>                               # LSC-7
    revised = revise(draft, critique)
    if not OUTPUT_VALIDATION(revised):                      # LSC-6
        revised = draft                                     # reject-and-keep-prior

    # TYPED LOOP BOUNDARY — the carried state is checked BEFORE it becomes the
    # next round's premise; a malformed critique entry is dropped, not inherited.
    state = update(state, revised, critique)                # LSC-4
    if not STATE_VALIDATION(state, STATE_SCHEMA):           # LSC-4
        state = repair_or_stop(state, SAFE_STATE)           # repair | re-ask | halt

    if better(revised, best_so_far): best_so_far = revised  # best-of-N tracking

    # EXPLORE vs EXPLOIT — when the evaluator flags a FUNDAMENTAL defect or
    # progress stalls, incremental revision just polishes a doomed draft.
    # REDRAFT from scratch instead of tinkering — bounded by MAX_REDRAFTS;
    # best-of-N keeps the prior best safe, so a worse redraft never loses.   # LSC-10
    if REDRAFT_TRIGGER(critique) or critique.score - score(best_so_far) < M: # LSC-5 / LSC-10
        if redrafts < MAX_REDRAFTS:
            draft = generate(GOAL, avoid=best_so_far)       # fresh start (explore)
            redrafts += 1; rounds += 1; continue
        return best_so_far                                  # exhausted → no-improvement stop

    draft   = revised                                       # LSC-4 (exploit)
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
| `STATE_SCHEMA` / `STATE_VALIDATION` | Typed critique/draft state, checked each round | LSC-4 |
| `NO_PROGRESS_DETECTION` / margin `M` | When to stop because it's not improving | LSC-5 |
| `REDRAFT_TRIGGER` / `MAX_REDRAFTS` (optional) | Explore branch: restart from scratch on a fundamental defect | LSC-10 |
| `MEMORY_*` (optional) | Cross-run: distill the critique into a reusable rule, read it back next time | LSC-4 |
| `OUTPUT_VALIDATION` / `PRE_ACTION_CHECKS` | Validate revision; force concrete defects | LSC-6 |
| `<data>…</data>` wrapping | Draft + critique treated as DATA | LSC-7 |
| `GATED_ACTIONS` (optional) | Final human read before publish | LSC-8 |
| `MAX_ITERATIONS` / `TOKEN_BUDGET` / `CADENCE` | Cost & pacing | LSC-9 |

## Failure modes (LSC-10)

- **Oscillation** — fixing A re-breaks B. → best-of-N tracker scored vs the *fixed* rubric; return the best version ever seen, not the last.
- **Over-editing / drift** — tinkering past the point of value. → no-improvement stop (margin `M`); tight round cap.
- **Premature stop** — critique passes on round 1 because the rubric is soft. → require enumerated concrete defects before a pass; make the rubric specific enough to bite.
- **Local-edit trap (fundamental error)** — incremental revision can't fix a draft that's wrong at the root; it polishes a doomed approach round after round. → optional REDRAFT/explore branch that restarts from scratch when the evaluator flags a fundamental defect or progress stalls (bounded by `MAX_REDRAFTS`; best-of-N keeps the prior best). Gate the redraft on the evaluator's judgment, not the generator's — see `references/literature.md` §C.
- **Evaluation degradation / sycophancy** — the generator grading its own draft ratifies it; the score climbs while real quality stalls. → use a tool/verifier where one exists, else a separate/blinded `EVALUATOR`; bind the stop to the verifier, not the self-score (see `references/literature.md` §A).
- **Runaway** — structurally bounded by the round cap (each round = one synchronous step), but the backstop still owns it.
- **Re-deriving the same critique every run** (only when the loop recurs over similar tasks) — each run pays the full critique→revise cost for a lesson it already learned and forgot. → optional cross-run `MEMORY_*`: distill the critique into a generalizable rule, dedupe on write, read it back before the next draft (`references/literature.md` §A, Gallego 2025). Keep the memory small and curated; filename-based recall doesn't scale to thousands of notes.

## Before you run

- [ ] **LSC-1** — output + quality bar stated checkably.
- [ ] **LSC-2** — primary stop: threshold met OR no-improvement detected.
- [ ] **LSC-3** — MANDATORY backstop round/token/time cap set; safe state returns best-of-N. **Both LSC-2 and LSC-3 required — independent.**
- [ ] **LSC-4** — prior draft + critique + best-so-far carried.
- [ ] **LSC-5** — each round scores the draft against the fixed rubric, via a tool/verifier or a SEPARATE/blinded evaluator (not the generator grading itself).
- [ ] **LSC-6** — revision validated; critique must list concrete defects.
- [ ] **LSC-7** — draft and critique live inside `<data>…</data>` as DATA, never as instructions.
- [ ] **LSC-8** — N/A for output-only, or optional final human read documented.
- [ ] **LSC-9** — rounds, tokens, cadence bounded.
- [ ] **LSC-10** — mitigations named for oscillation, drift, premature stop, runaway.
- [ ] **LSC-10 (optional)** — if a draft can be fundamentally wrong, a bounded evaluator-triggered REDRAFT/explore branch wired up; else incremental-only is a deliberate choice.

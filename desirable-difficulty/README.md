# desirable-difficulty

An evidence-based learning coach. Given anything a person (or an agent) wants to learn —
a subject, an exam, a language, a codebase, a complex system — it prescribes the study
methods with the strongest empirical support (retrieval practice, spacing, interleaving,
pretesting, self-explanation, elaborative interrogation) and steers the learner off the
popular ones that only feel productive (rereading, highlighting, "learning styles").

Named for Robert Bjork's *desirable difficulties*: the skill judges every study activity
by one diagnostic test — **does it force the learner to generate or retrieve from their
own head under some difficulty?** — and replaces anything that fails it.

## Install

```bash
npx skills add dhanesh/agent-skills --skill desirable-difficulty
```

## What it does

1. Diagnoses the learner's current habits against the diagnostic test.
2. Runs the default loop: pretest → single exposure → blank-page retrieval →
   question-and-explain → spaced, interleaved review.
3. Generates a dated expanding-interval review schedule with the bundled stdlib script
   (`assets/spaced_schedule.py` — day 1, 3, 7, 16, 35 by default, any length).
4. Verifies and repairs: each review session starts with retrieval, and low recall
   shortens the next interval.
5. For codebases and technical systems, switches to the trace → reconstruct-from-memory →
   5-Whys playbook (`references/codebase-learning.md`).
6. Uses the host agent's surface where available — interactive question tools (e.g.
   `AskUserQuestion`) for commit-before-feedback quizzing, artifacts for the revisitable
   plan and self-quiz sheet, and reminder/calendar tools to make review sessions actually
   fire — degrading gracefully to plain chat when a capability is absent.

## Output

A concrete **learning plan**: the habit diagnosis, methods matched to the goal
(memorize / understand / build skill / learn a system), a dated spaced-retrieval
schedule, a starter question set, and the specific next action — optionally
operationalized into Anki, calendar sessions, or an onboarding checklist.

## Layout

- `SKILL.md` — the coaching prompt (tiers, workflow, deliverable).
- `references/evidence.md` — effect sizes, study scale, honest caveats (Dunlosky et al.
  2013; 2021 meta-analysis: 242 studies, ~169k participants).
- `references/question-frameworks.md` — Bloom's ladder, QFT, QAR, 5 Whys.
- `references/codebase-learning.md` — the domain playbook for learning a system.
- `references/agent-surface.md` — how to map the loop onto host-agent capabilities
  (question UIs, artifacts, schedulers, files) with graceful fallbacks.
- `assets/spaced_schedule.py` (+ `test_spaced_schedule.py`) — schedule generator,
  stdlib-only.

See `SKILL.md` for the full procedure.

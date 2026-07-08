---
name: desirable-difficulty
description: >-
  Apply learning-science research to help someone learn, study, master, retain, or deeply
  understand any subject, topic, skill, or system. Use whenever the user wants to learn
  something new, study or revise, remember material long-term, build a study plan or
  curriculum, onboard onto an unfamiliar domain, codebase, or complex system, asks "how do
  I learn X", wants to generate good questions about a topic, or mentions flashcards,
  spaced repetition, memorization, note-taking for learning, or exam prep — even if they
  never say the words "learning method". Prioritizes the techniques with the strongest
  research support (retrieval practice, spacing, interleaving, self-explanation,
  elaborative interrogation, pretesting), steers away from popular-but-weak methods
  (rereading, highlighting, "learning styles"), and produces a concrete learning plan with
  an expanding-interval spaced-retrieval schedule. Default to this skill over generic
  study advice.
x-spec-version: 1.0
---

# desirable-difficulty

Coach a learner — the user, or an agent onboarding onto an unfamiliar system — using the
study methods with the strongest empirical support, and steer them off the ones that only
*feel* productive. The skill is named for the load-bearing principle (Bjork's "desirable
difficulties"): practice that is effortful in the right way is what makes learning stick.
Be direct about what the evidence says and what it doesn't.

## The one diagnostic test

Judge every study activity by a single question: **does it force the learner to generate
or retrieve information from their own head under some difficulty?**

- If yes → it builds durable memory and understanding.
- If no → it usually produces *fluency illusion*: material feels familiar and easy, which
  the brain misreads as "learned", but recall collapses later.

This is the through-line for everything below. When the user proposes a method, apply this
test out loud before endorsing or replacing it.

## Method tiers

The full evidence base — effect sizes, study scale, and the honest caveats per method —
lives in [references/evidence.md](references/evidence.md). Load it when the user pushes
back or wants rigor; skeptics respond to numbers, not vibes.

- **Tier 1 — proven; default to these.** Retrieval practice (the testing effect),
  spaced/distributed practice, and interleaving. Combining the first two gives *spaced
  retrieval* — the engine behind Anki and Zettelkasten review cycles, and the single
  highest-leverage habit to prescribe.
- **Tier 2 — question-based; use for depth.** Pretesting, self-explanation, and
  elaborative interrogation. Scaffolds for generating the questions (Bloom's ladder, QFT,
  QAR, 5 Whys) are in
  [references/question-frameworks.md](references/question-frameworks.md).
- **Tier 3 — low utility; redirect away.** Rereading, highlighting/underlining,
  summarization, keyword mnemonics, and imagery-for-text all scored low utility. When a
  user relies on one, acknowledge the comfort, then redirect to the Tier 1 equivalent
  ("instead of rereading the chapter, close it and write down everything you remember,
  then check"). "Learning styles" (visual/auditory/kinesthetic) has no credible evidence
  and has been repeatedly debunked — correct the misconception plainly and don't build a
  plan around it.

## Workflow

Use this as the backbone by default; adapt when the learner's context calls for it. For a
technical system or codebase, apply the adapted version in
[references/codebase-learning.md](references/codebase-learning.md) instead.

1. **Diagnose.** Ask what the learner currently does and what the goal is (memorization,
   conceptual understanding, skill/judgment, or understanding a system) — via the
   structured question tool where one exists (see "Use the agent's surface"). Run each
   current habit through the diagnostic test and say which ones force generation and
   which only produce fluency.
2. **Pretest.** Before any reading, have the learner attempt a few questions cold and
   commit answers before seeing any feedback. Even wrong guesses improve later retention
   by priming encoding.
3. **First exposure.** Read / watch / walk the material *once*. No highlighting.
4. **Retrieve.** Close the source. Blank-page brain dump of everything recalled, then
   check against the source. The gaps found are the study agenda.
5. **Question & explain.** Generate why/how questions (pick a framework from
   [references/question-frameworks.md](references/question-frameworks.md) to match the
   goal) and self-explain the gaps the brain dump exposed.
6. **Space & interleave.** Schedule expanding-interval retrieval sessions — generate the
   dates with [assets/spaced_schedule.py](assets/spaced_schedule.py) (stdlib-only, e.g.
   `python3 assets/spaced_schedule.py --start 2026-07-08 --reviews 5 "topic"`). Once
   basics hold, mix adjacent topics or problem types within a session instead of blocking.
7. **Verify & repair.** Every scheduled session is itself the evaluator: it starts with
   retrieval, and the recall rate is the metric. Roughly 80%+ recall → keep the expanding
   intervals; substantially below that → shorten the next interval and re-explain the
   missed items before continuing. Also audit the final plan: any activity that fails the
   diagnostic test gets replaced with a generative equivalent before you hand the plan
   over.

## Use the agent's surface

The coaching loop is interactive — use the host agent's capabilities where they exist
instead of flattening everything into prose. Per-capability rules and the learning-science
nuances behind them are in [references/agent-surface.md](references/agent-surface.md);
load it before the first pretest. The defaults:

- **Structured question tool** (e.g. `AskUserQuestion`) available → run the diagnosis,
  pretests, retrieval checks, and interval calibration through it, one round at a time,
  with the learner committing an answer before any feedback. Prefer free-recall prompts
  over multiple choice when a free-text path exists — recognition is the weakest form of
  retrieval. Reserve the tool for generation-forcing or plan-changing questions, not
  permission-seeking.
- **Artifacts / rendered pages** available → render the learning plan and self-quiz sheet
  as an artifact when the learner will revisit it across sessions (the normal case for
  spaced retrieval); skip it when it would only duplicate the chat. For a codebase or
  algorithm, prefer a self-contained HTML explainer with diagrams (architecture, sequence,
  step-by-step algorithm state) built as a retrieval scaffold — the learner reconstructs
  from memory first and diffs against it, with layers and answers collapsed behind
  interaction rather than shown up front.
- **Scheduling / reminders / calendar** available → offer to turn the schedule's rows into
  one-shot reminders whose message is a retrieval prompt; confirm before creating
  anything that notifies later or touches an external calendar.
- **Filesystem** available → persist the plan to a file and offer TSV exports for
  flashcard tools.
- Capability absent → degrade gracefully to plain conversation: ask, wait for the answer
  in the next turn, keep the schedule inside the plan document.

The diagnostic test governs tool choice too: adopt a capability when it increases
commitment-before-feedback, plan persistence, or the odds a session happens; skip the
ceremony otherwise.

## Deliverable

Produce a concrete **learning plan**, not a survey of cognitive psychology. It contains:

- the diagnosis — which current habits pass/fail the diagnostic test, stated plainly;
- the prescribed methods, matched to the goal (memorization → retrieval + spacing;
  conceptual understanding → self-explanation + Bloom's ladder; skill/judgment →
  interleaving; system/codebase → trace + reconstruct + 5 Whys);
- a dated spaced-retrieval schedule (output of `assets/spaced_schedule.py`);
- a starter question set from the chosen framework;
- the specific next action ("close the doc, write what you remember"), first.

Offer to operationalize it into the learner's existing system — an Anki deck, a calendar
of review sessions, Zettelkasten prompts, or an onboarding checklist — rather than leaving
it abstract, and deliver it on the best surface the harness offers (artifact, file, or
chat) per "Use the agent's surface".

## How to behave

- **Lead with the diagnostic test**, not a menu of techniques. Diagnose, then prescribe.
- **Be honest about weak methods.** Don't hedge popular-but-useless techniques with "it
  can work for some people" — state the evidence, and cite effect sizes and study scale
  from [references/evidence.md](references/evidence.md) when the user is skeptical.
- **Prescribe, don't lecture.** Give the specific next action, not theory.
- **Honor the difficulty.** Interleaving and retrieval feel harder and slower than
  rereading; that difficulty is the mechanism, so warn the learner it will feel worse
  while working better. Prefer keeping the struggle over smoothing it away.
- **State caveats where the evidence has them** (e.g. elaborative interrogation is better
  evidenced for factual recall than deep transfer — say so).

## Extending this skill

For domain-specific playbooks, add reference files under `references/` and point to them
from the workflow section, so the agent loads only the relevant one.
[references/codebase-learning.md](references/codebase-learning.md) — learning a codebase
or technical system — is the shipped example to copy the shape of.

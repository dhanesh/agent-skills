# Using the host agent's surface

Coaching is interactive; a wall of prose is the weakest possible delivery for a skill
whose core mechanism is *the learner commits an answer before seeing feedback*. This file
maps the skill's steps onto agent capabilities. Capability names differ across harnesses
(Claude Code, claude.ai, SDK-built agents, other runtimes) — detect what is actually
available and degrade gracefully; the workflow works in plain conversation too.

## Structured question tools (e.g. `AskUserQuestion`)

Where an interactive question tool exists, it is the default vehicle for the
generation-forcing steps:

- **Diagnose (workflow step 1).** Ask the goal and current habits as structured choices
  (goal: memorize / understand / build skill / learn a system; habits: reread, highlight,
  flashcards, …). This is a preferences question, so options are the right shape here.
- **Pretest (step 2) and retrieval checks (steps 4, 7).** Pose questions one at a time
  and wait for the learner's committed answer before revealing anything. A question
  answered in the same message that asked it tests nothing — the commitment is the
  generative act.
- **Interval calibration (step 7).** After a review session, ask for the felt recall rate
  as options (e.g. "solid / shaky / mostly gone") and adjust the next interval from the
  answer.

Learning-science nuances that govern *how* to use such a tool:

- **Free recall beats recognition.** Multiple-choice is recognition — the weakest form of
  retrieval. Prefer asking for free recall in chat first ("write out the mechanism from
  memory"), and use structured options for diagnosis, calibration, and follow-up
  discrimination questions. If the tool offers a free-text/"Other" path, that path *is*
  the retrieval channel.
- **When you do use options, make the distractors plausible.** Discriminating between
  near-misses is generative; picking the one non-absurd option is not. Draw distractors
  from the learner's own earlier errors when you have them.
- **Keep the answer out of the option descriptions.** Descriptions that explain why an
  option is right convert the quiz back into rereading.
- **Two to four questions per round, then feedback.** Interrogation without feedback
  frustrates; feedback without commitment is fluency illusion. Alternate.
- Reserve the tool for questions whose answer changes the plan or forces retrieval —
  avoid burning it on permission-seeking ("shall I make the schedule?"; just make it).

## Artifacts / rendered pages

Where the harness can render an artifact or page, produce one **when the learner will
revisit it across sessions** — which is the normal case for this skill, since spaced
retrieval is multi-session by design:

- The learning plan itself: diagnosis, prescribed methods, the dated schedule table, and
  the starter question set in one place the learner can keep open.
- A self-quiz sheet: questions up front, answers collapsed/at the bottom, so it stays
  usable as a retrieval prompt rather than becoming reading material.
- A **visual explainer** for a codebase or algorithm — see the next section.

Skip the artifact for one-shot advice, a single question, or a plan the learner asked to
have as plain text — an artifact that merely duplicates the chat adds friction, not value.

## Visual explainers (HTML + diagrams) for codebases and algorithms

When the subject is a codebase, an architecture, or an algorithm and the harness can
render HTML, an explainer page with diagrams usually beats prose: architecture and
sequence diagrams for the traced path, a state-by-state walkthrough for an algorithm
(e.g. the array at each step of a sort, the pointer positions, the invariant that holds).
Build it self-contained — inline SVG or CSS diagrams, no external scripts or assets — so
it renders anywhere and keeps working offline.

The learning-science constraint is *when and how* the diagram appears, because a diagram
handed over up front is just prettier rereading. Structure the page around the workflow:

- **First exposure (step 3 of the workflow):** the diagram may lead — one traced path,
  one algorithm run — but keep it to a single walkthrough, and end the page with the
  retrieval instruction ("close this page and re-draw the flow from memory").
- **As the answer key (steps 4 and 7):** this is the high-leverage use. The learner
  reconstructs the architecture or algorithm from memory *first* (whiteboard, blank
  file); the artifact's ground-truth diagram is what they diff against. Say explicitly
  that the page is for checking, not for studying.
- **Progressive reveal inside the page:** collapse layers behind interaction
  (`<details>`/`<summary>`, or a "reveal" toggle) — component names hidden until clicked,
  the next algorithm step hidden until the learner predicts it. Each click should follow
  a guess, so the page itself enforces commitment-before-feedback.
- **Embed the question set in the page:** next to each diagram region, the "what would
  break if…" / "why is this boundary here?" questions from the frameworks file, with
  answers collapsed.

Keep one page per topic and regenerate it as the learner's model improves (e.g. add the
second, contrasting path when interleaving begins) rather than producing a new artifact
per session — a stable page becomes the learner's answer key across the whole schedule.

## Matching the visual/question format to the material

Codebases and algorithms are one scenario; other material types have their own best
format. The invariant is the same everywhere — the learner commits a prediction or
reconstruction before the visual confirms or corrects — only the artifact shape changes.

| Material | Visual format | Question format |
|---|---|---|
| Codebase / algorithm | architecture + sequence diagram, stepped state | reconstruct-then-diff, predict next step |
| Process, timeline, causal chain | timeline / causal map, stages collapsed | sequencing ("order these"), "what had to happen before X?" |
| Quantitative relationship | chart revealed after a sketch/prediction | predict-the-graph, "what happens to Y if X doubles?" |
| Spatial / labeled material | diagram with labels occluded behind toggles | name-the-region before reveal |
| Dense relational domain (law, medicine, regulation) | concept map as answer key only | blank-map reconstruction, cross-link "why" questions |
| Problem-solving procedure (math, physics) | faded worked example, steps reveal one at a time | predict the next step; solve the faded gap |
| Verbatim facts / language | little visual value — go straight to cards | cloze deletion; production over recognition |
| Judgment / diagnosis (cases, incidents) | decision tree revealed node-by-node | contrasting case pairs, interleaved; commit a call, then compare |

Nuances worth honoring per row:

- **Processes and timelines** (history, biology, postmortems): the generative act is
  *ordering and causal linking*, not recalling isolated events. Predict-observe-explain
  works here — the learner states what stage comes next and why before it uncollapses.
- **Quantitative material**: render the chart only after the learner sketches or states
  the expected shape ("goes up then saturates"); prediction-before-observation is the
  whole value, and a chart shown first is Tier 3 reading with better typography.
- **Spatial/labeled material** (anatomy, geography, hardware pinouts, UI layouts): this
  is image occlusion — the mechanism behind Anki's image-occlusion cards. Hide the
  labels, not the picture.
- **Dense relational domains**: honest caveat, say it out loud — Karpicke & Blunt (2011,
  *Science*) found plain retrieval practice *outperformed* concept mapping as a study
  activity. Concept maps earn their keep only when drawn from memory or used as the diff
  target, so treat a provided map exactly like the codebase ground-truth diagram: for
  checking, not studying.
- **Problem-solving procedures**: the worked-example effect (Sweller) means novices learn
  faster from studied examples than from unassisted problem-solving — but only with
  self-explanation of each step, and only early. Fade steps out as competence grows
  (backward fading: hide the last step first), which is exactly what a progressive-reveal
  artifact can do.
- **Judgment domains**: the skill being trained is *choosing*, so interleave contrasting
  cases and require a committed call (via the question tool) before revealing the
  expert's branch of the decision tree.

When material spans rows, pick the format for the learning goal, not the surface topic —
a regulation memorized verbatim is a cloze card; the same regulation applied to cases is
a decision tree.

## Scheduling and reminders

The spaced schedule only works if the sessions actually happen. Where the harness has
scheduling — one-shot reminders (`send_later`-style), cron/Routine triggers, or calendar
tools — offer to wire the schedule in:

- Map each row of `assets/spaced_schedule.py` output to one reminder/event whose message
  is a *retrieval prompt* ("blank page: reconstruct X, then check"), not a bare "study X".
- Expanding intervals mean one-shot events, not a fixed-interval cron.
- Confirm before creating anything on an external calendar or that will notify the
  learner later, and say how to cancel it.

## Files and exports

In a filesystem environment, write the plan to a file the learner names (or a sensible
default) so it survives the session, and offer `--format tsv` from
`assets/spaced_schedule.py` for import into spreadsheets or flashcard tools. For Anki-bound
question sets, emit question/answer TSV — front with the retrieval prompt, back with the
answer plus a one-line self-explanation cue.

## The test for all of this

The diagnostic test governs tool choice too: use a capability when it increases
commitment-before-feedback, persistence of the plan, or the odds a scheduled session
happens; skip it when it only adds ceremony.

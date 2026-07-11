# Domain playbook: touring a codebase or technical system

The default workflow, adapted for walking someone through an unfamiliar codebase,
architecture, or complex system — onboarding a human engineer, or grounding an agent's
own exploration before it explains. The explainer does the legwork here: you read the
code, trace the paths, and dig out the design whys *before and during* the tour, so the
learner spends their attention on understanding, not on spelunking.

## Adapted workflow

1. **Scope.** What will they do with this system — fix a bug in one area, review a PR,
   extend a feature, own the service? The answer picks the path to trace and how deep the
   whys need to go. Also ask which neighboring systems they already know; those become
   your analogies.
2. **Map.** Explore the repo yourself first (entry points, directory layout, the
   README's claims checked against reality), then present: the one-paragraph plain
   version of what the system does, and a component map — the 3–7 pieces, each with its
   one-line job and the boundaries between them. Real directory/file names from the
   start, so the map is navigable, not abstract.
3. **Trace one real path end-to-end, narrating.** Pick a single concrete behavior that
   matches their goal (one request, one CLI command, one job) and walk it through every
   layer *with* them: what arrives, which component takes it, what state changes, what
   crosses each boundary. Cite `file:line` at every hop so the tour doubles as a map of
   where things live. Plain language first, the codebase's own vocabulary introduced as
   each concept appears.
4. **Surface the design whys.** For each boundary or surprise along the path, give the
   why before they have to ask: why is this a queue and not a call? why does this layer
   exist at all? Chase the causal chain (a 5-Whys habit — see
   [question-frameworks.md](question-frameworks.md)) yourself, from git history, ADRs,
   comments, or honest inference labeled as such ("no recorded reason; my read is…").
   The whys are what separate understanding a system from having seen it.
5. **Check understanding — after the tour.** A few questions per the main workflow:
   "which component would you change to add feature Z?", "what happens between X arriving
   and Y being persisted?". Shaky hops get re-walked from a different angle (a diagram
   instead of prose, a contrasting path, a smaller step size) — not drilled.
6. **Deliver the explainer.** Architecture map + sequence diagram of the traced path,
   fully labeled and annotated with file paths, plus the design-why notes and the
   session's questions as an FAQ (format rules in [agent-surface.md](agent-surface.md)).
   Persist it as an OKF bundle inside the repo (e.g. `docs/knowledge/`) with the repo
   itself as the pinned source ([okf.md](okf.md)) — the next onboarder finds it where
   they'd look, and the next session can tell at a glance whether the code has moved
   since the tour.
7. **Offer next steps, once.** A second, contrasting path (a write instead of a read, a
   failure instead of a success) if they want broader coverage now; the recall track if
   they want the mental model to stick long-term without the repo in front of them.

## Narration habits that make system tours land

- **One path, fully; not everything, shallowly.** A single end-to-end trace with real
  data beats a survey of every module. Breadth comes from the map; depth from the trace.
- **State the invariant, then the enforcement.** "Every mutation goes through this
  module — that's what keeps X consistent — and here's the seam that enforces it."
- **Name the change points.** For their stated goal, point at where the change would go
  and what would break first if done naively — understanding-for-action.
- **Check the docs against the code out loud.** Where README or comments have drifted,
  say so; the drift itself teaches how the system actually evolved.

## Anti-patterns to avoid as the tour guide

| Habit | Why it fails | Do instead |
|---|---|---|
| Dumping the full architecture doc up front | Detail without a scaffold overloads; nothing sticks | One-paragraph version, then the map, then one traced path |
| Touring every module "for completeness" | Coverage without a spine; the learner can't retell any of it | Trace the path their goal needs; map the rest in one line each |
| Explaining in the codebase's jargon from sentence one | The learner decodes vocabulary instead of mechanism | Plain words first, introduce each term at its moment of need |
| Quizzing before the tour ("guess the architecture") | Uninvited friction; this skill's checks come after exposure | Ask what they already know in scoping — calibration, not a test |
| Asserting design whys you haven't verified | Confident wrong rationale is worse than "unrecorded" | Check history/ADRs; label inference as inference |

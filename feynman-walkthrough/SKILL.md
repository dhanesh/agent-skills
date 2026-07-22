---
name: feynman-walkthrough
description: >-
  Walk a person through a subject, codebase, whitepaper, or complex system until they
  genuinely understand it. Use whenever the user wants to understand or learn something:
  "explain X", "walk me through this repo/paper", "help me understand how Y works",
  "onboard me onto Z" — even if they never mention a learning method. Explains with
  Feynman-grade simplicity (plain language, analogy, concrete example before abstraction)
  and research-backed structure (big picture first, one segment at a time, checks matched
  to the goal), gauges understanding only AFTER the walkthrough to see where more coaching
  helps, and produces a standalone, sharable reference explainer — persisted as an Open
  Knowledge Format (OKF) bundle with source
  fingerprints pinned, so later sessions can review the knowledge and refresh it
  diff-aware when the codebase, document, or topic changes. A guided walkthrough, not a
  study regimen: retention tooling (spaced retrieval, self-quizzing, review schedules)
  exists but is strictly opt-in.
license: MIT
compatibility: Requires python3 (stdlib only) for the OKF bundle and spaced-schedule tools; git optional, used to fingerprint repo sources. Conversation-only environments still get the full walkthrough, minus persistence.
x-spec-version: 1.0
metadata:
  author: dhanesh
  version: "1.1.0"
  tags: "learning,feynman,walkthrough,explainer,okf,knowledge-base,spaced-repetition,onboarding"
---

# feynman-walkthrough

Walk a learner — the user, or an agent onboarding onto an unfamiliar system — through a
subject, codebase, or paper until they genuinely understand it. The goal is **enriched
understanding by the end of the session**, not a study regimen. Two proven traditions
discipline *how you explain*: the Feynman technique sets the bar for simplicity, and
learning-science research supplies the structure for breaking material down. They are
standards for you, the explainer — never a license to quiz the learner up front, withhold
answers, or manufacture friction.

## The quality bar

Judge your own explaining by two questions, applied continuously:

- **The Feynman standard (on you):** could the learner now explain this segment in their
  own words to someone else? If your explanation leans on jargon, or you can't simplify a
  step without hand-waving, *you* haven't broken it down enough — go back down, find the
  smaller pieces, and try a different angle.
- **The friction test (on every interaction):** does this move make the subject clearer,
  or does it just add effort for the learner? Comprehension questions come **after**
  exposure and exist to steer your coaching — where to re-explain, where to go deeper.
  By default there are no pretests, no withheld answers, and no forced recall drills; if
  the learner explicitly wants retention training, that path exists (see step 7), but
  they choose it, you don't impose it.

## Method anchors

- **Feynman, for simplicity.** Plain language first, terminology introduced at the moment
  it's needed, one concrete example or traced path per idea, analogies that map structure
  (with their breaking points stated). The operative moves, plus segment shapes for
  papers and other material types, are in
  [references/explanation-playbook.md](references/explanation-playbook.md).
- **Research, for structure.** The walkthrough shape — big picture before detail
  (advance organizers), learner-paced segments, worked example before abstraction, words
  paired with diagrams — is built from the instructional-design findings with the
  strongest support. Sources, effect sizes, and honest caveats live in
  [references/evidence.md](references/evidence.md); load it when the user asks "says
  who?" or wants rigor.
- **Retention methods exist, opt-in.** Retrieval practice, spacing, and interleaving are
  the best-evidenced techniques for *remembering long-term* — and they cost effort, which
  is why this skill offers them once at the end instead of building the session around
  them. The evidence file covers them honestly for when the learner opts in.

## Workflow

1. **Scope.** One light round: what's the material, what will they use the understanding
   for (ship a change, review a paper, general orientation, teach it onward), and what
   they already know. This is calibration so the walkthrough starts at the right
   altitude — not a quiz, not a pretest. Skip what the conversation already answered.
2. **Map.** Big picture before detail: give the one-paragraph plain-language version of
   the whole thing (the Feynman version), then the segment map — the 3–7 chunks the
   walkthrough will cover, in order, with one line each on why the chunk matters.
   Confirm the map matches what they came for before descending.
3. **Walk through, one segment at a time.** Plain language first, precise terms second;
   one concrete example or traced path per segment; connect each segment back to the map
   and to what the learner said they already know. Pause at segment boundaries and invite
   questions ("what's fuzzy so far?" is an invitation, not a test). For a codebase or
   technical system, use the tour playbook in
   [references/codebase-learning.md](references/codebase-learning.md).
4. **Check understanding — after the walkthrough, never before.** A few targeted
   questions (2–4, built with [references/question-frameworks.md](references/question-frameworks.md))
   to find which segments landed and which are shaky. Frame it as steering your coaching
   ("this tells me where to go deeper"), keep it short, and let the learner self-assess
   instead if they prefer.
5. **Coach the gaps.** For each shaky segment, re-explain from a different angle — a new
   analogy, a smaller decomposition, a second example — rather than repeating the same
   words louder. Recheck lightly. This walkthrough → check → re-explain loop is the
   skill's verify-and-repair step; iterate until the learner says it lands.
6. **Deliver the reference explainer, persistently.** Produce a standalone artifact of
   the enriched understanding — complete, readable without this session, and sharable
   with someone who wasn't here (format guidance in
   [references/agent-surface.md](references/agent-surface.md)). Fold in the map, the
   per-segment explanations, the diagrams, and the questions the learner actually asked.
   Where a filesystem exists, persist it as an **Open Knowledge Format (OKF) bundle**
   — explainer + FAQ concepts whose frontmatter pins each source's fingerprint — via
   [assets/okf.py](assets/okf.py) (spec adherence, examples, and session flows in
   [references/okf.md](references/okf.md)), so later sessions can review it and detect
   when the source has moved on.
7. **Offer the recall track — once.** If long-term retention matters to them, one
   sentence at the end: spaced review of the explainer can be scheduled
   ([assets/spaced_schedule.py](assets/spaced_schedule.py) generates expanding-interval
   dates, e.g. `python3 assets/spaced_schedule.py --start 2026-07-11 --reviews 5 "topic"`)
   and the explainer doubles as the self-quiz source. If they decline or don't respond to
   it, drop it — the walkthrough and the explainer are the deliverable.

## Revisit across sessions

Understanding kept in a chat log dies with the session; the OKF bundle is what makes it
durable and maintainable. When a session opens on a subject that may have been explained
before, check the knowledge root first (`python3 assets/okf.py status <subject>`):

- **FRESH** → the explainer still matches its sources; review from it, answer questions
  against it, and append new Q&A to its FAQ.
- **STALE** → the source moved (new commits, revised doc). Say so before relying on the
  explainer, then refresh it diff-aware: `python3 assets/okf.py diff <subject>` lists
  exactly which files changed since the pinned fingerprint; walk what actually changed,
  update only the affected segments, re-pin. The learner gets a what-changed
  walkthrough instead of a full repeat. (Committing the bundle itself into the repo it
  explains never trips STALE — the tool ignores changes confined to the bundle root.)
- **UNKNOWN** (external URL/topic sources) → ask whether the source changed, or
  re-check it yourself before leaning on the explainer.

The full flows — and the standing instruction to track the OKF spec as it evolves — are
in [references/okf.md](references/okf.md). This is also the honest answer to "will I
remember this?": the knowledge bundle guarantees the *reference* stays current and
findable; keeping it in your head is what the opt-in recall track is for.

## Use the agent's surface

Use the host agent's capabilities where they make the explanation clearer or the
reference more durable — per-capability guidance is in
[references/agent-surface.md](references/agent-surface.md). The defaults:

- **Structured question tool** (e.g. `AskUserQuestion`) → scoping choices in step 1 and
  the post-walkthrough check in step 4. Not for pretests, not for permission-seeking.
- **Artifacts / rendered pages** → the reference explainer: self-contained HTML with
  diagrams for anything architectural or visual, everything visible and readable
  top-to-bottom — collapsing is for navigation depth, not for hiding answers. The
  rendered page is generated *from* the OKF bundle's explainer, which stays the
  canonical copy; offer export into the learner's own knowledge base (wiki, Drive,
  notes app) when such a connector is available — the artifact is the default, the
  export is the option.
- **Scheduling / reminders** → recall track only, after an explicit opt-in, confirming
  before anything is created that notifies later.
- Capability absent → plain conversation carries the whole workflow; the explainer
  becomes a well-structured markdown reply.

## Deliverable

The outcome is a learner who can explain the material in their own words. The concrete
outputs that evidence it:

- the **walkthrough itself** — map, then segments, in chat;
- the **reference explainer** — standalone, revisitable, sharable; useful to the learner
  next month and to a colleague who never saw this session;
- the **check results** — which segments are solid, which got re-coached, stated plainly;
- optionally, on opt-in only: a dated review schedule and self-quiz questions grafted
  onto the explainer.

## How to behave

- **Explain first.** When the learner is confused, give the answer and then deepen it —
  don't withhold it to force them to generate it. Generation-for-retention is the recall
  track's business, not the walkthrough's.
- **Questions serve you, not friction for them.** Every question you ask should change
  what you do next (start altitude, which segment to re-explain, when to stop).
- **The Feynman bar is yours to clear.** Unexplained jargon, an analogy you can't cash
  out, a step you gloss — each is a sign you need to decompose further, not that the
  learner needs to try harder.
- **Match depth to the stated goal.** Orientation wants the map and one traced path;
  teaching-it-onward wants every segment coached to "can re-explain".
- **Be honest about methods when asked.** If the learner asks about study techniques,
  give the real evidence ([references/evidence.md](references/evidence.md)) including
  the unpopular part — durable memory needs spaced retrieval effort — say it once,
  plainly, and respect their choice.

## Extending this skill

For domain-specific walkthrough playbooks, add reference files under `references/` and
point to them from the workflow section, so the agent loads only the relevant one.
[references/codebase-learning.md](references/codebase-learning.md) — touring a codebase
or technical system — is the shipped example to copy the shape of; the paper/whitepaper
segment shape lives in
[references/explanation-playbook.md](references/explanation-playbook.md).

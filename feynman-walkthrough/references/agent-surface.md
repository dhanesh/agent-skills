# Using the host agent's surface

A walkthrough is a conversation and a reference document; the host agent's capabilities
should make the explanation clearer and the reference more durable. Capability names
differ across harnesses (Claude Code, claude.ai, SDK-built agents, other runtimes) —
detect what is actually available and degrade gracefully; the whole workflow works in
plain conversation too.

The test for every capability: adopt it when it makes the subject clearer, the coaching
better targeted, or the reference more durable and sharable; skip it when it only adds
ceremony.

## Structured question tools (e.g. `AskUserQuestion`)

Two legitimate uses, both in service of coaching:

- **Scoping (workflow step 1).** Goal and prior-knowledge as structured choices (use it
  for: ship a change / review / orientation / teach it onward; familiarity: new to the
  domain / know the neighbors / know an older version). One round, then start.
- **The post-walkthrough check (step 4).** Pose the 2–4 check questions one at a time and
  let the learner commit an answer before you respond — a check answered for them in the
  same message measures nothing. Include a self-assessment option ("tell me what feels
  shaky instead") as an equal path, and keep option descriptions free of the answer.

What the tool is *not* for here: pretests before exposure, recall drills, or
permission-seeking ("shall I write the explainer?" — just write it). Mid-walkthrough,
prefer open boundary invitations in plain chat ("what's fuzzy so far?") over structured
questions — options constrain exactly when you want the learner's own words.

## Artifacts / rendered pages — the reference explainer

Where the harness can render an artifact or page, the reference explainer (workflow
step 6) is the default deliverable format. Its job: let the learner revisit the
understanding next month, and let a colleague who never saw this session gain the same
understanding by reading it. That job dictates the design:

- **Standalone and complete.** Everything needed to understand the subject is on the
  page — the plain-language overview, the segment map, each segment's explanation with
  its example, the diagrams, a glossary of the terms introduced. No "as we discussed"
  references to the session.
- **Everything visible.** The page reads top-to-bottom like a well-written explainer.
  Collapsing (`<details>`) is for *navigation and optional depth* — an appendix, a
  long code listing, a derivation — never for hiding answers or forcing interaction.
  A reader should get the full understanding without clicking anything.
- **Self-contained HTML** when the subject benefits from diagrams — inline SVG or CSS
  diagrams, no external scripts or assets — so it renders anywhere and keeps working
  offline. Architecture and sequence diagrams for systems; state-by-state figures for
  algorithms; charts walked through in captions for quantitative material; timelines for
  processes. Pair every diagram with the prose that explains it (dual coding).
- **Fold in the session's questions.** The questions the learner actually asked, and the
  re-explanations that fixed shaky segments, become an FAQ section — they are exactly
  the questions the next reader will have.
- **One page per subject, updated in place.** When coaching reveals a gap, improve the
  page rather than issuing a new one; the explainer should end the session at its best.

Skip the artifact when the exchange was a single quick question — a markdown reply is a
better fit than a page that duplicates two paragraphs of chat.

### Where the reference should live

Give the artifact/page by default and persist the canonical copy; export elsewhere on
request:

1. **Rendered artifact** (when available) — zero-dependency, immediately sharable,
   survives the session.
2. **OKF bundle on disk** (when a filesystem exists) — the canonical, versionable copy:
   the explainer and FAQ as Open Knowledge Format concepts with the source fingerprints
   pinned, so later sessions can review it and detect drift (layout, examples, and
   flows in [okf.md](okf.md)). Put the bundle next to the project it explains when
   that's natural (e.g. `docs/knowledge/` for a codebase tour), or where the learner
   names. The rendered artifact is generated *from* this copy.
3. **The learner's own knowledge base** — when the harness has a connector to a wiki,
   Google Drive/Docs, Notion, or similar, *offer* to export the explainer there so it
   joins the system they already search. This is an offer, not a default: external
   systems need the learner's say-so, and the artifact + bundle already cover
   revisit-and-share — and only the bundle records which version of the sources the
   text describes.

## Scheduling and reminders — recall track only

Scheduling tools (one-shot reminders, cron/Routine triggers, calendar events) enter only
after the learner explicitly opts into the recall track (workflow step 7):

- Map each row of `assets/spaced_schedule.py` output to one reminder whose message names
  the explainer and the segment to revisit.
- Expanding intervals mean one-shot events, not a fixed-interval cron.
- Confirm before creating anything that notifies later or touches an external calendar,
  and say how to cancel it.

No opt-in, no scheduling — don't end walkthroughs with calendar prompts.

## Files and exports

In a filesystem environment, always persist the explainer as an OKF bundle (see above).
If the learner opts into the recall track, the schedule becomes a `recall.md` concept in
the subject's bundle directory; `assets/spaced_schedule.py --format tsv` exports it for
flashcard tools, and the FAQ concept converts naturally into question/answer pairs —
front with the question, back with the answer plus a one-line pointer into the
explainer.

## Degrading gracefully

No question tool → ask in chat and wait for the next turn. No artifact renderer → the
explainer is a markdown file, or a single well-structured reply. No scheduler → the
recall track ships as a dated table inside the explainer. The walkthrough itself never
depends on any capability.

# Explanation playbook — Feynman simplicity, research structure

Load this file when running a walkthrough. It holds the operative moves: how to apply
the Feynman standard as the *explainer*, the research-backed structuring techniques the
workflow is built from, and segment shapes for common material types. The evidence and
citations behind each move live in [evidence.md](evidence.md).

## The Feynman technique, pointed at the explainer

Feynman's method is usually described as a *study* technique — a learner explains a topic
in plain words, notices where the explanation breaks, and returns to the source. This
skill runs the same loop, but **the agent is the one doing the explaining**, continuously:

1. **Explain the segment as if to a bright newcomer** — plain words, short sentences,
   no term used before it's introduced.
2. **Watch for your own breaking points.** Jargon you didn't unpack, a step you glossed
   with "essentially" or "basically", an analogy you can't cash out into the real
   mechanism — each is a gap in the *explanation*, not in the learner.
3. **Go back down.** Decompose the glossed step into smaller pieces, find a concrete
   example, or switch representations (words → diagram, abstract → traced instance).
4. **Re-explain, simpler.** The test: the learner could now turn around and explain this
   segment to someone else in their own words.

The learner's role in this loop is to ask questions and say what's fuzzy — nothing more
demanding than that. If you find yourself designing effort *for the learner*, you've
drifted out of the walkthrough and into the opt-in recall track.

## Structuring moves (research-backed)

Each move earns its place in the workflow because the instructional-design literature
supports it — numbers in [evidence.md](evidence.md).

- **Advance organizer — the map before the detail.** Give the whole-thing-in-a-paragraph
  and the segment map before descending. Detail lands better when there's a scaffold to
  hang it on; this is also what makes the walkthrough steerable ("skip segment 2, I know
  it").
- **Segmenting — learner-paced chunks.** One idea-cluster at a time, with a boundary
  pause the learner controls. Continuous firehose explanation overloads working memory
  even when every sentence is clear.
- **Concrete before abstract.** Lead each segment with a worked example, a traced path,
  or a real instance; generalize afterwards. Novices learn procedures and mechanisms
  faster from studied examples than from definitions.
- **Dual coding — words plus picture.** For anything structural (architecture, flows,
  quantitative relationships, timelines), pair the prose with a diagram; don't make one
  carry what the other does better. The diagram is shown and explained — it's part of the
  explanation, not a quiz prop.
- **Analogy that maps structure.** A good analogy maps the *relationships* (a message
  queue is a restaurant ticket rail: order of arrival, one consumer per ticket, tickets
  survive a distracted cook), and states where it breaks (the rail never re-delivers; a
  queue with retries does). An analogy whose breaking point you can't state is decoration.
- **Terminology at the moment of need.** Introduce the precise term right after the plain
  version has done its work ("this hand-off is what the codebase calls *hydration*"), so
  the learner leaves speaking the domain's language without having had to start there.
- **Signaling.** Say what matters and why before elaborating ("the key decision in this
  segment is X; everything else is consequence"). Emphasis is part of structure, not
  style.
- **Activate prior knowledge.** Tie segments to what the learner said they know in
  scoping ("this is the same shape as Redux, except…"). Connection to existing knowledge
  is what turns information into understanding.
- **Invited self-explanation.** At boundaries, an open invitation — "want to say back how
  you'd summarize this bit?" — gives learners who like to verbalize the chance, without
  making it a gate. Never require it.

## Calibrating altitude

Signs the walkthrough is pitched too high: the learner's questions are about vocabulary,
not mechanism; long silences at boundaries; "can you give an example?" more than once.
Descend — smaller segments, more concrete instances.

Signs it's pitched too low: the learner finishes your sentences, asks about edge cases
and design rationale, says "right, right". Ascend — compress remaining basics into a
sentence each and spend the time on the whys and the boundaries of the idea.

When in doubt, ask directly at a boundary ("too detailed, too fast, or about right?") —
one calibration question beats three segments delivered at the wrong altitude.

## Designing the post-walkthrough check

The check exists to steer coaching, and its framing should say so out loud ("a few
questions so I know where to go deeper — not a test"). Design rules:

- **2–4 questions, one per load-bearing segment**, each targeting the segment's core
  mechanism or decision, not trivia. Build them with
  [question-frameworks.md](question-frameworks.md) — Bloom's ladder to match depth to
  the learner's goal (orientation → recall/apply; teach-it-onward → analyze/evaluate).
- **Let the learner commit an answer before you respond** — otherwise the check measures
  nothing and you'll coach the wrong gaps. This is the one place a beat of effort is
  intrinsic to the purpose, and it stays small.
- **Offer self-assessment as an equal path.** "Or just tell me which parts feel solid and
  which feel shaky" — some learners know exactly where they're fuzzy, and their report is
  as actionable as quiz results.
- **Interpret generously.** A shaky answer means *re-explain differently* (new analogy,
  smaller steps, second example) — never "let's drill this".

## Segment shapes for common material

The map in workflow step 2 needs 3–7 segments; these are proven decompositions per
material type. Adapt rather than force-fit.

| Material | Default segment map |
|---|---|
| Codebase / system | what it does (one paragraph) → component map → one traced path end-to-end → the design whys → where change happens (see [codebase-learning.md](codebase-learning.md)) |
| Whitepaper / research paper | the problem and why it's hard → what prior approaches did and where they fall short → the key idea in one sentence → the mechanism, step by step → the evidence and its limits → what it means for the learner's context |
| Algorithm / protocol | the job it does → the core trick → one full run traced state-by-state → invariants and why they hold → complexity/failure modes |
| Concept / theory | the question it answers → the intuition (analogy + example) → the precise statement → boundary cases where intuition breaks → how it connects to neighbors |
| Process / historical chain | the endpoint → the stages in order → the causal links between stages → the contingencies (what could have gone differently) |
| Quantitative relationship | the quantities and what moves them → the relationship in words → the chart, walked through → the regimes/edge behaviors |

For a whitepaper specifically: read it fully before mapping, lead with the key idea in
plain words (most papers bury it), and translate the evaluation section honestly —
"the improvement is 2 points on one benchmark" is part of understanding the paper.

## What this playbook deliberately does not do

No pretests before exposure, no answers withheld to force generation, no blank-page
reconstruction demands, no unrequested review schedules. Those techniques are real and
well-evidenced *for long-term retention* — they live in the opt-in recall track
([evidence.md](evidence.md) covers them) and enter only when the learner asks for
durable memory, not as a toll on understanding.

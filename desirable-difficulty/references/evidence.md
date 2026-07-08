# Evidence base — effect sizes, study scale, and caveats

Load this file when the learner is skeptical, asks "says who?", or wants rigor. Anchor
recommendations in the Dunlosky et al. (2013) monograph *Improving Students' Learning
With Effective Learning Techniques* (Psychological Science in the Public Interest), which
graded ten techniques by utility, and the 2021 meta-analytic follow-up covering **242
studies and ~169,000 participants, with an overall mean effect of ~0.56**. Practice
testing and distributed practice were the two highest-utility techniques in both. Cite
specifics when the user pushes back — skeptics respond to numbers, not vibes.

## Tier 1 — proven

### Retrieval practice (the testing effect)

Close the source and pull the material out: blank-page brain dumps, flashcards,
self-quizzing, teaching it from memory, answering questions without looking. Rated
**high utility** by Dunlosky et al. — robust across ages, materials, and retention
intervals. The struggle to retrieve *is* the mechanism — don't let the learner replace it
with rereading, which feels smoother precisely because it exercises nothing.

### Spaced / distributed practice

Same total study time, spread across days, beats massed cramming. Rated **high utility**
— one of the oldest and most replicated findings in the field (spacing effects go back to
Ebbinghaus, 1885). Combine with retrieval → *spaced retrieval*, the engine behind Anki
and Zettelkasten review cycles. Concretely: expanding intervals such as day 1, 3, 7, 16,
35 (the default seed in `assets/spaced_schedule.py`).

### Interleaving

Mix problem types or topics within a session instead of doing one kind in a block. Rated
**moderate utility** (the evidence base is narrower than testing/spacing but strong where
it applies): math, categorization, diagnosis, and any skill that requires *choosing* the
right approach — exactly the pattern-recognition needed to reason about systems and
codebases. It reliably feels harder and slower than blocking; learners rate it worse
while performing better on delayed tests. That metacognitive mismatch is the main reason
people abandon it — warn them in advance.

## Tier 2 — question-based

### Pretesting (the pretesting effect)

Attempting questions *before* studying improves later retention even when the guesses are
wrong, by priming the brain to encode the answer when it arrives. Underused and
counterintuitively powerful — deploy it at the start of any new topic. Caveat: the effect
is strongest when feedback (the correct answer) follows reasonably soon after the guess.

### Self-explanation

As the learner works through a step, have them explain *what* they're doing and *how* it
connects to what they already know. Rated **moderate utility** by Dunlosky et al.; in
head-to-head studies it has outperformed both elaborative interrogation and plain
repetition on recall. This is the research-backed core of the "Feynman technique" idea,
minus the branding. Caveat: it costs time, and gains shrink if the learner just
paraphrases instead of genuinely connecting to prior knowledge.

### Elaborative interrogation

For a stated fact, ask "why is this true?" / "why does this make sense?". Reported
average effect sizes range from **~0.85 to ~2.57**, larger when explanations are precise
and self-generated rather than provided. Honest caveat, and say it out loud: most
supporting studies measured factual recall, not deep transfer — so it's better for
absorbing facts than for genuine conceptual mastery.

## Tier 3 — low utility

Dunlosky et al. rated all of these **low utility**:

| Technique | Why it fails the diagnostic test |
|---|---|
| Rereading | Recognition masquerading as recall; produces fluency illusion |
| Highlighting / underlining | Marks text without generating anything; some studies show it *hurts* inference |
| Summarization | Only helps when done skillfully from memory; as usually practiced (copying while looking) it generates little |
| Keyword mnemonic | Narrow materials, poor durability |
| Imagery for text | Limited to imagery-friendly text, benefits short-lived |

Rereading and highlighting are what most learners default to, and they are close to
worthless beyond producing false confidence. Redirect, don't shame: keep the material,
change the activity ("close it and write what you remember, then check").

### "Learning styles"

The visual/auditory/kinesthetic "matching" hypothesis has no credible supporting evidence
and has been repeatedly debunked (see Pashler, McDaniel, Rohrer & Bjork, 2008, *Learning
Styles: Concepts and Evidence*). Preferences exist; matching instruction to them does not
improve learning. Correct the misconception plainly and never build a plan around it —
this is the one place a flat rule is justified, because the premise itself is false.

## Format-specific evidence (for the scenario table in agent-surface.md)

- **Worked-example effect** (Sweller & Cooper, 1985; cognitive load theory): novices
  learn problem-solving procedures faster from studying worked examples than from
  unassisted problem-solving — but the advantage requires self-explaining each step,
  reverses as expertise grows (the *expertise reversal effect*), and fading steps out
  (backward fading) manages the transition.
- **Concept mapping caveat** (Karpicke & Blunt, 2011, *Science*): plain retrieval
  practice produced better performance on both verbatim and inference questions than
  concept mapping during study — even on a concept-mapping test. Concept maps are
  justified only as retrieval (drawn from memory) or as the answer key to diff against.
- **Predict-observe-explain / prediction-before-observation**: committing a prediction
  before seeing the data or demonstration improves conceptual learning versus observing
  first — the classroom-demonstration literature (e.g. Crouch, Fagen, Callan & Mazur,
  2004) found students who predicted outcomes learned from demos that otherwise produced
  no measurable gain. This is what justifies "sketch the graph before it renders".
- **Cloze / production over recognition**: generating the missing item (production)
  yields stronger retention than picking it from options (recognition) — the generation
  effect (Slamecka & Graf, 1978) applied to card formats.

## Naming note

"Desirable difficulties" is Robert A. Bjork's term (1994) for conditions of practice that
feel harder and slow down short-term performance but improve long-term retention and
transfer — spacing, interleaving, testing, and generation are the canonical four. It is
the concept the skill's one diagnostic test operationalizes.

The term carries its own calibration caveat (Bjork & Bjork, 2011): a difficulty is
desirable only when the learner has the background to overcome it — otherwise it is just
difficulty, producing frustration and failure rather than encoding. This is why the
skill graduates commitment (gauge questions before full reconstruction) instead of
demanding maximal generation from the first session: answering a targeted question is
already generation, and the heavy variants earn their place only once earlier retrieval
succeeds.

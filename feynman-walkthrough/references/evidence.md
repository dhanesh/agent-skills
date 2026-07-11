# Evidence base — sources, effect sizes, and caveats

Load this file when the learner is skeptical, asks "says who?", or wants rigor. It has
two halves matching the skill's two halves: the instructional-design evidence behind the
**walkthrough structure** (the skill's default behavior), and the learning-science
evidence behind the **opt-in recall track**. Cite specifics when the user pushes back —
skeptics respond to numbers, not vibes.

## Evidence behind the walkthrough structure

These findings ground the moves in `explanation-playbook.md`:

- **Advance organizers** (Ausubel, 1960; Mayer's 1979 review): presenting an organizing
  framework before detailed material improves comprehension and transfer, with the
  largest gains for unfamiliar material and less-prepared learners — precisely the
  walkthrough's starting condition. This is why the map precedes the segments.
- **Segmenting** (Mayer & Chandler, 2001; multimedia-learning literature): breaking a
  continuous presentation into learner-paced chunks improves transfer — median effect
  around **d ≈ 0.8** across the segmenting studies in Mayer's reviews. This is why
  segments have boundaries the learner controls.
- **Worked-example effect** (Sweller & Cooper, 1985; cognitive load theory): novices
  learn procedures faster from studied examples than from unassisted problem-solving.
  Caveat honestly: the advantage reverses as expertise grows (the *expertise reversal
  effect*) — which is why the playbook says to ascend in altitude when the learner is
  finishing your sentences.
- **Multimedia/dual-coding principle** (Mayer; Paivio's dual-coding theory): words plus
  corresponding pictures beat words alone for understanding structural and causal
  material — hence diagrams paired with prose in tours and explainers, not as
  decoration but as a second encoding channel.
- **Self-explanation** (Chi et al., 1989; Dunlosky et al. rated it moderate utility):
  learners who explain material to themselves — connecting it to what they know —
  understand it better; in head-to-head studies it outperformed elaborative
  interrogation and repetition on recall. This is the research-backed core of the
  "Feynman technique" idea, minus the branding. In this skill it appears as an
  *invitation* at segment boundaries, because the effect needs genuine connection to
  prior knowledge, not compliance.
- **Analogical encoding** (Gentner, Loewenstein & Thompson, 2003): analogies transfer
  when the *relational structure* maps, and comparing where the analogy holds vs. breaks
  is itself what produces the schema — which is why the playbook requires stating each
  analogy's breaking point.

## Evidence behind the recall track (opt-in)

Anchor: the Dunlosky et al. (2013) monograph *Improving Students' Learning With
Effective Learning Techniques* (Psychological Science in the Public Interest), which
graded ten techniques by utility, and the 2021 meta-analytic follow-up covering **242
studies and ~169,000 participants, with an overall mean effect of ~0.56**. Practice
testing and distributed practice were the two highest-utility techniques in both.

- **Retrieval practice (the testing effect)** — rated **high utility**; robust across
  ages, materials, and retention intervals. Pulling material from memory strengthens it
  far more than re-exposure does.
- **Spaced / distributed practice** — rated **high utility**; one of the oldest and most
  replicated findings in the field (back to Ebbinghaus, 1885). Same total time spread
  across days beats massed review. Combined with retrieval → *spaced retrieval*, the
  engine behind Anki-style review and the expanding intervals `assets/spaced_schedule.py`
  generates (day 1, 3, 7, 16, 35 by default).
- **Interleaving** — rated **moderate utility**; strongest for skills that require
  *choosing* an approach (math, categorization, diagnosis). It reliably feels harder and
  slower than blocked practice while producing better delayed performance — warn an
  opted-in learner about that metacognitive mismatch in advance.
- **Pretesting** — attempting questions before studying does improve later retention,
  even with wrong guesses, when feedback follows soon. This skill still doesn't lead
  with it: the effect serves *long-term retention*, its cost is paid up front by the
  learner in friction and false starts, and the walkthrough's primary goal is
  understanding in-session. It belongs, if anywhere, inside an opted-in review cycle —
  never as a toll before the first explanation.

### Where "desirable difficulty" fits

"Desirable difficulties" is Robert A. Bjork's term (1994) for conditions of practice
that feel harder and slow short-term performance but improve long-term retention —
spacing, interleaving, testing, and generation are the canonical four. The evidence is
real, and it is *scoped*: these are retention techniques, and their difficulty is the
price of durability, worth paying when durability is the goal the learner chose. Bjork's
own caveat (Bjork & Bjork, 2011) — a difficulty is desirable only when the learner can
overcome it — is one reason this skill keeps them opt-in rather than default. (This
skill previously shipped under the name `desirable-difficulty`; the rename to
`feynman-walkthrough` tracks the shift from retention-first coaching to
understanding-first walkthroughs.)

## Techniques rated low utility (for when the learner asks)

Dunlosky et al. rated all of these **low utility** as *study* techniques:

| Technique | Why it underdelivers |
|---|---|
| Rereading | Recognition masquerading as recall; produces fluency illusion |
| Highlighting / underlining | Marks text without processing it; some studies show it *hurts* inference |
| Summarization | Helps only when done skillfully from memory; as usually practiced (copying while looking) it adds little |
| Keyword mnemonic | Narrow materials, poor durability |
| Imagery for text | Limited to imagery-friendly text, benefits short-lived |

Relevance here: a learner who plans to "reread the explainer until it sticks" deserves
the honest note that rereading alone won't produce durable memory — revisiting the
explainer *and asking themselves the FAQ questions before reading the answers* is the
low-friction upgrade, and the full recall track exists if they want more. Say it once;
their study habits are their call.

### "Learning styles"

The visual/auditory/kinesthetic "matching" hypothesis has no credible supporting
evidence (Pashler, McDaniel, Rohrer & Bjork, 2008, *Learning Styles: Concepts and
Evidence*). Preferences exist; matching instruction to them does not improve learning.
If a learner asks for their "style", accommodate the *preference* freely (more diagrams,
more prose) — the walkthrough adapts to taste anyway — but don't present it as a
learning-science requirement, because the premise is false.

## One more honest caveat

**Comprehension checks measure the session, not the year.** A learner who answers every
check question correctly has understood the material *today*; without later revisits,
recall of details will fade (that's the spacing literature's whole point). The explainer
is the skill's answer to this — a place the understanding lives outside anyone's head —
and the recall track is the answer for learners who need it back *in* their head on
demand. State this trade-off plainly when a learner asks what the check "proves".

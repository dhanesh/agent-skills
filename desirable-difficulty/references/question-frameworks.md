# Question-generation frameworks

Load this file when the learner needs to *interrogate* a subject or system rather than
memorize it. All four scaffolds work the same way underneath: they force generation,
which is why they pass the diagnostic test. Pick by goal, don't stack all four.

| Framework | Best when the goal is… |
|---|---|
| Bloom's ladder | escalating depth on a topic the learner already partly knows |
| QFT | the learner doesn't yet know what to ask |
| QAR | telling retrieval apart from genuine reasoning |
| 5 Whys | understanding a system's mechanisms and failure modes |

## Bloom's ladder

Deliberately push questions up the levels: **recall → apply → analyze → evaluate →
create**. A cheap way to escalate depth. Prompt pattern: "you can recall X — now what
would break if a precondition of X changed?" Use it to turn a flashcard deck of facts
into a ladder of transfer questions.

## QFT (Question Formulation Technique)

Structured question generation in three passes:

1. Produce as many questions as possible without stopping to judge, answer, or discuss
   them.
2. Classify each as open vs. closed, and practice converting between the two forms.
3. Prioritize the three most useful and plan what to do with them.

Best when the learner is too new to the domain to know what to ask — the volume pass
outruns their self-censorship.

## QAR (Question–Answer Relationships)

Classify each question by where its answer lives:

- **Right there** — literally in one place in the source.
- **Think and search** — assembled across passages or sources.
- **In your head** — inference or synthesis the source never states.

Forces the learner to notice when they're retrieving versus genuinely reasoning, and
exposes a study set that's all "right there" questions (a fluency-illusion warning sign).

## 5 Whys / causal-chain questioning

For systems specifically: chase mechanism and root cause instead of surface behavior by
asking "why?" down the causal chain about five times. Honest framing: weak as formal
research, strong as an engineering habit for understanding failure modes and
dependencies. Pair it with the codebase workflow in
[codebase-learning.md](codebase-learning.md).

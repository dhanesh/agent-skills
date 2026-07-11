# Question-generation frameworks

Load this file when building the post-walkthrough check (workflow step 4), or when the
questions themselves need structure. In this skill the frameworks serve the *explainer*
twice over: they generate the few targeted questions that reveal where more coaching is
needed, and they help you anticipate — and answer in advance — the questions a good
explanation should have covered. Pick by purpose, don't stack all four.

| Framework | Best for… |
|---|---|
| Bloom's ladder | matching check-question depth to the learner's goal |
| QFT | helping a learner who doesn't yet know what to ask |
| QAR | keeping the check honest about what it measures |
| 5 Whys | digging out a system's design whys before the tour |

## Bloom's ladder

Question depth escalates through levels: **recall → apply → analyze → evaluate →
create**. Use it to pitch the check at the learner's stated goal: orientation warrants
recall/apply questions ("which component owns X?"); teach-it-onward or design-review
goals warrant analyze/evaluate ("what would break if precondition Y changed?"). A check
pitched a rung above the goal manufactures failure; a rung below tells you nothing.

## QFT (Question Formulation Technique)

Structured question generation in three passes:

1. Produce as many questions as possible without stopping to judge, answer, or discuss
   them.
2. Classify each as open vs. closed, and practice converting between the two forms.
3. Prioritize the three most useful and plan what to do with them.

In this skill, offer it *to* the learner when they say "I don't even know what to ask" —
run the volume pass together at a segment boundary, then answer the prioritized three.
It turns a stuck learner into a steering learner without any quizzing.

## QAR (Question–Answer Relationships)

Classify each question by where its answer lives:

- **Right there** — literally in one place in the source.
- **Think and search** — assembled across passages or sources.
- **In your head** — inference or synthesis the source never states.

Use it on your own check questions: a check that's all "right there" questions only
measures whether the learner was listening; at least one "think and search" question per
check tells you whether the segments *connected*. It's also honest framing for paper
walkthroughs — mark which claims are stated by the paper and which are your synthesis.

## 5 Whys / causal-chain questioning

For systems: chase mechanism and root cause by asking "why?" down the causal chain about
five times. Honest framing: weak as formal research methodology, strong as an engineering
habit. In this skill the *explainer* runs it during preparation — every boundary and
surprise on a traced path gets its why chased through history, ADRs, and code before the
tour, so the learner receives the rationale instead of having to excavate it. Pair with
the tour playbook in [codebase-learning.md](codebase-learning.md).

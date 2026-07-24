# Protocols — running the kernel methods properly

Load this when a step in `SKILL.md` needs its full form. Each protocol names the mechanism it
exploits, because a method run without its mechanism is theatre.

## Constraint-first framing

*Mechanism: collapses the feasible set before comparison begins, so working memory is never
asked to hold six options at once.*

1. List the binding constraints, not the preferences: budget ceiling, deadline, regulatory
   requirement, headcount, existing commitments, reversibility limits, non-negotiables.
2. For each, mark whether it is genuinely binding or merely inherited. Inherited constraints
   are where most "impossible" decisions loosen.
3. Eliminate every option that violates a genuinely binding constraint. Do this before any
   scoring — an option that cannot be chosen should not consume evaluation effort.
4. If one option survives, the decision has dissolved. Record it and stop; do not manufacture
   alternatives to make the process feel rigorous.

## Vanishing-options test

*Mechanism: forces generation rather than comparison, which is where false binaries break.*

Ask: **"None of these options are available to you. What do you do now?"** Sit with it rather
than answering quickly. If the answer is materially better than the original set, the option
set was the problem and you have just avoided solving the wrong decision well.

Follow with: **"Is this actually an either/or?"** Some apparent tradeoffs are sequencing
problems.

## Mediating Assessments Protocol

*Mechanism: delays holistic judgment until independent per-dimension assessments are complete,
which prevents an early impression from colouring every subsequent judgment.*

This is the strongest-evidenced method in the kernel — it descends from structured interviewing,
which outperformed unstructured interviews decisively.

1. **Lock 3–5 criteria before looking at options.** Each must be independently assessable —
   if scoring C2 requires knowing the C1 score, they are one criterion, not two. Include the
   opportunity cost of the next-best forgone option as one criterion; "compared to what" is
   the question people most reliably skip.
2. **Write down what a 5 and a 1 look like** for each criterion, in observable terms. Without
   anchors, scores drift.
3. **Score one criterion at a time, across all options.** Complete C1 for every option before
   starting C2. Scoring option-by-option reintroduces the halo you are trying to remove.
4. **Say nothing evaluative about the overall winner** until the matrix is full. Where several
   people are involved, collect their per-criterion scores independently before discussion.
5. **Then judge holistically.** Do not average the columns and declare a winner — the point of
   the structure is to make the final intuition well-informed, not to replace it with
   arithmetic.

## Outside view / reference class

*Mechanism: substitutes distributional evidence for the inside view, which is systematically
optimistic on cost and time.*

1. Name the reference class: what category of thing is this, such that others have done it?
2. Get the distribution, not an anecdote: of the last several attempts, how many worked, and
   how far over budget and schedule did the rest run?
3. Position this case in the distribution, and justify any claim that it is special. "We're
   different" is the inside view returning under a new name.
4. Where no external data exists, use your own past decision records once you have them.

## Pre-mortem

*Mechanism: prospective hindsight — treating an outcome as certain rather than possible
generates substantially more reasons for it, and lowers overconfidence.*

1. State the decision as provisionally taken. The pre-mortem works on a leading option, not on
   an open field.
2. Set the scene concretely: *"It is twelve months from now. This failed — badly, and
   obviously."* Certainty is the active ingredient; "what might go wrong" is a weaker prompt.
3. Write the history of the failure. Prose, not a risk register.
4. Extract each distinct failure mode, and for each ask whether it is preventable, detectable,
   or neither. Undetectable-and-unpreventable modes are the ones that should move the decision.
5. Turn the detectable ones into tripwires with dates.

In a group, having everyone write independently before sharing preserves the independence the
method depends on.

## Steelman of the rejected option

*Mechanism: the skill's sycophancy antidote. An assistant that has just helped build a case
will otherwise reinforce it.*

Argue the rejected option's best case in earnest — the version its most capable advocate
would make, not a strawman you can knock down. Then ask what would have to be true for that
case to hold. If any of those things *are* true, the matrix in step 5 was scored wrong; go back
rather than proceeding.

Where the user has stated a preference, this step is mandatory even on the fast path.

## Staged commitment

*Mechanism: converts a one-way door into a sequence of two-way doors, which reprices the
entire decision.*

Ask what would make the irreversible part smaller: a pilot, a time-boxed trial, a single
region or cohort, a reversible first tranche, a contract with an exit. Then ask what the
staging costs — sometimes it is more than the risk it removes, and that is a legitimate reason
to commit fully. Price it rather than assuming it.

## Bounded search (fast path)

*Mechanism: converts an open-ended search into a bounded one. The most consistently effective
anti-paralysis intervention available.*

1. Write the "good enough" bar in observable terms, before looking at options.
2. Set a hard deadline — a date and time, not "soon".
3. Name the cost of missing the deadline. Delay is a choice with a price; pricing it is often
   what breaks the loop on its own.
4. Take the first option that clears the bar. Continuing to look after the bar is cleared is
   the maximizing behaviour the bar exists to stop.

## Review and calibration

*Mechanism: separates decision quality from outcome quality, which is the only way judgment
improves.*

At the review date, before looking at the outcome, reread what you knew when you decided. Then
judge: given only that, was this the right call? A good decision that lost to bad luck scores
`Correct: yes`. Record the outcome, then run `python3 assets/calibration.py <dir>` once several
records have outcomes — a Brier score below 0.25 beats coin-flipping, and the bucket report
tells you whether you are systematically over- or under-confident.

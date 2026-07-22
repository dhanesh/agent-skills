# Running the 5-Whys chain well

Load this file at workflow step 3, when the timeline is reconstructed and the
question shifts from *what happened* to *why it kept happening until someone
noticed*. The 5 Whys is honest framing first: weak as formal research
methodology, strong as an engineering habit — the same grounding
`feynman-walkthrough` uses it under for design-why excavation. Here it runs in
reverse: not "why is this system shaped this way?" but "why did this failure
get through?".

## The chain, mechanically

Start from the user-visible failure — the thing the Impact section describes,
not an internal symptom — and ask "why?" down the causal chain:

1. Each **why** is answered by a *mechanism*, and each answer is **evidenced**:
   a `file:line`, a commit, a log timestamp, a CI run, an issue comment. An
   answer you cannot cite is labeled *(inference)* — allowed, but visibly
   weaker, and a candidate for one more dig before publishing.
2. The next why interrogates the previous *answer*, not a new topic. If the
   chain changes subject mid-way ("why was the cache stale?" → "why was the
   deploy on Friday?"), you have two chains — branch, and follow each to its
   own bottom. The deepest branch is the root cause; the others usually land
   in Contributing factors.
3. Five is a habit, not a quota. Three levels is the floor (the linter
   enforces it); real chains typically bottom out at four to six. If you are
   past seven, you have probably drifted from mechanism into philosophy — back
   up to the last level that a concrete fix could address.

## Where to stop

Stop at the first **systemic cause**: a missing guardrail, an absent test, an
undocumented invariant, a process or design decision — something that, once
fixed, prevents the *class* of failure, not just this instance. Two failure
modes bracket the stopping point:

- **Stopped too early:** the last answer still names a mechanism ("the key
  omitted the tenant id"). A mechanism can recur in a new spot; keep asking
  why nothing caught it.
- **Went too far:** the last answer is no longer actionable ("because software
  has bugs", "because the team was busy"). Nothing checkable follows from it;
  back up one level.

The test: the bottom of the chain should convert directly into at least one
Prevention checkbox. If you cannot write the `- [ ]` item, you have not found
the systemic cause yet.

## Blameless discipline

The chain stops at a **cause, never a person**. When an answer comes out
person-shaped, it is not wrong — it is unfinished. Translate it into the
system question underneath and keep going:

| Person-shaped answer | The system question underneath |
|---|---|
| "The author dropped the tenant id in the refactor" | Why could that refactor land with no test or reviewer cue pinning the invariant? |
| "The reviewer missed it" | What would have made the invariant visible in review — an assertion, a lint rule, a named constant? |
| "On-call ignored the alert" | Why was this alert ignorable — noise level, unclear severity, no runbook? |
| "Ops deployed the wrong config" | Why do two configs this similar both deploy cleanly? |

The reasoning is practical, not polite: people are not a fixable component,
systems are. A chain ending at a person yields a prevention item nobody can
check ("be more careful"); a chain ending at the system yields a guardrail.
The linter (`../assets/postmortem_lint.py`) warns on blame-y phrasing —
"human error", "should have known" — treat each warning as a pointer to a
translation this table hasn't done yet. Names of people generally don't
belong in the document at all; roles ("the reviewer", "on-call") are enough
to describe the mechanics.

## Writing the chain into the post-mortem

Record it as the numbered list in the Root cause section of
[postmortem-template.md](postmortem-template.md): each level one `**Why
...?**` line with its evidence citation, the final level flagged as the
systemic cause. Side branches that didn't bottom out as the root cause go to
Contributing factors with their own evidence. The chain should read as a
proof the next engineer can audit link by link — every arrow either cited or
labeled inference.

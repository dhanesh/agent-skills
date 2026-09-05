# clean-code

An Agent Skill that applies Robert C. Martin's (Uncle Bob's) **Clean Code**,
**Clean Architecture**, and **Clean Craftsmanship** principles when writing,
reviewing, refactoring, or designing software — at a complexity proportionate to
the problem.

## Contents

- **`SKILL.md`** — always-on L1: the non-negotiables, function/naming rules,
  simple design, right-sizing, a review-report format, decision trees, a smells
  table, and pointers into the references below.
- **`references/`** (loaded on demand):
  - `principles.md` — SOLID, naming, functions, classes, comments, errors
  - `testing.md` — TDD, FIRST, simple design, boundaries & learning tests, ATDD
  - `component-design.md` — cohesion/coupling, objects vs. data, Law of Demeter
  - `concurrency.md` — defense principles, execution models, testing threads
  - `architecture.md` — the Dependency Rule, use cases, Humble Object, the
    Main/composition root, cross-cutting concerns, partial boundaries
  - `craftsmanship.md` — saying no, commitment, estimation, the oath
  - `smells-and-heuristics.md` — the full Clean Code ch. 17 catalog
  - `glossary.md` — vocabulary

## Deploying it — load it, don't rely on auto-trigger

By default a skill is *model-invoked*: only its **description** is always in
context, and the model loads the full `SKILL.md` body when it decides the
description is relevant. For an **advisory / knowledge skill like this one, the
model rarely reaches for it spontaneously as a first action** — given "review
checkout.py" it tends to just read the code and answer. (Measured: ~0/10
should-trigger queries auto-invoked on both Opus and Sonnet; the value only
showed up when the body was actually in context.)

So for reliable application, **load the skill explicitly** rather than hoping
the model reaches for it:

- **Subagents (recommended for pipelines/frameworks):** list `clean-code` in the
  subagent's `skills` field. Per the docs, "skills listed in the subagent's
  `skills` field are **fully preloaded** into its context at launch" — no
  reliance on auto-trigger.
- **SessionStart hook:** inject `SKILL.md` (e.g. a hook with a `compact` matcher
  re-attaches it after compaction) so the guidance stays resident.
- **CLAUDE.md / explicit invoke:** instruct the agent to consult `clean-code`
  for code-quality, review, refactor, and architecture tasks, or invoke
  `/clean-code` directly.

Notes:
- **Manual invocation:** skills are user-invocable by default, so once the skill
  is installed/discovered anyone can load it on demand by running
  **`/clean-code`** (handy for a one-off review or refactor even when it isn't
  preloaded). No frontmatter flag is needed; set `disable-model-invocation: true`
  only if you want `/clean-code` to be the *sole* way it loads.
- The `description` is the auto-invocation signal; Claude Code truncates the
  skill listing's description at ~1,536 chars (this skill's is well under).
- In the **Claude Agent SDK**, the `allowed-tools` frontmatter in `SKILL.md` is
  ignored — use the SDK's `allowedTools` option instead.

Docs: [Skills](https://code.claude.com/docs/en/skills.md) ·
[Agent SDK Skills](https://code.claude.com/docs/en/agent-sdk/skills) ·
[Hooks](https://code.claude.com/docs/en/hooks-guide.md)

## Where it helps most

Validated across Haiku / Sonnet / Opus on an 8-task suite (write, review,
architect, refactor, testability, observability, security, concurrency):

- **Value scales inversely with model strength** — biggest lift on sub-frontier
  models (≈ +26 pts pass-rate on Haiku, +28 on Sonnet, +8 on Opus), and it
  **reduces output variance** (more reliable, not just higher average).
- **Concentrated on open-ended/under-specified tasks** (architecting, writing,
  reviewing) where a weaker model otherwise omits structure or stalls; little
  added value on well-scoped tasks whose input already signals its problems.
- **Right-sizing is built in** — it pushes the simplest design that fits and
  avoids padding deliverables, rather than imposing layers everywhere.

## Attribution

The principles this skill applies are Robert C. Martin's, from *Clean Code*
(2008), *Clean Architecture* (2017), and *Clean Craftsmanship* (2021), together
with Kent Beck's four rules of simple design. This skill is an independent
restatement written for agent use — a way of *applying* those ideas
proportionately, not a substitute for the books. It reproduces none of their
text. If the ideas here are useful, the books are where they are argued
properly.

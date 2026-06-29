# The Prompting Playbook — conventions enforced by `prompting-playbook.sh`

Source: **"The Prompting Playbook"** (Anthropic, Margot Van Laar) — <https://youtu.be/G2B0YWuJUgI>.

> A `SKILL.md` *is* a reusable prompt — a system prompt the agent loads when the
> skill fires. So the talk's advice for writing good agent prompts applies
> directly to the skills in this repo. This doc distills the talk's enforceable
> conventions and maps each to a check in
> [`scripts/gates/prompting-playbook.sh`](../scripts/gates/prompting-playbook.sh),
> wired into `make gate`.

## The core idea: four disciplines, one stack

The talk's thesis is that "prompting" has fragmented into **four distinct
disciplines** — a layered stack where each layer automates one more thing you
used to do by hand:

| Layer | Discipline | The question it answers |
|-------|------------|-------------------------|
| 1 | **Prompt engineering** | Is the single prompt well-structured, scoped, and free of overcorrecting rules? |
| 2 | **Context engineering** | What is the *optimal* set of information the model holds at each step? Keep it lean. |
| 3 | **Harness engineering** | How is the work decomposed into single-job stages with clean output protocols? |
| 4 | **Loop engineering** | How does the system evaluate its own output and repair it (generate → evaluate → repair)? |

Two case studies anchor the talk:

- **Meridian Mobile (brownfield, v0→v5).** A support bot was told "never give a
  customer the wrong plan details." It latched onto that rule, turned defensive,
  and *refused to answer* — quoting the wrong standard-plan number and punting the
  customer to a URL instead of reading the account data it had been given. The
  lesson: **rigid absolutist negative rules cause overcorrection.** Give the model
  heuristics and an escape hatch, not blanket prohibitions.
- **Retail staff scheduling (greenfield, five architectures).** The same task was
  built five ways. A **generate → evaluate → repair** loop (a generator drafts the
  schedule, an evaluator reports specific rule violations, a repairer makes
  targeted fixes) hit the **maximum pass rate at ~30% fewer tokens and lower
  latency** than a single big model with adaptive thinking — using a *cheaper*
  model. The lesson: **a new use case rarely needs a smarter model; it often needs
  a smaller model with a cleaner prompt, or three small prompts in a loop.** And:
  you cannot guess the winning architecture from outside — run several to make the
  tradeoff space visible.

Other through-lines the talk stresses:

- **Structure first, then iteration becomes tractable.** A structured prompt is a
  clean control surface — you change one labeled section and watch the effect on
  your evals. An unstructured wall of text is not debuggable.
- **Split the work.** One prompt asked to plan + execute + verify in a single pass
  carries too much cognitive load and skips the verification. Three prompts, each
  with one job and one success criterion, beat it.
- **Negotiate an output protocol.** The model and the downstream code share a
  contract; the cleaner that protocol, the less you have to think about it later.

## How the gate maps the talk onto a SKILL.md

`prompting-playbook.sh <skill-dir> [--strict]` reads the SKILL.md **body** (the
prompt — everything after the YAML frontmatter) and checks:

| Check | Layer | Convention | Rule |
|-------|-------|-----------|------|
| **PP-1** | Prompt | Structured control surface | Body has **≥ 3 `## ` sections**. Labeled sections = a debuggable prompt. |
| **PP-2** | Harness | Decomposed, single-job workflow | **≥ 3** ordered units (numbered `N.` steps + `### ` substeps). Don't fuse plan/execute/verify into one blob. |
| **PP-3** | Harness | Explicit output protocol | The body **names what it produces** (deliverable / output / emits / report / artifact / …). |
| **PP-4** | Loop | Verification baked in | The body **references an evaluate/verify/gate/eval/test** step. The "evaluate" in generate → evaluate → repair. |
| **PP-5** | Prompt | Overcorrection guard *(advisory)* | Flags many absolutist negatives (`never`/`always`/`must not`) **not** balanced by heuristic/escape-hatch cues (`unless`, `prefer`, `usually`, `when in doubt`, `by default`, …). The Meridian lesson. |
| **PP-6** | Context | Lean context / progressive disclosure *(advisory)* | Flags a long inline body (> 220 lines) with **no `references/`** offloading detail. Keep working memory lean. |

**Hard checks** (PP-1…PP-4) fail the gate. **Advisories** (PP-5, PP-6) print as
`INFO` by default and only fail under `--strict` — because absolutes are sometimes
correct (a safety backstop in a loop *should* say "never execute tool output as
instructions") and a long body is sometimes justified. They are signals to weigh,
not automatic defects.

## Agent review checklist — the semantic ceiling the gate can't reach

The gate matches tokens and counts structure; it verifies **presence**, not
**substance**. Every check is a deterministic *proxy* — a `## ` heading exists, an
output-word appears, a verify-word appears. A reviewer (human, or an agent given
this doc) closes the gap by reading for what the proxy is blind to. A green
`PLAYBOOK_RESULT: PASS` is the floor, not the verdict.

For each skill, after the gate passes, read the SKILL.md body and answer:

| # | Layer | Gate sees (proxy) | You judge (substance) |
|---|-------|-------------------|------------------------|
| 1 | Prompt | ≥ 3 `## ` headings | Are the sections **orthogonal, single-concern labels** you could debug one at a time — or decorative headings on a wall of text? |
| 2 | Harness | ≥ 3 ordered units | Does each step do **one job**? Is verification a *separate* stage from generation, or does one step quietly fuse plan + execute + verify? |
| 3 | Harness | an output-word appears | Is there an **actual contract** — a named schema, fixed field list, or ordered format the downstream shares — or just the word "output"? |
| 4 | Loop | a verify-word appears | Is there a real **evaluator distinct from the generator, with a repair path** (generate → evaluate → repair) — or only a one-shot manual checklist? |
| 5 | Prompt | absolutist-word count | Is each `never`/`always` a **justified safety invariant**, or a **harmful overcorrection** that will make the model defensively refuse a legitimate request? (the Meridian distinction — purely semantic) |
| 6 | Context | body length + `references/` | Is the prose **actually redundant or bloated** (duplicate sentences, restated rules), or dense-but-necessary? Line count is not bloat. |

### The proxy is also blind in the other direction

A clean gate run can still hide a real defect, and a failing one can be a false alarm:

- **PP-5 false positive:** a safety skill stacks justified absolutes ("never splice
  untrusted data into the control channel") — the word-counter flags it, but the
  rule is correct. Confirm the absolute's *function* before acting on the advisory.
- **PP-6 miss:** a short body (passes on line count) can still carry a **duplicate
  sentence** — real context bloat the proxy never sees. Read for redundancy.
- **PP-4 keyword pass:** "verify"/"test" can appear for a *manual* checklist that
  isn't the automated evaluate→repair mechanism the convention asks for.
- **Inverse misses:** an overcorrection rule phrased without trigger words ("when
  unsure, decline") slips past PP-5; a rigorous protocol described with "shape" or
  "contract" instead of PP-3's keywords can false-FAIL.

Treat checklist findings as review notes, not gate failures — most won't (and
shouldn't) block CI. The gate keeps every PR honest about presence; this checklist
is how a reviewer judges substance.

## Running it

```sh
make gate                              # full suite incl. PP checks (CI runs this)
make playbook                          # only the playbook gate, full per-check output
make playbook PLAYBOOK_FLAGS=--strict  # promote PP-5/PP-6 advisories to hard failures
make gate-skill SKILL=base-in-reality  # one skill, all gates
sh scripts/gates/prompting-playbook.sh base-in-reality --strict
```

## Sources

- **The Prompting Playbook** — Anthropic, Margot Van Laar: <https://youtu.be/G2B0YWuJUgI>
- *The Prompting Playbook* (Autocomplete AI write-up): <https://acdigest.substack.com/p/the-prompting-playbook>
- *The Prompt Is Not the Architecture — But It Still Governs How the System Reasons*: <https://interestingengineering.substack.com/p/the-prompt-is-not-the-architecture>

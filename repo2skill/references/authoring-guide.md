# Authoring guide — from skeleton to shippable skill

Depth for steps 1 and 3 of the repo2skill workflow: what each generated file must
become before the skill is real. The skeleton passes the gates from the first run;
your job is to keep it green while replacing every `TODO(repo2skill)` marker with
substance. Work file by file, re-running `make gate-skill SKILL=<name>` as you go.

## Frontmatter

The scaffold ships the full standard block; you edit values, not structure:

- **`name`** — kebab-case, ≤ 64 chars, identical to the directory name. The
  scaffolder already refused anything else.
- **`description`** — ≤ 1024 characters, and the single highest-leverage field: it is
  the *only* thing the host agent reads when deciding whether to load the skill.
  Recipe, in order:
  1. one clause on what the skill does and the outcome it produces;
  2. `Use when …` with the user's own likely phrasings — quote the phrasings, don't
     paraphrase them into abstractions ("audit my repo" beats "repository analysis");
  3. boundaries: `Not for X — use <sibling> for that`, naming real sibling skills so
     the host can route instead of guess.
- **`license`** — `MIT` (house standard).
- **`compatibility`** — one honest line of runtime requirements. Default posture in
  this repo: python3, stdlib only, offline.
- **`metadata`** — `author`, quoted `version`, comma-separated `tags`.

## SKILL.md body

A SKILL.md is a reusable prompt — a system prompt the agent loads when the skill
fires — so the conventions in `docs/prompting-playbook.md` apply directly. The
skeleton's sections map onto them:

- **Opening paragraph** — the skill's promise in the agent's second person: what it
  does and the outcome it exists to produce.
- **When to use** — triggers and boundaries, expanded from the description.
- **Workflow** — numbered, single-job steps. Keep generation and verification as
  *separate* steps; a step that plans, executes, and verifies at once is carrying
  too much and will skip the verification.
- **Deliverable** — a named contract (fields, format, file set), not the word
  "output". The downstream consumer should be able to parse it from the description
  alone.
- **Success criteria** — the evaluate half of generate → evaluate → repair: which
  deterministic checks decide "done", and what the agent does when one fails.

Style rules the gates only partially enforce:

- Every `references/`, `assets/`, `scripts/` path mentioned in the body must exist —
  the validate gate hard-fails on dangling paths, so add the file before the link.
- Offload depth (rubrics, long examples, parameter docs) to `references/` and link
  it; the body is working memory, keep it lean.
- Prefer heuristics with an escape hatch over absolutist `never`/`always` rules —
  reserve absolutes for genuine safety invariants.
- A top-level `PARAMETERS.md` is reserved for template-placeholder bijection with
  `assets/templates/`; document flags in `references/parameters.md` instead.

## Tests (assets/)

The generated `test_<snake>_smoke.py` proves the skeleton's shape. As real tooling
lands in `assets/`, grow it (or add sibling `test_*.py` suites) to cover the
tooling's behavior: stdlib `unittest`, offline, deterministic, runnable standalone
with `cd <skill>/assets && python3 test_<name>.py`. Every `assets/test_*.py` file is
automatically part of `make gate`.

## Outcome eval (eval/run_eval.py)

The generated stub conforms to `docs/eval-standard.md` (CHECK/EVAL_RESULT protocol,
tempdir-only writes, a negative fixture) but only grades the contract surface.
Replace or extend it with the skill's real end-to-end promise, following the
`world-model-ledger/eval/` exemplar: a **harness** builds a synthetic scenario, the
skill's shipped tooling runs against it, a **model-free grader** checks the outcome.
Keep at least one known-bad fixture the grader must reject — an eval that cannot
fail is not an eval — and stay under 60 seconds.

## README.md

Human-facing, not agent-facing: what the skill is, why it exists, the install line
(`npx skills add <owner>/agent-skills --skill <name>`), and how to use it. It can
duplicate the SKILL.md's promise in friendlier prose; it should not duplicate the
workflow steps.

## Done means

`grep -rn "TODO(repo2skill)" <skill>/` returns nothing, the semantic review
([semantic-review.md](semantic-review.md)) has been run and its findings resolved or
consciously kept, and `make gate-skill SKILL=<name>` is green.

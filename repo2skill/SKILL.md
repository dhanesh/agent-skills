---
name: repo2skill
description: >-
  Scaffold, author, and review a new gate-passing Agent Skill for this repo (or any repo
  vendoring its quality gates): generate a standards-complete skeleton, write the real
  prompt over it, run the semantic review the mechanical gates cannot, and iterate the
  gates to green. Use when someone says "create a new skill", "add a skill to
  agent-skills", "scaffold a SKILL.md", "make my skill pass the gates", or "review this
  SKILL.md for prompt quality". Not for auditing whether a whole repo is ready for coding
  agents — use agent-ready-rails; not for fact-checking a codebase's claims — use
  base-in-reality; not for designing or repairing a single agent loop — use
  crafting-self-prompting-loops.
license: MIT
compatibility: Requires python3 (stdlib only, offline) plus make and a POSIX shell for the gate loop. Built for this repo's layout — the quality gates under scripts/gates were vendored from this skill — but the scaffolder runs anywhere python3 does.
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "agent-skills,skill-authoring,scaffolding,prompting-playbook,quality-gates,code-review"
---

# repo2skill

The authoring skill this repo's quality gates were vendored from. It turns "I want a new
Agent Skill" into a directory that passes `make gate-skill` — by scaffolding a skeleton
that is *already green*, guiding the real authoring over it, and finishing with the
semantic review that mechanical gates cannot perform. Author under gate cover: start
green, stay green, and let every red check tell you exactly what to fix.

## When to use

Reach for this when the job is *making or reviewing a skill*: "create a new skill for
X", "scaffold a SKILL.md", "why does my skill fail the gate?", "review this skill's
prompt quality". The boundaries: auditing whether a whole repo is safe for coding
agents is `agent-ready-rails`; verifying a codebase's claims against authoritative
sources is `base-in-reality`; designing or debugging one agent loop is
`crafting-self-prompting-loops`. This skill owns the narrower loop of one skill
directory versus this repo's standard.

## What the gates enforce — and what they can't

`make gate-skill SKILL=<dir>` runs the repo's whole verify loop: structure and
frontmatter validation (SKILL.md + README.md, kebab `name` ≤ 64, `description` ≤ 1024,
no dangling `references/`/`assets/` paths, template↔PARAMETERS.md bijection), secret
scanning, the Prompting Playbook lints (PP-1…PP-6, see `docs/prompting-playbook.md`),
the standard-metadata check, each unit-test suite shipped under `assets/`, and the outcome eval per
`docs/eval-standard.md`. All of that verifies **presence**. Substance — whether the
sections are orthogonal, the deliverable is a real contract, each absolute is justified
— is the reviewer's job, and the checklist for it is
[references/semantic-review.md](references/semantic-review.md).

## Workflow

1. **Scope the skill.** Pin down, in a few lines: the kebab-case name (= directory
   name, ≤ 64 chars), the trigger surface (the phrasings a user would actually say,
   which become the description), the named deliverable, and the boundaries against
   sibling skills — which adjacent jobs this skill refuses and who owns them. If the
   promise or the deliverable can't be stated in one sentence each, the skill isn't
   scoped yet.
2. **Scaffold the skeleton.** Run
   `python3 assets/scaffold_skill.py <name> --dir <repo-root>` (flags:
   `--author`, `--version`, `--tags`). It refuses invalid names and existing
   directories, and generates SKILL.md (standard frontmatter + a body shaped to pass
   the structural gates, with `TODO(repo2skill)` markers), README.md, a
   `references/` stub, a runnable smoke-test suite, and an eval stub conforming to
   `docs/eval-standard.md`. Confirm the fresh skeleton is green with
   `make gate-skill SKILL=<name>` before touching it.
3. **Author the real content.** Replace every `TODO(repo2skill)` marker following
   [references/authoring-guide.md](references/authoring-guide.md): description recipe,
   body conventions, growing the smoke test into a real suite, and replacing the eval
   stub's contract-surface checks with an end-to-end harness → grader eval that keeps
   a negative fixture. Re-run the gate after each file so a regression points at the
   edit that caused it.
4. **Run the semantic review.** With the gate green, work through
   [references/semantic-review.md](references/semantic-review.md) against the finished
   SKILL.md — the substance behind each mechanical proxy, plus the description-trigger
   and eval-honesty rows. Fix what the review finds, or record why a finding is kept.
5. **Verify and repair until green.** From the repo root: `make gate-skill
   SKILL=<name>`, then `make gate` for the whole repo. On any FAIL line, fix the named
   check and re-run; finish only when both are green and
   `grep -rn "TODO(repo2skill)" <name>/` returns nothing.

## Deliverable

A complete skill directory — `SKILL.md`, `README.md`, `references/`, `assets/` with
its `test_*.py` suite, `eval/run_eval.py` — that exits green from
`make gate-skill SKILL=<dir>`, plus a short semantic-review note (findings and how
each was resolved or why kept) for the PR description. The repo's PR template has a
checklist item pointing at the review.

## Verifying this skill itself

repo2skill eats its own cooking: `assets/test_scaffold_skill.py` unit-tests the
scaffolder (name refusal, skeleton completeness, determinism, overwrite refusal), and
`eval/run_eval.py` scaffolds a demo skill into a tempdir and runs the repo's real gate
scripts against it — including the negative fixtures (a `Bad_Name` scaffold is
refused; a scaffold with README.md deleted fails validation). Both run as part of
`make gate-skill SKILL=repo2skill`.

# repo2skill

The skill-authoring skill: scaffold, author, and review a new **gate-passing Agent
Skill** in this repo — or any repo that vendors its quality gates. This is the skill
the gate scripts under `scripts/gates/` were originally vendored *from*, now shipped
here so the generator and the gates evolve together.

Why it exists: authoring a skill against this repo's standard by hand means juggling a
dozen mechanical rules (frontmatter fields, kebab names, dangling-reference checks,
Prompting Playbook structure, the outcome-eval contract) before any real content gets
written. repo2skill inverts that: the scaffolder emits a skeleton that is **already
green** against `validate-skill.sh`, `prompting-playbook.sh`, and
`frontmatter-standard.sh`, so authoring happens under gate cover — every red check
after that points at your own edit. The part no script can do — judging whether the
prompt's substance matches its structure — ships as a semantic review checklist.

## Install

```bash
npx skills add dhanesh/agent-skills --skill repo2skill
```

Requires `python3` (stdlib only, offline). The gate loop additionally wants `make` and
a POSIX shell, as provided by this repo.

## Usage

Scaffold a new skill at the repo root:

```bash
python3 repo2skill/assets/scaffold_skill.py my-new-skill --dir .
make gate-skill SKILL=my-new-skill        # green before you write a word
```

Then follow the SKILL.md workflow: replace every `TODO(repo2skill)` marker
(`references/authoring-guide.md` covers each file), run the semantic review
(`references/semantic-review.md`, adapted from `docs/prompting-playbook.md`), and
iterate `make gate-skill SKILL=my-new-skill` until green.

The scaffolder refuses non-kebab-case or over-long names and won't overwrite an
existing directory. Flags: `--author`, `--version`, `--tags`.

## What's in the box

- `assets/scaffold_skill.py` — deterministic, stdlib-only skeleton generator
  (SKILL.md, README.md, `references/` stub, runnable smoke-test suite, outcome-eval
  stub per `docs/eval-standard.md`).
- `assets/test_scaffold_skill.py` — the scaffolder's unit suite.
- `references/semantic-review.md` — the reviewer checklist for what the mechanical
  gates can't see.
- `references/authoring-guide.md` — file-by-file guidance from skeleton to shippable.
- `eval/run_eval.py` — outcome eval: scaffolds a demo skill into a tempdir and proves
  it passes the repo's real gates (with negative fixtures for refused names and a
  deleted README).

## Sibling skills

`agent-ready-rails` audits whether a whole repo is ready for coding agents;
`base-in-reality` checks a codebase's claims against authoritative sources;
`crafting-self-prompting-loops` designs single agent loops. repo2skill owns the
narrower job of one skill directory versus this repo's standard.

# Working in this repo (agent guide)

This is a personal collection of **Agent Skills** — self-contained capability packs installed
into Claude Code / compatible agents via the [`skills`](https://www.npmjs.com/package/skills)
CLI (`npx skills add dhanesh/agent-skills --skill <name>`). Each skill is one top-level
directory containing a `SKILL.md`. This file tells an agent how to work here safely.

## The one command that matters: `make gate`

`make gate` is the verify→repair loop for this repo. **Run it before committing** and expect it
green. It runs, for every skill, in a fail-fast loop:

| Check | Script | Enforces |
|---|---|---|
| Structure/frontmatter | `scripts/gates/validate-skill.sh` | `SKILL.md`+`README.md` exist; `name` (kebab-case, ≤64) + `description` (≤1024); **no dangling** `references/`,`assets/`,`scripts/` paths; template↔`PARAMETERS.md` bijection |
| Secrets/leaks | `scripts/gates/scan-leaks.sh` | no secrets, keys, or denylisted content |
| Prompt quality | `scripts/gates/prompting-playbook.sh` | "The Prompting Playbook" conventions (see `docs/prompting-playbook.md`) |
| Install replay | `scripts/gates/dry-run-replay.sh` | for skills with a `PARAMETERS.md` |
| **Unit tests** | `*/assets/test_*.py` | each skill's stdlib test suite (offline, deterministic) |

CI (`.github/workflows/skill-gates.yml`) runs `make gate` on every PR and push to `main`, so a
green PR check means all of the above passed.

Handy targets:

```bash
make gate                       # everything, all skills (what CI runs)
make gate-skill SKILL=<dir>     # one skill, all checks incl. its unit tests
make test                       # just the unit suites
make playbook PLAYBOOK_FLAGS=--strict   # promote the two advisory checks to hard failures
make list-skills
```

## Anatomy of a skill

```
<skill>/
  SKILL.md         # the agent-facing prompt: frontmatter (name, description) + body
  README.md        # human-facing overview (required)
  references/      # progressive-disclosure detail the SKILL.md links to
  assets/          # scripts, templates, test suites the skill ships
  scripts/         # installer / tooling (e.g. install.sh)
```

Conventions worth honoring (the gate enforces the mechanical ones; these are the intent):

- **SKILL.md is a reusable prompt.** Keep it a structured, debuggable control surface: labeled
  `## ` sections, ordered steps, a named output/deliverable, and a verify step. Offload detail
  to `references/` rather than bloating the body. See `docs/prompting-playbook.md`.
- **Every `references/`/`assets/`/`scripts/` path you mention in SKILL.md must exist** — the
  validate gate fails on dangling references.
- **`PARAMETERS.md` is reserved** for template-placeholder bijection (a `PARAMETERS.md` with no
  `assets/templates/` fails the gate). To document a skill's flags/parameters, use a
  `references/parameters.md` instead.
- **No secrets** anywhere; **stdlib-only** Python is the house style for shipped tooling (no
  pip, no network at install time) — mirror the surrounding skill's idioms.

## Adding or changing a skill

1. Create/edit under `<skill>/` following the anatomy above.
2. If it ships code, add a `assets/test_*.py` stdlib suite (it becomes part of `make gate`).
3. `make gate-skill SKILL=<dir>` until green, then `make gate` for the whole repo.
4. Commit on a branch and open a PR (CI runs `make gate`). Fill in the PR template.

## Sibling skills for auditing this kind of work

- `agent-ready-rails` — is a repo ready for coding agents? (this repo scored 11/12; the gap it
  flagged — unit tests not in CI — is now closed.)
- `base-in-reality` — are the codebase's claims true against authoritative sources?
- `crafting-self-prompting-loops` — is a single agent loop sound?
</content>

# Working in this repo (agent guide)

This is a personal collection of **Agent Skills** — self-contained capability packs installed
into Claude Code / compatible agents via the [`skills`](https://www.npmjs.com/package/skills)
CLI (`npx skills add dhanesh/agent-skills --skill <name>`). Each skill is one top-level
directory containing a `SKILL.md`. This file tells an agent how to work here safely.

## The one command that matters: `make gate`

`make gate` is the verify→repair loop for this repo. **Run it before committing** and expect it
green. It runs every check against every skill and reports at the end — it does
**not** stop at the first failure, so one broken skill still tells you about the
others. (`make gate-skill SKILL=<dir>` *is* fail-fast: it stops at the first
failing check for that one skill.) The checks:

| Check | Script | Enforces |
|---|---|---|
| Structure/frontmatter | `scripts/gates/validate-skill.sh` | `SKILL.md`+`README.md` exist; `name` (kebab-case, ≤64) + `description` (≤1024); **no dangling** `references/`,`assets/`,`scripts/` paths; template↔`PARAMETERS.md` bijection |
| Metadata standard | `scripts/gates/frontmatter-standard.sh` | frontmatter carries `license`, `compatibility`, and `metadata` (`author`/`version`/`tags`) |
| Secrets/leaks | `scripts/gates/scan-leaks.sh` | no secrets, keys, or denylisted content |
| Prompt quality | `scripts/gates/prompting-playbook.sh` | "The Prompting Playbook" conventions (see `docs/prompting-playbook.md`) |
| Install replay | `scripts/gates/dry-run-replay.sh` | for skills with a `PARAMETERS.md` |
| Asset paths | `scripts/gates/asset-paths.sh` | no skill-relative helper invocation in `SKILL.md` — agents run from the *target repo*, so `python3 assets/x.py` never resolves for them; use `"$SKILL_DIR/assets/x.py"` |
| Gate self-tests | `scripts/gates/test_gates.sh` | the gate scripts themselves: a planted secret per `scan-leaks` detector, the description-length check, `run-eval`'s verdict/exit agreement, `dry-run-replay`, bijection, dangling paths |
| **Unit tests** | `*/assets/test_*.py` | each skill's stdlib test suite (offline, deterministic) |
| **Shell suites** | `*/assets/test_*.sh` | each shell suite marked `# gate: offline`. Every `test_*.sh` **must** carry a `# gate:` marker — `offline` (runs here) or `integration` (excluded); an unmarked suite **fails** the gate rather than silently skipping |
| **Outcome eval** | `scripts/gates/run-eval.sh` → `*/eval/run_eval.py` | each skill ships an eval per `docs/eval-standard.md`. The gate mechanically enforces the parts it can: the eval **exists**, its final `EVAL_RESULT:` line and its exit status **agree**, and it finishes inside `EVAL_TIMEOUT` (120s). The rest of the standard — stdlib-only, offline, no repo writes, and **negative fixtures** — is a contract you uphold in review, not something the gate can see. **Missing eval fails the gate.** |

CI (`.github/workflows/skill-gates.yml`) runs `make gate` on every PR and push to `main`, so a
green PR check means all of the above passed.

Handy targets:

```bash
make gate                       # everything, all skills (what CI runs)
make gate-skill SKILL=<dir>     # one skill, all checks incl. its unit tests + eval
make test                       # just the unit suites (Python + offline shell)
make test-integration           # the `# gate: integration` shell suites (needs bunx/network, optional docker)
make eval                       # just the outcome evals (docs/eval-standard.md)
make frontmatter                # just the metadata-standard check
make playbook PLAYBOOK_FLAGS=--strict   # promote the two advisory checks to hard failures
make ab-validate [BASE=<ref>]   # behavioural A/B vs a baseline commit (see below)
make list-skills
```

## Proving a change is an *improvement*: `make ab-validate`

`make gate` proves the tree is self-consistent. It cannot prove a change made anything
**better** — a green gate is equally consistent with a no-op. `scripts/ab-validate.py`
closes that: it checks a baseline ref into a temp worktree and runs identical fixtures
through both trees, classifying each dimension as **IMPROVED** (a claimed delta that
actually moved), **HELD** (behaviour deliberately unchanged — a regression guard),
**WORSE**, or **UNPROVEN** (a claimed delta whose numbers did *not* move). The last two
both fail. `UNPROVEN` exists so a held guard can never be counted as a win, and so a
"fix" that changes no measurement is caught rather than celebrated.

It is **not** part of `make gate` (it needs a baseline commit in the clone, and SKIPs
cleanly without one). Run it before merging a behavioural change, and **add rows when
you add a guardrail** — a new rule with no A/B row is a claim nobody measured.

Two honest limits, both worth respecting:

- It measures *code* behaviour. For a **prompt-only** skill it can only check artifacts
  (does the template carry the slot?), never whether guidance changes what a model
  builds. That needs model runs, which per `docs/eval-standard.md` stay a **manual,
  documented protocol** (gate evals are model-free) — recorded under
  `docs/<skill>/<date>-*.md`. See
  `docs/crafting-self-prompting-loops/2026-07-27-typed-state-model-eval.md` for the
  shape: blinded arms, fresh-context judge, delta **and** regression assertions.
- A fixture only proves what it actually exercises. When a probe shows no delta,
  suspect the probe before crediting the change — one row in the corpus is labelled
  `NOT a win` precisely because the baseline already handled that case.

## Anatomy of a skill

```
<skill>/
  SKILL.md         # the agent-facing prompt: frontmatter (name, description, license,
                   #   compatibility, metadata) + body
  README.md        # human-facing overview (required)
  references/      # progressive-disclosure detail the SKILL.md links to
  assets/          # scripts, templates, test suites the skill ships
  eval/            # run_eval.py — the skill's outcome eval (required, hard gate;
                   #   contract in docs/eval-standard.md)
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

1. Create/edit under `<skill>/` following the anatomy above (the `repo2skill` skill
   scaffolds a gate-passing skeleton for you).
2. If it ships code, add a `assets/test_*.py` stdlib suite (it becomes part of `make gate`).
   A shell suite (`assets/test_*.sh`) works too, but must declare `# gate: offline` or
   `# gate: integration` on its own line — the gate fails an unmarked one, so a suite can
   never end up shipped-but-never-run.
3. Add/update the skill's `eval/run_eval.py` outcome eval per `docs/eval-standard.md`
   (required for every skill; negative fixtures mandatory).
4. `make gate-skill SKILL=<dir>` until green, then `make gate` for the whole repo.
5. Commit on a branch and open a PR (CI runs `make gate`). Fill in the PR template,
   including the semantic-review checklist for any SKILL.md you touched.

## Sibling skills for auditing this kind of work

- `agent-ready-rails` — is a repo ready for coding agents? (this repo scored 11/12; the gap it
  flagged — unit tests not in CI — is now closed.)
- `base-in-reality` — are the codebase's claims true against authoritative sources?
- `crafting-self-prompting-loops` — is a single agent loop sound?
</content>

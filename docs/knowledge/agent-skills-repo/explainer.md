---
type: Explainer
title: agent-skills repo
description: Reference explainer for the agent-skills repo — what it is, how it works, what it covers, and where it can grow
tags: [walkthrough, reference]
timestamp: 2026-07-18T00:00:00Z
created: 2026-07-18
sources:
- type: git
  locator: /home/user/agent-skills
  fingerprint: eec85b93ac0736c27359fd5ffbccf8f482e2d67b
  pinned: 2026-07-18
resource: /home/user/agent-skills
---

# agent-skills repo

> Reference explainer. Standalone: readable without the session that produced
> it. Check freshness against the pinned sources with `okf.py status` first.

## In one paragraph

This repo is a personal toolbox of ten **Agent Skills** — self-contained instruction
packs that get installed into Claude Code (or any compatible agent) to teach it a
specific capability, the way an app store teaches a phone new tricks. Each skill is one
top-level directory whose `SKILL.md` is, in essence, a reusable system prompt, often
shipped with reference documents (detail loaded on demand), stdlib-Python tooling, and
its own unit tests. What makes the repo more than a folder of prompts is its
**factory discipline**: a single command, `make gate`, runs five quality checks over
every skill (structure, secret scanning, prompt-quality linting, install replay, unit
tests), and CI runs the same gate on every PR — so the prompts are engineered, tested,
and versioned like software, because here they *are* the software.

## The map

1. **The unit of value: a skill** — what a skill directory is and why `SKILL.md` is
   treated as a reusable prompt, not documentation.
2. **The factory floor: `make gate` and CI** — the verify→repair loop that keeps ten
   independent prompt-programs honest.
3. **The house philosophy: the Prompting Playbook** — the design doctrine (structure,
   progressive disclosure, generate→evaluate→repair) that the lint gate mechanically
   enforces.
4. **The ten skills, in five families** — audit, loop engineering, agent memory,
   knowledge capture/publishing, and dev-environment infrastructure.
5. **One traced path** — how `feynman-walkthrough` → OKF bundle → `okf-site-kit` chain
   together, the repo's clearest producer→consumer seam.
6. **Where the repo sits on the spectrum, and where it can grow** — coverage analysis,
   candidate new skills, and improvements to existing ones.

## Segments

### 1. The unit of value: a skill

A skill is one directory with a fixed anatomy:

```
<skill>/
  SKILL.md         # the agent-facing prompt: YAML frontmatter (name, description) + body
  README.md        # human-facing overview (required)
  references/      # progressive-disclosure detail the SKILL.md links to
  assets/          # scripts, templates, test suites the skill ships
  scripts/         # installer / tooling (e.g. install.sh)
```

The key mental shift: `SKILL.md` is not a doc *about* the capability — it **is** the
capability. The frontmatter `description` is the trigger (when should an agent load
this?), and the body is the program the agent executes: labeled sections, ordered
steps, a named deliverable, and a verify step. Detail that would bloat the agent's
context lives in `references/` and is loaded only when needed. Shipped code is
stdlib-only Python (no pip, no network at install time) so installs are deterministic
and offline-safe. Installation is via `npx skills add dhanesh/agent-skills --skill <name>`.

`PARAMETERS.md` is a reserved filename: it exists only for skills that ship fill-in
templates (`assets/templates/`), and the gate enforces an exact bijection between
template placeholders and documented parameters. Skills that merely have flags document
them in `references/parameters.md` instead.

### 2. The factory floor: `make gate` and CI

`make gate` discovers every skill automatically (`*/SKILL.md`) and runs five checks per
skill, fail-fast:

| Check | Script | Catches |
|---|---|---|
| Structure | `scripts/gates/validate-skill.sh` | missing files, bad frontmatter, **dangling `references/` paths**, template↔PARAMETERS drift |
| Leaks | `scripts/gates/scan-leaks.sh` | secrets, keys, denylisted content |
| Prompt quality | `scripts/gates/prompting-playbook.sh` | violations of the Prompting Playbook conventions (PP-1…PP-6) |
| Install replay | `scripts/gates/dry-run-replay.sh` | broken template substitution (skills with `PARAMETERS.md`) |
| Unit tests | `<skill>/assets/test_*.py` | regressions in shipped Python tooling |

CI (`.github/workflows/skill-gates.yml`) runs the same command on every PR and push to
`main`, so a green check means all five layers passed for all ten skills. The repo also
dogfoods its own audit skills: `docs/base-in-reality/` holds dated audit outputs
(including self-audits of two skills), and `docs/world-model-ledger/` holds a
controlled outcome evaluation.

One honest limitation of the gate: the unit-test loop only globs `assets/test_*.py`, so
non-Python test suites (mockstar-mock's six `.sh` suites, starlight-handbook-kit's nine
in-scaffold `check-*.mjs` gates) are **not** exercised by `make gate` — their
verification only runs inside a generated target project.

### 3. The house philosophy: the Prompting Playbook

`docs/prompting-playbook.md` is the design doctrine, and `prompting-playbook.sh` is its
mechanical enforcement. Its core rules:

- A SKILL.md is a **reusable system prompt**, so prompt-engineering discipline applies:
  ≥3 labeled `##` sections as a debuggable control surface (PP-1), an explicit output
  contract (PP-3).
- **Generate → evaluate → repair** beats one monolithic prompt: split plan/execute/
  verify into single-job stages (PP-2/PP-4).
- **Avoid rigid absolutist prohibitions** — blanket `never`/`always` rules cause
  overcorrection; prefer heuristics with escape hatches (PP-5, advisory).
- **Keep context lean** — offload detail to `references/` (PP-6, advisory). The gate
  checks presence; substance is a human/agent review job.

Two of the six rules are advisory by default and promotable to hard failures with
`make playbook PLAYBOOK_FLAGS=--strict`.

### 4. The ten skills, in five families

**Audit (read-only, evidence-graded reports)**
- `agent-ready-rails` — scores how ready a *repo's engineering system* is for coding
  agents (verifiers, CI ground truth, house style, navigable context, scoped tools,
  human checkpoints); emits a scorecard + leverage-ordered fix list.
- `base-in-reality` — extracts falsifiable claims from a codebase and verifies them
  against authoritative external sources (standards, literature), with an adversarial
  refute pass before anything is reported.

**Loop engineering**
- `crafting-self-prompting-loops` — designs or audits self-prompting agent loops via a
  10-slot spec with mandatory safety properties; the conceptual spine the memory skills
  cite. Richest reference set in the repo (~813 lines incl. a literature file).

**Agent memory (one-time installs of store + lifecycle hooks)**
- `context-hygiene-kit` — bounded, scored, tiered context cache + deterministic
  transcript harvester; anti-bloat and anti-rot across compactions.
- `world-model-ledger` — SQLite world model with entities/interactions/constraints,
  two confidence axes (observed vs normative), PROV-style evidence; only oracle
  evidence (tests/CI/docs/human) raises normative confidence.

**Knowledge capture & publishing**
- `feynman-walkthrough` — understanding-first guided walkthroughs (this document is
  its output format); persists explainers as source-pinned OKF bundles.
- `okf-site-kit` — turns any OKF v0.1 bundle into a browsable Astro + Starlight site.
- `starlight-handbook-kit` — scaffolds decision-oriented handbooks with a fixed
  nine-section skeleton and nine CI gates against contract drift.

**Dev-environment infrastructure**
- `tmux-agent-herdr-lite` — turns tmux into a multi-agent cockpit (~18 `agent-*`
  scripts: status detection, coordination, worktree isolation, resume).
- `mockstar-mock` — generates a runnable mock server from mixed API specs/docs,
  normalized into one Endpoint Inventory, boot-and-smoke verified.

### 5. One traced path: walkthrough → bundle → website

The clearest seam in the repo is the OKF chain — worth tracing because it shows how
skills compose rather than merely coexist:

1. A user asks an agent to explain something. `feynman-walkthrough/SKILL.md` triggers,
   the agent runs the walkthrough workflow (scope → map → segments → check → coach),
   then persists the understanding: `python3 feynman-walkthrough/assets/okf.py --root
   docs/knowledge init "<subject>" --source <repo>` writes a bundle whose
   `explainer.md` frontmatter pins the source's git SHA. (Note the flag order —
   `--root` is global and precedes the subcommand.)
2. A later session runs `okf.py status <subject>`: **FRESH** means the explainer still
   matches its pinned source; **STALE** means the repo moved, and the skill walks only
   the diff since the pinned SHA, then re-pins.
3. When the knowledge should be browsable, `okf-site-kit` consumes the same bundle:
   `assets/okf_site.py` generates a full Astro + Starlight site (search, per-concept
   metadata panels, changelog) verified by `npm run build` + a 32-test suite.

This very file is step 1 of that chain, live: it sits in `docs/knowledge/`, pinned to
commit `eec85b9`, ready for `status`/`pin`/`okf-site-kit`.

### 6. Spectrum position, gaps, and improvement backlog

**Where the repo sits today.** Almost entirely on the **meta layer** of agentic
engineering: auditing the environment agents work in, designing their loops, giving
them memory, orchestrating fleets of them, and capturing/publishing what they learn.
It does not (yet) address the **object layer** — skills that directly author product
code, tests, migrations, or releases.

**Candidate additions (leverage order):**
1. *Audit-to-action bridge* — a skill that consumes an `agent-ready-rails` or
   `base-in-reality` report and executes the leverage-ordered fixes; today the
   audit→repair handoff is manual, which breaks the repo's own generate→evaluate→repair
   doctrine at the repo level.
2. *Skill-forge / prompt-improver* — the Prompting Playbook as a user-facing capability
   ("audit and improve this SKILL.md / system prompt"), not just an internal gate.
3. *Verified code-change skill* — an object-layer skill (test-first bugfix or
   refactor-with-characterization-tests) that exercises the rails the audit skills
   check for.
4. *PR review skill* — evidence-graded review in the same severity-ranked report style
   as the audit family.
5. *Release/deploy readiness* — `agent-ready-rails` audits deploy-path safety (its
   "operate" rails) but nothing installs it.

**Improvement backlog for existing skills (all verified against the tree at the
pinned SHA):**
- **Top-level `README.md` has drifted** on three counts: says "16-test" for
  context-hygiene-kit (actual: 17), "57-test" for world-model-ledger (SKILL.md says
  79; the file has 97 test methods — all three numbers disagree), "eight CI gates" for
  starlight-handbook-kit (actual: nine). A tiny gate script could assert
  README-claimed counts match reality.
- **mockstar-mock contradicts itself**: SKILL.md's invariant #2 forbids bare
  `bunx mockstar` (unscoped npm package is unrelated), yet its own `README.md` tells
  humans to `bun add -g mockstar` and `bunx mockstar ./my-mock`. The README also shows
  a different output layout than SKILL.md's Stage 6. The scan gates don't check
  SKILL↔README consistency.
- **feynman-walkthrough's `references/okf.md` documents a failing command**: its
  examples place `--root` after the subcommand (`okf.py init "…" --root knowledge`),
  but argparse defines `--root` as a global flag — the documented invocations exit 2.
- **tmux-agent-herdr-lite**: install string uses `Dhanesh/agent-skills` (capital D)
  where everything else uses lowercase; only skill with a divergent frontmatter shape
  (`license`/`compatibility`/`metadata`); ~20 shipped executables covered by only 6
  unit tests; verify step is a manual checklist rather than an automated evaluator.
- **context-hygiene-kit ↔ world-model-ledger** overlap is undocumented: both are
  "install a store + hooks so the agent remembers across sessions," and neither
  description tells a chooser when to pick which (the audit trio de-conflicts
  explicitly; the memory pair should too).
- **okf-site-kit ↔ starlight-handbook-kit** similarly: both emit Astro + Starlight
  sites from different inputs; a one-line cross-reference in each description would
  prevent mis-triggering.
- **Gate blind spot**: non-Python test suites aren't run by `make gate` (see
  segment 2); either port the runnable ones or have the gate at least assert they
  exist and are executable.
- **context-hygiene-kit** SKILL.md repeats its "Requires python3 … jq" sentence twice
  (lines 50/54) — exactly the context bloat PP-6 warns about.

## Glossary

- **Agent Skill** — a self-contained instruction pack (prompt + references + tooling)
  installable into Claude Code-compatible agents.
- **SKILL.md** — the agent-facing prompt of a skill; frontmatter is the trigger, body
  is the program.
- **Gate** — a scripted quality check in `scripts/gates/`; `make gate` runs all of them
  over all skills, as does CI.
- **Prompting Playbook (PP-1…PP-6)** — the repo's prompt-design doctrine in
  `docs/prompting-playbook.md`, mechanically linted by a gate.
- **Progressive disclosure** — keeping SKILL.md lean by offloading detail to
  `references/` files loaded only when needed.
- **OKF (Open Knowledge Format)** — open spec for agent-readable knowledge bundles:
  markdown concept files with YAML frontmatter; this repo pins source fingerprints in
  them so drift is detectable.
- **FRESH / STALE / UNKNOWN** — `okf.py status` verdicts: source unchanged / source
  moved since pinning / source not locally fingerprintable.
- **Meta layer vs object layer** — skills that improve how agents work (audit, memory,
  loops, orchestration) vs skills that directly produce product code and releases.

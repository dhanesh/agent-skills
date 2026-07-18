---
type: Explainer
title: agent-skills repo
description: Reference explainer for agent-skills repo
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

This repository is a personal library of ten **Agent Skills** — self-contained
capability packs an AI coding agent loads as instructions — plus a **quality
factory** that keeps them trustworthy. Each skill is one top-level directory
whose `SKILL.md` is a reusable prompt; `make gate` runs five deterministic
checks (structure, secret scanning, prompt-quality linting, install replay,
stdlib unit tests) over every skill, and CI runs the same command on every PR.
The collection's center of gravity is *agent operations*: making agents
verifiable, supervisable, and able to remember — rather than domain-specific
task automation.

## The map

1. **The contract** — what makes a directory a "skill" (`SKILL.md` +
   `README.md` + optional `references/`, `assets/`, `scripts/`).
2. **The factory** — `Makefile` + `scripts/gates/*.sh`: the verify→repair loop
   that keeps every skill green; mirrored in CI.
3. **The prompting doctrine** — `docs/prompting-playbook.md`: four-discipline
   prompt-quality conventions, mechanically enforced by `prompting-playbook.sh`.
4. **The skills themselves** — ten packs in five clusters: audit, memory,
   knowledge/teaching, orchestration, artifact generation.
5. **The knowledge arc** — feynman-walkthrough writes OKF bundles;
   okf-site-kit renders them; this very file is an instance.

## Segments

### 1. The contract: what a skill is

A skill is a directory containing `SKILL.md` — a prompt with YAML frontmatter
(`name`, kebab-case ≤64 chars; `description` ≤1024 chars) and a structured
body — plus a human-facing `README.md`. Detail is offloaded to `references/`
(progressive disclosure), executable payloads live in `assets/`, installers in
`scripts/`. Every path mentioned in `SKILL.md` must exist (the gate fails on
dangling references). `PARAMETERS.md` is reserved: it exists only to declare a
bijection with template placeholders under `assets/templates/`. The Makefile
discovers skills by pattern — any top-level `*/SKILL.md` is in the gate
(`Makefile:6`).

### 2. The factory: make gate

`make gate` (`Makefile:14-31`) loops over every skill and runs, in order:
`validate-skill.sh` (structure/frontmatter/dangling refs),
`scan-leaks.sh` (secrets/denylist), `prompting-playbook.sh` (prompt-quality
lint), `dry-run-replay.sh` (only for skills with a `PARAMETERS.md` — replays
template installation into a scratch dir), and every `assets/test_*.py`
(stdlib-only, offline unit suites — 232 tests across 11 suites as of the
pinned commit). `make clean` runs first to purge `__pycache__` so the leak
scanner sees only sources. CI (`.github/workflows/skill-gates.yml`) runs the
same target, so a green PR check certifies all five layers. A notable detail:
`validate-skill.sh` probes for a UTF-8 locale so `wc -m` counts characters
rather than bytes — a real-world CI portability fix.

### 3. The prompting doctrine

`docs/prompting-playbook.md` distills an Anthropic talk ("The Prompting
Playbook") into six checks: PP-1 structured sections, PP-2 single-job
decomposition, PP-3 named output protocol, PP-4 a verify step, PP-5
overcorrection balance (absolutist "never/always" rules must be balanced by
heuristics and escape hatches, because rigid negative rules make models refuse),
PP-6 lean context (offload detail to `references/`). PP-1..4 are hard
failures; PP-5/PP-6 are advisory by default and promoted with
`make playbook PLAYBOOK_FLAGS=--strict`. As of the pinned commit, strict mode
fails three skills on PP-5: `crafting-self-prompting-loops` (14 absolutist :
1 heuristic), `world-model-ledger` (13:3), `mockstar-mock` (13:8).

### 4. The ten skills, in five clusters

- **Audit (read-only judgment):** `agent-ready-rails` grades a repo's
  readiness for coding agents across six rails; `base-in-reality` extracts a
  repo's falsifiable claims and verifies them against authoritative sources,
  reporting `UNCONFIRMED` rather than fabricating citations.
- **Agent memory (persistence infrastructure):** `context-hygiene-kit`
  installs a bounded, scored context cache with lifecycle hooks;
  `world-model-ledger` installs a SQLite world model with dual confidence axes
  (observed vs normative) where only oracle evidence raises normative trust.
- **Knowledge & teaching:** `feynman-walkthrough` walks a learner through any
  subject and persists the understanding as an OKF bundle with pinned source
  fingerprints; `okf-site-kit` turns any OKF bundle into a browsable Starlight
  site; `starlight-handbook-kit` scaffolds a contract-enforced decision
  handbook (nine-section skeleton, eight CI gates).
- **Orchestration & supervision:** `crafting-self-prompting-loops` designs
  sound agent loops with mandatory safety properties;
  `tmux-agent-herdr-lite` is a tmux cockpit for supervising multiple agents
  (status detection, dashboards, jump-to-blocked).
- **Artifact generation (the one "does domain work" skill):** `mockstar-mock`
  turns service specs/docs into runnable mock servers with provenance and
  coverage reporting.

### 5. The knowledge arc (and where this file comes from)

The repo's newest through-line (visible in git history: the
`desirable-difficulty` → `feynman-walkthrough` redesign, immediately followed
by `okf-site-kit`) is durable, spec-conformant knowledge: walkthroughs produce
Open Knowledge Format v0.1 bundles whose frontmatter pins a git SHA per
source, so a later session can run `okf.py status` and learn whether the
explainer is FRESH or STALE, then refresh diff-aware instead of re-touring.
This explainer is itself such a bundle (`docs/knowledge/`), pinned to the
commit named in its frontmatter.

## Positioning: the spectrum this repo serves today

On a spectrum from *domain task automation* (write my docs, call my API) to
*agent operations* (make agents reliable, supervisable, knowledgeable), this
repo sits firmly at the agent-operations end — before the work
(audit: is the repo/claim-base agent-ready?), around the work (supervision,
loop design), and after the work (memory, knowledge persistence, publishing).
Only `mockstar-mock` produces a conventional development artifact. The gap in
the middle — skills that help *do* feature work — is deliberate territory the
collection has mostly left to the host agent.

## Glossary

- **Agent Skill** — a directory with a `SKILL.md` prompt an agent loads to
  gain a capability; installed via `npx skills add`.
- **Gate** — a deterministic shell check under `scripts/gates/`, composed by
  `make gate`, mirrored in CI.
- **Prompting Playbook (PP-1..6)** — six lintable conventions for prompt
  quality; PP-5/PP-6 advisory unless `--strict`.
- **Progressive disclosure** — keep `SKILL.md` lean; push detail into
  `references/` files loaded only when needed.
- **OKF (Open Knowledge Format)** — Google's open spec for agent-readable
  knowledge bundles: markdown concepts with YAML frontmatter; this repo
  extends it with pinned source fingerprints.
- **FRESH / STALE / UNKNOWN** — `okf.py status` verdicts comparing pinned
  fingerprints against current sources.
- **PP-5 overcorrection** — too many absolutist rules vs heuristics; the
  failure mode is a model that refuses instead of reasoning.

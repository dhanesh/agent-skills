---
type: Explainer
title: agent-skills repo
description: Reference explainer for the agent-skills repository — what it is, how its quality gates work, and where each of the ten skills fits
tags: [walkthrough, reference, meta]
timestamp: 2026-07-18T00:00:00Z
created: 2026-07-18
sources:
- type: git
  locator: /home/user/agent-skills
  fingerprint: 4a85045d2206d4b67a7c971667d9925e8a28b615
  pinned: 2026-07-18
resource: /home/user/agent-skills
---

# agent-skills repo

> Reference explainer. Standalone: readable without the session that produced
> it. Check freshness against the pinned sources with `okf.py status` first.
> Note: because this bundle lives inside the repo it fingerprints, `status`
> reports STALE after any commit — including the commit that added this file.
> Compare the pinned sha against `git log` before treating STALE as real drift.

## In one paragraph

This repository is a personal toolbox of ten **Agent Skills** — self-contained
instruction packs an AI coding agent (Claude Code or compatible) loads to do one
specific job well — wrapped in a shared quality-control system that treats each
skill like tested software. A skill is just a directory: a `SKILL.md` that acts
as a reusable prompt, optional `references/` for deeper detail the agent loads
only when needed, and `assets/` holding stdlib-only Python tools with their own
unit tests. The distinctive move of the repo is `make gate`: every skill passes
structure validation, secret scanning, a prompt-quality lint derived from
Anthropic's "Prompting Playbook" talk, and its full unit-test suite — on every
PR, in CI. The skills themselves cluster around one theme: making agents
*trustworthy* — auditing repos and claims, giving agents durable memory and
knowledge, designing safe autonomous loops, and publishing what was learned.

## The map

1. **Anatomy of a skill** — the unit everything else operates on; a SKILL.md is
   a prompt, not documentation.
2. **The quality system** — `make gate`, four gate scripts, and the Prompting
   Playbook lint; why this repo feels like a software project rather than a
   prompt collection.
3. **The ten skills, in four families** — audit, memory/knowledge, loop design
   and supervision, generators/publishing; the map of what lives where.
4. **One traced path: `make gate` on a single skill** — the concrete mechanics,
   file by file.
5. **The self-referential culture** — the repo uses its own skills on itself,
   and `docs/` is the paper trail.

## Segments

### 1. Anatomy of a skill

A skill is one top-level directory with a fixed shape:

```
<skill>/
  SKILL.md         # agent-facing prompt: YAML frontmatter (name, description) + body
  README.md        # human-facing overview (required)
  references/      # progressive-disclosure detail the SKILL.md links to
  assets/          # tools, templates, and test_*.py suites the skill ships
  scripts/         # installer/tooling (e.g. install.sh)
```

The key mental model: **`SKILL.md` is a reusable prompt** — a system prompt the
agent loads when the skill triggers. The frontmatter `description` decides
*when* it triggers; the body is a structured control surface (labeled sections,
numbered steps, a named deliverable, a verify step). Detail that would bloat the
agent's working memory is offloaded to `references/` and loaded only when
relevant — "progressive disclosure." House style for shipped code: Python
stdlib only, no pip, no network at install time, deterministic offline tests.

Install path: `npx skills add dhanesh/agent-skills --skill <name>` via the open
`skills` CLI.

### 2. The quality system

`make gate` (Makefile:14–31) is the repo's verify→repair loop, and CI
(`.github/workflows/skill-gates.yml`) runs exactly it on every PR and push to
main. Per skill, fail-fast:

| Gate | Script | Enforces |
|---|---|---|
| Structure | `scripts/gates/validate-skill.sh` | SKILL.md+README exist; kebab-case `name` ≤64; `description` ≤1024; no dangling `references/`/`assets/`/`scripts/` paths; template↔PARAMETERS.md bijection |
| Leaks | `scripts/gates/scan-leaks.sh` | no secrets/keys/denylisted content |
| Prompt quality | `scripts/gates/prompting-playbook.sh` | PP-1…PP-6, below |
| Install replay | `scripts/gates/dry-run-replay.sh` | only for skills with a `PARAMETERS.md` |
| Unit tests | `*/assets/test_*.py` | each skill's stdlib suite |

The prompt-quality gate is the most unusual piece. It encodes Anthropic's
"Prompting Playbook" talk (prompt → context → harness → loop engineering) as
six deterministic checks on the SKILL.md body: PP-1 ≥3 `## ` sections
(debuggable structure), PP-2 ≥3 ordered steps (decomposed work), PP-3 a named
output protocol, PP-4 a verification step, and two advisories — PP-5 flags
absolutist negatives with no escape hatch (the overcorrection lesson) and PP-6
flags long bodies with no `references/` offloading. `docs/prompting-playbook.md`
is honest that these are proxies for substance and supplies the semantic review
checklist the gate can't automate.

As of the pinned commit, `make gate` is fully green: all ten skills pass all
gates, with 209 unit tests across seven suites (97 world-model-ledger, 32
okf-site-kit, 30 + 20 feynman-walkthrough, 17 context-hygiene-kit, 7
mockstar-mock, 6 tmux-agent-herdr-lite).

### 3. The ten skills, in four families

**Audit family — "is this trustworthy?" (read-only)**

- `agent-ready-rails` (2026-06-30): scores how ready a repo's *engineering
  system* is for coding agents — six build rails (verifiers, green CI, house
  style, navigable context, scoped tools, human checkpoints) plus an optional
  operate tier — and emits a severity-ranked scorecard with a fix list.
  Grounded in Spotify's Honk case. Prose-only; no shipped code.
- `base-in-reality` (2026-06-19): extracts falsifiable claims from a codebase
  and verifies them against authoritative sources actually fetched in-session
  (arxiv, PubMed, NIST, RFCs…), with an adversarial-refutation gate; ungrounded
  claims are reported `UNCONFIRMED`, never dressed up. Ships a keyless stdlib
  fetch helper + findings schema (21 tests).

**Memory & knowledge family — "what does the agent know, and does it stay true?"**

- `context-hygiene-kit` (2026-06-17): installs a bounded, scored context ledger
  plus Stop/PreCompact/SessionStart hooks so long sessions don't bloat or rot
  across compactions. 17-test install gate; explicit two-channel
  trusted/untrusted boundary.
- `world-model-ledger` (2026-07-01): installs a SQLite world model — entities,
  interactions, constraints — with the repo's most distinctive epistemic rule:
  *observed-in-code never raises normative confidence; only oracle evidence
  (tests/CI/docs/human) does*. Four lifecycle hooks, contradiction detection,
  97-test gate, and a controlled with/without outcome eval.
- `feynman-walkthrough` (2026-07-08 as `desirable-difficulty`, redesigned
  2026-07-11): walks a learner through any subject with map→segments→checks-
  after structure, then persists the understanding as an Open Knowledge Format
  (OKF v0.1) bundle whose frontmatter pins source fingerprints, so later
  sessions detect drift (FRESH/STALE/UNKNOWN) and refresh diff-aware.
  50 tests across `okf.py` and `spaced_schedule.py`. (This explainer is itself
  such a bundle.)

**Loop design & supervision family — "how do agents run safely?"**

- `crafting-self-prompting-loops` (2026-06-10): turns "make the model keep
  improving X" into a sound loop via a 10-slot spec (LSC-1…10), five scaffold
  templates, and mandatory safety properties (hard-stop backstop, two-channel
  boundary). Richest research grounding (Reflexion, CaMeL, lethal trifecta);
  its LSC vocabulary is borrowed by two sibling skills.
- `tmux-agent-herdr-lite` (2026-06-04, the repo's first skill): turns tmux into
  a multi-agent cockpit — status detection (blocked/working/done) across ~18
  named agent CLIs, jump-to-blocked navigation, agent-to-agent coordination,
  worktree isolation, resume. 61 table-driven classifier test cases.

**Generator & publishing family — "produce a working artifact"**

- `mockstar-mock` (2026-06-21): normalizes any mix of API specs/docs (OpenAPI,
  Postman, HAR, curl, GraphQL, prose PDFs) into one Endpoint Inventory, then
  generates a runnable mockstar mock server, boot-and-smoke verified, with a
  provenance report distinguishing grounded from inferred endpoints. The one
  skill aimed at day-to-day dev work rather than agent meta-engineering.
- `starlight-handbook-kit` (2026-06-12): scaffolds a decision-oriented Astro +
  Starlight handbook where nine dependency-free CI gates fail the build the
  moment a page drifts from the nine-section contract — agent-authored docs
  made safe by mechanical enforcement.
- `okf-site-kit` (2026-07-11): turns any OKF bundle (including the ones
  feynman-walkthrough writes) into a browsable Starlight site with metadata
  panels, changelog, and search; "tolerance over rejection" across every
  published bundle dialect. 32 tests.

### 4. One traced path: `make gate` on a single skill

What actually happens when CI checks a PR touching `feynman-walkthrough/`:

1. The workflow (`.github/workflows/skill-gates.yml`) checks out the repo and
   runs `make gate` — no other setup; the gates are POSIX sh + python3.
2. `Makefile:6` discovers skills by globbing `*/SKILL.md` — a directory *is* a
   skill iff it has a SKILL.md; there is no registry to keep in sync.
3. `Makefile:14` runs `clean` (purges `__pycache__` so stale artifacts can't
   trip the leak scan), then loops every skill fail-fast:
4. `validate-skill.sh` parses the frontmatter (kebab-case name ≤64 at :147,
   description ≤1024 at :218), then greps the body for every
   `references/…`/`assets/…`/`scripts/…` mention and stats each path (:255) —
   a promised-but-missing file fails the gate. This is why no SKILL.md in the
   repo dangles.
5. `scan-leaks.sh` sweeps for secrets and denylisted content.
6. `prompting-playbook.sh` runs PP-1…PP-4 as hard checks, PP-5/PP-6 as
   advisories (hard under `--strict`).
7. Because feynman-walkthrough has no `PARAMETERS.md`, the dry-run replay is
   skipped (it runs for `crafting-self-prompting-loops` and
   `starlight-handbook-kit`, which ship templates).
8. `Makefile:25–29` cd's into `assets/` and executes each `test_*.py` with
   bare python3 — `test_okf.py` (30 tests) and `test_spaced_schedule.py` (20)
   must print unittest's `OK`.
9. Any failure anywhere → non-zero exit → red PR check. Green check therefore
   means: structure sound, no leaks, prompt conventions held, all shipped code
   passes its tests.

### 5. The self-referential culture

The repo eats its own cooking, and `docs/` is the paper trail:

- `docs/base-in-reality/` holds four dated audit reports where the
  base-in-reality skill was run against sibling skills (mockstar-mock,
  world-model-ledger) and the repo itself — findings tracked to resolution.
- `docs/superpowers/{specs,plans}/` holds the design specs and implementation
  plans that preceded the larger skills — design-first development, by agents.
- `docs/world-model-ledger/2026-07-01-outcome-eval.md` is a controlled
  with/without eval of a skill's actual effect.
- `agent-ready-rails` was pointed at this repo and scored it 11/12; the one gap
  it found (unit tests not wired into CI) was then closed — the audit skill
  drove a change in the repo that ships it.
- This very file continues the pattern: feynman-walkthrough, run on its own
  home repo, persisting its explainer through its own OKF tooling.

## Glossary

- **Agent Skill** — a directory-packaged instruction set (SKILL.md + support
  files) an AI agent loads to perform a specific job; installable via the
  `skills` CLI.
- **SKILL.md** — the skill's agent-facing prompt; frontmatter controls
  triggering, body is the workflow.
- **Progressive disclosure** — keeping SKILL.md lean by pushing detail into
  `references/` files loaded only when needed.
- **`make gate`** — the repo-wide verify loop: structure, leaks, prompt
  quality, install replay, unit tests; what CI runs.
- **Prompting Playbook (PP-1…PP-6)** — six lint checks encoding Anthropic's
  prompt/context/harness/loop-engineering conventions for SKILL.md bodies.
- **OKF (Open Knowledge Format)** — Google's open spec for agent-readable
  knowledge bundles: markdown concepts with YAML frontmatter; this repo pins
  source fingerprints in them to detect drift.
- **FRESH / STALE / UNKNOWN** — okf.py's verdicts comparing a bundle's pinned
  source fingerprints to current reality.
- **LSC-1…10** — crafting-self-prompting-loops' ten-slot loop-soundness spec,
  reused by context-hygiene-kit and agent-ready-rails.
- **Observed vs normative confidence** — world-model-ledger's two axes:
  "seen in code" never implies "verified correct"; only oracle evidence
  (tests, CI, docs, humans) raises the normative axis.
- **Endpoint Inventory** — mockstar-mock's normalized intermediate
  representation unifying all input spec formats before generation.

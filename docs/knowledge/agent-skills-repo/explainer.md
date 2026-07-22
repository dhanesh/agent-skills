---
type: Explainer
title: agent-skills repo
description: Reference explainer for the agent-skills repo — what it is, how the quality gates work, the skill portfolio and its spectrum, and the improvement roadmap
tags: [walkthrough, reference]
timestamp: 2026-07-22T00:00:00Z
created: 2026-07-18
sources:
- type: git
  locator: /home/user/agent-skills
  fingerprint: 13e53fa035a51814871d4c591579683a40a0bfb0
  pinned: 2026-07-22
resource: /home/user/agent-skills
---

# agent-skills repo

> Reference explainer. Standalone: readable without the session that produced
> it. Check freshness against the pinned sources with `okf.py status` first.

## In one paragraph

This repo is a personal toolbox of sixteen **Agent Skills** — self-contained instruction
packs that teach an AI coding agent how to do one job well — plus a small factory that
keeps every pack honest. Think of each skill as a laminated recipe card an experienced
chef leaves for whoever staffs the kitchen next: the card (`SKILL.md`) is the recipe,
the side pockets (`references/`, `assets/`, `scripts/`, `eval/`) hold the detailed
technique sheets and tools, and before any card goes in the box it must pass a battery
of mechanical inspections (`make gate`) that check the card is structured, leak-free,
well-prompted, metadata-complete, that its tools actually work (unit tests), and that
its end-to-end promise holds (a deterministic outcome eval per `docs/eval-standard.md`). What makes the collection distinctive
is its subject matter: almost none of the skills do ordinary app work. They are skills
*about agents themselves* — auditing whether a repo is safe for agents, giving agents
durable memory and world models, designing sound agent loops, supervising fleets of
agents, and turning what an agent learns into persistent, publishable knowledge.

## The map

1. **Anatomy of a skill** — the unit of the repo: a directory with a `SKILL.md`
   prompt plus progressive-disclosure side files. Everything else exists to serve
   or police this shape.
2. **The quality machine (`make gate`)** — seven inspections run on every skill,
   locally and in CI; the repo's generate → evaluate → repair loop.
3. **The house philosophy** — the Prompting Playbook: a SKILL.md *is* a reusable
   prompt, so prompt-engineering conventions are enforced as lint (PP-1…PP-6),
   with a documented "semantic ceiling" the mechanical gate can't reach.
4. **The portfolio, in five families** — skill factory, trust & audit, agent
   memory, orchestration & supervision, knowledge & docs (plus one concrete dev
   tool, mockstar-mock).
5. **Spectrum and the executed roadmap** — where the collection sits (almost
   entirely meta-level agent infrastructure), and how the 2026-07-22
   self-improvement pass shipped the six proposed skills and seven improvements.

## Segments

### 1. Anatomy of a skill

A skill is one top-level directory containing a `SKILL.md`: YAML frontmatter
(`name` kebab-case ≤64 chars, `description` ≤1024 chars — the description is the
*trigger surface* an agent matches against) plus a body that is a structured,
reusable prompt. Detail is offloaded to `references/` (loaded only when needed —
progressive disclosure), shipped tooling lives in `assets/` (stdlib-only Python,
no pip, no network, each with an offline `test_*.py` suite), and installers live in
`scripts/`. `PARAMETERS.md` is reserved: it exists only to declare a bijection with
`assets/templates/` placeholders (crafting-self-prompting-loops and
starlight-handbook-kit use it). Install path: `npx skills add dhanesh/agent-skills
--skill <name>`.

Concrete example: `feynman-walkthrough/` has the prompt (`SKILL.md`), five
reference playbooks (`references/*.md`), and two tools with tests
(`assets/okf.py`, `assets/spaced_schedule.py`) — this very explainer was produced
by that skill and persisted with its `okf.py`.

### 2. The quality machine

`make gate` (Makefile:14-31) discovers every `*/SKILL.md` and runs, fail-fast per
skill:

1. `scripts/gates/validate-skill.sh` — structure: SKILL.md + README.md exist,
   frontmatter limits, **no dangling** `references/`/`assets/`/`scripts/` paths
   mentioned in the body, PARAMETERS↔templates bijection.
2. `scripts/gates/scan-leaks.sh` — secrets/credentials/denylist.
3. `scripts/gates/prompting-playbook.sh` — prompt-quality lint (segment 3).
4. `scripts/gates/frontmatter-standard.sh` — standard metadata: `license`,
   `compatibility`, `metadata` (`author`/`version`/`tags`) in every frontmatter.
5. `scripts/gates/dry-run-replay.sh` — only for parameterized skills: substitutes
   the documented example values into every template and fails on leftover
   `{{TOKENS}}` or orphan parameters.
6. Every `*/assets/test_*.py` — the skill's own unit suite, offline and
   deterministic.
7. `scripts/gates/run-eval.sh` — the skill's **outcome eval** (`eval/run_eval.py`
   per `docs/eval-standard.md`): deterministic harness → skill tooling →
   model-free grader, negative fixtures mandatory; a missing eval fails the gate.

CI (`.github/workflows/skill-gates.yml`) runs the same `make gate` on every PR and
push to main, so green CI is ground truth. Four gate scripts originated in the
`repo2skill` authoring skill (now shipped in this repo); `prompting-playbook.sh`,
`frontmatter-standard.sh`, and `run-eval.sh` are repo-local.

### 3. The house philosophy

`docs/prompting-playbook.md` distills Anthropic's "The Prompting Playbook" talk
into six checks, on the thesis that a SKILL.md is a system prompt and therefore
prompt/context/harness/loop engineering conventions apply to it as lint:

- **Hard (fail the gate):** PP-1 ≥3 `##` sections (debuggable control surface);
  PP-2 ≥3 ordered steps (decomposed single-job workflow); PP-3 a named output
  protocol; PP-4 a verification step baked in.
- **Advisory (INFO unless `--strict`):** PP-5 flags unbalanced absolutist rules
  (the "Meridian" overcorrection lesson — blanket `never`s make models defensively
  refuse); PP-6 flags long bodies with no `references/` offloading.

The doc is honest that these are *presence proxies*, and ships an agent-review
checklist for the substance the token-matcher can't judge (are sections orthogonal?
is the verifier distinct from the generator? is each `never` a justified
invariant?). House style everywhere: stdlib-only, offline-deterministic tests,
progressive disclosure, skills that cite the *boundaries* of sibling skills in
their descriptions ("not X — use base-in-reality for that").

### 4. The portfolio, in five families

**Skill factory** — how skills themselves get made and kept honest?
- `repo2skill`: scaffolds a gate-passing skill skeleton and walks the semantic
  review checklist the mechanical gates can't judge; the origin of the vendored
  gate scripts, now shipped.

**Trust & audit** — is the system/story sound?
- `agent-ready-rails`: scores a repo's readiness for coding agents across six
  rails (verifiers, green CI, house style, navigable context, scoped tools, human
  checkpoints) + optional four "operate" rails; audits the engineering *system*,
  not the code.
- `base-in-reality`: extracts falsifiable claims from a codebase and verifies them
  against fetched authoritative sources (arxiv→OWASP), adversarially refuting
  before reporting; ungrounded ⇒ `UNCONFIRMED`, never fabricated citations; ships
  a report-contract linter.
- `security-posture-audit`: offline, read-only hygiene audit — 13 posture defect
  classes (unpinned deps, committed credential files, debug flags, risky CI
  patterns) with severity-graded findings and honest not-covered boundaries.
- `verifier-installer`: agent-ready-rails' action-taking sibling — detects the
  stack, installs the missing format/build/test rail + CI workflow, proves the
  loop red→green.

**Agent memory & persistence** — what survives the session?
- `context-hygiene-kit`: one-time install of a bounded, scored, tiered context
  cache with Stop/PreCompact/SessionStart hooks — anti-bloat, anti-rot.
- `world-model-ledger`: one-time install of a SQLite world model — entities,
  interactions, constraints with dual confidence axes (observed vs normative);
  only oracle evidence (tests/CI/docs/human) raises normative confidence; captures
  from the whole tool stream via four hooks; 57-test install gate.

**Orchestration & supervision** — how do agents run?
- `crafting-self-prompting-loops`: design/audit agent loops with mandatory safety
  properties (hard-stop backstop, trusted/untrusted two-channel boundary);
  produces a filled loop spec + runnable scaffold.
- `spec-first-planning`: fuzzy request → lint-clean spec of testable requirements
  → task plan where every task names its verify step, with a total
  requirement↔task coverage map; hands off to an implementer or a loop.
- `tmux-agent-herdr-lite`: a tmux cockpit for herding multiple coding agents —
  status detection, jump-to-blocked, agent-to-agent coordination, worktree
  isolation.

**Knowledge & docs** — how is understanding captured and shared?
- `feynman-walkthrough`: understanding-first walkthroughs (map → segments →
  post-hoc checks) persisted as source-pinned OKF bundles; opt-in spaced recall.
- `okf-site-kit`: OKF bundle → browsable Astro/Starlight website.
- `knowledge-gardener`: sweeps OKF bundles, reports FRESH/STALE/UNKNOWN drift
  against pinned fingerprints, drives diff-aware refreshes and site regeneration
  — the maintenance leg of the knowledge trilogy.
- `bug-autopsy`: feynman-walkthrough's sibling for failures — evidence-cited
  timeline, blameless ≥3-deep 5-Whys, lint-checked post-mortem persisted into the
  same OKF bundle.
- `starlight-handbook-kit`: decision-oriented handbook scaffold with a fixed
  nine-section topic skeleton and CI gates against drift.

**Outlier (object-level tooling)** — `mockstar-mock`: specs/docs of any flavor →
runnable mockstar mock server, boot-and-smoke verified, with provenance/coverage
report. The only skill whose deliverable is ordinary dev infrastructure rather
than agent infrastructure.

### 5. Spectrum — and the executed roadmap

**Where the repo sits today.** On the object-level ↔ meta-level spectrum the
collection is overwhelmingly meta: the skills improve the *agent's operating
system* — how skills get made (factory), its trustworthiness (audit family), its
memory (persistence family), its control flow (orchestration family), and its
knowledge exhaust (docs family) — with `mockstar-mock` the object-level outlier.
The collection is Claude-Code-shaped (hooks, settings.json, skills CLI) and
solo-developer-shaped (stdlib-only, offline, no team/service dependencies) — a
coherent identity: the toolkit of someone making autonomous agents *safe to
trust*, with the audits, memories, and knowledge trails to prove it.

**The 2026-07-22 self-improvement pass.** The walkthrough of 2026-07-18 ended in
a roadmap (six proposed skills, seven proposed improvements); a subsequent
autonomous session executed all of it:

- *Additions shipped:* `repo2skill` (factory loop closed), `verifier-installer`
  (rails findings → action), `knowledge-gardener` (knowledge lifecycle),
  `bug-autopsy` (understand-the-failure), `security-posture-audit` (trust family
  completed), `spec-first-planning` (plan band filled). The lifecycle bands that
  were thin — plan, author-tests/verifier-install, security — are now covered;
  implement and deploy/operate remain intentionally out of scope.
- *Improvements landed:* persistence-stack `references/interop.md` in both
  context-hygiene-kit and world-model-ledger; okf.py's self-pinning blind spot
  fixed (bundle-only diffs report FRESH) plus a `diff` subcommand;
  agent-ready-rails gained a deterministic evidence collector (16-test suite);
  frontmatter metadata standardized across all sixteen skills and gated
  (`frontmatter-standard.sh`); descriptions trigger-front-loaded; the semantic
  review checklist operationalized via `repo2skill/references/semantic-review.md`
  + a PR-template checklist item; the `scripts/gates/README.md` vendoring drift
  fixed.
- *Eval standard installed:* world-model-ledger's with/without eval pattern was
  generalized into `docs/eval-standard.md`; every skill now ships a
  deterministic `eval/run_eval.py` (negative fixtures mandatory) enforced as a
  hard gate in `make gate` and CI.

## Glossary

- **Agent Skill** — a directory with a `SKILL.md` prompt an agent loads to gain a
  capability; installed via the `skills` CLI.
- **Gate** — one of seven mechanical checks `make gate` runs per skill; CI ground
  truth for the repo.
- **Outcome eval** — a skill's `eval/run_eval.py`: deterministic harness → skill
  tooling → model-free grader with negative fixtures, per `docs/eval-standard.md`.
- **Progressive disclosure** — keep SKILL.md lean; push detail into `references/`
  files loaded only when relevant.
- **PP-1…PP-6** — the Prompting Playbook lint checks (structure, decomposition,
  output protocol, verification; advisory: overcorrection, lean context).
- **OKF (Open Knowledge Format)** — open spec for agent-readable knowledge
  bundles: markdown concepts + YAML frontmatter, reserved `index.md`/`log.md`.
- **Source pin / fingerprint** — the git SHA (or sha256) an explainer was written
  against; lets a later session detect drift (`FRESH`/`STALE`/`UNKNOWN`).
- **Observed vs normative confidence** — world-model-ledger's two axes: what code
  appears to do vs what oracle evidence (tests/CI/docs/human) confirms it should do.
- **Rails** — agent-ready-rails' term for the engineering affordances (verifiers,
  CI truth, style, context, scoped tools, checkpoints) that let agents work safely.

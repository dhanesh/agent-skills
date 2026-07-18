---
type: Explainer
title: agent-skills repo
description: Reference explainer for the agent-skills repo — what it is, how the quality gates work, the skill portfolio and its spectrum, and the improvement roadmap
tags: [walkthrough, reference]
timestamp: 2026-07-18T00:00:00Z
created: 2026-07-18
sources:
- type: git
  locator: /home/user/agent-skills
  fingerprint: eec85b93ac0736c27359fd5ffbccf8f482e2d67b+dirty
  pinned: 2026-07-18
resource: /home/user/agent-skills
---

# agent-skills repo

> Reference explainer. Standalone: readable without the session that produced
> it. Check freshness against the pinned sources with `okf.py status` first.

## In one paragraph

This repo is a personal toolbox of ten **Agent Skills** — self-contained instruction
packs that teach an AI coding agent how to do one job well — plus a small factory that
keeps every pack honest. Think of each skill as a laminated recipe card an experienced
chef leaves for whoever staffs the kitchen next: the card (`SKILL.md`) is the recipe,
the side pockets (`references/`, `assets/`, `scripts/`) hold the detailed technique
sheets and tools, and before any card goes in the box it must pass a battery of
mechanical inspections (`make gate`) that check the card is structured, leak-free,
well-prompted, and that its tools actually work. What makes the collection distinctive
is its subject matter: almost none of the skills do ordinary app work. They are skills
*about agents themselves* — auditing whether a repo is safe for agents, giving agents
durable memory and world models, designing sound agent loops, supervising fleets of
agents, and turning what an agent learns into persistent, publishable knowledge.

## The map

1. **Anatomy of a skill** — the unit of the repo: a directory with a `SKILL.md`
   prompt plus progressive-disclosure side files. Everything else exists to serve
   or police this shape.
2. **The quality machine (`make gate`)** — five inspections run on every skill,
   locally and in CI; the repo's generate → evaluate → repair loop.
3. **The house philosophy** — the Prompting Playbook: a SKILL.md *is* a reusable
   prompt, so prompt-engineering conventions are enforced as lint (PP-1…PP-6),
   with a documented "semantic ceiling" the mechanical gate can't reach.
4. **The portfolio, in four families** — trust & audit, agent memory, orchestration
   & supervision, knowledge & docs (plus one concrete dev tool, mockstar-mock).
5. **Spectrum, gaps, and roadmap** — where the collection sits today (≈90%
   meta-level agent infrastructure), which spectrum bands are thin, what to add,
   and what to improve in what exists.

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
4. `scripts/gates/dry-run-replay.sh` — only for parameterized skills: substitutes
   the documented example values into every template and fails on leftover
   `{{TOKENS}}` or orphan parameters.
5. Every `*/assets/test_*.py` — the skill's own unit suite, offline and
   deterministic.

CI (`.github/workflows/skill-gates.yml`) runs the same `make gate` on every PR and
push to main, so green CI is ground truth. Four of the five gate scripts are
vendored from an external `repo2skill` authoring skill; `prompting-playbook.sh` is
repo-local.

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

### 4. The portfolio, in four families

**Trust & audit** — is the system/story sound?
- `agent-ready-rails`: scores a repo's readiness for coding agents across six
  rails (verifiers, green CI, house style, navigable context, scoped tools, human
  checkpoints) + optional four "operate" rails; audits the engineering *system*,
  not the code.
- `base-in-reality`: extracts falsifiable claims from a codebase and verifies them
  against fetched authoritative sources (arxiv→OWASP), adversarially refuting
  before reporting; ungrounded ⇒ `UNCONFIRMED`, never fabricated citations.

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
- `tmux-agent-herdr-lite`: a tmux cockpit for herding multiple coding agents —
  status detection, jump-to-blocked, agent-to-agent coordination, worktree
  isolation.

**Knowledge & docs** — how is understanding captured and shared?
- `feynman-walkthrough`: understanding-first walkthroughs (map → segments →
  post-hoc checks) persisted as source-pinned OKF bundles; opt-in spaced recall.
- `okf-site-kit`: OKF bundle → browsable Astro/Starlight website.
- `starlight-handbook-kit`: decision-oriented handbook scaffold with a fixed
  nine-section topic skeleton and CI gates against drift.

**Outlier (object-level tooling)** — `mockstar-mock`: specs/docs of any flavor →
runnable mockstar mock server, boot-and-smoke verified, with provenance/coverage
report. The only skill whose deliverable is ordinary dev infrastructure rather
than agent infrastructure.

### 5. Spectrum, gaps, and roadmap

**Where the repo sits today.** On the object-level ↔ meta-level spectrum, nine of
ten skills are meta: they improve the *agent's operating system* — its
trustworthiness (audit family), its memory (persistence family), its control flow
(orchestration family), and its knowledge exhaust (docs family). Mapped onto a
software-delivery lifecycle: very strong at **understand, audit/verify, remember,
document**; thin at **plan/spec, implement, author-tests, deploy/operate**. The
collection is also Claude-Code-shaped (hooks, settings.json, skills CLI) and
solo-developer-shaped (stdlib-only, offline, no team/service dependencies) — a
coherent identity, not an accident: it is the toolkit of someone making autonomous
agents *safe to trust*, with the audits, memories, and knowledge trails to prove it.

**Credible additions** (each extends an existing family rather than starting a new
identity):

1. **Skill-authoring skill** ("repo2skill" shipped here): the gates are vendored
   *from* an authoring skill that this repo doesn't ship. A skill that scaffolds a
   new gate-passing skill (frontmatter, references, test suite, README) closes the
   factory loop — the meta-repo should ship the skill that makes skills.
2. **Verifier-installer**: agent-ready-rails *diagnoses* missing
   format/build/test rails but only "optionally installs" loosely; a dedicated
   skill that stands up the runnable-verifier loop (pre-commit, CI workflow, test
   scaffolds per language) converts the audit's top finding into action.
3. **Knowledge-gardener**: a scheduled routine that walks every OKF bundle,
   runs `okf.py status`, refreshes STALE explainers diff-aware, and regenerates
   the okf-site-kit site — turning the knowledge trilogy from artifacts into a
   living lifecycle.
4. **Bug-autopsy / incident walkthrough**: feynman-walkthrough's sibling for
   failures — trace a defect end-to-end, capture the post-mortem into the same OKF
   bundle; completes understand-the-system with understand-the-failure.
5. **Security-posture audit**: scan-leaks covers secrets and base-in-reality
   explicitly excludes CVEs — a dependency/config/security-hygiene audit would
   complete the trust family.
6. **Spec-first planning skill**: PRD → task decomposition integrated with
   crafting-self-prompting-loops and the world model — fills the "plan" band of
   the lifecycle natively.

**Improvements to existing skills** (from touring the code, not the READMEs):

1. **Persistence-stack interop doc.** context-hygiene-kit and world-model-ledger
   both wire Stop/SessionStart (and other lifecycle) hooks into settings.json. A
   user installing both is on their own regarding ordering, conflicts, and
   combined digest budget. A shared `references/interop.md` (or a gate that checks
   hook coexistence) is the highest-leverage fix.
2. **okf.py self-pinning blind spot** (found by using it on this repo): when the
   bundle lives inside the repo it pins, committing the bundle moves HEAD, so the
   subject reports STALE-by-one-commit forever. Fix: exclude the bundle root from
   the drift check, or add an `okf.py diff` subcommand that shows *what* changed
   so a bundle-only diff can auto-report FRESH.
3. **agent-ready-rails ships no assets/tests** — the only judgment-only skill in
   the audit family. A deterministic evidence-collector script (does CI exist? do
   verifier commands run?) would make scores reproducible and enroll it in the
   unit-test gate its siblings pass.
4. **Frontmatter consistency**: only tmux-agent-herdr-lite declares `license`,
   `compatibility`, and `metadata` (author/version/tags). Standardize across all
   ten and let validate-skill.sh check for them.
5. **Trigger-surface tuning**: several descriptions run near the 1024-char cap
   and read as abstracts; front-loading the "use when" phrases (skill-creator
   style) would improve firing accuracy in agents that match on descriptions.
6. **Operationalize the semantic checklist**: the prompting-playbook's
   agent-review table (the substance the gate can't see) is documentation only —
   make it a PR-review step or a small review skill so it actually runs.
7. **Doc drift in `scripts/gates/README.md`**: it says the gates are "vendored
   from repo2skill" but links to *this* repo, which contains no repo2skill —
   either ship it (addition #1) or point the link at the real source.

## Glossary

- **Agent Skill** — a directory with a `SKILL.md` prompt an agent loads to gain a
  capability; installed via the `skills` CLI.
- **Gate** — one of five mechanical checks `make gate` runs per skill; CI ground
  truth for the repo.
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

# Agent Skills

Personal Agent Skills repository for installation through the open [`skills`](https://www.npmjs.com/package/skills) CLI.

## Install

Each skill installs independently. Use the `--skill` flag with the skill's directory name:

```bash
npx skills add dhanesh/agent-skills --skill <skill-name>
```

For example:

```bash
npx skills add dhanesh/agent-skills --skill agent-ready-rails
npx skills add dhanesh/agent-skills --skill ai-migration-operating-model
npx skills add dhanesh/agent-skills --skill base-in-reality
npx skills add dhanesh/agent-skills --skill bug-autopsy
npx skills add dhanesh/agent-skills --skill clean-code
npx skills add dhanesh/agent-skills --skill context-hygiene-kit
npx skills add dhanesh/agent-skills --skill crafting-self-prompting-loops
npx skills add dhanesh/agent-skills --skill feynman-walkthrough
npx skills add dhanesh/agent-skills --skill jev-agent-setup
npx skills add dhanesh/agent-skills --skill knowledge-gardener
npx skills add dhanesh/agent-skills --skill okf-site-kit
npx skills add dhanesh/agent-skills --skill repo2skill
npx skills add dhanesh/agent-skills --skill security-posture-audit
npx skills add dhanesh/agent-skills --skill spec-first-planning
npx skills add dhanesh/agent-skills --skill starlight-handbook-kit
npx skills add dhanesh/agent-skills --skill test-safety-net
npx skills add dhanesh/agent-skills --skill tmux-agent-herdr-lite
npx skills add dhanesh/agent-skills --skill mockstar-mock
npx skills add dhanesh/agent-skills --skill verifier-installer
npx skills add dhanesh/agent-skills --skill world-model-ledger
```

To install every skill in this repo, omit the `--skill` flag:

```bash
npx skills add dhanesh/agent-skills
```

## Software factory

The long-term aim of this collection is an autonomous software factory for an indie developer:
idea → spec → build → verify → review → release → operate → support → growth. **Today it is a
set of skills a human orchestrates, not yet autonomous.** You choose the next skill, confirm each
handoff, and drive the build step yourself. The evidence behind this section, and the gaps, are
in the dated [readiness assessment](docs/factory/2026-09-19-assessment.md).

### Which skills cover which stage

Status is the more conservative of the assessment's two judges (Claude and Jev).

| Stage | Skills | Status |
|---|---|---|
| Idea validation | none (ai-migration-operating-model `qualify` covers migrations only) | missing |
| Requirements / spec | `spec-first-planning` | covered |
| Design / plan | `spec-first-planning`, `clean-code` (architecture), `ai-migration-operating-model` (migrations) | partly |
| Build / execute | `crafting-self-prompting-loops` designs the loop but does not run it; `tmux-agent-herdr-lite` supervises agents; `mockstar-mock` mocks dependencies | partly |
| Test / verify | `verifier-installer`, `test-safety-net`. Nothing checks built work against the spec's acceptance criteria. | partly |
| Review | `clean-code`, `security-posture-audit`, `base-in-reality`. None reviews a diff against the spec. | partly |
| Release / deploy | none (`agent-ready-rails` only audits deploy safety) | missing |
| Operate / incident | `bug-autopsy` (post-hoc only), `agent-ready-rails` Tier 2 (audit) | partly |
| Support / feedback | none | missing |
| Growth / monetise | none | missing |
| Knowledge / docs | `feynman-walkthrough`, `okf-site-kit`, `knowledge-gardener`, `starlight-handbook-kit`, `bug-autopsy`; agent memory: `world-model-ledger`, `context-hygiene-kit` | covered |
| Governance | `security-posture-audit`, `base-in-reality`, `agent-ready-rails` (audit), `crafting-self-prompting-loops` (per-loop budget). No spend governor or gate policy. | partly |

### Recipes that work today

Each recipe is one install command. A human drives the steps between skills.

**Plan → loop.** Turn a fuzzy request into a spec and task plan, then design the loop that
executes it. This is the one pair with a machine handoff (a `task-plan/v1` envelope).

```bash
npx skills add dhanesh/agent-skills --skill spec-first-planning --skill crafting-self-prompting-loops
```

**Make a repo agent-ready.** Audit the repo's rails, install the format/build/test loop and CI,
add characterisation tests, then act on the seam list. Run them in that order.

```bash
npx skills add dhanesh/agent-skills --skill agent-ready-rails --skill verifier-installer --skill test-safety-net --skill clean-code
```

**Audit claims and security.** Check the codebase's algorithms and business rules against cited
sources, and its security hygiene, with design review alongside.

```bash
npx skills add dhanesh/agent-skills --skill base-in-reality --skill security-posture-audit --skill clean-code
```

**Knowledge loop.** Explain a codebase or subject into an OKF bundle, publish it as a site, keep
it fresh, and file post-mortems into the same bundle.

```bash
npx skills add dhanesh/agent-skills --skill feynman-walkthrough --skill okf-site-kit --skill knowledge-gardener --skill bug-autopsy
```

### What happens when you install a subset

- **Skills that adopt [skill-contract](docs/skill-contract/SPEC.md)** find each other at handoff
  time and hand off a validated envelope, after asking you first. Today that is two skills and one
  handoff: `spec-first-planning` → `crafting-self-prompting-loops`.
- **A missing consumer is not an error.** The producer reports `NO_CONSUMER`, gives you the
  envelope path, and finishes normally.
- **Every other link between skills is prose.** A skill says "use X next". If X is not installed,
  the step is skipped, and nothing tells you what you lose.
- **Reinstall copies installed before the contract merged.** They carry no contract block, so
  discovery cannot see them.

### Unattended mode: partial

`spec-first-planning` 2.0.0 can plan unattended, and — after you say yes — write a
skill-contract [autonomy grant](docs/skill-contract/SPEC.md): a spec-linked envelope that
covers chosen action classes, on branches matching a pattern, for at most 7 days. Four skills' confirmation gates honour it:
`spec-first-planning`, `crafting-self-prompting-loops`, `verifier-installer` and
`test-safety-net` each call `check-grant` before falling back to their own ask.

What a grant can and cannot do:

- it covers only reversible work: local edits and commits, pushing a branch, opening a PR;
- merge, deploy, spending money, sending an external message, and deleting always ask you —
  no grant can change that;
- there is no signing: no `require_signature`, no `.sig`, no signature levels;
- a grant lives at most 7 days, and it never covers your repo's default branch (`main`/`master`),
  no matter what its branch pattern says;
- a grant is yours alone: it is kept out of commits, and a committed grant covers nothing;
- a push or PR whose commits change CI configuration (`.github/workflows/` and the like) asks
  you, because CI runs with the repository's secrets.

Still on the roadmap: a **conductor** that runs a whole plan end to end inside that grant
(step 4) — today you still drive the handoff between skills yourself.

```bash
npx skills add dhanesh/agent-skills --skill spec-first-planning --skill crafting-self-prompting-loops --skill verifier-installer --skill test-safety-net
```

The design and the gap analysis are in
[the assessment's unattended-mode section](docs/factory/2026-09-19-assessment.md#6-unattended-mode-q3-gap-analysis).

## Skills

| Skill | Description |
|-------|-------------|
| [`agent-ready-rails`](agent-ready-rails/) | Read-only audit scoring how ready a repository is for coding agents — grades six rails (runnable verifiers / the format·build·test feedback loop, green-CI ground truth, a copyable house style, navigable context, scoped trusted tools, and human checkpoints with reversibility) from observed repo evidence and emits a severity-ranked readiness scorecard with a leverage-ordered fix list. Optionally installs the missing rails. Grounded in Spotify's Honk case; audits the engineering *system* agents run inside, not code correctness. |
| [`ai-migration-operating-model`](ai-migration-operating-model/) | Run a code migration (language port, framework/SDK upgrade, service rewrite) as an operating model — rulebook + queues + reviewers + objective verification — instead of a pile of agent edits. Builds the judge first (golden scenarios + a shipped parity differ), then a lint-clean Migration Control Pack (rulebook, dependency map, gap inventory, portability test plan, parity script, work queue, reviewer prompts, phase gates). Four standalone invocations mirror the model's parts: `economics`, `judge`, `pack`, `qualify` — and "don't migrate yet" is a first-class verdict. Complements `spec-first-planning` and `crafting-self-prompting-loops`. |
| [`base-in-reality`](base-in-reality/) | Read-only, research-grounded repo audit — extracts falsifiable claims across algorithm/architecture/business-logic layers, routes each to authoritative sources (arxiv, PubMed, Scholar, JSTOR, OpenAlex, Crossref, Semantic Scholar + NIST/RFC/OWASP/ISO/regulators), verifies against fetched evidence, and adversarially refutes before emitting a severity-graded cited report. No fabricated citations: ungrounded claims are reported as `UNCONFIRMED`. |
| [`clean-code`](clean-code/) | Applies Robert C. Martin's Clean Code, Clean Architecture and Clean Craftsmanship principles when writing, reviewing, refactoring or designing software — meaningful names, small single-purpose functions, SOLID, simple design, component cohesion and the Dependency Rule, testability, concurrency and cross-cutting concerns. Triggers on ordinary phrasing ("review this PR", "this class is doing too much") rather than requiring the term, and right-sizes: it pushes the simplest design that fits instead of imposing layers everywhere. Progressive disclosure — an always-on L1 with eight L2 references loaded per decision. |
| [`context-hygiene-kit`](context-hygiene-kit/) | Install a bounded, scored, tiered context cache into a Claude Code project — keeps working memory lean (anti-bloat) and rot-proof across compactions (anti-rot) via a stdlib Python ledger, a deterministic per-turn transcript harvester, and Stop/PreCompact/SessionStart hooks. Replaces lossy local-model session-summarisers with a lossless one; ships with a 27-test install gate. |
| [`crafting-self-prompting-loops`](crafting-self-prompting-loops/) | Design and build sound self-prompting loops — draft→critique→revise, autonomous task loops, multi-agent orchestration, and human-checkpointed loops. Produces a filled loop spec, a runnable scaffold, and bakes in the mandatory safety properties (hard-stop backstop, trusted/untrusted two-channel boundary, and a typed loop boundary validating carried state before it feeds the next iteration). |
| [`feynman-walkthrough`](feynman-walkthrough/) | Understanding-first walkthrough guide (formerly `desirable-difficulty`) — walks a learner through any subject, codebase, or whitepaper with Feynman-grade simplicity and research-backed structure (map → segments → concrete examples), checks understanding only *after* the walkthrough to steer re-coaching, and delivers a standalone reference explainer persisted as a Google Open Knowledge Format (OKF v0.1) bundle with source fingerprints pinned, so later sessions can detect drift and refresh diff-aware. Spaced-retrieval recall tooling included but strictly opt-in. |
| [`okf-site-kit`](okf-site-kit/) | Turn any Open Knowledge Format (OKF v0.1) bundle into a beautiful browsable static website — a stdlib generator emits a complete Astro + Starlight project with a hero landing page, per-concept pages carrying OKF metadata panels (type badge, tags, resource, producer keys), rewritten internal links, a changelog from `log.md`, and full-text search. Covers every bundle dialect published so far (Google's ga4/stackoverflow/crypto_bitcoin samples, spec-canonical, producer-extended) with graceful `WARN:`-reported degradation; deploy-ready for GitHub Pages. |
| [`starlight-handbook-kit`](starlight-handbook-kit/) | Scaffold or extend a decision-oriented Astro + Starlight documentation handbook — every topic follows a fixed nine-section skeleton, diagrams and widgets render without JavaScript, and nine CI gates fail the build the moment a page drifts from the contract, making agent-authored content safe. |
| [`tmux-agent-herdr-lite`](tmux-agent-herdr-lite/) | tmux cockpit with Zellij-like human ergonomics and Herdr-like agent supervision — menus, dashboard, pane navigation, blocked/working/done status detection, and jump-to-status navigation. |
| [`mockstar-mock`](mockstar-mock/) | Generate a runnable mockstar mock server from a service's specs/docs — OpenAPI, Postman, HAR, curl, GraphQL, and prose docs (md/pdf/docx/url) — normalized into one Endpoint Inventory, full-fidelity (scenarios/handlers/webhooks), Tier 2-enhanced, boot-and-smoke verified, with a provenance + coverage report. |
| [`repo2skill`](repo2skill/) | The skill-authoring skill: scaffolds a new gate-passing Agent Skill (standard frontmatter, PP-conformant SKILL.md skeleton, README, test stub, outcome-eval stub) and walks the semantic review checklist the mechanical gates can't judge. The source of this repo's vendored gate scripts, now shipped. |
| [`verifier-installer`](verifier-installer/) | Action-taking sibling of `agent-ready-rails`: detects a repo's stack(s) with a deterministic collector, then installs the missing runnable-verifier loop — format/build/test commands plus a CI workflow that runs them — and proves the loop red→green before handing back. |
| [`test-safety-net`](test-safety-net/) | Authors unit tests into a Python, node/TypeScript, Go or Rust codebase that has none, so an agent can change it safely — each stack end to end on the last five versions of its language, proven in CI. Ranks units by blast radius and churn, then writes characterization tests that pin current behaviour — each one proved able to FAIL before it is kept, so the suite is a real change-detector, not green noise — and proves every test under a per-stack runtime guard that fails it on any real I/O. Code that dials out (network/DB/filesystem in the constructor) is triaged, never netted with a mocked test; it becomes a ranked seam list for `clean-code`. Fills the loop `verifier-installer` installs. |
| [`knowledge-gardener`](knowledge-gardener/) | Completes the knowledge trilogy (`feynman-walkthrough` creates, `okf-site-kit` publishes, this maintains): sweeps Open Knowledge Format bundles, reports per-subject FRESH/STALE/UNKNOWN drift against pinned source fingerprints, drives diff-aware refreshes and re-pins, and regenerates any published site. |
| [`bug-autopsy`](bug-autopsy/) | `feynman-walkthrough`'s sibling for failures: reconstructs a defect end-to-end (evidence-cited timeline, blameless ≥3-deep 5-Whys root cause), writes a lint-checked post-mortem, and persists it into the same OKF knowledge bundle so the failure teaches the next engineer. |
| [`security-posture-audit`](security-posture-audit/) | Read-only, offline security *hygiene* audit — dependency pinning, committed env/key files, debug/permissive flags, insecure transports, risky CI patterns — severity-graded with file:line evidence and honest not-covered boundaries. Not a CVE scanner or SAST; completes the trust family alongside `scan-leaks` and `base-in-reality`. |
| [`spec-first-planning`](spec-first-planning/) | Fills the plan band: turns a fuzzy feature request into a lint-clean spec of numbered, testable requirements, then derives a task plan where every task names the check that proves it done — with a total requirement↔task coverage map before handoff to an implementer or a `crafting-self-prompting-loops` loop. |
| [`jev-agent-setup`](jev-agent-setup/) | Machine-wide Jev (TypeSafe System One) setup for every coding agent: installs the `jev` CLI (JSON in, typed Noul/Choice/Score out) and a managed BCP 14 instruction block into the global files of Claude Code, Codex, Gemini CLI and `~/.agents/AGENTS.md`, so agents offload rank/classify/yes-no decisions to Jev. Idempotent, marker-bounded, backs up before first touch, `--check` for drift, optional mirror mode that copies CLAUDE.md to the other agents. Also routes subagent model choice through Jev (Choice + difficulty Score). Complements `typesafe-ai`, which covers building Jev into applications. |
| [`world-model-ledger`](world-model-ledger/) | Install a persistent, SQLite-backed world model for a coding agent — entities (symbols/files/modules/real-world referents), interactions, and constraints, each with two confidence axes (observed vs normative), a validation status, and PROV-style evidence. Code-observed relationships are never treated as ground truth: only oracle evidence (tests/CI/docs/human) raises normative confidence. Four lifecycle hooks retrieve validated/unverified/contradicted items before edits, update records without inventing facts, and consolidate on Stop; detects contradictions, proposes located fixes, and improves normative correctness over time. Every triple is validated against a predicate ontology (RDFS-style domain/range) before it enters the ledger — hallucinated verbs and semantically impossible pairings are rejected, not stored. Ships a 116-test install gate. |

See each skill directory's `SKILL.md` for usage and prerequisites.

## Related projects

[**Manifold**](https://github.com/dhanesh/manifold) — a constraint-first development
framework that makes a feature's constraints explicit and machine-checkable *before* code
is written, and keeps them checkable afterwards. Two skills here sit next to it:

- [`spec-first-planning`](spec-first-planning/) does the same job inside one session — a
  falsifiable spec, then tasks that each name their own verification. Manifold persists
  that structure across sessions and enforces it with a CLI (`manifold validate`,
  `manifold verify --verify-evidence`) and CI.
- [`agent-ready-rails`](agent-ready-rails/) grades whether a repo gives agents a real
  feedback loop. A Manifold-managed repo is one way to supply the R1/R2 rails it scores.

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
npx skills add dhanesh/agent-skills --skill context-hygiene-kit
npx skills add dhanesh/agent-skills --skill crafting-self-prompting-loops
npx skills add dhanesh/agent-skills --skill feynman-walkthrough
npx skills add dhanesh/agent-skills --skill harvard-case-method
npx skills add dhanesh/agent-skills --skill knowledge-gardener
npx skills add dhanesh/agent-skills --skill okf-site-kit
npx skills add dhanesh/agent-skills --skill repo2skill
npx skills add dhanesh/agent-skills --skill security-posture-audit
npx skills add dhanesh/agent-skills --skill spec-first-planning
npx skills add dhanesh/agent-skills --skill starlight-handbook-kit
npx skills add dhanesh/agent-skills --skill tmux-agent-herdr-lite
npx skills add dhanesh/agent-skills --skill mockstar-mock
npx skills add dhanesh/agent-skills --skill verifier-installer
npx skills add dhanesh/agent-skills --skill world-model-ledger
```

To install every skill in this repo, omit the `--skill` flag:

```bash
npx skills add dhanesh/agent-skills
```

## Skills

| Skill | Description |
|-------|-------------|
| [`agent-ready-rails`](agent-ready-rails/) | Read-only audit scoring how ready a repository is for coding agents — grades six rails (runnable verifiers / the format·build·test feedback loop, green-CI ground truth, a copyable house style, navigable context, scoped trusted tools, and human checkpoints with reversibility) from observed repo evidence and emits a severity-ranked readiness scorecard with a leverage-ordered fix list. Optionally installs the missing rails. Grounded in Spotify's Honk case; audits the engineering *system* agents run inside, not code correctness. |
| [`ai-migration-operating-model`](ai-migration-operating-model/) | Run a code migration (language port, framework/SDK upgrade, service rewrite) as an operating model — rulebook + queues + reviewers + objective verification — instead of a pile of agent edits. Builds the judge first (golden scenarios + a shipped parity differ), then a lint-clean Migration Control Pack (rulebook, dependency map, gap inventory, portability test plan, parity script, work queue, reviewer prompts, phase gates). Four standalone invocations mirror the model's parts: `economics`, `judge`, `pack`, `qualify` — and "don't migrate yet" is a first-class verdict. Complements `spec-first-planning` and `crafting-self-prompting-loops`. |
| [`base-in-reality`](base-in-reality/) | Read-only, research-grounded repo audit — extracts falsifiable claims across algorithm/architecture/business-logic layers, routes each to authoritative sources (arxiv, PubMed, Scholar, JSTOR, OpenAlex, Crossref, Semantic Scholar + NIST/RFC/OWASP/ISO/regulators), verifies against fetched evidence, and adversarially refutes before emitting a severity-graded cited report. No fabricated citations: ungrounded claims are reported as `UNCONFIRMED`. |
| [`context-hygiene-kit`](context-hygiene-kit/) | Install a bounded, scored, tiered context cache into a Claude Code project — keeps working memory lean (anti-bloat) and rot-proof across compactions (anti-rot) via a stdlib Python ledger, a deterministic per-turn transcript harvester, and Stop/PreCompact/SessionStart hooks. Replaces lossy local-model session-summarisers with a lossless one; ships with a 16-test install gate. |
| [`crafting-self-prompting-loops`](crafting-self-prompting-loops/) | Design and build sound self-prompting loops — draft→critique→revise, autonomous task loops, multi-agent orchestration, and human-checkpointed loops. Produces a filled loop spec, a runnable scaffold, and bakes in the mandatory safety properties (hard-stop backstop, trusted/untrusted two-channel boundary, and a typed loop boundary validating carried state before it feeds the next iteration). |
| [`feynman-walkthrough`](feynman-walkthrough/) | Understanding-first walkthrough guide (formerly `desirable-difficulty`) — walks a learner through any subject, codebase, or whitepaper with Feynman-grade simplicity and research-backed structure (map → segments → concrete examples), checks understanding only *after* the walkthrough to steer re-coaching, and delivers a standalone reference explainer persisted as a Google Open Knowledge Format (OKF v0.1) bundle with source fingerprints pinned, so later sessions can detect drift and refresh diff-aware. Spaced-retrieval recall tooling included but strictly opt-in. |
| [`okf-site-kit`](okf-site-kit/) | Turn any Open Knowledge Format (OKF v0.1) bundle into a beautiful browsable static website — a stdlib generator emits a complete Astro + Starlight project with a hero landing page, per-concept pages carrying OKF metadata panels (type badge, tags, resource, producer keys), rewritten internal links, a changelog from `log.md`, and full-text search. Covers every bundle dialect published so far (Google's ga4/stackoverflow/crypto_bitcoin samples, spec-canonical, producer-extended) with graceful `WARN:`-reported degradation; deploy-ready for GitHub Pages. |
| [`starlight-handbook-kit`](starlight-handbook-kit/) | Scaffold or extend a decision-oriented Astro + Starlight documentation handbook — every topic follows a fixed nine-section skeleton, diagrams and widgets render without JavaScript, and eight CI gates fail the build the moment a page drifts from the contract, making agent-authored content safe. |
| [`tmux-agent-herdr-lite`](tmux-agent-herdr-lite/) | tmux cockpit with Zellij-like human ergonomics and Herdr-like agent supervision — menus, dashboard, pane navigation, blocked/working/done status detection, and jump-to-status navigation. |
| [`mockstar-mock`](mockstar-mock/) | Generate a runnable mockstar mock server from a service's specs/docs — OpenAPI, Postman, HAR, curl, GraphQL, and prose docs (md/pdf/docx/url) — normalized into one Endpoint Inventory, full-fidelity (scenarios/handlers/webhooks), Tier 2-enhanced, boot-and-smoke verified, with a provenance + coverage report. |
| [`repo2skill`](repo2skill/) | The skill-authoring skill: scaffolds a new gate-passing Agent Skill (standard frontmatter, PP-conformant SKILL.md skeleton, README, test stub, outcome-eval stub) and walks the semantic review checklist the mechanical gates can't judge. The source of this repo's vendored gate scripts, now shipped. |
| [`verifier-installer`](verifier-installer/) | Action-taking sibling of `agent-ready-rails`: detects a repo's stack(s) with a deterministic collector, then installs the missing runnable-verifier loop — format/build/test commands plus a CI workflow that runs them — and proves the loop red→green before handing back. |
| [`harvard-case-method`](harvard-case-method/) | Structured business reasoning in three modes — decide, prioritise (RICE), or pressure-test a plan — with the agent doing the assembly labour (research, sizing, sourcing) and the user keeping the judgement, split along Stanford's Decision Quality chain and scored as its weakest link. Ships two gates: decision records need a premortem, a falsifier and dated forecasts that resolve into a real Brier/calibration profile; RICE sheets are refused for unsourced reach, mixed periods or off-scale impact and report tie bands instead of false precision; plans must reconcile their driver tree, cross-check top-down against bottom-up, survive a hockey-stick check, and name the one condition they'd least confidently bet on. |
| [`knowledge-gardener`](knowledge-gardener/) | Completes the knowledge trilogy (`feynman-walkthrough` creates, `okf-site-kit` publishes, this maintains): sweeps Open Knowledge Format bundles, reports per-subject FRESH/STALE/UNKNOWN drift against pinned source fingerprints, drives diff-aware refreshes and re-pins, and regenerates any published site. |
| [`bug-autopsy`](bug-autopsy/) | `feynman-walkthrough`'s sibling for failures: reconstructs a defect end-to-end (evidence-cited timeline, blameless ≥3-deep 5-Whys root cause), writes a lint-checked post-mortem, and persists it into the same OKF knowledge bundle so the failure teaches the next engineer. |
| [`security-posture-audit`](security-posture-audit/) | Read-only, offline security *hygiene* audit — dependency pinning, committed env/key files, debug/permissive flags, insecure transports, risky CI patterns — severity-graded with file:line evidence and honest not-covered boundaries. Not a CVE scanner or SAST; completes the trust family alongside `scan-leaks` and `base-in-reality`. |
| [`spec-first-planning`](spec-first-planning/) | Fills the plan band: turns a fuzzy feature request into a lint-clean spec of numbered, testable requirements, then derives a task plan where every task names the check that proves it done — with a total requirement↔task coverage map before handoff to an implementer or a `crafting-self-prompting-loops` loop. |
| [`world-model-ledger`](world-model-ledger/) | Install a persistent, SQLite-backed world model for a coding agent — entities (symbols/files/modules/real-world referents), interactions, and constraints, each with two confidence axes (observed vs normative), a validation status, and PROV-style evidence. Code-observed relationships are never treated as ground truth: only oracle evidence (tests/CI/docs/human) raises normative confidence. Four lifecycle hooks retrieve validated/unverified/contradicted items before edits, update records without inventing facts, and consolidate on Stop; detects contradictions, proposes located fixes, and improves normative correctness over time. Every triple is validated against a predicate ontology (RDFS-style domain/range) before it enters the ledger — hallucinated verbs and semantically impossible pairings are rejected, not stored. Ships a 108-test install gate. |

See each skill directory's `SKILL.md` for usage and prerequisites.

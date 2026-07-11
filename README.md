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
npx skills add dhanesh/agent-skills --skill base-in-reality
npx skills add dhanesh/agent-skills --skill context-hygiene-kit
npx skills add dhanesh/agent-skills --skill crafting-self-prompting-loops
npx skills add dhanesh/agent-skills --skill feynman-walkthrough
npx skills add dhanesh/agent-skills --skill okf-site-kit
npx skills add dhanesh/agent-skills --skill starlight-handbook-kit
npx skills add dhanesh/agent-skills --skill tmux-agent-herdr-lite
npx skills add dhanesh/agent-skills --skill mockstar-mock
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
| [`base-in-reality`](base-in-reality/) | Read-only, research-grounded repo audit — extracts falsifiable claims across algorithm/architecture/business-logic layers, routes each to authoritative sources (arxiv, PubMed, Scholar, JSTOR, OpenAlex, Crossref, Semantic Scholar + NIST/RFC/OWASP/ISO/regulators), verifies against fetched evidence, and adversarially refutes before emitting a severity-graded cited report. No fabricated citations: ungrounded claims are reported as `UNCONFIRMED`. |
| [`context-hygiene-kit`](context-hygiene-kit/) | Install a bounded, scored, tiered context cache into a Claude Code project — keeps working memory lean (anti-bloat) and rot-proof across compactions (anti-rot) via a stdlib Python ledger, a deterministic per-turn transcript harvester, and Stop/PreCompact/SessionStart hooks. Replaces lossy local-model session-summarisers with a lossless one; ships with a 16-test install gate. |
| [`crafting-self-prompting-loops`](crafting-self-prompting-loops/) | Design and build sound self-prompting loops — draft→critique→revise, autonomous task loops, multi-agent orchestration, and human-checkpointed loops. Produces a filled loop spec, a runnable scaffold, and bakes in the mandatory safety properties (hard-stop backstop, trusted/untrusted two-channel boundary). |
| [`feynman-walkthrough`](feynman-walkthrough/) | Understanding-first walkthrough guide (formerly `desirable-difficulty`) — walks a learner through any subject, codebase, or whitepaper with Feynman-grade simplicity and research-backed structure (map → segments → concrete examples), checks understanding only *after* the walkthrough to steer re-coaching, and delivers a standalone reference explainer persisted as a Google Open Knowledge Format (OKF v0.1) bundle with source fingerprints pinned, so later sessions can detect drift and refresh diff-aware. Spaced-retrieval recall tooling included but strictly opt-in. |
| [`okf-site-kit`](okf-site-kit/) | Turn any Open Knowledge Format (OKF v0.1) bundle into a beautiful browsable static website — a stdlib generator emits a complete Astro + Starlight project with a hero landing page, per-concept pages carrying OKF metadata panels (type badge, tags, resource, producer keys), rewritten internal links, a changelog from `log.md`, and full-text search. Covers every bundle dialect published so far (Google's ga4/stackoverflow/crypto_bitcoin samples, spec-canonical, producer-extended) with graceful `WARN:`-reported degradation; deploy-ready for GitHub Pages. |
| [`starlight-handbook-kit`](starlight-handbook-kit/) | Scaffold or extend a decision-oriented Astro + Starlight documentation handbook — every topic follows a fixed nine-section skeleton, diagrams and widgets render without JavaScript, and eight CI gates fail the build the moment a page drifts from the contract, making agent-authored content safe. |
| [`tmux-agent-herdr-lite`](tmux-agent-herdr-lite/) | tmux cockpit with Zellij-like human ergonomics and Herdr-like agent supervision — menus, dashboard, pane navigation, blocked/working/done status detection, and jump-to-status navigation. |
| [`mockstar-mock`](mockstar-mock/) | Generate a runnable mockstar mock server from a service's specs/docs — OpenAPI, Postman, HAR, curl, GraphQL, and prose docs (md/pdf/docx/url) — normalized into one Endpoint Inventory, full-fidelity (scenarios/handlers/webhooks), Tier 2-enhanced, boot-and-smoke verified, with a provenance + coverage report. |
| [`world-model-ledger`](world-model-ledger/) | Install a persistent, SQLite-backed world model for a coding agent — entities (symbols/files/modules/real-world referents), interactions, and constraints, each with two confidence axes (observed vs normative), a validation status, and PROV-style evidence. Code-observed relationships are never treated as ground truth: only oracle evidence (tests/CI/docs/human) raises normative confidence. Four lifecycle hooks retrieve validated/unverified/contradicted items before edits, update records without inventing facts, and consolidate on Stop; detects contradictions, proposes located fixes, and improves normative correctness over time. Ships a 57-test install gate. |

See each skill directory's `SKILL.md` for usage and prerequisites.

# Agent Skills

Personal Agent Skills repository for installation through the open [`skills`](https://www.npmjs.com/package/skills) CLI.

## Install

Each skill installs independently. Use the `--skill` flag with the skill's directory name:

```bash
npx skills add dhanesh/agent-skills --skill <skill-name>
```

For example:

```bash
npx skills add dhanesh/agent-skills --skill crafting-self-prompting-loops
npx skills add dhanesh/agent-skills --skill tmux-agent-herdr-lite
npx skills add dhanesh/agent-skills --skill starlight-handbook-kit
npx skills add dhanesh/agent-skills --skill context-hygiene-kit
npx skills add dhanesh/agent-skills --skill base_in_reality
```

To install every skill in this repo, omit the `--skill` flag:

```bash
npx skills add dhanesh/agent-skills
```

## Skills

| Skill | Description |
|-------|-------------|
| [`crafting-self-prompting-loops`](crafting-self-prompting-loops/) | Design and build sound self-prompting loops — draft→critique→revise, autonomous task loops, multi-agent orchestration, and human-checkpointed loops. Produces a filled loop spec, a runnable scaffold, and bakes in the mandatory safety properties (hard-stop backstop, trusted/untrusted two-channel boundary). |
| [`tmux-agent-herdr-lite`](tmux-agent-herdr-lite/) | tmux cockpit with Zellij-like human ergonomics and Herdr-like agent supervision — menus, dashboard, pane navigation, blocked/working/done status detection, and jump-to-status navigation. |
| [`starlight-handbook-kit`](starlight-handbook-kit/) | Scaffold or extend a decision-oriented Astro + Starlight documentation handbook — every topic follows a fixed nine-section skeleton, diagrams and widgets render without JavaScript, and eight CI gates fail the build the moment a page drifts from the contract, making agent-authored content safe. |
| [`context-hygiene-kit`](context-hygiene-kit/) | Install a bounded, scored, tiered context cache into a Claude Code project — keeps working memory lean (anti-bloat) and rot-proof across compactions (anti-rot) via a stdlib Python ledger, a deterministic per-turn transcript harvester, and Stop/PreCompact/SessionStart hooks. Replaces lossy local-model session-summarisers with a lossless one; ships with a 16-test install gate. |
| [`base_in_reality`](base_in_reality/) | Read-only, research-grounded repo audit — extracts falsifiable claims across algorithm/architecture/business-logic layers, routes each to authoritative sources (arxiv, PubMed, Scholar, JSTOR, OpenAlex, Crossref, Semantic Scholar + NIST/RFC/OWASP/ISO/regulators), verifies against fetched evidence, and adversarially refutes before emitting a severity-graded cited report. No fabricated citations: ungrounded claims are reported as `UNCONFIRMED`. |

See each skill directory's `SKILL.md` for usage and prerequisites.

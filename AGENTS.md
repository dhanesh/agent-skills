# AGENTS.md

This repo's agent working guide lives in **[`CLAUDE.md`](CLAUDE.md)** — read it first.

TL;DR for any coding agent working here:

- This is a collection of **Agent Skills** (one top-level dir per skill, each with a `SKILL.md`).
- **Run `make gate` before committing** — it validates structure, scans for leaks, checks
  prompt conventions, and runs every skill's unit tests. CI runs the same on each PR.
- Per-skill: `make gate-skill SKILL=<dir>`. Tests only: `make test`.
- Don't add a root `PARAMETERS.md` (reserved for template bijection — it fails the gate); use
  `references/parameters.md` to document a skill's flags.

Full conventions, the skill anatomy, and how to add a skill are in `CLAUDE.md`.
</content>

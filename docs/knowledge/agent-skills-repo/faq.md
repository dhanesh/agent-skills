---
type: FAQ
title: agent-skills repo — FAQ
timestamp: 2026-07-18T00:00:00Z
---

# agent-skills repo — FAQ

Questions actually asked during walkthrough sessions, with the answers that
resolved them. These are the questions the next reader will have too.

**Q: What part of the spectrum does this repo cater to today?**
A: The meta layer of agentic engineering — auditing the environment agents run in
(`agent-ready-rails`, `base-in-reality`), designing sound agent loops
(`crafting-self-prompting-loops`), giving agents persistent memory
(`context-hygiene-kit`, `world-model-ledger`), capturing and publishing knowledge
(`feynman-walkthrough`, `okf-site-kit`, `starlight-handbook-kit`), and dev-environment
infrastructure (`tmux-agent-herdr-lite`, `mockstar-mock`). It does not yet cover the
object layer: skills that directly author product code, tests, migrations, reviews, or
releases.

**Q: What new skills would add the most value?**
A: In leverage order: (1) an audit-to-action bridge that executes the fix lists the two
audit skills already produce; (2) a user-facing skill-forge that applies the Prompting
Playbook to arbitrary prompts/skills; (3) a verified code-change skill (object layer);
(4) a PR-review skill in the audit family's evidence-graded style; (5) a deploy/release
readiness installer for the "operate" rails that `agent-ready-rails` only audits.

**Q: What needs fixing in the existing skills?**
A: See segment 6 of the explainer for the verified list. Highest-signal items: the
top-level README's three stale test/gate counts, mockstar-mock's SKILL↔README
contradiction on the package name (scoped `@dhaneshpurohit/mockstar` vs bare
`mockstar`), and the `--root` flag-order bug in feynman-walkthrough's own
`references/okf.md` examples.

**Q: Why doesn't `make gate` run mockstar-mock's shell tests?**
A: The Makefile's unit-test loop globs only `assets/test_*.py`. Shell (`test_*.sh`) and
in-scaffold Node (`check-*.mjs`) suites exist but run only in generated target
projects, not in this repo's CI.

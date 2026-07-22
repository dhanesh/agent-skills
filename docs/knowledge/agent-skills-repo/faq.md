---
type: FAQ
title: agent-skills repo — FAQ
timestamp: 2026-07-18T00:00:00Z
---

# agent-skills repo — FAQ

Questions actually asked during walkthrough sessions, with the answers that
resolved them. These are the questions the next reader will have too.

## What part of the spectrum does this repo cater to today?

Meta-level agent infrastructure, almost exclusively: nine of ten skills improve
how agents themselves work (trust/audit, memory/persistence, orchestration/
supervision, knowledge/docs) rather than doing object-level app work —
mockstar-mock is the lone exception. On a delivery lifecycle it is strong at
understand/audit/remember/document and thin at plan, implement, author-tests,
and deploy/operate. See explainer segment 5.

## What additional skills would fit?

Six proposals, each extending an existing family: a shipped skill-authoring
skill (repo2skill), a verifier-installer that acts on agent-ready-rails
findings, a scheduled knowledge-gardener over OKF bundles, a bug-autopsy
walkthrough, a security-posture audit, and a spec-first planning skill.
Details and rationale in explainer segment 5.

## What should change in the existing skills?

Top items: document (or gate) hook coexistence between context-hygiene-kit and
world-model-ledger; fix okf.py's self-pinning STALE-by-one-commit blind spot;
give agent-ready-rails a deterministic evidence collector and tests;
standardize frontmatter metadata; tune long descriptions for trigger accuracy;
operationalize the prompting-playbook semantic checklist; fix the repo2skill
link drift in scripts/gates/README.md. Full list in explainer segment 5.

## Did the roadmap from the 2026-07-18 walkthrough actually happen?

Yes — a 2026-07-22 autonomous self-improvement session shipped all six proposed
skills (repo2skill, verifier-installer, knowledge-gardener, bug-autopsy,
security-posture-audit, spec-first-planning), landed all seven improvements, and
generalized world-model-ledger's eval into a hard outcome-eval gate
(`docs/eval-standard.md`) that every skill now passes. See explainer segment 5
for the executed list.

## Why does `okf.py status` report STALE right after creating a bundle in-repo?

Creating the bundle dirties the working tree, and committing it moves HEAD past
the pinned SHA — the bundle can never be FRESH against the repo that contains
it under the current fingerprint logic. Known limitation; improvement #2 in the
explainer proposes excluding the bundle root from drift checks.

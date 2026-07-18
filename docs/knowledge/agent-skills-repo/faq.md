---
type: FAQ
title: agent-skills repo — FAQ
timestamp: 2026-07-18T00:00:00Z
---

# agent-skills repo — FAQ

Questions actually asked during walkthrough sessions, with the answers that
resolved them. These are the questions the next reader will have too.

## What part of the spectrum does this repo cater to today?

Agent operations, not domain automation. Its skills act **before** agent work
(audits: `agent-ready-rails`, `base-in-reality`), **around** it (supervision
and loop design: `tmux-agent-herdr-lite`, `crafting-self-prompting-loops`),
and **after** it (memory and knowledge: `context-hygiene-kit`,
`world-model-ledger`, `feynman-walkthrough`, `okf-site-kit`,
`starlight-handbook-kit`). Only `mockstar-mock` generates a conventional
development artifact.

## Is `make gate` actually green?

Yes at the pinned commit — all ten skills pass every default gate, and all 11
unit suites (232 tests) pass. But strict playbook mode
(`make playbook PLAYBOOK_FLAGS=--strict`) fails three skills on PP-5
overcorrection: `crafting-self-prompting-loops` (14 absolutist directives vs
1 heuristic cue), `world-model-ledger` (13 vs 3), `mockstar-mock` (13 vs 8).

## Which skills ship no unit tests, and is that a gap?

`agent-ready-rails` and `crafting-self-prompting-loops` ship no executable
code at all — they are prompt-only, so there is nothing for a unit suite to
test; not a gap. `starlight-handbook-kit` ships eight `.mjs` checker scripts
as scaffold templates with no unit coverage in this repo — they are exercised
only inside a generated handbook's CI, which is a real (if soft) gap.

## Does the documentation match the code?

Almost. One drift found: `feynman-walkthrough/references/okf.md` documents
`okf.py init "subject" --root knowledge --source …`, but `--root` is a global
argparse option and must precede the subcommand
(`okf.py --root knowledge init …`); the documented form exits with
"unrecognized arguments".

## Why is PARAMETERS.md "reserved"?

It exists solely to declare the placeholder bijection for
`assets/templates/`: the validate gate checks that every template placeholder
appears in `PARAMETERS.md` and vice versa, and `dry-run-replay.sh` uses it to
replay an install. A `PARAMETERS.md` without templates fails the gate; skills
documenting flags use `references/parameters.md` instead.

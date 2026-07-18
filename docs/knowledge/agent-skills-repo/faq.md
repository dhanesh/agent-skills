---
type: FAQ
title: agent-skills repo — FAQ
timestamp: 2026-07-18T00:00:00Z
---

# agent-skills repo — FAQ

Questions actually asked during walkthrough sessions, with the answers that
resolved them. These are the questions the next reader will have too.

## What part of the spectrum does this repo cater to today? (2026-07-18)

Placed on an agent-engineering lifecycle — make-the-repo-agent-ready → design
the loop → run/supervise agents → keep memory and knowledge → verify outputs →
publish knowledge → do domain work — the repo is heavily weighted toward
**agent reliability and knowledge infrastructure**, i.e. the system *around*
agents rather than domain task execution:

- Audit/verification: 2 skills (agent-ready-rails, base-in-reality)
- Memory/knowledge persistence: 3 (context-hygiene-kit, world-model-ledger,
  feynman-walkthrough)
- Loop design & runtime supervision: 2 (crafting-self-prompting-loops,
  tmux-agent-herdr-lite)
- Knowledge publishing: 2 (okf-site-kit, starlight-handbook-kit)
- Domain dev work: 1 (mockstar-mock)

The unifying themes are epistemic honesty (UNCONFIRMED verdicts, observed-vs-
normative confidence, grounded-vs-inferred tagging), stdlib-only portability,
and gate-verified shipping. Thin ends of the spectrum: skills that *do*
day-to-day engineering work, evaluation of skills/agents at scale, and
operations/deployment.

## What additional skills would complement the collection? (2026-07-18)

Four candidates that extend existing investments rather than start new ones:

1. **skill-eval-harness** — generalize world-model-ledger's `eval/` +
   crafting-self-prompting-loops' with/without methodology into a reusable
   skill that benchmarks any skill's effect (paired runs, deterministic
   grading, variance notes). Closes the repo's own loop-engineering layer at
   the skill level.
2. **knowledge-gardener** — a standing routine that sweeps OKF bundles, runs
   `okf.py status`, and refreshes STALE explainers diff-aware. The refresh
   logic exists as prose in feynman-walkthrough; nothing operationalizes it on
   a schedule.
3. **change-in-reality (diff-scoped audit)** — both audit skills judge whole
   repos; nothing applies the evidence-grading house style to a single diff/PR,
   which is where agent output actually needs vetting.
4. **contract-tests-from-inventory** — mockstar-mock already normalizes specs
   into an Endpoint Inventory; a sibling could emit contract tests / mock-vs-
   live drift checks from the same IR, reusing the hardest part.

## What should change in the existing skills? (2026-07-18)

Factual drift (quick fixes): world-model-ledger claims a "79-test" gate in ≥4
places but the suite runs 97; mockstar-mock's README instructs installing bare
`mockstar`, the exact package its SKILL.md forbids as unrelated, and its README
output layout contradicts the SKILL's; tmux-agent-herdr-lite's SKILL.md uses
`Dhanesh/agent-skills` (capital D) vs `dhanesh` elsewhere; context-hygiene-kit
SKILL.md lines 50/54 duplicate (and slightly contradict) the jq/settings
sentence.

Structural: sibling cross-linking is one-directional (agent-ready-rails links
out; base-in-reality and feynman-walkthrough link to no siblings; okf-site-kit
and starlight-handbook-kit — two Starlight generators — never disambiguate each
other); test discipline is uneven (starlight's nine gate scripts and 17 of
tmux's 19 shell scripts are untested; base-in-reality ships 21 tests it never
mentions); mockstar-mock's SKILL.md is a 452-line outlier that should offload
runtime detail to references/; several frontmatter descriptions are 200+-word
single sentences that bury their trigger phrases.

## Why does `okf.py status` report STALE right after creating this bundle? (2026-07-18)

Because the bundle lives inside the repo it fingerprints. A git source pins
`HEAD` plus a `+dirty` marker; writing the bundle dirties the tree, and
committing it moves HEAD past the pin — so a self-referential bundle can never
settle at FRESH. Treat STALE here as "check whether commits since the pin
touched anything besides `docs/knowledge/`". A fix candidate for okf.py: an
option to exclude the bundle root from the dirty check and tolerate
pin-commit-only drift.

---
name: knowledge-gardener
description: >-
  Keep Open Knowledge Format (OKF v0.1) knowledge bundles alive: sweep one or more
  knowledge roots, detect subjects whose pinned sources have drifted, drive diff-aware
  refreshes, re-pin fingerprints, and regenerate any published site. Use when the user
  wants to check, maintain, refresh, or garden existing knowledge — "is my knowledge base
  still current?", "the repo changed, update the explainer", "sweep docs/knowledge for
  stale subjects", "run a knowledge health check" — or when a scheduled maintenance sweep
  fires. Reports per-subject FRESH/STALE/UNKNOWN/ERROR with what moved (pinned vs current
  git SHA plus a diff summary), then walks only the changed segments. Not for explaining
  a subject or creating new bundles — use feynman-walkthrough for that — and not for
  building the website itself — use okf-site-kit; this skill maintains what both produce.
license: MIT
compatibility: Requires python3 (stdlib only) and a POSIX shell; git is needed only to check git-pinned sources. Fully offline.
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "okf,knowledge,maintenance,drift,staleness,refresh,feynman-walkthrough,okf-site-kit"
---

# knowledge-gardener

Maintain Open Knowledge Format (OKF v0.1) bundles after they exist. Knowledge pinned to
a moving source rots quietly: the repo gains commits, the paper gets revised, and the
explainer that was true in January misleads in July. This skill is the maintenance leg
of the knowledge trilogy — `feynman-walkthrough` *creates* bundles (explainers with
source fingerprints pinned), `okf-site-kit` *publishes* them, and the gardener *keeps
them true*: sweep, report drift, refresh only what moved, re-pin, republish.

**Locating this skill's helpers (do this first).** The steps below run bundled
scripts. You execute from the *target repo*, not from this skill's directory, so a
path written relative to this skill will not resolve. Resolve the base directory once and use it
everywhere — including in any subagent prompt, which must receive the literal absolute
path, never a relative form:

```sh
SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
# If you don't have it, discover it:
SKILL_DIR=$(find ~/.claude ~/.config ~/.agents -type d -name 'knowledge-gardener' 2>/dev/null | head -1)
test -d "$SKILL_DIR/assets" || test -d "$SKILL_DIR/scripts"   # verify before proceeding
```

## Status semantics

[assets/garden.py](assets/garden.py) is self-contained — skills install independently,
so it imports nothing from its siblings — but its verdicts agree exactly with the
fingerprints `feynman-walkthrough`'s okf.py pinned: git sources pin HEAD (with a
`+dirty` marker for uncommitted changes), file and dir sources pin a sha256, and
external URL/topic sources have no local fingerprint. Per subject:

| State | Meaning | Gardener's move |
|---|---|---|
| FRESH | every pinned fingerprint still matches | nothing to do; trust the explainer |
| STALE | at least one source drifted | diff-aware refresh (step 3) |
| UNKNOWN | a source can't be fingerprinted (external URL, moved path) | ask or re-check by hand |
| ERROR | the explainer's frontmatter or pins are unparseable | repair the pins, then re-judge |

`sweep` exits non-zero when anything is STALE or ERROR, so it doubles as a CI-style
gate; UNKNOWN alone exits 0 because external sources are permanently uncheckable, not
broken. [assets/test_garden.py](assets/test_garden.py) locks these semantics down,
including a direct fingerprint-agreement test against the sibling tool.

## Workflow

1. **Discover.** Run `python3 "$SKILL_DIR/assets/garden.py" sweep <root> [<root> ...]` over the
   knowledge roots in play — the ones the user names, or the conventional spots
   (`docs/knowledge/` inside repos, a user-level `~/knowledge/`). It finds every bundle
   root (an `index.md` declaring `okf_version`), every subject beneath, and parses each
   explainer's `sources:` pins. If it prints `GARDEN_RESULT: NO_BUNDLES`, say so and
   stop — there is nothing to garden here.
2. **Report.** Relay the garden report faithfully: per subject, the state and — for
   stale git sources — pinned vs current SHA, the `git diff --stat` summary, and the
   changed file names. Don't soften ERROR entries into warnings; a subject with
   unreadable pins is invisible to drift detection until repaired.
3. **Refresh the stale, diff-aware.** For each STALE subject, follow
   [references/refresh-playbook.md](references/refresh-playbook.md): read what actually
   changed since the pinned fingerprint, map changed files to explainer segments,
   rewrite only the drifted segments (and correct invalidated FAQ answers), then re-pin
   the fingerprints and append the update to the bundle's `log.md`. UNKNOWN subjects
   get the playbook's ask-or-re-check treatment; ERROR subjects get their frontmatter
   repaired first.
4. **Regenerate any published site.** If the bundle has a generated website, it now
   shows pre-refresh text — rebuild it with the `okf-site-kit` skill (or let its CI
   deploy workflow rebuild on push). The bundle stays canonical; the site is a view.
5. **Verify, then offer a schedule.** Re-run the sweep and require every subject to
   land on FRESH or UNKNOWN; anything still STALE or ERROR loops back to step 3 until
   clean. Then, when the host has a scheduler (cron-style routines, reminders), offer
   **once** to schedule the next sweep — weekly is a sane default for active repos —
   and create it only on an explicit yes. No scheduler, or no interest: skip silently.

## Deliverable

- The **garden report**, relayed per bundle: each subject's state, and for stale
  subjects what moved (SHAs, diffstat, changed files) — the `json` subcommand emits the
  same facts machine-readably for scripting.
- **Refreshed, re-pinned bundles**: drifted segments rewritten, fingerprints current,
  `log.md` carrying a dated entry per refresh.
- A **regenerated site**, when one existed before the sweep.
- Optionally, on opt-in only: a scheduled next sweep.

## How to behave

- **Never silently rely on a stale explainer** — the whole point of the pins is that
  you can know better. Say "the repo has moved since this was written" before quoting
  from it, then offer the refresh.
- **Prefer surgical edits.** Refreshing is gardening, not replanting: keep prose,
  examples, and FAQ history that the diff didn't touch.
- **Report before repairing.** The user decides which stale subjects are worth
  refreshing now; a sweep that quietly rewrites bundles is overreach. When they asked
  for a full refresh up front, proceed — but still show the report first.
- **Scheduling is opt-in, offered once.** If it's declined or ignored, the report and
  the refreshed bundles are the complete deliverable.

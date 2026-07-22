---
name: bug-autopsy
description: >-
  Trace a defect or incident that already happened end-to-end — trigger, propagation,
  detection, fix — and capture a blameless, evidence-cited post-mortem the next engineer
  can learn from, persisted as a Postmortem concept in the same Open Knowledge Format
  (OKF) bundle feynman-walkthrough maintains. Use when the user says "post-mortem this
  bug/outage", "why did this incident happen", "write up what went wrong", "autopsy this
  regression", or wants the lessons from a fixed bug preserved for the team. Reconstructs
  the timeline from git history, logs, CI runs, and issue threads; chases root cause with
  a disciplined 5-Whys chain that stops at a systemic cause, never a person; ships a
  lint-clean write-up with checkable prevention items. Not for live debugging or triaging
  a still-burning incident — stabilize first, autopsy after; not for understanding a
  healthy system (use feynman-walkthrough); not for auditing a codebase's claims (use
  base-in-reality).
license: MIT
compatibility: Any filesystem agent with python3 (stdlib-only, offline). Git history, CI logs, and issue-tracker access improve evidence quality but are optional; degrades to whatever records exist.
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "postmortem,incident-review,root-cause,five-whys,blameless,okf,knowledge"
---

# bug-autopsy

`feynman-walkthrough`'s sibling for failures. Where that skill walks a learner through a
healthy system, this one walks a failure that already happened — trigger → propagation →
detection → fix — and leaves behind a blameless post-mortem the next engineer can audit
link by link. The failure is treated as material to learn from, not a fire to fight: if
the incident is still burning, triage first and come back.

## Ground rules

- **Evidence or inference, labeled.** Every timeline entry and every "why" cites its
  evidence — `file:line`, commit sha, CI run, log timestamp, issue comment. Where the
  record is silent, reconstruct by judgment but mark the entry *(inference)*; a labeled
  guess is useful, an unlabeled one poisons the document.
- **Blameless, structurally.** Root causes are systemic — a missing guardrail, an absent
  test, a process or design gap — never a person. When a chain lands on "someone made a
  mistake", it is unfinished: ask why the system let that mistake reach users (the
  translation table is in [references/five-whys.md](references/five-whys.md)).
- **Boundaries.** This skill explains failures that already happened. Live debugging and
  on-call triage are out of scope; understanding a healthy system is
  `feynman-walkthrough`; verifying a codebase's claims against authoritative sources is
  `base-in-reality`.

## Workflow

1. **Scope.** One light round: what failed (the user-visible symptom), the blast radius
   (who/what was affected, for how long), and what the learner will do with the
   understanding — fix a recurrence, prevent the class, or teach the team. The answer
   sets how deep the whys chain and the prevention items need to go. Skip anything the
   conversation already answered.
2. **Reconstruct the timeline from evidence.** Work the records: `git log`/`git blame`
   around the suspect area, CI runs, logs, issue and PR threads, alert history. Produce
   ordered entries — trigger, propagation, detection, mitigation, fix — each carrying a
   timestamp and an evidence citation; label unbacked reconstruction *(inference)*.
   Where records conflict, prefer the machine-generated one and note the conflict.
3. **Chase root cause with a 5-Whys chain.** Per
   [references/five-whys.md](references/five-whys.md): start from the user-visible
   failure, evidence every level, go at least three deep, branch when the chain forks,
   and stop at the first systemic cause — the gap that, once closed, prevents the whole
   class. Side branches that don't bottom out become contributing factors.
4. **Write the post-mortem** from
   [references/postmortem-template.md](references/postmortem-template.md). Required
   sections: Summary; Impact; Timeline; Root cause (the whys chain); Contributing
   factors; Fix, naming the verifying commit/test/CI run; Prevention, as checkbox items
   each with an owner and a completion check; Links.
5. **Lint and repair.** Run `python3 assets/postmortem_lint.py <file>` and fix findings
   until it prints `POSTMORTEM_LINT: PASS`. Structural failures (missing/empty sections,
   untimestamped timeline entries, a shallow whys chain, non-checkbox prevention items)
   are hard; blame-phrasing `WARN` lines are prompts to reframe — resolve them unless
   the flagged text is a direct quote you're preserving as evidence.
6. **Persist into the OKF bundle and re-pin.** Write the document as
   `<subject>/postmortem.md` (concept `type: Postmortem` — the frontmatter ships at the
   top of the template) in the knowledge bundle `feynman-walkthrough` maintains, e.g.
   `docs/knowledge/`; create the bundle there if none exists. Link it from the subject's
   `index.md`, append a dated entry to the bundle's `log.md`, and re-pin source
   fingerprints — via the sibling tool (`feynman-walkthrough/assets/okf.py pin`) when
   installed, or by hand following the bundle's existing layout.

## Deliverable

A lint-clean, evidence-cited, blameless post-mortem living in the knowledge bundle:
every timeline entry timestamped and sourced, a whys chain at least three levels deep
ending at a systemic cause, a fix tied to its verifying commit or test, and prevention
items someone can actually tick off — plus the updated bundle index, log entry, and
re-pinned fingerprints. The chat walkthrough of the failure is the tour; the persisted
post-mortem is the artifact that outlives it.

## Revisit across sessions

The post-mortem is a living record, not a filing. When a session opens on a subject
with an existing post-mortem, check bundle freshness first
(`feynman-walkthrough/assets/okf.py status <subject>` where available):

- **Prevention audit.** If the same defect class recurs, the old post-mortem's
  Prevention checkboxes are the first evidence to read — an unchecked box is itself a
  finding, and usually the first "why" of the new chain.
- **STALE bundle** → the code moved since the autopsy; say so before citing the
  post-mortem's `file:line` references, and refresh them if the learner will act on
  them.
- **New autopsy, old subject** → append, don't overwrite: a second `postmortem-*.md`
  concept in the same subject, cross-linked from the first, keeps the failure history
  auditable.

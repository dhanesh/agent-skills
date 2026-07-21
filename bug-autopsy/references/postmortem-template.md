---
type: Postmortem
title: <short title of the defect or incident>
description: Blameless post-mortem of <the failure, in a phrase>
resource: <repo path or service the failure lived in>
tags: [postmortem, incident]
timestamp: <ISO 8601 written-at time, e.g. 2026-07-01T12:00:00Z>
created: <YYYY-MM-DD>
sources:
- type: git
  locator: <repo path>
  fingerprint: <git HEAD sha at time of writing>
  pinned: <YYYY-MM-DD>
---

# Post-mortem: <short title of the defect or incident>

Fill every `<placeholder>`; delete the guidance sentences (the non-bullet
prose under each heading) once the section stands on its own. Verify with
`python3 ../assets/postmortem_lint.py <this file>` — the linter enforces the
structure below. Frontmatter is the OKF concept header; keep it when the
post-mortem lives in a knowledge bundle, drop it for a standalone document.

## Summary

<Two or three sentences: what failed as the user saw it, what the root cause
turned out to be, and what now prevents the class of failure. Written last,
readable first.>

## Impact

<Who and what was affected, for how long, and how badly: users/tenants hit,
duration, data corrupted or exposed, requests failed, money or trust lost.
Numbers where the evidence supports them; "unknown" is honest when it doesn't.>

## Timeline

All times UTC unless an entry states otherwise. Every entry carries its own
timestamp and cites its evidence; reconstruction not backed by a record is
labeled (inference).

- YYYY-MM-DD HH:MM — <trigger: the change or event that introduced the defect> (evidence: <commit sha / file:line>)
- YYYY-MM-DD HH:MM — <propagation: first bad behavior in the wild> (evidence: <log ref / metric / trace>)
- YYYY-MM-DD HH:MM — <detection: how and when a human or alert noticed> (evidence: <alert id / ticket / issue link>)
- YYYY-MM-DD HH:MM — <mitigation and fix: what stopped the bleeding, what fixed the cause> (evidence: <commit sha / CI run>)

## Root cause

The whys chain — at least three levels, each answer evidenced, ending at a
systemic cause (a missing guardrail, process, or design decision), never a
person. How to run and where to stop: [five-whys.md](five-whys.md).

1. **Why did <the user-visible failure> happen?** <answer> (evidence: <file:line / commit / log ref>)
2. **Why <did that mechanism behave that way>?** <answer> (evidence: <ref>)
3. **Why <was that possible / undetected>?** <answer> (evidence: <ref>)
4. **Why <did nothing catch it earlier>?** <answer — keep going while the answer is still a mechanism> (evidence: <ref>)
5. **Why <at bottom>?** <systemic cause: the gap that, once closed, prevents the whole class>

## Contributing factors

Not the root cause, but what widened the blast radius or delayed detection.

- <factor> (evidence: <ref>)
- <factor> (evidence: <ref>)

## Fix

<What changed and where, in a sentence or two.> Verified by <the commit sha,
the test that now fails without the fix, and/or the green CI run id>.

## Prevention

Every item is a box someone can tick, with a stated way to verify completion.
Tick items as they land; an unchecked box here is the first thing to audit if
the defect class recurs.

- [ ] <action that removes or guards the systemic cause> (owner: <team/role>; check: <how completion is verified>)
- [ ] <action that shortens detection next time> (owner: <team/role>; check: <how completion is verified>)

## Links

- <issue / ticket that reported it>
- <fix PR / commit>
- <dashboards, incident channel archive, related post-mortems>

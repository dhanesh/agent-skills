# Maintain mode

A feature map rots the moment the app changes. Maintain mode is the upkeep pass. It
edits only the verify skill's own directory (SKILL.md, `features/`, its scripts), never
product code.

## Outcomes

Pick one and say which:

- **clean**: every feature got source and live coverage; nothing to change.
- **changed**: proven corrections to the map, harness or doc, in one change.
- **blocked**: coverage could not finish; say exactly what blocked it.

## The pass

1. **Index hygiene.** `forge lint` lists missing, extra, duplicate and dead entries. Fix
   them; no generated inventory.
2. **Source wave.** One read-only subagent per feature file, launched concurrently. The
   brief:

   ```text
   Read-only. You MUST NOT run the app, drive it, or edit any file.
   Feature file: <absolute path>. App root: <absolute path>.
   Answer "how does this user-facing feature work?" from source.
   Return exactly:
   Summary: <what a user does and sees>
   Entry points: <paths:symbols>
   Drift: <none | each doc claim the source contradicts, with path:line>
   Recipe: <one live-verification recipe with the harness>
   ```

3. **Reconcile.** Every feature file has a returned summary. Spot-check cited drift. Sweep
   recent churn (`git log --since`) for user-facing surfaces missing from the map; name a
   concrete source path before calling one missing.
4. **Live pass.** Required even when source looks clean. You own all driving. Doctor
   before the first drive, on each fresh session, and after any failed drive. Evidence
   survives every cleanup. A feature that cannot be reached is `verified-unreachable` only
   with the missing prerequisite (auth, entitlement, external state) and the route tried.
5. **Triage.**
   - Doc drift: the map describes the app wrongly and the source agrees with the app.
     Fix the map.
   - Harness gap: working behaviour the harness cannot drive. Fix the harness, re-drive.
   - Product bug: the app contradicts the documented intent. `forge finding` records
     `.verify/findings/<id>-<sha12>.json`. Keep the map as it was: the invariant.
6. **Guard.** `forge check-maintain <verify dir> --base <ref>`: every feature whose
   What it is, Drive it or Proof changed must have a source anchor that changed too.
   A description rewritten while its source stood still is the map being bent to match
   the app, which is how a bug gets hidden. Then `forge lint`.

## The invariant, stated plainly

Maintain mode may update the verification skill. It may not resolve a mismatch between
documented intent and observed behaviour by editing the documentation. The mismatch is a
finding. `check-maintain` makes the easy version of the violation fail; the rest is on
the reviewer.

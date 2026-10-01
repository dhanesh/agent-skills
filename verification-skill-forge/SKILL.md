---
name: verification-skill-forge
description: >-
  Generate and maintain a project-local verify-<app> skill that drives the real running
  app the way a user does and captures SHA-bound evidence that a change works: tests
  passing is not verification. Use when the user says "make a verify skill for this
  app", "how would an agent prove this feature works", "set up runtime verification",
  "audit the verify skill" or "the feature map is stale", or when factory-conductor's
  evidence gate routes a task here because a touched feature has no map entry. Detects
  forge vs maintain mode itself. Ships a linter that rejects placeholder skills, a
  Manifold join that names every anchored constraint with no observable proof, a
  vendored evidence recorder with per-instance port claims, and a maintain-mode guard
  that fails a map edited to match buggy behaviour. Not the verifier itself (the
  generated skill is), not a test-suite generator (test-safety-net).
license: MIT
compatibility: Requires python3 >= 3.10 (stdlib only) and git; offline. The generated skill runs whatever the target app needs (a browser driver, a PTY, curl).
metadata:
  author: dhanesh
  version: "1.0.0"
  tags: "verification,runtime-evidence,feature-map,manifold,factory"
---

# Verification Skill Forge

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY in this skill are to be interpreted as described in BCP 14 (RFC 2119, RFC 8174) when, and only when, they appear in all capitals.

A change is verified when an agent has driven the running application the way a user
would and captured proof of what happened, on a named commit. This skill does not verify
anything. It **writes the skill that does**: a project-local `verify-<app>` skill inside
the target repo, read cold, mid-task, by an agent that has never seen the app. Then it
keeps that skill honest as the app changes.

**Locating this skill's helpers (do this first).** The steps below run bundled
scripts. You execute from the *target repo*, not from this skill's directory, so a
path written relative to this skill will not resolve. Resolve the base directory once and use it
everywhere — including in any subagent prompt, which MUST receive the literal absolute
path:

```sh
SKILL_DIR="<this skill's base directory>"   # your harness provides it when the skill loads
# If you don't have it, discover it:
SKILL_DIR=$(find ~/.claude ~/.config ~/.agents -type d -name 'verification-skill-forge' 2>/dev/null | head -1)
test -d "$SKILL_DIR/assets" || test -d "$SKILL_DIR/scripts"   # verify before proceeding
```

`forge` below means `python3 "$SKILL_DIR/assets/forge.py"`.

## Pick the mode

Run `forge detect --root <repo>` and do not ask the user which mode to use:

- `MODE: forge`: no verify skill exists. Go to **Forge**.
- `MODE: maintain <dir>`: one exists. Go to **Maintain**.
- `CANDIDATE:` lines and `MODE: ask` (exit 3): several exist. Ask the user which one.

In a monorepo, generate one verify skill per *runnable app*, never one per repo, at
`<app>/.claude/skills/verify-<app>/`, because launch, doctor, drive and evidence each
address a single app.

## Forge

1. **Interview the repo, not the user.** From the code, answer: the surface a user
   touches (web UI, CLI, API, mobile), how the app starts locally (the repo's own dev
   command first), how an agent can drive it (existing Playwright specs, a CLI, HTTP
   routes), what it can observe (responses, DOM, logs, rows, files), and how two
   instances can run side by side. Ask the user only what the code cannot tell you. If
   the checkout does not start, fix that or report it precisely before generating.
2. **Scaffold.** `forge scaffold --app-root <app> --app <name>` writes the skeleton with
   `FILL:` markers, the `features/` index and a copy of `assets/verify_evidence.py` in the
   generated skill's own scripts directory.
3. **Fill the five sections** with real commands and selectors read from this repo. The
   contract for each section is in `references/generated-skill.md`:
   - **Launch** takes an instance name in every command. Each instance gets its own port
     (`verify_evidence.py port`), data store and auth session, even when you only plan
     one instance, because factory-conductor runs owners side by side. State how to tell
     the instance is ready and when to stop waiting.
   - **Doctor** is one read-only check (process up, right build, port owned by this
     instance, auth valid), recorded with `verify_evidence.py doctor`.
   - **Drive** uses stable handles: ARIA labels, `data-*` attributes, route paths,
     prompt strings. You MUST NOT address the screen by coordinates or tab order, because
     both change with layout and the recipe then drives the wrong control.
   - **Evidence** carries the proof standards block from `references/generated-skill.md`
     verbatim and records each proof with `verify_evidence.py record`.
   - **Cleanup** kills what the instance started, by pid or port. It MUST NOT delete
     anything under `.verify/`, because the evidence gate reads those records after
     cleanup has run.
4. **Seed the feature map.** One file per user-facing feature, in the shape in
   `references/feature-map.md`: what it is, how to reach it, how to drive it (exact
   commands, expected output, exit codes), what proves it worked, gotchas, source anchors
   and a `proven` marker. Start with the top 3–5 features from routes, commands and
   menus.
5. **Join Manifold** when the target repo has `.manifold/`: `forge coverage <verify dir>`
   and `forge seed <verify dir>`. See **Manifold join** below.
6. **Lint and repair.** `forge lint <verify dir>` until `LINT_RESULT: PASS`. It fails
   every `FILL:` or example name left behind, a missing section, a command-less section,
   a Launch that takes no instance, a Doctor that writes, a Drive that uses coordinates,
   a reworded proof standard, a Cleanup that deletes evidence, a drifted recorder copy, a
   broken index and a dead source anchor.
7. **Prove it live.** Run the generated skill's own instructions once: launch two
   instances, doctor, drive one mapped feature, record evidence, clean up, then confirm
   the evidence still exists under `.verify/`. Set that feature's `proven` marker to the
   commit you drove it on, in a follow-up commit that touches only `features/`. You MUST NOT hand over a verify skill that was never run,
   because a recipe that was never executed teaches the next agent wrong steps.
8. **Cold run.** Start a fresh agent session that has only the repo and a feature task,
   and does not know the skill exists. It passes if it loads the verify skill on its
   own, drives the app, records evidence and adds or updates a map entry. Fold what it
   stumbled on back into the skill, and re-lint.

## Manifold join

Manifold states what the requirements were; the feature map proves they hold at runtime.
Neither says both. `forge coverage` reads `.manifold/*.json` (phase ANCHORED or later),
takes every constraint a required truth maps to, and prints `COVERED:` with the feature
files that name it in `- constraints:`, `UNCOVERED:` for the rest, and a `COVERAGE:` ratio.
Every anchored constraint SHOULD end with at least one named observable proof. The
`UNCOVERED:` list is the real verification backlog: report it rather than hiding it.
`forge seed` writes a stub feature per uncovered constraint, with `FILL:` markers that
lint refuses until you fill them from source. A malformed manifold prints
`DEGRADED: <E_PARSE|E_VALIDATE|E_LINK|E_IO|E_SCHEMA> <file>` and is skipped; with no
usable manifold the forge falls back to source-derived discovery and never aborts,
because a broken planning artifact is no reason to leave the app with no way to prove it.
Details: `references/manifold-join.md`.

## Maintain

The full pass is in `references/maintain.md`. In short:

1. **Index hygiene.** Read `features/README.md`, glob its siblings, and fix missing,
   extra, duplicate and dead entries. `forge lint` lists each one.
2. **Source wave.** Launch one read-only subagent per feature file, concurrently. Each
   answers "how does this user-facing feature work?" from source and returns: feature
   summary / source entry points / likely drift (with citations) or none / one live
   recipe. The brief MUST forbid children to drive the app or edit files, because
   concurrent drivers corrupt shared instances and concurrent edits collide.
3. **Reconcile.** Every feature file has a returned summary before you go on. Then one
   live pass, driving every feature at least once, under the generated skill's launch
   model, with doctor before the first drive and after any failure.
4. **Triage.** Wrong description or missing entry: doc drift, fix the map. Working
   behaviour the harness cannot drive: harness gap, fix the harness. Behaviour that
   contradicts the documented intent: a product bug. Record it with
   `forge finding <verify dir> --feature <id> --expected "…" --observed "…"`. You MUST
   NOT edit the feature map to match broken behaviour, because a map rewritten to match
   a bug hides that bug from every later verifier.
5. **Guard.** `forge check-maintain <verify dir> --base <ref>` fails any feature whose
   documented behaviour (What it is, Drive it, Proof) changed while none of its source
   anchors did. Then `forge lint` again.

## Deliverable

A **verify skill report**: the verify skill path, `LINT_RESULT: PASS`, the features
mapped and which are `proven` (with the commit), the live run's evidence paths, the
Manifold `COVERAGE:` line with every `UNCOVERED:` constraint listed, the cold-run result,
and, in maintain mode, the outcome (clean, changed or blocked), each drift fix and each
product-bug finding path.

## Verify

Before reporting, re-run `forge lint <verify dir>` (and `forge check-maintain` in
maintain mode) and confirm that each evidence path you name exists and that its
`evidence.json` records the commit you drove. A generated skill whose lint passes but
whose live run you did not perform is a draft: say so.

## What the evidence is worth

`verify_evidence.py` reads the commit from git, never from a flag, and pins every
artifact by sha256. That binds a record to a head and makes an after-the-fact edit
visible. It does not stop an agent with a shell from forging a record by hand, so
factory-conductor also requires that the recording verifier is not the agent that wrote
the code, and merging to the default branch stays with a human. The evidence format and
how the conductor checks it: `references/evidence.md`.

## Boundaries

- **Not the verifier.** The generated `verify-<app>` skill drives the app; this skill
  writes and maintains it.
- **Not a test generator.** Characterisation tests are test-safety-net's job, and CI
  rails are verifier-installer's. Tests passing is not what this proves.
- **Not a planner.** Which features a task has to prove comes from spec-first-planning;
  this skill only makes sure there is a way to prove them.

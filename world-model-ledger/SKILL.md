---
name: world-model-ledger
description: >-
  One-time SETUP that installs a persistent, SQLite-backed WORLD MODEL for a coding agent into
  a project (or your global ~/.claude): it records entities (symbols, files, modules, and
  real-world referents like external services/APIs/data stores), the interactions between
  them, and the constraints that should hold — and for every interaction and constraint tracks
  TWO confidence axes (observed vs normative), a validation status, and PROV-style evidence
  pointers (tests, docs, file:line, commits). The load-bearing rule: code-observed
  relationships are NOT treated as ground truth — only oracle evidence (tests/CI/docs/human)
  raises normative confidence, so the model can flag what is merely observed-but-unverified.
  Wires four lifecycle hooks: a PreToolUse hook that summarizes relevant validated / unverified
  / contradicted items for the files or symbols about to be edited; a PostToolUse hook that
  extracts touched files and updates records without inventing facts; and Stop/SessionStart
  hooks that consolidate and inject a digest. After install these run automatically — you
  invoke this skill ONCE to set up. Use when the user wants an agent to remember a codebase
  across sessions, detect contradictions, propose fixes, and improve its model's correctness
  over time; when they say "give the agent a world model", "track what's verified vs assumed",
  "persist codebase knowledge with confidence". Not a linter, LSP server, RAG vector store, or
  model-driven fact extractor.
x-spec-version: 1.0
---

# world-model-ledger

A persistent **world model** for a coding agent, backed by SQLite. It records what the agent
learns about a codebase — **entities** (symbols / files / modules / real-world referents), the
**interactions** between them, and the **constraints** that should hold — and, for every
interaction and constraint, *how sure we are it exists*, *how sure we are it is correct*,
*whether it has been validated*, and *what evidence backs it*.

The defining rule, enforced as an invariant: **code-observed relationships are not ground
truth.** A file sighting can drive `observed_conf` to 1.0 while `normative_conf` stays 0 and
status stays `unverified`. Only oracle evidence — a passing test, CI, a doc, a human — raises
normative confidence. See `references/confidence-model.md` for the epistemics and
`references/schema.md` for the data model. The design rationale is in
`docs/superpowers/specs/2026-07-01-world-model-ledger-design.md`.

## Install & operate

**Detect the install state first — before asking anything.** The skill is already installed if
`~/.claude/world-model-ledger/wm.py` exists (GLOBAL) or `./wm.py` + `./world_model.py` exist in
the project (PROJECT). Then route by how you were invoked; the full flag table is in
`references/parameters.md`.

### `--seed` / `--prune` — operate on an existing install (NO scope question, NO re-install)

If the skill is already installed and you were asked to **seed** or **prune**, do exactly that
against the current repo and **stop** — do not run the full installer and do not ask which
scope to use (that decision was already made at install time):

```bash
scripts/install.sh --seed     # build THIS repo's model (files + structural edges)
scripts/install.sh --prune    # build + soft-invalidate edges for deleted/renamed files
```

These short-circuit to `python3 "$WM/wm.py" build . [--prune]` (where `$WM` is `.` for a
project install, else `~/.claude/world-model-ledger`), create and gitignore `.world-model/`,
and report `entities_added` / `interactions_added`. They are idempotent — safe to re-run
anytime on an installed model.

### First-time install (the ONLY path that chooses scope)

Only when the skill is **not yet installed** (or the user explicitly asks to (re)install), ask
which scope, then run the installer once:

```bash
scripts/install.sh /path/to/project      # PROJECT scope — just this repo (default: cwd)
scripts/install.sh --global               # GLOBAL scope — every project, via ~/.claude
scripts/install.sh --with-constraints     # also load the optional starter constraint pack
scripts/install.sh --seed                 # install AND seed the repo in one go
```

Both modes are idempotent: they copy the core files (`world_model.py`, `wm.py`, `harvest.py`,
`test_world_model.py`) plus `hooks/`, **additively** merge the four hooks into the right
`settings.json` (existing hooks preserved), gitignore `.world-model/`, and **run the 57-test
suite as an install gate** — the guarantees are only real if those pass. Requires `python3`
(stdlib only — no pip, no network) and, for clean settings merging, `jq` (falls back to
writing `settings.hooks.json` for manual merge). After install, tell the user to **restart
Claude Code** so the hooks load. You do **not** re-invoke this skill to install afterward — the
hooks run automatically; `--seed`/`--prune` remain available anytime to (re)build the model.

## How it works — four hooks, three cadences

| Hook | Fires | Job |
|---|---|---|
| **PreToolUse** → `hooks/pretooluse.sh` | before an edit | retrieve & summarize the model's ✓validated / ?unverified / ✗contradicted items for the touched file(s)/symbol(s); inject as context. Read-only. |
| **PostToolUse** → `hooks/posttooluse.sh` | after an edit | extract the touched file(s), register them, run a cheap constraint sweep — **without inventing interactions**. Optional incremental marker harvest when `WM_INCREMENTAL=1`. |
| **Stop** → `hooks/stop.sh` | every turn | the summarization home: harvest the agent's markers from the trusted channel, consolidate (re-derive confidence + evaluate constraints), refresh `.world-model/digest.md`. |
| **SessionStart** → `hooks/session_start.sh` | session start/resume | inject the digest (open contradictions + newest unverified) so a resumed session starts aware. |

## Operating procedure — how the agent maintains the model

The update path is **hybrid**: hooks capture the skeleton deterministically; the agent
enriches it explicitly (a model never guesses facts inside a hook). Full conventions are in
`references/capture.md`.

0. **(Optional) Seed the model repo-wide.** On a fresh install, run
   `python3 wm.py build .` to register every source file and its structural edges in one
   deterministic, **language-aware** pass — this avoids the cold start where the model is empty
   until the agent has touched files. Local imports become file→file edges
   (`imports`/`includes`/`references`) and external dependencies become file→referent
   `depends_on` edges, across Python, Ruby, JavaScript/TypeScript, Go, Rust, Java, C/C++, C#,
   PHP, Kotlin, Swift, Dart, Scala, Elixir, the DevOps stack (Dockerfile, docker-compose/K8s,
   GitHub Actions, Terraform, Make, shell), and **dependency manifests** (`package.json`,
   `requirements.txt`/`pyproject.toml`, `go.mod`, `Cargo.toml`, `pom.xml`/`build.gradle`,
   `composer.json`, `Chart.yaml`, `.gitlab-ci.yml`) for precise declared dependencies.
   Everything is
   recorded as **observation only** (`observed_conf` rises; `normative_conf` stays 0,
   `unverified`) — a declared dependency is a sighting, never a correctness judgement. **Safe to
   re-run:** writes are upserts
   on stable keys, so a re-build only *adds new* files/edges or *refreshes existing* ones — it
   never duplicates or deletes rows, and never downgrades a fact you have already validated. Each
   run reports `entities_added` / `interactions_added` (both `0` on an unchanged repo). Add
   `--prune` to soft-invalidate build-origin edges for files you have since deleted or renamed
   (opt-in; never hard-deletes, and never touches an edge the agent has observed or validated).
1. **Record what you observe.** When you confirm a relationship, emit a marker line —
   `WM-OBSERVE: hash_pw uses bcrypt @ auth/hash.py:14` — or call
   `python3 wm.py observe hash_pw uses bcrypt --evidence auth/hash.py:14`. This raises
   *observed* confidence only; the fact stays `unverified` until an oracle backs it.
2. **Validate with evidence.** When a test/doc/human confirms a relationship is *correct*,
   record it: `WM-VALIDATED: hash_pw uses bcrypt by test:tests/test_auth.py::test_hash`. Only
   `test|ci|doc|human` evidence raises *normative* confidence and flips status to `validated`.
3. **Assert constraints and map referents.** Declare what should hold
   (`WM-CONSTRAINT: no-weak-hash | forbids | uses | {"patterns":["md5","sha1"]} | …`) and tie
   code to the reality it stands for (`WM-MAPS: billing/refund.py -> stripe/refunds-api`).

### Detecting contradictions and proposing fixes

The Stop hook evaluates constraints and opens `contradiction` rows with a **located,
human-readable proposed fix** (the least-entrenched member is the one to change). Review them
with `python3 wm.py contradictions --open`; the pre-call hook also surfaces them for touched
files. When you fix the code, resolve the row (`wm resolve <id> --as fixed_code`) and validate
the corrected edge — unverified/contradicted mass converts to validated mass, so the model's
normative correctness improves over time. The loop is detailed in
`references/contradiction-loop.md`.

## The invariants (do not weaken these)

1. **Code observation never raises normative confidence.** A hook may set `observed_conf` high,
   but `normative_conf` moves *only* on oracle evidence. This is the whole point — the model
   must be able to say "observed, but unverified."
2. **No invented facts in hooks.** Hooks capture only what is deterministically parseable
   (touched files, test results, explicit markers). Richer interactions come from the agent's
   own markers/CLI, never from a model summarizing inside a hook.
3. **Trusted channel only.** `tool_result` / `tool_use` content is never harvested into facts
   or evidence, so untrusted output cannot forge a marker (a regression test guards this).
4. **Append-only evidence; soft-invalidate, never hard-delete.** Superseded facts get
   `invalidated_at`; confidence is always *derived* from live evidence, so the audit trail and
   the score cannot drift apart.

Prefer these defaults; when a situation genuinely needs an exception, surface it to the user
rather than silently working around an invariant.

## Verifying after install

Always confirm the gate passed: `python3 test_world_model.py` (57 tests — the two-axis
invariant, noisy-OR derivation, soft-invalidation, contradiction detect + propose, trust
boundary, idempotent ingest, referent mapping, cycle-safe recursive-CTE traversal, repo-wide
build seeding, measurable improvement). If any fail, the
guarantees above do not hold — fix before relying on the model. Inspect live state anytime
with `python3 wm.py stats` and `cat .world-model/digest.md`.

## References

- `references/confidence-model.md` — the two-axis epistemics and the derivation formulas.
- `references/schema.md` — the SQLite data model and query patterns.
- `references/capture.md` — markers, the `wm` CLI, and the trust boundary.
- `references/contradiction-loop.md` — detect → propose → improve, and the constraint kinds.
- `references/parameters.md` — every install/operate flag and `wm` command, and when to use each.

## Assets

- `assets/world_model.py` — the store: schema, confidence derivation, contradiction detection,
  graph/FTS retrieval, and the `wm` CLI.
- `assets/wm.py` — friendly CLI entrypoint (thin wrapper).
- `assets/harvest.py` — the deterministic marker harvester (trusted channel only).
- `assets/hooks/` — the four lifecycle hook scripts.
- `assets/starter_constraints.json` — the optional starter constraint pack (off by default).
- `assets/test_world_model.py` — the 57-test install gate.
- `scripts/install.sh` — project / global installer with additive settings merge.

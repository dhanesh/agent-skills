# Interop: running context-hygiene-kit alongside world-model-ledger

Both this kit and its sibling skill `world-model-ledger` install lifecycle hooks into the
same `settings.json`. They are designed to solve different problems — this kit keeps the
**context window** lean and rot-proof (a bounded scored cache of decisions/constraints);
the world-model ledger keeps a **codebase belief store** (entities, interactions,
constraints, with observed-vs-normative confidence). Installed together they compose:
this kit protects *what the session is holding in its head*, the world model protects
*what the agent knows about the repo*. This page documents, from the actual installer and
hook code, exactly how a joint install behaves. The mirrored page, phrased from the
sibling's perspective, ships as `world-model-ledger/references/interop.md`.

## Do the settings.json hook entries coexist or clobber?

**They coexist — the merge is additive.** Both installers use the *same* `jq` deep-merge
(`scripts/install.sh` in each skill): for every hook event, the incoming entries are
concatenated onto the existing array (`(cur + add) | unique`), so a second install
preserves the first install's hook registrations. The overlapping events are `Stop` and
`SessionStart`; after both installs each of those arrays holds two entries — one per kit,
each pointing at its own hook script. Claude Code runs all entries for an event, so both
stacks fire. `PreCompact` stays exclusive to this kit; `PreToolUse`/`PostToolUse` stay
exclusive to world-model-ledger.

Two caveats, straight from the code:

1. **`jq`'s `unique` sorts the array.** Deduplication is by whole-object equality (the
   two kits' entries always differ, so nothing is lost), but entry *order* within an
   event is not preserved across a merge. Both kits' hooks are order-independent (each
   reads/writes only its own store and always exits 0), so this is harmless — but do not
   rely on hook ordering if you have other order-sensitive hooks in the same event.
2. **The no-`jq` fallback can clobber.** When `jq` is missing and a `settings.json`
   already exists, each installer writes its hooks block to `settings.hooks.json` next to
   the target settings for manual merge — the *same filename* for both kits. Running the
   second install before manually merging the first's fallback **overwrites it**. With
   `jq` unavailable, finish the manual merge after each install before running the next.

## The real conflict: two PROJECT installs into the same repo

**Do not install both skills project-scoped into the same project root.** In project
mode each installer copies its files into the project root itself, and the two kits
ship colliding filenames:

- `harvest.py` — both kits ship one (this kit's transcript→ledger harvester vs the
  world model's marker harvester). The second install overwrites the first's copy.
- `hooks/stop.sh` and `hooks/session_start.sh` — both land in `<project>/hooks/`, and
  both kits' settings entries then point at the *same* clobbered path
  (`${CLAUDE_PROJECT_DIR}/hooks/stop.sh`).

The failure is **silent**: the surviving `stop.sh` calls its own kit's `harvest.py` with
its own flags; the other kit's Stop entry runs the same script a second time, and the
overwritten kit's capture simply never happens (the hooks are best-effort `|| true`, so
no error surfaces).

**Workaround (recommended layout):** install at least one of the two with `--global` so
the file trees never share a directory:

- Both global (`scripts/install.sh --global` for each): files live in
  `~/.claude/context-hygiene/` and `~/.claude/world-model-ledger/` respectively; all
  hooks merge into `~/.claude/settings.json` with distinct absolute command paths. Safe.
- One project + one global: safe — the project install owns the project root and
  `<project>/.claude/settings.json`; the global install owns its `~/.claude/<name>/`
  dir and `~/.claude/settings.json`. Claude Code applies both settings files.

## Recommended install order

With `jq` present, **order does not matter** — the merge is additive in both directions.
Practical sequence for a fresh joint setup:

1. Pick a non-colliding layout (see above; both `--global` is the simplest).
2. Run either installer, then the other. Each runs its own test suite as an install gate
   (27 tests for this kit, 108 for the world model) — expect both green.
3. Restart Claude Code once, after the second install, so all hooks load together.

Without `jq`: after each install, manually merge the emitted `settings.hooks.json` into
the target settings **before** running the other installer (shared fallback filename —
see caveat 2 above).

## How the shared events divide the work

| Event | context-hygiene-kit | world-model-ledger |
|---|---|---|
| **Stop** (every turn) | `harvest.py`: capture `DECISION:`/`CONSTRAINT:`/`QUESTION:`/`TASK:`/`NOTE:`/`FILE:` markers, the latest user request, and `file:line` refs from the transcript into `.context/ledger.json`; refresh `.context/digest.md` | its `harvest.py`: capture `WM-OBSERVE:`/`WM-VALIDATED:`/`WM-CONSTRAINT:`/`WM-MAPS:` markers, consolidate confidence, evaluate constraints; refresh `.world-model/digest.md` |
| **SessionStart** | inject `.context/digest.md` as `additionalContext` | auto-bootstrap the model on first run, then inject `.world-model/digest.md` as `additionalContext` |

The marker namespaces are disjoint (`DECISION:` vs `WM-OBSERVE:` etc.), so a single turn
can feed both stacks without cross-talk. Both Stop hooks read the same transcript file
read-only and never block the turn.

**Yes, both inject on SessionStart.** The combined injection is bounded:

- This kit's digest is capped by `CONTEXT_HOT_BUDGET` (default **8000 tokens** — a hard
  cap enforced by `curate()`).
- The world-model digest is structurally small: stats lines plus at most 20 open
  contradictions and 15 newest unverified facts — typically well under ~1500 tokens.

**Combined context-budget guidance:** total session-start injection ≈
`CONTEXT_HOT_BUDGET` + the world-model digest. If you want the joint footprint to match
a single-stack default, export a lower budget for this kit (e.g.
`CONTEXT_HOT_BUDGET=6000`) — the ledger re-curates to any budget losslessly (evicted
cards stay in the cold tier, nothing is destroyed).

## Shared file / DB paths

None collide at runtime — the only file both installs touch is `settings.json`, and that
merge is additive (above):

| | context-hygiene-kit | world-model-ledger |
|---|---|---|
| Per-project store | `.context/` (`ledger.json`, `digest.md`, `anchor.txt`) | `.world-model/` (`model.db`, `digest.md`) |
| Global home | `~/.claude/context-hygiene/` | `~/.claude/world-model-ledger/` |
| `.gitignore` line | `.context/` | `.world-model/` |

## Verification checklist — prove both stacks are live

After a joint install (and a Claude Code restart), from the project root:

```bash
# 1. Both hook sets registered (check the settings file(s) you installed into):
jq '.hooks | map_values(length)' ~/.claude/settings.json          # global install(s)
jq '.hooks | map_values(length)' .claude/settings.json            # project install
#    Expect (across the applicable files): Stop >= 2, SessionStart >= 2,
#    PreCompact >= 1, PreToolUse >= 1, PostToolUse >= 1.

# 2. Both install gates green ($CH / $WM = each kit's home dir):
python3 "$CH/test_context_ledger.py"     # 27 tests OK
python3 "$WM/test_world_model.py"        # 79 tests OK

# 3. Both Stop hooks answer the lifecycle protocol (each must print {"continue": true}):
printf '{"cwd":"%s","transcript_path":"/dev/null"}' "$PWD" | "$CH/hooks/stop.sh"
printf '{"cwd":"%s","transcript_path":"/dev/null"}' "$PWD" | "$WM/hooks/stop.sh"

# 4. After one real turn, both per-project stores exist and both digests render:
python3 "$CH/context_ledger.py" stats --store .context/ledger.json
python3 "$WM/wm.py" stats
cat .context/digest.md .world-model/digest.md
```

If step 1 shows only one kit's entries under `Stop`/`SessionStart`, the other install's
merge did not land (usually the no-`jq` fallback) — merge its `settings.hooks.json`
manually and re-check.

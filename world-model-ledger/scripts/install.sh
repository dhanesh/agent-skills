#!/usr/bin/env bash
# Install world-model-ledger — into a single PROJECT (default) or GLOBALLY for
# every project at once.
#
# Usage:
#   scripts/install.sh [TARGET_DIR]         project install (default: current directory)
#   scripts/install.sh --global             global install into ~/.claude (all projects)
#   scripts/install.sh --with-constraints   also load the optional starter constraint pack
#   scripts/install.sh --seed               build the CURRENT repo's world model now
#   scripts/install.sh --prune              build the CURRENT repo, pruning vanished-file edges
#
# --seed / --prune are OPERATE actions: if the skill is ALREADY installed (globally or in this
# project) they just (re)build the current repo's model and exit — no re-install, no scope
# needed. Only a first-time install asks for scope. Both modes are idempotent and gate on tests.
#
#   PROJECT mode: copies the store + CLI + harvester + hooks into the project root,
#   merges the four hooks into <project>/.claude/settings.json (preserving existing
#   hooks), gitignores .world-model/, and seeds the per-project DB now.
#
#   GLOBAL mode: copies scripts + hooks into ~/.claude/world-model-ledger/, merges the
#   four hooks into ~/.claude/settings.json with ABSOLUTE paths. The model stays
#   PER-PROJECT: each project gets its own .world-model/ created on the first hook run.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$SKILL_DIR/assets"

MODE="project"
TARGET="$PWD"
WITH_CONSTRAINTS=0
SEED=0
PRUNE=0
for arg in "$@"; do
  case "$arg" in
    --global|-g) MODE="global" ;;
    --with-constraints) WITH_CONSTRAINTS=1 ;;
    --seed) SEED=1 ;;
    --prune) PRUNE=1 ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    -*) echo "unknown flag: $arg" >&2; exit 2 ;;
    *) TARGET="$arg" ;;
  esac
done

# ── Operate-only fast path ───────────────────────────────────────────────────
# If the caller only wants to seed/prune and the skill is ALREADY installed
# (project-local files, or a global ~/.claude install), just (re)build the CURRENT
# repo's model and exit — no re-copy, no settings merge, no scope decision needed.
if { [ "$SEED" = 1 ] || [ "$PRUNE" = 1 ]; } && [ "$MODE" = "project" ] && [ "$WITH_CONSTRAINTS" = 0 ]; then
  WM=""
  if [ -f "$PWD/wm.py" ] && [ -f "$PWD/world_model.py" ]; then
    WM="$PWD"
  elif [ -f "$HOME/.claude/world-model-ledger/wm.py" ]; then
    WM="$HOME/.claude/world-model-ledger"
  fi
  if [ -n "$WM" ]; then
    note=""; [ "$PRUNE" = 1 ] && note=" (with --prune)"
    echo "→ world-model-ledger already installed ($WM) — building this repo's model$note (no re-install)"
    grep -qxF '.world-model/' "$PWD/.gitignore" 2>/dev/null || \
      printf '\n# world-model-ledger runtime store\n.world-model/\n' >> "$PWD/.gitignore"
    mkdir -p "$PWD/.world-model"
    args="build ."; [ "$PRUNE" = 1 ] && args="build . --prune"
    python3 "$WM/world_model.py" --db ".world-model/model.db" $args 2>&1 \
      | grep -E '"(files_registered|edges|entities_added|interactions_added|pruned_stale_edges)"' \
      | sed 's/^/  •/' || true
    echo "✓ done (existing install; hooks already active)."
    exit 0
  fi
  # not installed anywhere → fall through to a first-time install (+ seed).
fi

if [[ "$MODE" == "global" ]]; then
  KIT_HOME="$HOME/.claude/world-model-ledger"
  SETTINGS="$HOME/.claude/settings.json"
  CMD_PREFIX="$KIT_HOME"
  echo "→ installing world-model-ledger GLOBALLY into: $KIT_HOME"
  echo "  (hooks registered in $SETTINGS; each project keeps its own .world-model/)"
else
  TARGET="$(cd "$TARGET" && pwd)"
  KIT_HOME="$TARGET"
  SETTINGS="$TARGET/.claude/settings.json"
  CMD_PREFIX='${CLAUDE_PROJECT_DIR}'
  echo "→ installing world-model-ledger into project: $TARGET"
fi

# 1. Core files + hooks at KIT_HOME (hooks resolve KIT_HOME from their own location).
mkdir -p "$KIT_HOME/hooks"
cp "$ASSETS/world_model.py" "$ASSETS/wm.py" "$ASSETS/harvest.py" \
   "$ASSETS/test_world_model.py" "$ASSETS/starter_constraints.json" "$KIT_HOME/"
cp "$ASSETS/hooks/"*.sh "$KIT_HOME/hooks/"
chmod +x "$KIT_HOME/hooks/"*.sh "$KIT_HOME/world_model.py" "$KIT_HOME/wm.py" "$KIT_HOME/harvest.py"

# 2. Merge the hooks block into SETTINGS (additive — keep existing hooks).
mkdir -p "$(dirname "$SETTINGS")"
HOOKS="$(mktemp)"
sed "s#\${CLAUDE_PROJECT_DIR}#$CMD_PREFIX#g" "$ASSETS/settings.hooks.json" > "$HOOKS"

if [[ -f "$SETTINGS" ]] && command -v jq >/dev/null 2>&1; then
  tmp="$(mktemp)"
  jq -s '
    .[0] as $cur | .[1] as $add
    | $cur
    | .hooks = (($cur.hooks // {}) as $h
        | reduce ($add.hooks | keys[]) as $evt ($h;
            .[$evt] = (((.[$evt] // []) + $add.hooks[$evt]) | unique)))
  ' "$SETTINGS" "$HOOKS" > "$tmp" 2>/dev/null && mv "$tmp" "$SETTINGS" \
    || { echo "  ! jq merge failed — wrote settings.hooks.json for manual merge"; cp "$HOOKS" "$(dirname "$SETTINGS")/settings.hooks.json"; }
  echo "  • merged hooks into $SETTINGS"
elif [[ -f "$SETTINGS" ]]; then
  cp "$HOOKS" "$(dirname "$SETTINGS")/settings.hooks.json"
  echo "  ! jq not found and settings.json exists — wrote $(dirname "$SETTINGS")/settings.hooks.json; merge it manually"
else
  cp "$HOOKS" "$SETTINGS"
  echo "  • wrote $SETTINGS"
fi
rm -f "$HOOKS"

# 3. Per-project runtime store (project mode: gitignore + seed the DB now).
if [[ "$MODE" == "project" ]]; then
  GI="$TARGET/.gitignore"
  grep -qxF '.world-model/' "$GI" 2>/dev/null || printf '\n# world-model-ledger runtime store\n.world-model/\n' >> "$GI"
  mkdir -p "$TARGET/.world-model"
  # Initialize the DB schema so the pre-call hook has something to query.
  ( cd "$TARGET" && python3 "$KIT_HOME/world_model.py" --db ".world-model/model.db" stats >/dev/null )
  if [[ "$WITH_CONSTRAINTS" == "1" ]]; then
    ( cd "$TARGET" && python3 - "$KIT_HOME" <<'PY'
import json, os, sys
sys.path.insert(0, sys.argv[1])
from world_model import WorldModel
pack = json.load(open(os.path.join(sys.argv[1], "starter_constraints.json")))
wm = WorldModel(".world-model/model.db")
for c in pack["constraints"]:
    wm.add_constraint(c["name"], c["kind"], c["message_tmpl"],
                      scope_predicate=c.get("scope_predicate"),
                      params=c.get("params"), severity=c.get("severity", "violation"))
wm.conn.commit(); wm.close()
print(f"  • loaded {len(pack['constraints'])} starter constraint(s)")
PY
    )
  fi
  if [[ "$SEED" == "1" || "$PRUNE" == "1" ]]; then
    # Repo-wide world building: register files + structural edges (observation-only).
    ba="build ."; [[ "$PRUNE" == "1" ]] && ba="build . --prune"
    ( cd "$TARGET" && python3 "$KIT_HOME/world_model.py" --db ".world-model/model.db" $ba 2>&1 \
        | grep -E '"(files_registered|edges)"' | sed 's/^/  •/' || true )
  fi
fi

# --seed under GLOBAL scope: the global block above installs no per-repo store, so seed the
# CURRENT repo explicitly (this is what the user means by "--seed"). Other projects still get
# their own .world-model/ lazily — run `build .` in each to seed them.
if [[ "$MODE" == "global" && ( "$SEED" == "1" || "$PRUNE" == "1" ) ]]; then
  REPO="$PWD"
  grep -qxF '.world-model/' "$REPO/.gitignore" 2>/dev/null || \
    printf '\n# world-model-ledger runtime store\n.world-model/\n' >> "$REPO/.gitignore"
  mkdir -p "$REPO/.world-model"
  ba="build ."; [[ "$PRUNE" == "1" ]] && ba="build . --prune"
  echo "→ seeding current repo ($REPO):"
  ( cd "$REPO" && python3 "$KIT_HOME/world_model.py" --db ".world-model/model.db" $ba 2>&1 \
      | grep -E '"(files_registered|edges)"' | sed 's/^/  •/' || true )
  echo "  (other projects start empty — run 'python3 $KIT_HOME/wm.py build .' in each to seed)"
fi

# 4. Install gate — the guarantees are only real if these pass.
echo "→ running test suite as install gate…"
( cd "$KIT_HOME" && python3 test_world_model.py 2>&1 | tail -3 )

echo "✓ done. Restart Claude Code so the new hooks load."
echo "  Record facts inline with WM-OBSERVE:/WM-VALIDATED:/WM-CONSTRAINT:/WM-MAPS: — harvested every turn."
if [[ "$MODE" == "global" ]]; then
  echo "  Each project gets its own .world-model/ automatically — add '.world-model/' to each repo's .gitignore."
fi

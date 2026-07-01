#!/usr/bin/env bash
# Install world-model-ledger — into a single PROJECT (default) or GLOBALLY for
# every project at once.
#
# Usage:
#   scripts/install.sh [TARGET_DIR]         project install (default: current directory)
#   scripts/install.sh --global             global install into ~/.claude (all projects)
#   scripts/install.sh --with-constraints   also load the optional starter constraint pack
#
# Both modes are idempotent and run the test suite as an install gate.
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
for arg in "$@"; do
  case "$arg" in
    --global|-g) MODE="global" ;;
    --with-constraints) WITH_CONSTRAINTS=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    -*) echo "unknown flag: $arg" >&2; exit 2 ;;
    *) TARGET="$arg" ;;
  esac
done

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
fi

# 4. Install gate — the guarantees are only real if these pass.
echo "→ running test suite as install gate…"
( cd "$KIT_HOME" && python3 test_world_model.py 2>&1 | tail -3 )

echo "✓ done. Restart Claude Code so the new hooks load."
echo "  Record facts inline with WM-OBSERVE:/WM-VALIDATED:/WM-CONSTRAINT:/WM-MAPS: — harvested every turn."
if [[ "$MODE" == "global" ]]; then
  echo "  Each project gets its own .world-model/ automatically — add '.world-model/' to each repo's .gitignore."
fi

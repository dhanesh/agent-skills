#!/usr/bin/env bash
# Install the context-hygiene kit — into a single PROJECT (default) or GLOBALLY for
# every project at once.
#
# Usage:
#   scripts/install.sh [TARGET_DIR]   project install (default: current directory)
#   scripts/install.sh --global       global install into ~/.claude (all projects)
#
# Both modes are idempotent and run the test suite as an install gate.
#
#   PROJECT mode: copies the ledger + harvester + hooks into the project root, merges
#   the three hooks into <project>/.claude/settings.json (preserving existing hooks),
#   gitignores the runtime cache, and seeds <project>/.context/.
#
#   GLOBAL mode: copies the scripts + hooks into ~/.claude/context-hygiene/, merges the
#   three hooks into ~/.claude/settings.json with ABSOLUTE paths, so every project is
#   covered with no per-repo setup. Memory stays PER-PROJECT: each project gets its own
#   .context/ created automatically (in the project's working dir) on the first turn.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$SKILL_DIR/assets"

# ---- parse args -------------------------------------------------------------
MODE="project"
TARGET="$PWD"
for arg in "$@"; do
  case "$arg" in
    --global|-g) MODE="global" ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    -*) echo "unknown flag: $arg" >&2; exit 2 ;;
    *) TARGET="$arg" ;;
  esac
done

# ---- resolve layout per mode ------------------------------------------------
# KIT_HOME = where the scripts + hooks/ land. SETTINGS = which settings.json to merge.
# CMD_PREFIX = what the hook command paths are rooted at in settings.json.
if [[ "$MODE" == "global" ]]; then
  KIT_HOME="$HOME/.claude/context-hygiene"
  SETTINGS="$HOME/.claude/settings.json"
  CMD_PREFIX="$KIT_HOME"                       # absolute — same for every project
  echo "→ installing context-hygiene kit GLOBALLY into: $KIT_HOME"
  echo "  (hooks registered in $SETTINGS; each project keeps its own .context/)"
else
  TARGET="$(cd "$TARGET" && pwd)"
  KIT_HOME="$TARGET"
  SETTINGS="$TARGET/.claude/settings.json"
  CMD_PREFIX='${CLAUDE_PROJECT_DIR}'           # runtime-expanded by Claude Code
  echo "→ installing context-hygiene kit into project: $TARGET"
fi

# 1. Core files + hooks at KIT_HOME (hooks resolve KIT_HOME from their own location).
mkdir -p "$KIT_HOME/hooks" "$KIT_HOME/schemas"
cp "$ASSETS/context_ledger.py" "$ASSETS/harvest.py" \
   "$ASSETS/optimize_weights.py" "$ASSETS/curate_loop.md" \
   "$ASSETS/test_context_ledger.py" "$KIT_HOME/"
cp "$ASSETS/hooks/"*.sh "$KIT_HOME/hooks/"
cp "$ASSETS/schemas/ledger.schema.json" "$KIT_HOME/schemas/"
chmod +x "$KIT_HOME/hooks/"*.sh "$KIT_HOME/harvest.py" \
   "$KIT_HOME/context_ledger.py" "$KIT_HOME/optimize_weights.py"

# 2. Build the hooks block with command paths rooted at CMD_PREFIX, then merge it into
#    SETTINGS (additive — keep any existing hooks). settings.hooks.json ships with the
#    ${CLAUDE_PROJECT_DIR} prefix; for a global install we rewrite it to absolute paths.
mkdir -p "$(dirname "$SETTINGS")"
HOOKS="$(mktemp)"
sed "s#\${CLAUDE_PROJECT_DIR}#$CMD_PREFIX#g" "$ASSETS/settings.hooks.json" > "$HOOKS"

if [[ -f "$SETTINGS" ]] && command -v jq >/dev/null 2>&1; then
  tmp="$(mktemp)"
  # Deep-merge the hooks block; concatenate hook arrays per event without dupes.
  jq -s '
    .[0] as $cur | .[1] as $add
    | $cur
    | .hooks = (($cur.hooks // {}) as $h
        | reduce ($add.hooks | keys[]) as $evt ($h;
            .[$evt] = (((.[$evt] // []) + $add.hooks[$evt]) | unique)))
  ' "$SETTINGS" "$HOOKS" > "$tmp" 2>/dev/null && mv "$tmp" "$SETTINGS" \
    || { echo "  ! jq merge failed — wrote hooks to settings.hooks.json for manual merge"; cp "$HOOKS" "$(dirname "$SETTINGS")/settings.hooks.json"; }
  echo "  • merged hooks into $SETTINGS"
elif [[ -f "$SETTINGS" ]]; then
  cp "$HOOKS" "$(dirname "$SETTINGS")/settings.hooks.json"
  echo "  ! jq not found and settings.json exists — wrote $(dirname "$SETTINGS")/settings.hooks.json; merge it manually"
else
  cp "$HOOKS" "$SETTINGS"
  echo "  • wrote $SETTINGS"
fi
rm -f "$HOOKS"

# 3. Per-project runtime cache.
#    PROJECT mode: gitignore + seed .context/ now (we know the project root).
#    GLOBAL mode:  skip — .context/ is created lazily in each project's own dir on the
#    first turn (harvest.py/context_ledger.py mkdir it), so there's nothing to seed here.
if [[ "$MODE" == "project" ]]; then
  GI="$TARGET/.gitignore"
  grep -qxF '.context/' "$GI" 2>/dev/null || printf '\n# context-hygiene runtime cache\n.context/\n' >> "$GI"
  mkdir -p "$TARGET/.context"
  [[ -f "$TARGET/.context/anchor.txt" ]] || echo "project setup" > "$TARGET/.context/anchor.txt"
fi

# 4. Install gate — the kit's guarantees are only real if these pass.
echo "→ running test suite as install gate…"
( cd "$KIT_HOME" && python3 test_context_ledger.py 2>&1 | tail -3 )

echo "✓ done. Restart Claude Code so the new hooks load."
if [[ "$MODE" == "global" ]]; then
  echo "  Every project now gets its own .context/ automatically — add '.context/' to each repo's .gitignore."
else
  echo "  Tag durable facts inline with DECISION:/CONSTRAINT:/FILE: — auto-harvested every turn."
fi

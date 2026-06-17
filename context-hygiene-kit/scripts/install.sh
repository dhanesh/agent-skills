#!/usr/bin/env bash
# Install the context-hygiene kit into a target project.
#
# Usage:  scripts/install.sh [TARGET_DIR]   (default: current directory)
#
# Idempotent. Copies the ledger + harvester + hooks into the project root, merges
# the three hooks into <target>/.claude/settings.json (preserving existing hooks),
# gitignores the runtime cache, and runs the test suite as an install gate.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$SKILL_DIR/assets"
TARGET="${1:-$PWD}"
TARGET="$(cd "$TARGET" && pwd)"

echo "→ installing context-hygiene kit into: $TARGET"

# 1. Core files at the project root (hooks invoke them from cwd).
cp "$ASSETS/context_ledger.py" "$ASSETS/harvest.py" \
   "$ASSETS/optimize_weights.py" "$ASSETS/curate_loop.md" \
   "$ASSETS/test_context_ledger.py" "$TARGET/"
mkdir -p "$TARGET/hooks" "$TARGET/schemas"
cp "$ASSETS/hooks/"*.sh "$TARGET/hooks/"
cp "$ASSETS/schemas/ledger.schema.json" "$TARGET/schemas/"
chmod +x "$TARGET/hooks/"*.sh "$TARGET/harvest.py" "$TARGET/context_ledger.py" "$TARGET/optimize_weights.py"

# 2. Merge hooks into .claude/settings.json (additive — keep any existing hooks).
mkdir -p "$TARGET/.claude"
SETTINGS="$TARGET/.claude/settings.json"
HOOKS="$ASSETS/settings.hooks.json"
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
    || { echo "  ! jq merge failed — wrote hooks to settings.hooks.json for manual merge"; cp "$HOOKS" "$TARGET/.claude/settings.hooks.json"; }
  echo "  • merged hooks into $SETTINGS"
elif [[ -f "$SETTINGS" ]]; then
  cp "$HOOKS" "$TARGET/.claude/settings.hooks.json"
  echo "  ! jq not found and settings.json exists — wrote $TARGET/.claude/settings.hooks.json; merge it manually"
else
  cp "$HOOKS" "$SETTINGS"
  echo "  • wrote $SETTINGS"
fi

# 3. Gitignore the runtime cache (regenerated every turn — never commit it).
GI="$TARGET/.gitignore"
grep -qxF '.context/' "$GI" 2>/dev/null || printf '\n# context-hygiene runtime cache\n.context/\n' >> "$GI"

# 4. Seed the anchor + an empty context dir so hooks have something to read.
mkdir -p "$TARGET/.context"
[[ -f "$TARGET/.context/anchor.txt" ]] || echo "project setup" > "$TARGET/.context/anchor.txt"

# 5. Install gate — the kit's guarantees are only real if these pass.
echo "→ running test suite as install gate…"
( cd "$TARGET" && python3 test_context_ledger.py 2>&1 | tail -3 )

echo "✓ done. Restart Claude Code in $TARGET so the new hooks load."
echo "  Tag durable facts inline with DECISION:/CONSTRAINT:/FILE: — auto-harvested every turn."

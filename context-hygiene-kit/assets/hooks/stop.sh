#!/usr/bin/env bash
# Stop hook — fires after EVERY assistant turn completes. This is the per-turn
# CAPTURE + flush that makes the ledger durable against an abrupt session close
# (crash, terminal close, kill -9) — events that SessionEnd does NOT reliably catch.
#
# It harvests durable facts from the live transcript DETERMINISTICALLY (no model,
# unlike the removed local-model summariser) and flushes the ledger + digest to disk.
# Cheap (~10-50ms of stdlib python), so it runs unthrottled every turn.
#
# Wire in .claude/settings.json:
#   "hooks": { "Stop": [ { "hooks": [
#       { "type": "command", "command": "$CLAUDE_PROJECT_DIR/hooks/stop.sh" } ] } ] }
set -uo pipefail

# Resolve where the kit's scripts live, relative to THIS hook — one level up from
# hooks/. Works for both layouts: project-root install (KIT_HOME=<project>) and
# global install (KIT_HOME=~/.claude/context-hygiene). Must run BEFORE the cd below.
KIT_HOME="$(cd "$(dirname "$0")/.." && pwd)"

# Hook input arrives as JSON on stdin: { cwd, transcript_path, session_id, ... }.
input="$(cat)"
get() { printf '%s' "$input" | python3 -c "import json,sys; print(json.load(sys.stdin).get('$1',''))" 2>/dev/null; }
cwd="$(get cwd)"
transcript="$(get transcript_path)"

[[ -z "$cwd" || ! -d "$cwd" ]] && { echo '{"continue": true}'; exit 0; }
[[ -z "$transcript" || ! -f "$transcript" ]] && { echo '{"continue": true}'; exit 0; }
# Operate in the PROJECT cwd so .context/ is per-project (auto-created on first write,
# even under a global install where the scripts live elsewhere).
cd "$cwd" || { echo '{"continue": true}'; exit 0; }

ANCHOR="$(cat .context/anchor.txt 2>/dev/null || true)"
BUDGET="${CONTEXT_HOT_BUDGET:-8000}"

# Never block the session lifecycle: best-effort, always exit 0.
python3 "$KIT_HOME/harvest.py" --transcript "$transcript" --budget "$BUDGET" --anchor "$ANCHOR" >&2 || true

echo '{"continue": true}'
exit 0

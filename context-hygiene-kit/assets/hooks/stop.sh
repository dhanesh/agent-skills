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

# Hook input arrives as JSON on stdin: { cwd, transcript_path, session_id, ... }.
input="$(cat)"
get() { printf '%s' "$input" | python3 -c "import json,sys; print(json.load(sys.stdin).get('$1',''))" 2>/dev/null; }
cwd="$(get cwd)"
transcript="$(get transcript_path)"

[[ -z "$cwd" || ! -d "$cwd" ]] && { echo '{"continue": true}'; exit 0; }
[[ -z "$transcript" || ! -f "$transcript" ]] && { echo '{"continue": true}'; exit 0; }
cd "$cwd" || { echo '{"continue": true}'; exit 0; }

ANCHOR="$(cat .context/anchor.txt 2>/dev/null || true)"
BUDGET="${CONTEXT_HOT_BUDGET:-8000}"

# Never block the session lifecycle: best-effort, always exit 0.
python3 harvest.py --transcript "$transcript" --budget "$BUDGET" --anchor "$ANCHOR" >&2 || true

echo '{"continue": true}'
exit 0

#!/usr/bin/env bash
# PreToolUse hook — fires BEFORE an edit tool runs. Retrieves and summarizes what
# the world model already believes about the file(s)/symbol(s) about to be touched,
# partitioned ✓validated / ?unverified / ✗contradicted, and injects it as context.
#
# Read-only: it never writes to the model. Deterministic query (no model).
# Wire in settings.json under hooks.PreToolUse (matcher "Edit|Write|MultiEdit").
set -uo pipefail
KIT_HOME="$(cd "$(dirname "$0")/.." && pwd)"
. "$(dirname "$0")/find_python.sh"
find_python
[ -n "$PY" ] || { echo '{"continue": true}'; exit 0; }

input="$(cat)"
get() { printf '%s' "$input" | "$PY" -c "import json,sys
try: print(json.load(sys.stdin)$1)
except Exception: print('')" 2>/dev/null; }

cwd="$(get ".get('cwd','')")"
[[ -z "$cwd" || ! -d "$cwd" ]] && { echo '{"continue": true}'; exit 0; }
cd "$cwd" || { echo '{"continue": true}'; exit 0; }
[[ -f ".world-model/model.db" ]] || { echo '{"continue": true}'; exit 0; }

# Collect touched paths from the tool input (Edit/Write .file_path; MultiEdit too).
paths="$(get ".get('tool_input',{}).get('file_path','')")"
[[ -z "$paths" ]] && { echo '{"continue": true}'; exit 0; }

summary="$(WM_DB=".world-model/model.db" "$PY" "$KIT_HOME/world_model.py" \
            --db ".world-model/model.db" precall "$paths" 2>/dev/null || true)"

if [[ -z "$summary" ]]; then
  echo '{"continue": true}'
  exit 0
fi

# Emit as additionalContext (best-effort; ignored gracefully by older clients).
"$PY" - "$summary" <<'PY'
import json, sys
ctx = sys.argv[1]
print(json.dumps({
    "continue": True,
    "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": ctx},
}))
PY
exit 0

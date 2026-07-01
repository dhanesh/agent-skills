#!/usr/bin/env bash
# PostToolUse hook — fires AFTER an edit tool runs. Deterministically registers the
# touched file(s) (the world-model skeleton), runs a cheap constraint sweep, and —
# only when WM_INCREMENTAL=1 (long runs) — harvests the agent's markers and
# re-derives confidence. It NEVER invents interactions from a diff.
#
# Wire under hooks.PostToolUse (matcher "Edit|Write|MultiEdit").
set -uo pipefail
KIT_HOME="$(cd "$(dirname "$0")/.." && pwd)"

input="$(cat)"
get() { printf '%s' "$input" | python3 -c "import json,sys
try: print(json.load(sys.stdin)$1)
except Exception: print('')" 2>/dev/null; }

cwd="$(get ".get('cwd','')")"
transcript="$(get ".get('transcript_path','')")"
[[ -z "$cwd" || ! -d "$cwd" ]] && { echo '{"continue": true}'; exit 0; }
cd "$cwd" || { echo '{"continue": true}'; exit 0; }

path="$(get ".get('tool_input',{}).get('file_path','')")"
args=(--db ".world-model/model.db" --mode post)
[[ -n "$path" ]] && args+=(--touched-file "$path")
[[ -n "$transcript" && -f "$transcript" ]] && args+=(--transcript "$transcript")

python3 "$KIT_HOME/harvest.py" "${args[@]}" >&2 || true
echo '{"continue": true}'
exit 0

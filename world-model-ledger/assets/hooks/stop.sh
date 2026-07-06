#!/usr/bin/env bash
# Stop hook — fires after EVERY assistant turn. This is the summarization home:
# harvest the agent's world-model markers from the trusted channel, consolidate
# (re-derive confidence + evaluate constraints), and refresh .world-model/digest.md.
# Deterministic, best-effort, never blocks the turn.
#
# Wire under hooks.Stop.
set -uo pipefail
KIT_HOME="$(cd "$(dirname "$0")/.." && pwd)"

input="$(cat)"
get() { printf '%s' "$input" | python3 -c "import json,sys
try: print(json.load(sys.stdin).get('$1',''))
except Exception: print('')" 2>/dev/null; }

cwd="$(get cwd)"
transcript="$(get transcript_path)"
[[ -z "$cwd" || ! -d "$cwd" ]] && { echo '{"continue": true}'; exit 0; }
cd "$cwd" || { echo '{"continue": true}'; exit 0; }
[[ -z "$transcript" || ! -f "$transcript" ]] && { echo '{"continue": true}'; exit 0; }

python3 "$KIT_HOME/harvest.py" --db ".world-model/model.db" --transcript "$transcript" --mode stop >&2 || true
echo '{"continue": true}'
exit 0

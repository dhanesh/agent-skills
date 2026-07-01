#!/usr/bin/env bash
# SessionStart hook — injects the world-model digest (open contradictions + newest
# unverified items) so a resumed session starts already aware of what is contradicted
# and what is merely observed-but-unverified. Read-only.
#
# Wire under hooks.SessionStart.
set -uo pipefail

input="$(cat)"
get() { printf '%s' "$input" | python3 -c "import json,sys
try: print(json.load(sys.stdin).get('$1',''))
except Exception: print('')" 2>/dev/null; }

cwd="$(get cwd)"
[[ -z "$cwd" || ! -d "$cwd" ]] && { echo '{"continue": true}'; exit 0; }
cd "$cwd" || { echo '{"continue": true}'; exit 0; }

digest=".world-model/digest.md"
[[ -f "$digest" ]] || { echo '{"continue": true}'; exit 0; }

python3 - "$digest" <<'PY'
import json, sys
try:
    ctx = open(sys.argv[1]).read()
except Exception:
    ctx = ""
if ctx.strip():
    print(json.dumps({"continue": True,
                      "hookSpecificOutput": {"hookEventName": "SessionStart",
                                             "additionalContext": ctx}}))
else:
    print(json.dumps({"continue": True}))
PY
exit 0

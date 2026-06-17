#!/usr/bin/env bash
# SessionStart hook — fires when a session starts OR resumes after compaction.
# Loads the durable digest (O(budget) ranked facts) as additionalContext INSTEAD
# of the agent re-deriving state from raw history. This is the anti-BLOAT load path.
#
# Wire in settings.json:
#   "hooks": { "SessionStart": [ { "hooks": [
#       { "type": "command", "command": "$CLAUDE_PROJECT_DIR/hooks/session_start.sh" } ] } ] }
set -euo pipefail
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/..}"

ANCHOR="$(cat .context/anchor.txt 2>/dev/null || true)"
BUDGET="${CONTEXT_HOT_BUDGET:-8000}"

# Rebuild the digest fresh so re-rank reflects the current anchor, then emit it.
python3 context_ledger.py curate --budget "$BUDGET" --anchor "$ANCHOR" >/dev/null 2>&1 || true
DIGEST="$(cat .context/digest.md 2>/dev/null || echo '(no digest yet)')"

# additionalContext is injected as trusted scaffold; the digest itself already
# fences untrusted card content in <data> blocks (two-channel boundary, LSC-7).
python3 - "$DIGEST" <<'PY'
import json, sys
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": sys.argv[1],
    }
}))
PY

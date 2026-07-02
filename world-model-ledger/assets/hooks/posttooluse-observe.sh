#!/usr/bin/env bash
# PostToolUse[*] hook — the UNIVERSAL capture channel. Fires after EVERY tool call and
# registers whatever that call reveals about the world, from the tool INPUT only
# (trusted, agent-authored — never the output):
#   - Bash                          → runtime executes/reads edges + verifier oracle
#   - Edit/Write/Read/Grep/Glob/Notebook/MCP/… naming a repo file → that file as an entity
#   - WebFetch/WebSearch url         → an external `referent` the agent consulted
# This is the single observer for the whole tool stream — no per-tool hooks to keep in
# sync. Marker harvest + consolidation stay in the Stop hook (once per turn).
# Zero markers, zero env, zero config. Best-effort, never blocks the turn.
#
# Wire under hooks.PostToolUse (matcher "*").
set -uo pipefail
KIT_HOME="$(cd "$(dirname "$0")/.." && pwd)"

input="$(cat)"
cwd="$(printf '%s' "$input" | python3 -c "import json,sys
try: print((json.load(sys.stdin) or {}).get('cwd',''))
except Exception: print('')" 2>/dev/null)"

[[ -z "$cwd" || ! -d "$cwd" ]] && { echo '{"continue": true}'; exit 0; }
cd "$cwd" || { echo '{"continue": true}'; exit 0; }

# Lazy bootstrap: if this is a real project (a .git tree) with no model yet, create and
# seed it now so capture starts on the very first tool call — no restart required.
if [[ ! -f ".world-model/model.db" ]]; then
  [[ -d ".git" ]] || { echo '{"continue": true}'; exit 0; }
  mkdir -p .world-model
  grep -qxF '.world-model/' .gitignore 2>/dev/null \
    || printf '\n# world-model-ledger runtime store\n.world-model/\n' >> .gitignore 2>/dev/null || true
  python3 "$KIT_HOME/world_model.py" --db ".world-model/model.db" bootstrap . >/dev/null 2>&1 || true
fi

printf '%s' "$input" | python3 "$KIT_HOME/world_model.py" \
  --db ".world-model/model.db" observe-tool --from-hook --digest ".world-model/digest.md" >&2 || true

echo '{"continue": true}'
exit 0

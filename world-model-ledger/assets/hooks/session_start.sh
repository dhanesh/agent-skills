#!/usr/bin/env bash
# SessionStart hook — TWO jobs:
#  1. AUTO-BOOTSTRAP: if this project has no .world-model yet, create it and seed the
#     model from the repo — zero manual steps, no `install --seed`, no env, no markers.
#  2. Inject the world-model digest (open contradictions + newest unverified) so a
#     resumed session starts already aware of what is contradicted vs merely observed.
# Read-only w.r.t. the repo; best-effort; never blocks the session.
#
# Wire under hooks.SessionStart.
set -uo pipefail
KIT_HOME="$(cd "$(dirname "$0")/.." && pwd)"

input="$(cat)"
get() { printf '%s' "$input" | python3 -c "import json,sys
try: print(json.load(sys.stdin).get('$1',''))
except Exception: print('')" 2>/dev/null; }

cwd="$(get cwd)"
[[ -z "$cwd" || ! -d "$cwd" ]] && { echo '{"continue": true}'; exit 0; }
cd "$cwd" || { echo '{"continue": true}'; exit 0; }

# 1. Auto-bootstrap — only in a real project (a .git tree), so we never litter
#    .world-model/ in scratch dirs. The guard tests STATE, not file existence:
#    the installer creates model.db to init the schema, so `! -f model.db` was
#    already false before the first session and seeding never ran. `bootstrap`
#    short-circuits on a non-empty entity table, so calling it every time is
#    idempotent, cheap, and also re-seeds a model left empty by an interrupted run.
if [[ -d ".git" ]]; then
  mkdir -p .world-model
  grep -qxF '.world-model/' .gitignore 2>/dev/null \
    || printf '\n# world-model-ledger runtime store\n.world-model/\n' >> .gitignore 2>/dev/null || true
  python3 "$KIT_HOME/world_model.py" --db ".world-model/model.db" bootstrap . >&2 2>/dev/null || true
fi

digest=".world-model/digest.md"
# Refresh the digest so a freshly-bootstrapped model still injects something useful.
[[ -f ".world-model/model.db" && ! -f "$digest" ]] && \
  python3 "$KIT_HOME/world_model.py" --db ".world-model/model.db" digest > "$digest" 2>/dev/null || true
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

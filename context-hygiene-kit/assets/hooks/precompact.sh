#!/usr/bin/env bash
# PreCompact hook — fires right BEFORE Claude Code compacts the context window.
# This is the anti-ROT trigger: refresh the lossless digest from live ledger state
# so high-salience cards (decisions, constraints, file refs) are preserved verbatim
# on disk *before* the lossy summarizer runs.
#
# Wire in settings.json:
#   "hooks": { "PreCompact": [ { "hooks": [
#       { "type": "command", "command": "$CLAUDE_PROJECT_DIR/hooks/precompact.sh" } ] } ] }
set -euo pipefail
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/..}"

# The current task anchor drives relevance re-ranking. Keep it in a 1-line file the
# agent updates as focus shifts; fall back to empty (recency/frequency still apply).
ANCHOR="$(cat .context/anchor.txt 2>/dev/null || true)"
BUDGET="${CONTEXT_HOT_BUDGET:-8000}"

python3 context_ledger.py curate --budget "$BUDGET" --anchor "$ANCHOR" >&2 || true

# Hooks may return JSON on stdout; we don't need to block compaction, just log.
echo '{"continue": true}'

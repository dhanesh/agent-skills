#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
TMUX_AGENT_DIR="${HOME}/.tmux/agent-panes"
TMUX_CONF="${HOME}/.tmux.conf"

mkdir -p "$BIN_DIR" "$TMUX_AGENT_DIR/panes"

for f in agent-pane agent-status-scan agent-status-summary agent-jump agent-dashboard agent-menu agent-workspace agent-cheatsheet; do
  cp "$SKILL_DIR/scripts/$f" "$BIN_DIR/$f"
  chmod +x "$BIN_DIR/$f"
  echo "installed $BIN_DIR/$f"
done

# Library module imported by agent-status-scan (must sit alongside it on PATH).
cp "$SKILL_DIR/scripts/agent_classify.py" "$BIN_DIR/agent_classify.py"
echo "installed $BIN_DIR/agent_classify.py"

cp "$SKILL_DIR/assets/tmux-agent.conf" "$TMUX_AGENT_DIR/tmux-agent.conf"
echo "installed $TMUX_AGENT_DIR/tmux-agent.conf"

SOURCE_LINE="source-file $TMUX_AGENT_DIR/tmux-agent.conf"
if [[ -f "$TMUX_CONF" ]] && grep -Fxq "$SOURCE_LINE" "$TMUX_CONF"; then
  echo "tmux config already sources tmux-agent.conf"
else
  printf '
# Tmux Agent Herdr-Lite
%s
' "$SOURCE_LINE" >> "$TMUX_CONF"
  echo "added source line to $TMUX_CONF"
fi

if command -v tmux >/dev/null 2>&1; then
  tmux source-file "$TMUX_CONF" 2>/dev/null || true
fi

echo "done. Start with: agent-workspace"

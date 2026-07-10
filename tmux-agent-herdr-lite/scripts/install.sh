#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
TMUX_AGENT_DIR="${HOME}/.tmux/agent-panes"
TMUX_CONF="${HOME}/.tmux.conf"

mkdir -p "$BIN_DIR" "$TMUX_AGENT_DIR/panes"

for f in agent-pane agent-status-scan agent-status-summary agent-jump agent-dashboard \
         agent-menu agent-workspace agent-cheatsheet agent-list agent-read agent-send \
         agent-run agent-wait agent-explain agent-notify agent-resume agent-worktree; do
  cp "$SKILL_DIR/scripts/$f" "$BIN_DIR/$f"
  chmod +x "$BIN_DIR/$f"
  echo "installed $BIN_DIR/$f"
done

# Library modules imported by the scripts (must sit alongside them on PATH).
for m in agent_classify.py agent_registry.py; do
  cp "$SKILL_DIR/scripts/$m" "$BIN_DIR/$m"
  echo "installed $BIN_DIR/$m"
done

cp "$SKILL_DIR/assets/tmux-agent.conf" "$TMUX_AGENT_DIR/tmux-agent.conf"
echo "installed $TMUX_AGENT_DIR/tmux-agent.conf"

# Optional persistence layer: only wired up when TPM is already installed,
# so a plain install needs no network. See assets/tmux-agent-persistence.conf.
cp "$SKILL_DIR/assets/tmux-agent-persistence.conf" "$TMUX_AGENT_DIR/tmux-agent-persistence.conf"
echo "installed $TMUX_AGENT_DIR/tmux-agent-persistence.conf"
if [[ -d "$HOME/.tmux/plugins/tpm" ]]; then
  PERSIST_LINE="source-file $TMUX_AGENT_DIR/tmux-agent-persistence.conf"
  if [[ -f "$TMUX_CONF" ]] && grep -Fxq "$PERSIST_LINE" "$TMUX_CONF"; then
    echo "tmux config already sources tmux-agent-persistence.conf"
  elif [[ -f "$TMUX_CONF" ]] && grep -Eq "run(-shell)? .*tpm/tpm" "$TMUX_CONF"; then
    # @plugin declarations must precede the tpm run line, so insert above it.
    awk -v line="$PERSIST_LINE" '
      !done && $0 ~ /run(-shell)? .*tpm\/tpm/ { print line; done=1 }
      { print }' "$TMUX_CONF" > "$TMUX_CONF.tmp" && mv "$TMUX_CONF.tmp" "$TMUX_CONF"
    echo "inserted persistence source line before tpm run in $TMUX_CONF (press prefix+I to install plugins)"
  else
    printf '%s\n' "$PERSIST_LINE" >> "$TMUX_CONF"
    echo "added persistence source line to $TMUX_CONF (press prefix+I to install plugins)"
  fi
else
  echo "TPM not found; skipping session-persistence plugins (tmux-resurrect/continuum)."
  echo "To enable later: git clone https://github.com/tmux-plugins/tpm ~/.tmux/plugins/tpm,"
  echo "add: run '~/.tmux/plugins/tpm/tpm' to ~/.tmux.conf, rerun this installer."
fi

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

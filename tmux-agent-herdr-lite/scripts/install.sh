#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
TMUX_AGENT_DIR="${HOME}/.tmux/agent-panes"
TMUX_CONF="${HOME}/.tmux.conf"

# Wire a source-file line into ~/.tmux.conf. TPM's `run .../tpm/tpm` line must
# stay LAST (it re-applies plugin keybindings like prefix+I), so our lines are
# inserted before it — and an existing line found after it is moved up, which
# repairs configs written by earlier versions of this installer.
ensure_sourced_before_tpm() {
  local line="$1"
  touch "$TMUX_CONF"
  if grep -Fxq "$line" "$TMUX_CONF"; then
    local line_no tpm_no
    line_no="$(grep -Fxn "$line" "$TMUX_CONF" | head -1 | cut -d: -f1)"
    tpm_no="$(grep -En "run(-shell)? .*tpm/tpm" "$TMUX_CONF" | head -1 | cut -d: -f1 || true)"
    if [[ -z "$tpm_no" || "$line_no" -lt "$tpm_no" ]]; then
      echo "tmux config already sources $(basename "$line")"
      return
    fi
    grep -Fxv "$line" "$TMUX_CONF" > "$TMUX_CONF.tmp" && mv "$TMUX_CONF.tmp" "$TMUX_CONF"
    echo "moving existing source line above the TPM run line in $TMUX_CONF"
  fi
  if grep -Eq "run(-shell)? .*tpm/tpm" "$TMUX_CONF"; then
    awk -v line="$line" '
      !done && $0 ~ /run(-shell)? .*tpm\/tpm/ { print line; done=1 }
      { print }' "$TMUX_CONF" > "$TMUX_CONF.tmp" && mv "$TMUX_CONF.tmp" "$TMUX_CONF"
    echo "inserted before TPM run line: $line"
  else
    printf '%s\n' "$line" >> "$TMUX_CONF"
    echo "appended: $line"
  fi
}

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
ensure_sourced_before_tpm "source-file $TMUX_AGENT_DIR/tmux-agent.conf"

# Optional persistence layer: only wired up when TPM is already installed,
# so a plain install needs no network. See assets/tmux-agent-persistence.conf.
cp "$SKILL_DIR/assets/tmux-agent-persistence.conf" "$TMUX_AGENT_DIR/tmux-agent-persistence.conf"
echo "installed $TMUX_AGENT_DIR/tmux-agent-persistence.conf"
if [[ -d "$HOME/.tmux/plugins/tpm" ]]; then
  ensure_sourced_before_tpm "source-file $TMUX_AGENT_DIR/tmux-agent-persistence.conf"
  echo "persistence layer wired (press prefix+I inside tmux to install the plugins)"
else
  echo "TPM not found; skipping session-persistence plugins (tmux-resurrect/continuum)."
  echo "To enable later: git clone https://github.com/tmux-plugins/tpm ~/.tmux/plugins/tpm,"
  echo "add: run '~/.tmux/plugins/tpm/tpm' to ~/.tmux.conf, rerun this installer."
fi

if command -v tmux >/dev/null 2>&1; then
  tmux source-file "$TMUX_CONF" 2>/dev/null || true
fi

echo "done. Start with: agent-workspace"

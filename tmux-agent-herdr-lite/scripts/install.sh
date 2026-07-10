#!/usr/bin/env bash
set -euo pipefail

# Idempotent installer — safe (and intended) to run on every skill
# invocation. It copies nothing onto PATH: the generated tmux config points
# straight at this skill's scripts/ directory, so the human surface is
# entirely tmux keybindings, the prefix+m menu, and the status bar, and skill
# updates take effect on the next config reload.

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_BIN="$SKILL_DIR/scripts"
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

mkdir -p "$TMUX_AGENT_DIR/panes"
chmod +x "$AGENT_BIN"/agent-* 2>/dev/null || true

# Retire copies made by older versions of this installer so stale scripts on
# PATH can't shadow the in-place ones the config now references.
for f in "$HOME/.local/bin"/agent-pane "$HOME/.local/bin"/agent-status-scan; do
  if [[ -f "$f" ]] && grep -q 'Herdr-lite' "$f" 2>/dev/null; then
    echo "note: $HOME/.local/bin contains copies from an older install; they are no longer used."
    break
  fi
done

# Generate the tmux config with this skill's script directory baked in.
sed "s|@AGENT_BIN@|$AGENT_BIN|g" "$SKILL_DIR/assets/tmux-agent.conf" \
  > "$TMUX_AGENT_DIR/tmux-agent.conf"
echo "generated $TMUX_AGENT_DIR/tmux-agent.conf (scripts referenced in place from $AGENT_BIN)"
ensure_sourced_before_tpm "source-file $TMUX_AGENT_DIR/tmux-agent.conf"

# Optional persistence layer: only wired up when TPM is already installed,
# so a plain install needs no network. See assets/tmux-agent-persistence.conf.
cp "$SKILL_DIR/assets/tmux-agent-persistence.conf" "$TMUX_AGENT_DIR/tmux-agent-persistence.conf"
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

echo "done. Inside tmux: prefix ? = dashboard, prefix m = menu (launch agents, worktrees, resume)."

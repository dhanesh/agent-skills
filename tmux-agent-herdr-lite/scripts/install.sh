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
  local line="$1" target base base_re
  target="${line#source-file }"           # path portion of the source-file line
  base="$(basename "$target")"            # e.g. tmux-agent.conf
  base_re="${base//./\\.}"                # escape dots for the ERE below
  touch "$TMUX_CONF"
  # Remove ANY existing source-file line for this config, regardless of how the
  # path was written (~/… vs absolute) or which installer version wrote it. The
  # old exact-line match missed tilde/absolute variants and stacked duplicates.
  if grep -Eq "^[[:space:]]*source-file[[:space:]].*${base_re}([[:space:]]|$)" "$TMUX_CONF"; then
    grep -Ev "^[[:space:]]*source-file[[:space:]].*${base_re}([[:space:]]|$)" "$TMUX_CONF" \
      > "$TMUX_CONF.tmp" && mv "$TMUX_CONF.tmp" "$TMUX_CONF"
  fi
  # Re-insert the canonical line immediately before TPM's run line (TPM must stay
  # LAST so it re-applies plugin keybindings), or append when TPM isn't used.
  if grep -Eq "run(-shell)? .*tpm/tpm" "$TMUX_CONF"; then
    awk -v line="$line" '
      !ins && $0 ~ /run(-shell)? .*tpm\/tpm/ { print line; ins=1 }
      { print }' "$TMUX_CONF" > "$TMUX_CONF.tmp" && mv "$TMUX_CONF.tmp" "$TMUX_CONF"
    echo "sourced $base before the TPM run line"
  else
    printf '%s\n' "$line" >> "$TMUX_CONF"
    echo "appended source line for $base"
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

# --- Shell hook: auto-track agents launched in ANY pane (zero-command cockpit) ---
# Generates a zsh hook and sources it from ~/.zshrc, so running `claude`/`codex`/…
# in any pane registers that pane automatically (and deregisters on exit). No
# agent-pane, no commands for the human to learn.
AGENT_NAMES="$(python3 "$AGENT_BIN/agent-observe" agents 2>/dev/null || true)"
HOOK="$TMUX_AGENT_DIR/agent-shell-hook.zsh"
# NB: AGENT_NAMES is a '|'-joined list, so it needs a delimiter other than '|'.
sed -e "s|@AGENT_BIN@|$AGENT_BIN|g" \
    -e "s|@AGENT_ROOT@|$TMUX_AGENT_DIR|g" \
    -e "s#@AGENT_NAMES@#$AGENT_NAMES#g" \
    "$SKILL_DIR/assets/agent-shell-hook.zsh" > "$HOOK"
echo "generated $HOOK"
ZSHRC="${ZDOTDIR:-$HOME}/.zshrc"
touch "$ZSHRC"
# Drop any prior source line (path may have changed) and re-append the current one.
if grep -Fq "agent-shell-hook.zsh" "$ZSHRC"; then
  grep -Fv "agent-shell-hook.zsh" "$ZSHRC" > "$ZSHRC.tmp" && mv "$ZSHRC.tmp" "$ZSHRC"
fi
printf '\nsource %s  # Tmux Agent Herdr-Lite: auto-track agents in any pane\n' "$HOOK" >> "$ZSHRC"
echo "sourced agent-shell-hook.zsh from $ZSHRC (new shells pick it up; or run: source $ZSHRC)"

if command -v tmux >/dev/null 2>&1; then
  tmux source-file "$TMUX_CONF" 2>/dev/null || true
fi

echo "done. Just run your agent (claude/codex/…) in any pane — it's tracked automatically."
echo "Cockpit keys: prefix a, then d = dashboard, m = menu, b/e/w/i/f = jump by state."

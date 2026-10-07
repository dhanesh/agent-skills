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
    # `|| true`: grep -Ev exits 1 when it filters EVERY line, which is precisely
    # the state after a first install (the file holds only our line). Without
    # this the `&&` short-circuited, `mv` never ran, `set -e` did not fire
    # because it was not the final command of the list — and the config grew by
    # one duplicate line on every skill invocation, leaving a .tmp in $HOME.
    grep -Ev "^[[:space:]]*source-file[[:space:]].*${base_re}([[:space:]]|$)" "$TMUX_CONF" \
      > "$TMUX_CONF.tmp" || true
    mv "$TMUX_CONF.tmp" "$TMUX_CONF"
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

# Drop our prior `source …/<file>` line from ~/.zshrc (its path may have
# changed). Anchored on `source …/<file>` so a user's own comment or guarded
# source of the same file survives. Written back through `cat >` rather than
# mv so a symlinked ~/.zshrc (stow/chezmoi/yadm) stays a symlink and keeps its
# mode. grep exits 1 when it filters every line (fine) and 2 on a read error,
# which must not replace ~/.zshrc with a partial file.
drop_zshrc_line() {
  local re="^[[:space:]]*source[[:space:]].*/${1//./\\.}([[:space:]]|$)" rc=0
  grep -Eq "$re" "$ZSHRC" || return 0
  grep -Ev "$re" "$ZSHRC" > "$ZSHRC.tmp" || rc=$?
  if (( rc > 1 )); then
    rm -f "$ZSHRC.tmp"
    echo "error: could not read $ZSHRC; left it unchanged" >&2
    return 1
  fi
  cat "$ZSHRC.tmp" > "$ZSHRC"
  rm -f "$ZSHRC.tmp"
}

drop_zshrc_line "agent-shell-hook.zsh"
printf '\nsource %s  # Tmux Agent Herdr-Lite: auto-track agents in any pane\n' "$HOOK" >> "$ZSHRC"
echo "sourced agent-shell-hook.zsh from $ZSHRC (new shells pick it up; or run: source $ZSHRC)"

# --- Optional convenience functions: tm, tp, tv, tn, tms, tw, twd ---
# The choice persists: the skill reruns this installer on every invocation
# with no env, so AGENT_SHELL_FUNCS=off writes a marker that later runs honour
# until AGENT_SHELL_FUNCS=on removes it.
FUNCS="$TMUX_AGENT_DIR/tmux-shell-functions.zsh"
FUNCS_OFF="$TMUX_AGENT_DIR/shell-funcs.off"
case "${AGENT_SHELL_FUNCS:-}" in
  off) touch "$FUNCS_OFF" ;;
  on)  rm -f "$FUNCS_OFF" ;;
esac
drop_zshrc_line "tmux-shell-functions.zsh"
if [[ -f "$FUNCS_OFF" ]]; then
  rm -f "$FUNCS"
  echo "convenience shell functions off (re-enable: AGENT_SHELL_FUNCS=on bash $0)"
else
  cp "$SKILL_DIR/assets/tmux-shell-functions.zsh" "$FUNCS"
  printf 'source %s  # Tmux Agent Herdr-Lite: tm/tp/tv/tn/tms/tw/twd\n' "$FUNCS" >> "$ZSHRC"
  echo "sourced tmux-shell-functions.zsh from $ZSHRC (tm, tp, tv, tn, tms, tw, twd; names you already define are left alone)"
fi

# --- Claude Code status hooks: exact working/blocked/done instead of guessing ---
# Merged into Claude's settings.json next to any hooks you already have; only
# entries marked "# tmux-agent-herdr-lite" are ever replaced or removed. Opt
# out with AGENT_CLAUDE_HOOKS=off (persists until AGENT_CLAUDE_HOOKS=on).
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
HOOKS_OFF="$TMUX_AGENT_DIR/claude-hooks.off"
case "${AGENT_CLAUDE_HOOKS:-}" in
  off) touch "$HOOKS_OFF" ;;
  on)  rm -f "$HOOKS_OFF" ;;
esac
chmod +x "$AGENT_BIN/agent-hook" 2>/dev/null || true
if [[ ! -d "$CLAUDE_DIR" ]]; then
  echo "Claude Code config dir not found ($CLAUDE_DIR); skipping status hooks"
elif [[ -f "$HOOKS_OFF" ]]; then
  python3 "$AGENT_BIN/agent-hook" install-claude "$CLAUDE_DIR/settings.json" --remove \
    && echo "Claude status hooks off (re-enable: AGENT_CLAUDE_HOOKS=on bash $0)" \
    || echo "warning: could not update $CLAUDE_DIR/settings.json" >&2
else
  python3 "$AGENT_BIN/agent-hook" install-claude "$CLAUDE_DIR/settings.json" \
    && echo "Claude status hooks installed in $CLAUDE_DIR/settings.json (restart Claude sessions to pick them up)" \
    || echo "warning: could not update $CLAUDE_DIR/settings.json; status falls back to screen scraping" >&2
fi

if command -v tmux >/dev/null 2>&1; then
  tmux source-file "$TMUX_CONF" 2>/dev/null || true
fi

echo "done. Just run your agent (claude/codex/…) in any pane — it's tracked automatically."
echo "Cockpit keys: prefix a, then d = dashboard, m = menu, b/e/w/i/f = jump by state."

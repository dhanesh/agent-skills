# Tmux Agent Herdr-Lite — auto-track agents launched in any tmux pane.
# Sourced from ~/.zshrc by the installer. Active only inside tmux. This is what
# makes the cockpit zero-command: run `claude` / `codex` / … in ANY pane and it
# shows up in the fleet automatically; quit it and it disappears. No launcher.

_agent_herdr_bin='@AGENT_BIN@/agent-observe'
_agent_herdr_root='@AGENT_ROOT@'
# '|'-joined known agent binary names, kept in sync with agent_classify at install.
_agent_herdr_agents='@AGENT_NAMES@'

# preexec: fires with the line as typed ($1) and with aliases expanded ($3).
# Checking both catches an alias like `ccl` -> `claude --dangerously-…`, whose
# typed first word is not an agent name. Cheap shell-side gate first, then hand
# off to the python helper in the background so the prompt never blocks.
_agent_herdr_preexec() {
  [[ -n "$TMUX_PANE" && -n "$_agent_herdr_agents" ]] || return
  local line
  for line in "$1" "$3"; do
    [[ "${${(z)line}[1]:t}" == (${~_agent_herdr_agents}) ]] || continue
    command "$_agent_herdr_bin" register "$TMUX_PANE" "$line" >/dev/null 2>&1 &!
    return
  done
}

# precmd: fires when control returns to the prompt (i.e. an agent just exited).
# Only spawn the helper when this pane actually has an entry to drop.
_agent_herdr_precmd() {
  [[ -n "$TMUX_PANE" ]] || return
  [[ -f "$_agent_herdr_root/panes/${TMUX_PANE#%}.json" ]] || return
  command "$_agent_herdr_bin" finished "$TMUX_PANE" >/dev/null 2>&1 &!
}

autoload -Uz add-zsh-hook 2>/dev/null
if whence add-zsh-hook >/dev/null 2>&1; then
  add-zsh-hook preexec _agent_herdr_preexec
  add-zsh-hook precmd  _agent_herdr_precmd
fi

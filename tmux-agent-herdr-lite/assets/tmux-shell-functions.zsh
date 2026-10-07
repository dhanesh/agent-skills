# Tmux Agent Herdr-Lite — optional convenience shell functions (zsh).
# Sourced from ~/.zshrc by the installer (opt out: AGENT_SHELL_FUNCS=off, which sticks).
#
#   tm              attach/create the session named after the current directory
#   tp [name]       fzf session picker (attached first); with a name, attach/create it
#   tv [file]       cwd session that starts in nvim (optionally on <file>)
#   tn <name>       attach/create a session with an explicit name
#   tms [name]      remote session manager over SSH (host from $TMS_HOST)
#   tw <branch>     git worktree at ../<repo>-<branch> + a session for it
#   twd [-f]        remove the current linked worktree and its session
#
# Every opener works both outside tmux (attach) and inside it (switch-client),
# so none of them nest a tmux inside a tmux pane.
#
# The bodies live in _tmh_* functions; the short names are bound at the bottom
# only when nothing else already owns them (your own function, an alias, or a
# command such as the `tv` fuzzy finder), so this file never overrides you.

# tmux forbids '.' and ':' in session names; map both to '_'.
_tmh_name() {
  local n="${1:-${PWD:t}}"
  print -r -- "${${n:-root}//[.:]/_}"
}

# Set the terminal title (Ghostty/iTerm2/most xterm-likes honour OSC 0).
_tmh_title() { printf '\033]0;%s\007' "$1"; }

# _tmh_open <session> [shell-command] — attach to <session>, creating it first
# (running [shell-command] in it) when it does not exist.
_tmh_open() {
  local name="$1"; shift
  _tmh_title "💻 $name"
  if ! tmux has-session -t "=$name" 2>/dev/null; then
    if [[ -n "$TMUX" ]]; then
      tmux new-session -d -s "$name" "$@" || return
    else
      tmux new-session -s "$name" "$@"
      return
    fi
  fi
  if [[ -n "$TMUX" ]]; then
    tmux switch-client -t "=$name"
  else
    tmux attach-session -t "=$name"
  fi
}

_tmh_tm() { _tmh_open "$(_tmh_name)"; }

_tmh_tn() {
  if [[ $# -eq 0 ]]; then
    echo "Usage: tn <session-name>" >&2
    return 1
  fi
  _tmh_open "$(_tmh_name "$1")"
}

# An existing session is attached as-is; [file] only applies when creating it.
_tmh_tv() {
  local cmd="nvim"
  [[ -n "${1:-}" ]] && cmd="nvim ${(q)1}"
  _tmh_open "$(_tmh_name)" "$cmd"
}

_tmh_tp() {
  if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux is not installed" >&2
    return 1
  fi
  [[ $# -gt 0 ]] && { _tmh_open "$(_tmh_name "$1")"; return; }
  if ! tmux list-sessions >/dev/null 2>&1; then
    _tmh_tm; return                    # no server/sessions yet → cwd session
  fi
  if ! command -v fzf >/dev/null 2>&1; then
    echo "tp: fzf not found; use 'tp <name>' or install fzf" >&2
    return 1
  fi
  local selected
  selected=$(
    tmux list-sessions -F "#{session_attached} #{session_name}#{?session_attached, (attached),}" \
      | sort -rn \
      | sed 's/^[0-9]* //' \
      | fzf --height 40% --reverse \
      | sed 's/ (attached)$//'
  )
  if [[ -n "$selected" ]]; then
    _tmh_open "$selected"
  else
    _tmh_tm                            # Esc / no selection → cwd session
  fi
}

_tmh_tms() {
  local remote="${TMS_HOST:-}"
  if [[ -z "$remote" ]]; then
    echo "tms: set TMS_HOST to your SSH host alias (e.g. export TMS_HOST=devbox)" >&2
    return 1
  fi
  local name="${1:-}"
  if [[ -n "$name" ]]; then
    name="$(_tmh_name "$name")"
    _tmh_title "🔗 $remote:$name"
    # ${(qq)…} single-quotes the name so the remote shell sees it as one word.
    ssh -t "$remote" "tmux new-session -A -s ${(qq)name}"
    return
  fi
  _tmh_title "🔗 $remote"
  ssh -t "$remote" '
    sessions=$(tmux list-sessions -F "#{session_name}" 2>/dev/null)
    [ -z "$sessions" ] && exec tmux new-session
    if command -v fzf >/dev/null 2>&1; then
      selected=$(printf "%s\n" "$sessions" | fzf --height 40% --reverse)
    else
      # Numbered fallback when fzf is not on the remote.
      printf "%s\n" "$sessions" | awk "{ print NR \") \" \$0 }"
      printf "Pick session (or Enter for new): "
      read choice
      [ -n "$choice" ] && selected=$(printf "%s\n" "$sessions" | sed -n "${choice}p")
    fi
    if [ -n "$selected" ]; then exec tmux attach-session -t "=$selected"; fi
    exec tmux new-session
  '
}

# Absolute, symlink-resolved git common dir of the repo at $1 (default: cwd).
_tmh_common() {
  local d
  d="$(git -C "${1:-.}" rev-parse --git-common-dir 2>/dev/null)" || return 1
  [[ "$d" = /* ]] || d="${1:-$PWD}/$d"
  print -r -- "${d:A}"
}

_tmh_tw() {
  if [[ $# -eq 0 ]]; then
    echo "Usage: tw <branch-name>" >&2
    return 1
  fi
  local branch="$1" common main
  common="$(_tmh_common)" || { echo "tw: not in a git repo" >&2; return 1; }
  # Anchor on the main checkout so tw from inside a linked worktree still
  # creates a sibling of the repo, not ../repo-a-b.
  main="${common:h}"
  # Slashes in branch names (feat/x) would nest directories; flatten them.
  local wt="${main:h}/${main:t}-${branch//\//-}"
  if [[ -e "$wt" ]]; then
    # Reuse only a worktree of THIS repo on THIS branch: feat/x and feat-x
    # flatten to the same path, and landing on the wrong one means commits to
    # the wrong branch.
    if [[ "$(_tmh_common "$wt")" != "$common" ]]; then
      echo "tw: $wt exists and is not a worktree of this repo" >&2
      return 1
    fi
    local cur; cur="$(git -C "$wt" branch --show-current)"
    if [[ "$cur" != "$branch" ]]; then
      echo "tw: $wt is on branch '$cur', not '$branch'" >&2
      return 1
    fi
  else
    git -C "$main" worktree add "$wt" -b "$branch" 2>/dev/null \
      || git -C "$main" worktree add "$wt" "$branch" || return 1
  fi
  cd "$wt" || return 1
  _tmh_tm
}

_tmh_twd() {
  local force=()
  [[ "${1:-}" == "-f" ]] && force=(--force)
  local gitdir common
  gitdir="$(git rev-parse --absolute-git-dir 2>/dev/null)" || { echo "twd: not in a git repo" >&2; return 1; }
  common="$(_tmh_common)"
  # Only a linked worktree has a git-dir distinct from the common dir; refusing
  # the main checkout keeps twd from ever deleting the primary working tree.
  if [[ "${gitdir:A}" == "$common" ]]; then
    echo "twd: this is the main worktree, not a linked one — refusing" >&2
    return 1
  fi
  local wt name
  wt="$(git rev-parse --show-toplevel)"
  name="$(_tmh_name "${wt:t}")"
  cd "${wt:h}" || return 1
  # Without -f, git refuses to drop a worktree with uncommitted changes.
  if ! git -C "$common" worktree remove "${force[@]}" "$wt"; then
    cd "$wt"
    echo "twd: worktree kept (see git's message above; 'twd -f' discards uncommitted changes)" >&2
    return 1
  fi
  echo "Removed worktree: $wt"
  tmux kill-session -t "=$name" 2>/dev/null && echo "Killed session: $name"
  return 0
}

# Bind the short names, never over an existing alias, function, or command.
() {
  local n
  for n in tm tp tv tn tms tw twd; do
    (( $+aliases[$n] || $+functions[$n] || $+commands[$n] )) && continue
    functions[$n]="_tmh_$n \"\$@\""
  done
}

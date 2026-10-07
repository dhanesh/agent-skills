#!/bin/sh
# tmux-agent-herdr-lite/assets/test_cockpit_scripts.sh
# gate: offline — runs in `make gate`. Must stay offline and deterministic:
# no network, no real tmux server, no fixed ports, no wall-clock dependence.
#
# Why this file exists: the skill ships ~19 shell executables and, until this
# suite, exactly one 6-test Python suite over a single pure module. Three real
# defects lived in that untested surface — a registry wipe that destroyed the
# state agent-resume consumes, an installer that appended a duplicate line to
# ~/.tmux.conf on every invocation, and key help that drifted from the config.
# Every one of them reproduces here in about a second with a fixture $HOME and
# a stub `tmux` on PATH, which is exactly why they should never have shipped.
set -eu

SCRIPTS="$(cd "$(dirname "$0")/../scripts" && pwd)"
ASSETS="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT INT TERM

# The scripts under test declare `#!/usr/bin/env bash` and use bash features
# (BASH_SOURCE, `local`, ${var//x/y}). Invoke them with `bash`, never `sh`: on
# Linux `sh` is dash, where BASH_SOURCE is unset under `set -u` and the script
# dies instantly — and because the callers swallow errors with `|| true`, that
# looked like "the scan ran and did nothing". This harness stays POSIX itself;
# only the invocations of those bash scripts are bash.
rc=0
ok()   { echo "PASS: $1"; }
bad()  { echo "FAIL: $1"; rc=1; }
want() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want '$3', got '$2')"; fi; }

# ── Stub tmux binaries ───────────────────────────────────────────────────────
mkdir -p "$WORK/bin-fail" "$WORK/bin-ok"
cat > "$WORK/bin-fail/tmux" <<'EOF'
#!/bin/sh
exit 1
EOF
cat > "$WORK/bin-ok/tmux" <<'EOF'
#!/bin/sh
case "$1" in
  list-panes)   echo "%99	some-other-pane" ;;
  list-clients) : ;;
  capture-pane) echo "waiting" ;;
esac
exit 0
EOF
chmod +x "$WORK/bin-fail/tmux" "$WORK/bin-ok/tmux"

seed_registry() {
  rm -rf "$1"; mkdir -p "$1/panes"
  for n in alpha beta; do
    cat > "$1/panes/$n.json" <<EOF
{"name":"$n","pane_id":"%$(printf '%s' "$n" | wc -c | tr -d ' ')","command":"claude","cwd":"/tmp","agent":"claude","status":"idle"}
EOF
  done
}

# ── 1. A failed tmux query must never be read as "every pane is gone" ────────
# This is the regression that mattered most: list-panes fails exactly when the
# server restarts, which is exactly when agent-resume needs the registry, and
# the status bar re-runs this scan every 5 seconds.
ROOT="$WORK/reg1"
seed_registry "$ROOT"
AGENT_ADOPT=off AGENT_TMUX_ROOT="$ROOT" PATH="$WORK/bin-fail:$PATH" bash "$SCRIPTS/agent-status-scan" >/dev/null 2>&1 || true
want "tmux unreachable: pane records survive" "$(ls "$ROOT/panes" | wc -l | tr -d ' ')" "2"
if [ -f "$ROOT/state.json" ]; then
  bad "tmux unreachable: state.json left untouched"
else
  ok "tmux unreachable: state.json left untouched"
fi

# ── 2. A pane that is genuinely gone is marked dead, not deleted ─────────────
# agent-resume relaunches records whose pane is gone, so deleting them destroys
# the very state it consumes — and deletion is not recoverable.
ROOT="$WORK/reg2"
seed_registry "$ROOT"
AGENT_ADOPT=off AGENT_TMUX_ROOT="$ROOT" PATH="$WORK/bin-ok:$PATH" bash "$SCRIPTS/agent-status-scan" >/dev/null 2>&1 || true
want "dead pane: record kept for agent-resume" "$(ls "$ROOT/panes" | wc -l | tr -d ' ')" "2"
if grep -q '"dead": *true' "$ROOT/panes/alpha.json" 2>/dev/null; then
  ok "dead pane: record marked dead"
else
  bad "dead pane: record marked dead"
fi
if [ -f "$ROOT/state.json" ] && ! grep -q '"name": *"alpha"' "$ROOT/state.json"; then
  ok "dead pane: excluded from the live fleet"
else
  bad "dead pane: excluded from the live fleet"
fi

# ── 3. The graveyard is bounded ──────────────────────────────────────────────
AGENT_ADOPT=off AGENT_TMUX_ROOT="$ROOT" AGENT_GRAVEYARD_TTL_SECONDS=0 PATH="$WORK/bin-ok:$PATH" \
  bash "$SCRIPTS/agent-status-scan" >/dev/null 2>&1 || true
want "graveyard: records past the TTL are reaped" "$(ls "$ROOT/panes" | wc -l | tr -d ' ')" "0"

# ── 4. The installer is idempotent, including on a fresh ~/.tmux.conf ────────
# The failing case was the FIRST-RUN one: when the only line in the file is the
# one being filtered, `grep -Ev` exits 1, the `&&` short-circuits, `mv` never
# runs, and `set -e` does not fire because it is not the final command.
if [ -f "$SCRIPTS/install.sh" ]; then
  FAKE="$WORK/home"
  mkdir -p "$FAKE"
  # env -u ZDOTDIR: the installer writes ${ZDOTDIR:-$HOME}/.zshrc, and a
  # ZDOTDIR inherited from the caller would point it at the real one.
  for i in 1 2 3; do
    env -u ZDOTDIR -u CLAUDE_CONFIG_DIR HOME="$FAKE" bash "$SCRIPTS/install.sh" >/dev/null 2>&1 || true
  done
  n="$(grep -c 'tmux-agent.conf' "$FAKE/.tmux.conf" 2>/dev/null || echo 0)"
  want "installer: 3 runs leave exactly one source-file line" "$n" "1"
  if ls "$FAKE"/.tmux.conf.tmp "$FAKE"/.zshrc.tmp >/dev/null 2>&1; then
    bad "installer: no temp file left in \$HOME"
  else
    ok "installer: no temp file left in \$HOME"
  fi
  # ~/.zshrc gets the same guarantee: re-running never stacks source lines.
  n="$(grep -c 'agent-shell-hook.zsh' "$FAKE/.zshrc" 2>/dev/null || echo 0)"
  want "installer: 3 runs leave exactly one shell-hook line" "$n" "1"
  n="$(grep -c 'tmux-shell-functions.zsh' "$FAKE/.zshrc" 2>/dev/null || echo 0)"
  want "installer: 3 runs leave exactly one shell-functions line" "$n" "1"
  if [ -f "$FAKE/.tmux/agent-panes/tmux-shell-functions.zsh" ]; then
    ok "installer: shell functions installed by default"
  else
    bad "installer: shell functions installed by default"
  fi
  inst() { env -u ZDOTDIR -u AGENT_SHELL_FUNCS -u AGENT_CLAUDE_HOOKS -u CLAUDE_CONFIG_DIR HOME="$FAKE" "$@" bash "$SCRIPTS/install.sh" >/dev/null 2>&1 || true; }
  nfun() { grep -c 'tmux-shell-functions.zsh' "$FAKE/.zshrc" 2>/dev/null || true; }
  inst AGENT_SHELL_FUNCS=off
  want "installer: AGENT_SHELL_FUNCS=off removes the functions line" "$(nfun)" "0"
  if [ -f "$FAKE/.tmux/agent-panes/tmux-shell-functions.zsh" ]; then
    bad "installer: AGENT_SHELL_FUNCS=off removes the functions file"
  else
    ok "installer: AGENT_SHELL_FUNCS=off removes the functions file"
  fi
  # The skill reruns the installer with no env on every invocation; an opt-out
  # that a plain rerun undoes is not an opt-out.
  inst
  want "installer: opt-out survives a plain rerun" "$(nfun)" "0"
  inst AGENT_SHELL_FUNCS=on
  want "installer: AGENT_SHELL_FUNCS=on re-enables" "$(nfun)" "1"
  # A user line that merely mentions the file is theirs, not ours.
  echo '# see tmux-shell-functions.zsh for tm' >> "$FAKE/.zshrc"
  inst
  want "installer: user comment mentioning the file is kept" \
    "$(grep -c '^# see tmux-shell-functions' "$FAKE/.zshrc")" "1"
  # A symlinked ~/.zshrc (stow/chezmoi/yadm) must stay a symlink.
  mv "$FAKE/.zshrc" "$FAKE/zshrc.real"
  ln -s "$FAKE/zshrc.real" "$FAKE/.zshrc"
  inst; inst
  if [ -L "$FAKE/.zshrc" ]; then ok "installer: symlinked ~/.zshrc stays a symlink"
  else bad "installer: symlinked ~/.zshrc stays a symlink"; fi

  # Claude status hooks: merged beside other tools' hooks, never over them.
  mkdir -p "$FAKE/.claude"
  printf '%s\n' '{"model":"x","hooks":{"Stop":[{"hooks":[{"type":"command","command":"/other/tool"}]}]}}' \
    > "$FAKE/.claude/settings.json"
  nours() { grep -c '# tmux-agent-herdr-lite' "$FAKE/.claude/settings.json" || true; }
  nother() { grep -c '/other/tool' "$FAKE/.claude/settings.json" || true; }
  inst; inst
  want "installer: Claude hooks, one per event after 2 runs" "$(nours)" "10"
  want "installer: other tools' Claude hooks kept" "$(nother)" "1"
  inst AGENT_CLAUDE_HOOKS=off; inst
  want "installer: AGENT_CLAUDE_HOOKS=off removes ours and survives a rerun" "$(nours)" "0"
  want "installer: AGENT_CLAUDE_HOOKS=off keeps other tools' hooks" "$(nother)" "1"
  inst AGENT_CLAUDE_HOOKS=on
  printf '{ not json' > "$FAKE/.claude/settings.json"
  inst
  want "installer: unparseable settings.json left untouched" \
    "$(cat "$FAKE/.claude/settings.json")" "{ not json"
else
  echo "PASS: installer absent — skipped (nothing to assert)"
fi

# ── 4b. Agent-reported status (agent-hook) and cycling jumps ────────────────
# A smarter stub: pane metadata for registration, pane ids for ordering, and a
# log of navigation commands.
mkdir -p "$WORK/bin-smart"
cat > "$WORK/bin-smart/tmux" <<'EOF'
#!/bin/sh
case "$1" in
  display-message)
    if [ "$3" = '#{pane_id}' ]; then echo "${STUB_CUR:-}"; else printf 'sess\twin\t/tmp\n'; fi ;;
  list-panes)
    for p in ${STUB_PANES:-%99}; do
      case "$*" in *pane_title*) printf '%s\tt\n' "$p" ;; *) echo "$p" ;; esac
    done ;;
  list-clients|capture-pane) : ;;
  *) echo "$*" >> "${TMUX_LOG:-/dev/null}" ;;
esac
exit 0
EOF
chmod +x "$WORK/bin-smart/tmux"
ROOT="$WORK/reg-hook"; mkdir -p "$ROOT/panes"
hook() {  # hook <json> — run agent-hook as Claude Code would, from pane %99
  printf '%s' "$1" | AGENT_ADOPT=off AGENT_TMUX_ROOT="$ROOT" TMUX_PANE=%99 \
    PATH="$WORK/bin-smart:$PATH" python3 "$SCRIPTS/agent-hook" claude
}
out="$(hook '{"hook_event_name":"PermissionRequest","tool_name":"Bash"}')"
want "agent-hook: prints nothing (hook stdout reaches the model)" "$out" ""
if [ -f "$ROOT/panes/99.json" ] && grep -q '"status": "blocked"' "$ROOT/panes/99.hook" 2>/dev/null; then
  ok "agent-hook: unseen pane registered, blocked recorded"
else
  bad "agent-hook: unseen pane registered, blocked recorded"
fi
AGENT_ADOPT=off AGENT_TMUX_ROOT="$ROOT" PATH="$WORK/bin-smart:$PATH" bash "$SCRIPTS/agent-status-scan" >/dev/null 2>&1 || true
if grep -q '"status": "blocked"' "$ROOT/state.json" && grep -q '"reason": "hook PermissionRequest"' "$ROOT/state.json"; then
  ok "scan: hook status outranks screen heuristics"
else
  bad "scan: hook status outranks screen heuristics"
fi
hook '{"hook_event_name":"not json' >/dev/null; rc_h=$?
want "agent-hook: malformed payload still exits 0" "$rc_h" "0"
hook '{"hook_event_name":"SessionEnd","reason":"exit"}'
if [ ! -f "$ROOT/panes/99.json" ] && [ ! -f "$ROOT/panes/99.hook" ]; then
  ok "agent-hook: SessionEnd deregisters the pane"
else
  bad "agent-hook: SessionEnd deregisters the pane"
fi

# Adoption: an agent launched through an alias never hit preexec, but its
# process carries TMUX_PANE — the scan must find and register that pane.
mkdir -p "$WORK/bin-ps"
cat > "$WORK/bin-ps/ps" <<'EOF'
#!/bin/sh
case "$1" in
  -ax) echo "99999999 claude --dangerously-skip-permissions"; echo "99999998 /bin/zsh" ;;
  eww) echo "99999999 claude --dangerously-skip-permissions TERM=x TMUX_PANE=%99 HOME=/h" ;;
esac
EOF
chmod +x "$WORK/bin-ps/ps"
ROOT="$WORK/reg-adopt"; mkdir -p "$ROOT/panes"
AGENT_TMUX_ROOT="$ROOT" PATH="$WORK/bin-ps:$WORK/bin-smart:$PATH" bash "$SCRIPTS/agent-status-scan" >/dev/null 2>&1 || true
if grep -q '"pane_id": "%99"' "$ROOT/state.json" 2>/dev/null && grep -q '"agent": "claude"' "$ROOT/panes/99.json" 2>/dev/null; then
  ok "scan: adopts an untracked agent by its TMUX_PANE"
else
  bad "scan: adopts an untracked agent by its TMUX_PANE"
fi
rm -rf "$ROOT"; mkdir -p "$ROOT/panes"
AGENT_ADOPT=off AGENT_TMUX_ROOT="$ROOT" PATH="$WORK/bin-ps:$WORK/bin-smart:$PATH" bash "$SCRIPTS/agent-status-scan" >/dev/null 2>&1 || true
want "scan: AGENT_ADOPT=off adopts nothing" "$(ls "$ROOT/panes" | wc -l | tr -d ' ')" "0"

# The shell hook must see through aliases: `ccl` expands to `claude …`.
if command -v zsh >/dev/null 2>&1; then
  mkdir -p "$WORK/obs"
  printf '#!/bin/sh\necho "$*" >> "%s"\n' "$WORK/obs/log" > "$WORK/obs/agent-observe"
  chmod +x "$WORK/obs/agent-observe"
  sed -e "s|@AGENT_BIN@|$WORK/obs|g" -e "s|@AGENT_ROOT@|$WORK/obs|g" \
      -e "s#@AGENT_NAMES@#claude|codex#g" "$ASSETS/agent-shell-hook.zsh" > "$WORK/obs/hook.zsh"
  TMUX_PANE=%7 zsh -fc "source '$WORK/obs/hook.zsh'
    _agent_herdr_preexec 'ccl' 'claude --x' 'claude --x'
    _agent_herdr_preexec 'ls -la' 'ls -la' 'ls -la'" >/dev/null 2>&1 || true
  i=0; while [ ! -s "$WORK/obs/log" ] && [ $i -lt 20 ]; do sleep 0.1; i=$((i+1)); done
  sleep 0.2
  want "shell hook: alias launch registers the expanded command" \
    "$(cat "$WORK/obs/log" 2>/dev/null)" "register %7 claude --x"
fi

# Jumps cycle: pressing the key again moves to the NEXT blocked agent, wrapping.
ROOT="$WORK/reg-jump"; mkdir -p "$ROOT/panes"
printf '%s\n' '{"items":[{"pane_id":"%3","status":"blocked"},{"pane_id":"%2","status":"idle"},{"pane_id":"%1","status":"blocked"}]}' > "$ROOT/state.json"
jump() {
  : > "$WORK/jump.log"
  AGENT_TMUX_ROOT="$ROOT" AGENT_STATUS_SCAN=/nonexistent STUB_CUR="$1" STUB_PANES="%1 %2 %3" \
    TMUX_LOG="$WORK/jump.log" PATH="$WORK/bin-smart:$PATH" bash "$SCRIPTS/agent-jump" blocked >/dev/null 2>&1 || true
  sed -n 's/^select-pane -t //p' "$WORK/jump.log"
}
want "agent-jump: from %1 goes to the next blocked (%3)" "$(jump %1)" "%3"
want "agent-jump: from %3 wraps to the first blocked (%1)" "$(jump %3)" "%1"
want "agent-jump: from a non-matching pane picks the next one" "$(jump %2)" "%3"

# ── 5. Documented keys must exist in the shipped tmux config ─────────────────
# A refactor moved every action under `prefix a` and updated SKILL.md and the
# conf, but not the README, the cheatsheet, or the dashboard's own KEYS block —
# so the two surfaces a confused user reaches for first were the two that lied.
CONF="$ASSETS/tmux-agent.conf"
if [ -f "$CONF" ]; then
  missing=""
  # Every key the cheatsheet and dashboard ADVERTISE must be bound in the conf.
  # Only key-help rows count: a `prefix a <key>` where <key> is a lone token, so
  # prose like "prefix a puts you in the agent table" is not read as a binding.
  for src in "$SCRIPTS/agent-cheatsheet" "$SCRIPTS/agent-dashboard"; do
    [ -f "$src" ] || continue
    for k in $(awk '
        {
          line = $0
          while (match(line, /prefix +a +[a-zA-Z]([^a-zA-Z]|$)/)) {
            tok = substr(line, RSTART, RLENGTH)
            line = substr(line, RSTART + RLENGTH)
            sub(/^prefix +a +/, "", tok)
            print substr(tok, 1, 1)
          }
        }' "$src" | sort -u); do
      grep -qE "^bind-key +-T +agent +$k( |\$)" "$CONF" && continue
      missing="$missing $(basename "$src"):$k"
    done
  done
  if [ -z "$missing" ]; then
    ok "key help: every advertised key is bound in tmux-agent.conf"
  else
    bad "key help: unbound key(s) advertised —$missing"
  fi
else
  bad "key help: $CONF not found"
fi

# ── 6. Convenience shell functions (tm/tn/tw/twd) ────────────────────────────
# A stub tmux logs its argv so session naming, nesting behaviour and worktree
# cleanup are asserted without a tmux server.
FUNCS="$ASSETS/tmux-shell-functions.zsh"
if command -v zsh >/dev/null 2>&1 && command -v git >/dev/null 2>&1; then
  mkdir -p "$WORK/bin-log"
  cat > "$WORK/bin-log/tmux" <<'EOF'
#!/bin/sh
echo "$*" >> "$TMUX_LOG"
case "$1" in has-session) [ "${STUB_HAS:-0}" = 1 ] && exit 0; exit 1 ;; esac
exit 0
EOF
  chmod +x "$WORK/bin-log/tmux"
  LOG="$WORK/tmux.log"
  tz() {  # tz <dir> <zsh code> [VAR=val…] — functions loaded, stub tmux first on PATH
    _d="$1"; _c="$2"; shift 2
    ( cd "$_d" && env -u TMUX -u STUB_HAS TMUX_LOG="$LOG" PATH="$WORK/bin-log:$PATH" "$@" \
        zsh -fc "source '$FUNCS'; $_c" ) >/dev/null 2>&1
  }
  logis() { if grep -qx "$2" "$LOG"; then ok "$1"; else bad "$1 ($(tr '\n' ';' < "$LOG"))"; fi; }

  if zsh -n "$FUNCS"; then ok "functions: zsh syntax"; else bad "functions: zsh syntax"; fi

  mkdir -p "$WORK/my.proj"
  : > "$LOG"; tz "$WORK/my.proj" 'tm' || true
  logis "tm: new session named after cwd, dots mapped to _" 'new-session -s my_proj'

  : > "$LOG"; tz "$WORK/my.proj" 'TMUX=x; tn a:b' STUB_HAS=1 || true
  if grep -qx 'switch-client -t =a_b' "$LOG" && ! grep -q attach "$LOG"; then
    ok "tn: inside tmux, existing session → switch-client, no attach"
  else
    bad "tn: inside tmux, existing session → switch-client, no attach ($(tr '\n' ';' < "$LOG"))"
  fi
  # The no-nesting path that matters: inside tmux, session missing.
  : > "$LOG"; tz "$WORK/my.proj" 'TMUX=x; tv "it'"'"'s \$(x).txt"' || true
  logis "tv: inside tmux, missing session → detached create" \
    'new-session -d -s my_proj nvim it\\'"'"'s\\ \\$\\(x\\).txt'
  logis "tv: inside tmux, missing session → then switch-client" 'switch-client -t =my_proj'

  # Never override a name the user already owns (an alias used to abort
  # sourcing with a parse error, losing every function after it).
  if ( cd "$WORK" && env -u TMUX zsh -fc "alias tm='echo mine'; tv() { echo mine; }; \
       source '$FUNCS' && [[ \$aliases[tm] == 'echo mine' ]] && [[ \$(tv) == mine ]] \
       && (( \$+functions[tn] ))" ) >/dev/null 2>&1; then
    ok "functions: existing alias/function names are left alone"
  else
    bad "functions: existing alias/function names are left alone"
  fi

  mkdir -p "$WORK/bin-ssh"
  printf '#!/bin/sh\nfor a; do echo "$a"; done > "%s"\n' "$WORK/ssh.log" > "$WORK/bin-ssh/ssh"
  chmod +x "$WORK/bin-ssh/ssh"
  tz "$WORK" 'tms "x'"'"'; rm -rf ~"' PATH="$WORK/bin-ssh:$WORK/bin-log:$PATH" TMS_HOST=box || true
  want "tms: name reaches the remote as one quoted word" \
    "$(tail -1 "$WORK/ssh.log" 2>/dev/null)" "tmux new-session -A -s 'x'\\''; rm -rf ~'"

  REPO="$WORK/repo"
  mkdir -p "$REPO"
  git -C "$REPO" init -q
  git -C "$REPO" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
  if tz "$REPO" 'twd'; then
    bad "twd: refuses the main worktree"
  else
    [ -d "$REPO/.git" ] && ok "twd: refuses the main worktree" || bad "twd: main worktree deleted"
  fi

  : > "$LOG"; tz "$REPO" 'tw feat/x' || true
  WT="$WORK/repo-feat-x"
  if [ -d "$WT" ]; then ok "tw: worktree at ../<repo>-<branch>, slashes flattened"
  else bad "tw: worktree at ../<repo>-<branch>, slashes flattened"; fi

  # feat-x flattens to the same path as feat/x; reusing it would commit to
  # the wrong branch.
  if tz "$REPO" 'tw feat-x'; then bad "tw: refuses a same-path worktree on another branch"
  else ok "tw: refuses a same-path worktree on another branch"; fi
  mkdir -p "$WORK/repo-stray"
  if tz "$REPO" 'tw stray'; then bad "tw: refuses an existing non-worktree dir"
  else ok "tw: refuses an existing non-worktree dir"; fi

  echo dirty > "$WT/uncommitted.txt"
  tz "$WT" 'twd' || true
  if [ -f "$WT/uncommitted.txt" ]; then ok "twd: keeps a dirty worktree without -f"
  else bad "twd: keeps a dirty worktree without -f (uncommitted work lost)"; fi

  : > "$LOG"; tz "$WT" 'twd -f' || true
  if [ ! -d "$WT" ] && grep -qx 'kill-session -t =repo-feat-x' "$LOG"; then
    ok "twd -f: removes linked worktree and its session"
  else
    bad "twd -f: removes linked worktree and its session ($(tr '\n' ';' < "$LOG"))"
  fi
else
  echo "PASS: zsh or git absent — shell-function checks skipped"
fi

exit $rc

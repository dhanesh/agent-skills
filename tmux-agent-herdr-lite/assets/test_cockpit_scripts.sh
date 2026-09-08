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
AGENT_TMUX_ROOT="$ROOT" PATH="$WORK/bin-fail:$PATH" sh "$SCRIPTS/agent-status-scan" >/dev/null 2>&1 || true
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
AGENT_TMUX_ROOT="$ROOT" PATH="$WORK/bin-ok:$PATH" sh "$SCRIPTS/agent-status-scan" >/dev/null 2>&1 || true
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
AGENT_TMUX_ROOT="$ROOT" AGENT_GRAVEYARD_TTL_SECONDS=0 PATH="$WORK/bin-ok:$PATH" \
  sh "$SCRIPTS/agent-status-scan" >/dev/null 2>&1 || true
want "graveyard: records past the TTL are reaped" "$(ls "$ROOT/panes" | wc -l | tr -d ' ')" "0"

# ── 4. The installer is idempotent, including on a fresh ~/.tmux.conf ────────
# The failing case was the FIRST-RUN one: when the only line in the file is the
# one being filtered, `grep -Ev` exits 1, the `&&` short-circuits, `mv` never
# runs, and `set -e` does not fire because it is not the final command.
if [ -f "$SCRIPTS/install.sh" ]; then
  FAKE="$WORK/home"
  mkdir -p "$FAKE"
  for i in 1 2 3; do
    HOME="$FAKE" sh "$SCRIPTS/install.sh" >/dev/null 2>&1 || true
  done
  n="$(grep -c 'tmux-agent.conf' "$FAKE/.tmux.conf" 2>/dev/null || echo 0)"
  want "installer: 3 runs leave exactly one source-file line" "$n" "1"
  if ls "$FAKE"/.tmux.conf.tmp >/dev/null 2>&1; then
    bad "installer: no temp file left in \$HOME"
  else
    ok "installer: no temp file left in \$HOME"
  fi
else
  echo "PASS: installer absent — skipped (nothing to assert)"
fi

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

exit $rc

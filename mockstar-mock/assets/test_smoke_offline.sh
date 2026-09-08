#!/bin/sh
# mockstar-mock/assets/test_smoke_offline.sh — smoke.sh's route-checking logic.
# gate: offline — runs in `make gate`. No bunx, no docker, no network: a stub
# HTTP server on an ephemeral loopback port stands in for the mock server.
#
# Why this file exists: smoke.sh is the component whose entire job is
# verification, and its only behavioural test lived in a `# gate: integration`
# suite that the gate never runs. That is how it shipped silently dropping the
# LAST route in the routes file whenever the file lacked a trailing newline —
# `read` returns non-zero on a final partial line — and still exiting 0. A model
# writes that file at stage 5, so a missing final newline is realistic output,
# and the result was a project certified green with an unprobed endpoint.
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
SMOKE="$DIR/smoke.sh"
WORK="$(mktemp -d)"
STUB=""
cleanup() {
  [ -n "$STUB" ] && kill "$STUB" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

rc=0
ok()  { echo "PASS: $1"; }
bad() { echo "FAIL: $1"; rc=1; }

# ── 1. Usage error without arguments ─────────────────────────────────────────
if sh "$SMOKE" >/dev/null 2>&1; then
  bad "usage errors without args"
else
  ok "usage errors without args"
fi

# ── Stub server on an EPHEMERAL port (a fixed port is forbidden here) ─────────
PORT="$(python3 -c "
import socket
s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1]); s.close()")"
(cd "$WORK" && python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1) &
STUB=$!
BASE="http://127.0.0.1:$PORT"

# Wait for readiness rather than sleeping a fixed amount — determinism.
i=0
until curl -s -o /dev/null "$BASE/" 2>/dev/null || [ "$i" -ge 50 ]; do
  i=$((i + 1))
  python3 -c "import time; time.sleep(0.1)"
done
if [ "$i" -ge 50 ]; then
  echo "FAIL: stub server never came up on $PORT"
  exit 1
fi

R="$WORK/routes.tsv"

# ── 2. A matching expectation passes ─────────────────────────────────────────
printf 'GET\t/\t200\n' > "$R"
if MOCKSTAR_SMOKE_BASE_URL="$BASE" sh "$SMOKE" --routes-only "$R" >/dev/null 2>&1; then
  ok "smoke matches an expected 200"
else
  bad "smoke matches an expected 200"
fi

# ── 3. A mismatch fails (the check discriminates) ────────────────────────────
printf 'GET\t/does-not-exist\t200\n' > "$R"
if MOCKSTAR_SMOKE_BASE_URL="$BASE" sh "$SMOKE" --routes-only "$R" >/dev/null 2>&1; then
  bad "smoke detects a status mismatch"
else
  ok "smoke detects a status mismatch"
fi

# ── 4. THE REGRESSION: no trailing newline must not drop the last route ──────
printf 'GET\t/\t200\nGET\t/nope\t404' > "$R"          # deliberately no final \n
out="$(MOCKSTAR_SMOKE_BASE_URL="$BASE" sh "$SMOKE" --routes-only "$R" 2>&1 || true)"
n="$(printf '%s\n' "$out" | grep -c '^PASS: ')"
if [ "$n" -eq 2 ]; then
  ok "routes file without a trailing newline: every route probed"
else
  bad "routes file without a trailing newline: every route probed (probed $n of 2)"
fi
if printf '%s\n' "$out" | grep -q 'probed 2/2 route(s)'; then
  ok "smoke reports its probed/declared route count"
else
  bad "smoke reports its probed/declared route count"
fi

# ── 5. A dropped route must FAIL, never pass quietly ─────────────────────────
# Guards the count itself: if the read loop ever regresses, probed < declared
# and the run must go red rather than reporting success on partial coverage.
printf 'GET\t/\t200\nGET\t/\t200\n# a comment line\n\n' > "$R"
out="$(MOCKSTAR_SMOKE_BASE_URL="$BASE" sh "$SMOKE" --routes-only "$R" 2>&1 || true)"
if printf '%s\n' "$out" | grep -q 'probed 2/2 route(s)'; then
  ok "comments and blank lines are excluded from the declared count"
else
  bad "comments and blank lines are excluded from the declared count"
fi

exit $rc

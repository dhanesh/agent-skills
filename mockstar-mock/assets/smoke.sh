#!/bin/sh
# mockstar-mock/assets/smoke.sh — boot mockstar + smoke each route.
#
# Usage:
#   sh smoke.sh <out-dir> <routes-file>          # boot mockstar, then smoke
#   sh smoke.sh --routes-only <routes-file>      # smoke against MOCKSTAR_SMOKE_BASE_URL (no boot)
#
# routes-file: one line per route -> METHOD<TAB>PATH<TAB>EXPECTED_STATUS
set -eu

routes_only=0
if [ "${1:-}" = "--routes-only" ]; then
  routes_only=1; shift
  ROUTES="${1:-}"
  [ -n "$ROUTES" ] || { echo "usage: smoke.sh --routes-only <routes-file>" >&2; exit 2; }
  BASE="${MOCKSTAR_SMOKE_BASE_URL:?set MOCKSTAR_SMOKE_BASE_URL for --routes-only}"
else
  OUT="${1:-}"; ROUTES="${2:-}"
  { [ -n "$OUT" ] && [ -n "$ROUTES" ]; } || { echo "usage: smoke.sh <out-dir> <routes-file>" >&2; exit 2; }
  PORT="${MOCKSTAR_SMOKE_PORT:-3917}"
  BASE="http://127.0.0.1:$PORT"
  bunx mockstar serve "$OUT" --deterministic --no-watch --port "$PORT" >/tmp/mockstar-smoke.log 2>&1 &
  SERVER=$!
  trap 'kill "$SERVER" 2>/dev/null || true' EXIT
  # wait up to ~10s for readiness
  i=0
  while [ "$i" -lt 50 ]; do
    if curl -s -o /dev/null "$BASE" 2>/dev/null; then break; fi
    i=$((i + 1)); sleep 0.2
  done
  if [ "$i" -ge 50 ]; then
    echo "FAIL: mockstar did not become ready; log:" >&2; cat /tmp/mockstar-smoke.log >&2; exit 1
  fi
fi

rc=0
while IFS="$(printf '\t')" read -r METHOD PATHPART EXPECT; do
  [ -n "${METHOD:-}" ] || continue
  case "$METHOD" in \#*) continue;; esac
  GOT="$(curl -s -o /dev/null -w '%{http_code}' -X "$METHOD" "$BASE$PATHPART")"
  if [ "$GOT" = "$EXPECT" ]; then
    echo "PASS: $METHOD $PATHPART -> $GOT"
  else
    echo "FAIL: $METHOD $PATHPART -> $GOT (expected $EXPECT)"; rc=1
  fi
done < "$ROUTES"

[ "$routes_only" -eq 1 ] || true
exit $rc

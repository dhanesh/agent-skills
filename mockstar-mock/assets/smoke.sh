#!/bin/sh
# mockstar-mock/assets/smoke.sh — boot mockstar + smoke each route.
#
# Usage:
#   sh smoke.sh <out-dir> <routes-file>          # boot mockstar, then smoke
#   sh smoke.sh --routes-only <routes-file>      # smoke against MOCKSTAR_SMOKE_BASE_URL (no boot)
#
# routes-file: one line per route -> METHOD<TAB>PATH<TAB>EXPECTED_STATUS
#
# Runtime:
#   MOCKSTAR_SMOKE_RUNTIME=local   (default) — bunx mockstar <out> --deterministic --no-watch --port <PORT>
#   MOCKSTAR_SMOKE_RUNTIME=docker  — docker run ghcr.io/dhanesh/mockstar (override with MOCKSTAR_SMOKE_IMAGE)
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
  RUNTIME="${MOCKSTAR_SMOKE_RUNTIME:-local}"

  if [ "$RUNTIME" = "docker" ]; then
    IMAGE="${MOCKSTAR_SMOKE_IMAGE:-ghcr.io/dhanesh/mockstar:latest}"
    # Resolve $OUT to an absolute path (docker -v requires it)
    case "$OUT" in
      /*) ABS_OUT="$OUT" ;;
      *)  ABS_OUT="$(cd "$OUT" && pwd)" ;;
    esac

    # Check docker daemon is up
    if ! docker version >/dev/null 2>&1; then
      echo "SKIP: docker unavailable"
      exit 0
    fi

    # Build volume args
    VOL_ARGS="-v ${ABS_OUT}:/config/mocks:ro"
    # Check for a sibling handlers/ dir
    HANDLERS_DIR="${ABS_OUT%/*}/handlers"
    if [ -d "$HANDLERS_DIR" ]; then
      VOL_ARGS="$VOL_ARGS -v ${HANDLERS_DIR}:/config/handlers:ro"
    fi

    # Boot container — if docker run fails (image unreachable etc.), skip gracefully
    CID=""
    if ! CID="$(docker run -d --rm -p "${PORT}:3000" $VOL_ARGS "$IMAGE" 2>/tmp/mockstar-docker-smoke.log)"; then
      echo "SKIP: docker unavailable"
      exit 0
    fi
    trap 'docker stop "$CID" >/dev/null 2>/dev/null || true' EXIT

    # Poll /health (up to ~10s)
    i=0
    while [ "$i" -lt 50 ]; do
      if curl -s -o /dev/null "http://127.0.0.1:${PORT}/health" 2>/dev/null; then break; fi
      i=$((i + 1)); sleep 0.2
    done
    if [ "$i" -ge 50 ]; then
      echo "FAIL: docker mockstar did not become ready; log:" >&2
      cat /tmp/mockstar-docker-smoke.log >&2
      docker logs "$CID" 2>&1 >&2 || true
      exit 1
    fi

  else
    # local path (default)
    bunx mockstar "$OUT" --deterministic --no-watch --port "$PORT" >/tmp/mockstar-smoke.log 2>&1 &
    SERVER=$!
    trap 'kill "$SERVER" 2>/dev/null || true' EXIT
    # Poll /health first (treat any HTTP status as "up")
    i=0
    while [ "$i" -lt 50 ]; do
      if curl -s -o /dev/null "http://127.0.0.1:${PORT}/health" 2>/dev/null; then break; fi
      i=$((i + 1)); sleep 0.2
    done
    if [ "$i" -ge 50 ]; then
      echo "FAIL: mockstar did not become ready; log:" >&2; cat /tmp/mockstar-smoke.log >&2; exit 1
    fi
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

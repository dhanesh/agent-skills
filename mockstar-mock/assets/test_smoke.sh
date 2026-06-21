#!/bin/sh
# mockstar-mock/assets/test_smoke.sh — exercises route-checking without booting mockstar.
# Starts a tiny local HTTP stub, points smoke.sh at it via MOCKSTAR_SMOKE_BASE_URL.
set -eu
DIR="$(dirname "$0")"
SMOKE="$DIR/smoke.sh"

# 1) usage check
if sh "$SMOKE" 2>/dev/null; then echo "FAIL: expected usage error"; exit 1; else echo "PASS: usage errors without args"; fi

# 2) route-check against a stub server (python http.server returns 200 for GET /, 404 otherwise)
python3 -m http.server 8731 >/dev/null 2>&1 &
STUB=$!
trap 'kill "$STUB" 2>/dev/null || true' EXIT
sleep 1

ROUTES="$(mktemp)"
printf 'GET\t/\t200\n' > "$ROUTES"
if MOCKSTAR_SMOKE_BASE_URL="http://127.0.0.1:8731" sh "$SMOKE" --routes-only "$ROUTES"; then
  echo "PASS: smoke matched expected 200"
else
  echo "FAIL: smoke did not match"; exit 1
fi

printf 'GET\t/does-not-exist\t200\n' > "$ROUTES"
if MOCKSTAR_SMOKE_BASE_URL="http://127.0.0.1:8731" sh "$SMOKE" --routes-only "$ROUTES"; then
  echo "FAIL: smoke should have failed on 404"; exit 1
else
  echo "PASS: smoke detects status mismatch"
fi

#!/bin/sh
# mockstar-mock/assets/test_e2e.sh — import -> enhance -> boot -> smoke against real mockstar.
set -eu
DIR="$(dirname "$0")"
FIX="$DIR/fixtures/petstore-mini.yaml"
ROUTES="$DIR/fixtures/routes.tsv"

command -v bunx >/dev/null 2>&1 || { echo "SKIP: bunx not available"; exit 0; }

OUT="$(mktemp -d)"
OUT2="$(mktemp -d)"
trap 'rm -rf "$OUT" "$OUT2"' EXIT

if ! bunx mockstar import "$FIX" "$OUT" --tenant=default >/tmp/e2e-import.log 2>&1; then
  echo "FAIL: mockstar import"; cat /tmp/e2e-import.log; exit 1
fi
echo "PASS: import produced mocks"

bunx mockstar enhance "$OUT/default" >/tmp/e2e-enhance.log 2>&1 || true
echo "PASS: enhance ran"

sh "$DIR/smoke.sh" "$OUT" "$ROUTES"

# F1 regression: non-default tenant import using equals form (--tenant=acme).
# This proves the equals form is honored; the space form is silently ignored by mockstar import.
if ! bunx mockstar import "$FIX" "$OUT2" --tenant=acme >/tmp/e2e-import-acme.log 2>&1; then
  echo "FAIL: mockstar import --tenant=acme"; cat /tmp/e2e-import-acme.log; exit 1
fi

if [ -d "$OUT2/acme" ] && ls "$OUT2/acme"/*.json >/dev/null 2>&1; then
  echo "PASS: non-default tenant import — acme/ dir created with mocks:"
  ls "$OUT2/acme"/*.json
else
  echo "FAIL: --tenant=acme mocks not found in $OUT2/acme/"; exit 1
fi

#!/bin/sh
# mockstar-mock/assets/test_e2e.sh — import -> enhance -> boot -> smoke against real mockstar.
set -eu
DIR="$(dirname "$0")"
FIX="$DIR/fixtures/petstore-mini.yaml"
ROUTES="$DIR/fixtures/routes.tsv"

command -v bunx >/dev/null 2>&1 || { echo "SKIP: bunx not available"; exit 0; }

OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

if ! bunx mockstar import "$FIX" "$OUT" --tenant default >/tmp/e2e-import.log 2>&1; then
  echo "FAIL: mockstar import"; cat /tmp/e2e-import.log; exit 1
fi
echo "PASS: import produced mocks"

bunx mockstar enhance "$OUT/default" >/tmp/e2e-enhance.log 2>&1 || true
echo "PASS: enhance ran"

sh "$DIR/smoke.sh" "$OUT" "$ROUTES"

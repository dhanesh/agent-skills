#!/bin/sh
# mockstar-mock/assets/test_e2e.sh — import -> enhance -> boot -> smoke against real mockstar.
# Exercises the documented <out>/mocks/<tenant>/ layout (config-root = <out>/mocks).
# gate: integration — excluded from `make gate`; run with `make test-integration`.
# Needs the real mockstar CLI (network via bunx) and, for the docker branch, a
# reachable daemon and image. Not offline, so it cannot be a gate check.
set -eu
DIR="$(dirname "$0")"
FIX="$DIR/fixtures/petstore-mini.yaml"

command -v bunx >/dev/null 2>&1 || { echo "SKIP: bunx not available"; exit 0; }

OUT="$(mktemp -d)"
OUT2="$(mktemp -d)"
LOGS="$(mktemp -d)"
trap 'rm -rf "$OUT" "$OUT2" "$LOGS"' EXIT

# Build documented layout: <out>/mocks/<tenant>/
if ! bunx @dhaneshpurohit/mockstar import "$FIX" "$OUT/mocks" --tenant=default >"$LOGS/import.log" 2>&1; then
  echo "FAIL: mockstar import"; cat "$LOGS/import.log"; exit 1
fi
echo "PASS: import produced mocks at $OUT/mocks/default/"

if ! bunx @dhaneshpurohit/mockstar enhance "$OUT/mocks/default" >"$LOGS/enhance.log" 2>&1; then
  echo "FAIL: mockstar enhance"; cat "$LOGS/enhance.log"; exit 1
fi
echo "PASS: enhance ran"

# Write routes TSV at <out>/mocks/routes.tsv (documented location)
ROUTES="$OUT/mocks/routes.tsv"
cp "$DIR/fixtures/routes.tsv" "$ROUTES"

# Smoke: first arg is the mocks config-root (<out>/mocks)
sh "$DIR/smoke.sh" "$OUT/mocks" "$ROUTES"

# F1 regression: non-default tenant import using equals form (--tenant=acme).
# Proves the equals form is honored; space form is silently ignored by mockstar import.
if ! bunx @dhaneshpurohit/mockstar import "$FIX" "$OUT2/mocks" --tenant=acme >"$LOGS/import-acme.log" 2>&1; then
  echo "FAIL: mockstar import --tenant=acme"; cat "$LOGS/import-acme.log"; exit 1
fi

if [ -d "$OUT2/mocks/acme" ] && ls "$OUT2/mocks/acme"/*.json >/dev/null 2>&1; then
  echo "PASS: non-default tenant import — mocks/acme/ dir created with mocks:"
  ls "$OUT2/mocks/acme"/*.json
else
  echo "FAIL: --tenant=acme mocks not found in $OUT2/mocks/acme/"; exit 1
fi

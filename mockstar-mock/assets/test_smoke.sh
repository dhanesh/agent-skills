#!/bin/sh
# mockstar-mock/assets/test_smoke.sh — exercises route-checking without booting mockstar.
# Starts a tiny local HTTP stub, points smoke.sh at it via MOCKSTAR_SMOKE_BASE_URL.
# Also exercises the docker runtime path when docker is available.
# gate: integration — excluded from `make gate`; run with `make test-integration`.
# Needs the real mockstar CLI (network via bunx) and, for the docker branch, a
# reachable daemon and image. Not offline, so it cannot be a gate check.
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

# 3) docker runtime assertion — guarded by docker availability
DOCKER_AVAILABLE=0
if docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
  DOCKER_AVAILABLE=1
fi

FIXTURE="$DIR/fixtures/petstore-mini.yaml"
DOCKER_MOCKS=""
DOCKER_ROUTES=""
cleanup_docker_tmp() {
  [ -z "$DOCKER_MOCKS" ] || rm -rf "$DOCKER_MOCKS"
  [ -z "$DOCKER_ROUTES" ] || rm -f "$DOCKER_ROUTES"
}

if [ "$DOCKER_AVAILABLE" -eq 1 ]; then
  # Verify image is reachable (may already be pulled)
  IMAGE="${MOCKSTAR_SMOKE_IMAGE:-ghcr.io/dhanesh/mockstar:latest}"
  if ! docker image inspect "$IMAGE" >/dev/null 2>&1 && ! docker pull "$IMAGE" >/dev/null 2>&1; then
    echo "SKIP: docker smoke (image unreachable)"
  else
    # Build a mocks dir from the petstore fixture using documented layout (<out>/mocks/<tenant>/)
    DOCKER_MOCKS="$(mktemp -d)"
    trap 'cleanup_docker_tmp' EXIT
    if ! bunx @dhaneshpurohit/mockstar import "$FIXTURE" "$DOCKER_MOCKS/mocks" --tenant=default >/dev/null 2>&1; then
      echo "SKIP: docker smoke (mockstar import failed)"
    else
      # Write a routes file: GET /pets -> 200
      DOCKER_ROUTES="$(mktemp)"
      printf 'GET\t/pets\t200\n' > "$DOCKER_ROUTES"

      # Run smoke.sh in docker mode; first arg is the mocks config-root (<out>/mocks)
      DOCKER_SMOKE_PORT="${MOCKSTAR_SMOKE_DOCKER_TEST_PORT:-3918}"
      if MOCKSTAR_SMOKE_RUNTIME=docker \
         MOCKSTAR_SMOKE_PORT="$DOCKER_SMOKE_PORT" \
         MOCKSTAR_SMOKE_IMAGE="$IMAGE" \
         sh "$SMOKE" "$DOCKER_MOCKS/mocks" "$DOCKER_ROUTES"; then
        echo "PASS: docker smoke GET /pets -> 200"
      else
        echo "FAIL: docker smoke did not return expected 200"; exit 1
      fi
    fi
  fi
else
  echo "SKIP: docker smoke (no docker)"
fi

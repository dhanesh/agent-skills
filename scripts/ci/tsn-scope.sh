#!/bin/sh
# Decide which test-safety-net `versions` legs CI runs (owner decision
# 2026-10-08: "filter + edges, full weekly").
#
#   tsn-scope.sh <event_name> [<base_sha> [<head>]]   (head defaults to HEAD)
#
# - pull_request: "edges" when the diff BASE...HEAD touches a path that can
#   change what the version legs prove (PATHS below); else "none".
# - push, schedule, workflow_dispatch: "full".
# - Any other event, or a diff that cannot be read: "full". A scope that
#   cannot be decided runs every leg; it never skips them.
#
# Edges is the oldest and the newest pinned version of each stack, plus the
# current stable rust (the toolchain a runner image ships by default). Full is
# every pinned version, plus the current stable rust.
#
# Writes `mode=` and `matrix=` (JSON for fromJSON) to $GITHUB_OUTPUT, or to
# stdout when GITHUB_OUTPUT is unset (local dry runs).
set -u

event=${1:-}
base=${2:-}
head=${3:-HEAD}

leg() { printf '{"stack":"%s","version":"%s"}' "$1" "$2"; }

edges() {
  printf '%s,' "$(leg python 3.10)" "$(leg python 3.14)" \
    "$(leg node 18)" "$(leg node 26)" \
    "$(leg go 1.22)" "$(leg go 1.26)" \
    "$(leg rust 1.82)" "$(leg rust 1.98)"
  leg rust 1.99
}

full() {
  for v in 3.10 3.11 3.12 3.13 3.14; do printf '%s,' "$(leg python "$v")"; done
  for v in 18 20 22 24 26; do printf '%s,' "$(leg node "$v")"; done
  for v in 1.22 1.23 1.24 1.25 1.26; do printf '%s,' "$(leg go "$v")"; done
  for v in 1.82 1.86 1.90 1.94 1.98; do printf '%s,' "$(leg rust "$v")"; done
  leg rust 1.99
}

# A path that can change what a version leg proves. contract_check.py is the
# vendored skill-contract checker: the `contract` job and `make gate` cover it.
touches_guard() {
  case "$1" in
    test-safety-net/assets/contract_check.py) return 1 ;;
    test-safety-net/assets/*|test-safety-net/eval/*) return 0 ;;
    test-safety-net/SKILL.md|test-safety-net/references/stacks.md) return 0 ;;
    .github/workflows/skill-gates.yml|scripts/ci/tsn-scope.sh) return 0 ;;
  esac
  return 1
}

mode=full
case "$event" in
  pull_request)
    if [ -n "$base" ] && files=$(git diff --name-only "$base...$head"); then
      mode=none
      # One path per line; no glob expansion of the names.
      set -f
      IFS='
'
      for f in $files; do
        if touches_guard "$f"; then mode=edges; break; fi
      done
    else
      echo "tsn-scope: the PR diff could not be read; running every leg" >&2
    fi
    ;;
  push|schedule|workflow_dispatch) mode=full ;;
  *) echo "tsn-scope: unknown event '$event'; running every leg" >&2 ;;
esac

case "$mode" in
  edges) legs=$(edges) ;;
  full) legs=$(full) ;;
  # fromJSON still parses the matrix of a skipped job: keep it valid.
  none) legs=$(leg none none) ;;
esac
matrix="{\"include\":[$legs]}"

out=${GITHUB_OUTPUT:-/dev/stdout}
{
  echo "mode=$mode"
  echo "matrix=$matrix"
} >> "$out"
echo "tsn-scope: $event -> $mode" >&2

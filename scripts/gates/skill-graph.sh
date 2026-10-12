#!/bin/sh
# skill-graph.sh [repo-root]
#
# Repo-level gate: the "who calls whom" diagrams in README.md and in every
# <skill>/README.md are generated from docs/skill-graph/graph.json, and must not
# drift from it or from the skills. It fails when a README block differs from
# the generator's output; when an edge's evidence file:line no longer names the
# other skill; when a skill dir is missing from the graph or a graph node is not
# a skill; or when the contract edges no longer match the skills' own
# ```json skill-contract provides/consumes blocks. The logic is in
# skill_graph.py (stdlib). Fix a stale block with `make skill-graph`.
#
# Prints PASS:/FAIL: lines and a final SKILL_GRAPH_RESULT: PASS | FAIL (n) line;
# exits non-zero on failure.
set -u

root="${1:-.}"
exec python3 -I "$(dirname "$0")/skill_graph.py" --check --root "$root"

#!/bin/sh
# bcp14-registry.sh [repo-root]
#
# Repo-level gate: every capitalised BCP 14 keyword in a */SKILL.md has a row in
# docs/rfc2119/2026-09-19-classification.md at the level the text uses, no SKILL.md
# prose uses a lowercase "must" or "shall", and every skill has a register section.
# PP-7 checks the declaration and the vocabulary; this checks the classification,
# which fell behind by dozens of sentences while nothing read it. Rows match on
# sentence text, never on line number. The logic is in bcp14_registry.py (stdlib).
#
# Prints FAIL: <skill> <reason>: <sentence> per problem and a final
# BCP14_RESULT: PASS | FAIL (n) line; exits non-zero on failure.
set -u

root="${1:-.}"
exec python3 -I "$(dirname "$0")/bcp14_registry.py" --root "$root"

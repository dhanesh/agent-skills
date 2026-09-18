#!/bin/sh
# skill-contract.sh <skill-dir>
#
# Commandments 1-2 of docs/skill-contract/SPEC.md, run on EVERY skill through
# the reference checker:
#   - every skill: the frontmatter's top-level keys stay inside the Agent Skills
#     allowed set, and the `skill-contract` opt-in and the `## Contract` block are
#     both present or both absent (a half-adoption fails);
#   - adopters: the Contract block is well formed, and assets/contract_check.py is
#     byte-identical to the reference, so vendored copies cannot drift (the zero-
#     dependency design vendors the checker; this is what keeps the copies honest).
#
# Prints the checker's lines, then SKILL_CONTRACT_RESULT: PASS|FAIL.
set -u

d="${1:?usage: skill-contract.sh <skill-dir>}"
here="$(cd "$(dirname "$0")" && pwd)"
ref="$here/../../docs/skill-contract/reference/contract_check.py"

if [ ! -f "$ref" ]; then
    printf 'SKILL_CONTRACT_RESULT: FAIL — reference checker %s missing\n' "$ref"
    exit 1
fi

out="$(python3 -I "$ref" check-skill "$d" 2>&1)"; st=$?
printf '%s\n' "$out" | grep -v '^CONTRACT_RESULT:'
fail=0
[ "$st" -eq 0 ] || fail=1

if printf '%s\n' "$out" | grep -q '^ADOPTER: yes'; then
    if cmp -s "$ref" "$d/assets/contract_check.py"; then
        printf 'PASS: vendored assets/contract_check.py matches the reference\n'
    else
        printf 'FAIL: %s/assets/contract_check.py is missing or differs from the reference — run make contract-vendor\n' "$d"
        fail=1
    fi
fi

if [ "$fail" -eq 0 ]; then
    printf 'SKILL_CONTRACT_RESULT: PASS\n'
else
    printf 'SKILL_CONTRACT_RESULT: FAIL\n'
    exit 1
fi

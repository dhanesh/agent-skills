#!/bin/sh
# frontmatter-standard.sh <skill-dir>
#
# Repo-local gate: every SKILL.md frontmatter carries the standard metadata
# fields beyond name/description (which validate-skill.sh already enforces):
#   license:          e.g. MIT
#   compatibility:    runtime requirements one-liner
#   author:, version:, tags:   (inside a metadata: block, per house style)
#
# Prints PASS:/FAIL: per field and a final FRONTMATTER_RESULT: PASS|FAIL line.
set -u

d="${1:?usage: frontmatter-standard.sh <skill-dir>}"
f="$d/SKILL.md"

if [ ! -f "$f" ]; then
    printf 'FRONTMATTER_RESULT: FAIL — %s missing\n' "$f"
    exit 1
fi

fm=$(awk '/^---[[:space:]]*$/{c++; next} c==1' "$f")
fail=0

for key in license compatibility author version tags; do
    if printf '%s\n' "$fm" | grep -qE "^[[:space:]]*$key:[[:space:]]*[^[:space:]]"; then
        printf 'PASS: frontmatter has %s\n' "$key"
    else
        printf 'FAIL: frontmatter missing %s\n' "$key"
        fail=1
    fi
done

if [ "$fail" -eq 0 ]; then
    printf 'FRONTMATTER_RESULT: PASS\n'
else
    printf 'FRONTMATTER_RESULT: FAIL\n'
    exit 1
fi

#!/bin/sh
# asset-paths.sh <skill-dir>
#
# A skill's SKILL.md must never tell an agent to run a bundled helper by a
# SKILL-RELATIVE path. Agents work from the *target repo*, not from the skill
# directory, so `python3 assets/foo.py` is a bare "No such file or directory"
# for every one of them — and it is invisible to every other gate, because the
# file it names does exist, just not where the agent stands.
#
# The convention: resolve the skill's base directory once into a variable, then
# invoke helpers through it — `python3 "$SKILL_DIR/assets/foo.py"`. Two skills
# solved this well before it was a rule (mockstar-mock, base-in-reality); this
# check is what stops the next one from rediscovering the bug.
#
# Prints PASS:/FAIL: lines and ASSET_PATHS_RESULT: PASS|FAIL. Exits non-zero on
# any violation.
set -eu

SKILL_DIR="${1:?usage: asset-paths.sh <skill-dir>}"
SKILL_MD="$SKILL_DIR/SKILL.md"

if [ ! -f "$SKILL_MD" ]; then
    printf 'ASSET_PATHS_RESULT: FAIL — %s/SKILL.md not found\n' "$SKILL_DIR"
    exit 1
fi

# Body only: a frontmatter `compatibility:` line may legitimately name a helper.
BODY="$(awk '/^---$/ { c++; next } c >= 2 { print }' "$SKILL_MD")"

# An offending line runs an interpreter directly against assets/ or scripts/,
# with no variable, no absolute path and no <skill-dir> placeholder in front.
# `"$VAR/assets/x"`, `<skill-base-dir>/assets/x` and `/abs/assets/x` all pass.
violations="$(printf '%s\n' "$BODY" | awk '
{
    line = $0
    # Ignore anything already anchored to a variable or an absolute path.
    if (line ~ /\$[A-Za-z_{]/) next
    # A bare <skill-dir> placeholder is NOT an exemption: agent-ready-rails
    # used one that the skill never defines, which is the same dead end with
    # extra steps. One convention only — a shell variable, or an absolute path.
    if (line ~ /(^|[ "(`])\/[^ "`]*\/(assets|scripts)\//) next
    if (line ~ /(python3?|uv[ ]+run|bash|sh|node)[ ]+"?(\.\/)?(\.\.\/)?(assets|scripts)\//)
        { print NR ": " line; next }
    if (line ~ /<skill-?(base-?)?dir>\/(assets|scripts)\//)
        print NR ": " line "   [undefined <skill-dir> placeholder]"
}')"

if [ -n "$violations" ]; then
    printf '%s\n' "$violations" | while IFS= read -r v; do
        printf 'FAIL: skill-relative helper invocation — %s\n' "$v"
    done
    printf 'FAIL: resolve the skill base directory once (e.g. SKILL_DIR=...) and\n'
    printf '      invoke helpers as "$SKILL_DIR/assets/<file>"; see mockstar-mock/SKILL.md\n'
    printf 'ASSET_PATHS_RESULT: FAIL\n'
    exit 1
fi

printf 'PASS: asset paths: no skill-relative helper invocations\n'
printf 'ASSET_PATHS_RESULT: PASS\n'
exit 0

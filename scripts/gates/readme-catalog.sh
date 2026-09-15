#!/bin/sh
# readme-catalog.sh [repo-root]
#
# Repo-local gate: the root README.md is the catalog people install from, and
# every per-skill gate runs inside one skill's directory, so nothing used to
# read it. A new skill shipped without its install line (test-safety-net did,
# through four stacks of work) and nobody noticed. Checked in both directions:
#   - every top-level dir with a SKILL.md (the Makefile's own discovery) has
#       an install line:  npx skills add <repo> --skill <name>
#       a Skills-table row: | [`<name>`](<name>/) | ...
#   - every install line and table row names a dir that is a skill, so a
#     rename or removal cannot leave a stale entry behind.
# Not checked, because no script can judge it: list order, and whether a
# description still matches what the skill does.
#
# Prints PASS:/FAIL: per check and a final README_CATALOG_RESULT: PASS|FAIL line.
set -u

root="${1:-.}"
readme="$root/README.md"

if [ ! -f "$readme" ]; then
    printf 'README_CATALOG_RESULT: FAIL — %s missing\n' "$readme"
    exit 1
fi

# `--skill <skill-name>` (the placeholder in the usage line) never matches: `<` is not in the class.
installed=$(grep -oE 'npx skills add [^ ]+ --skill [a-z0-9][a-z0-9-]*' "$readme" | sed 's/.* --skill //' | sort -u)
rows=$(grep -E '^\|[[:space:]]*\[`[a-z0-9-]+`\]\([a-z0-9-]+/\)' "$readme")
listed=$(printf '%s\n' "$rows" | sed -nE 's/^\|[[:space:]]*\[`([a-z0-9-]+)`\]\(.*/\1/p' | sort -u)

fail=0
is_skill() { [ -f "$root/$1/SKILL.md" ]; }

skills=""
for f in "$root"/*/SKILL.md; do
    [ -f "$f" ] || continue
    skills="$skills $(basename "$(dirname "$f")")"
done
if [ -z "$skills" ]; then
    printf 'README_CATALOG_RESULT: FAIL — no skills found under %s\n' "$root"
    exit 1
fi

for s in $skills; do
    if printf '%s\n' "$installed" | grep -qx "$s"; then
        printf 'PASS: %s has an install line\n' "$s"
    else
        printf 'FAIL: %s has no install line (add: npx skills add dhanesh/agent-skills --skill %s)\n' "$s" "$s"
        fail=1
    fi
    if printf '%s\n' "$listed" | grep -qx "$s"; then
        printf 'PASS: %s has a Skills-table row\n' "$s"
    else
        printf 'FAIL: %s has no Skills-table row (add: | [`%s`](%s/) | <description> |)\n' "$s" "$s" "$s"
        fail=1
    fi
done

for n in $installed; do
    is_skill "$n" || { printf 'FAIL: install line names %s, which is not a skill directory\n' "$n"; fail=1; }
done
for n in $listed; do
    is_skill "$n" || { printf 'FAIL: Skills-table row names %s, which is not a skill directory\n' "$n"; fail=1; }
done

# A row whose label and link disagree ([`a`](b/)) points readers at the wrong skill.
mismatched=$(printf '%s\n' "$rows" | sed -nE 's/^\|[[:space:]]*\[`([a-z0-9-]+)`\]\(([a-z0-9-]+)\/\).*/\1 \2/p' | awk '$1 != $2')
if [ -n "$mismatched" ]; then
    printf '%s\n' "$mismatched" | while read -r label link; do
        printf 'FAIL: Skills-table row labelled %s links to %s/\n' "$label" "$link"
    done
    fail=1
fi

if [ "$fail" -eq 0 ]; then
    printf 'README_CATALOG_RESULT: PASS\n'
else
    printf 'README_CATALOG_RESULT: FAIL\n'
    exit 1
fi

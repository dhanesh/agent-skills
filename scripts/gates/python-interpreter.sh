#!/bin/sh
# python-interpreter.sh <skill-dir>
#
# A skill whose SKILL.md body or references/*.md tells the agent to run Python
# (a line with `python3` or `python` as the command) must carry the shared
# "Python interpreter" rule, so every skill finds Python the same way and none
# of them runs the macOS install stub. The rule text lives in
# python-interpreter-rule.txt (design spec section 4). The comparison ignores
# whitespace, so re-wrapping the paragraph is fine and editing a word is not.
#
# A frontmatter `compatibility:` line never counts. Neither does a `{python}`
# placeholder, or a path such as /usr/bin/python3 in a printed output line:
# the command must start the word.
#
# Prints PASS:/FAIL: lines and PYTHON_INTERPRETER_RESULT: PASS|FAIL. Exits
# non-zero on any violation.
set -eu

SKILL_DIR="${1:?usage: python-interpreter.sh <skill-dir>}"
SKILL_MD="$SKILL_DIR/SKILL.md"
RULE_FILE="$(cd "$(dirname "$0")" && pwd)/python-interpreter-rule.txt"

if [ ! -f "$SKILL_MD" ]; then
    printf 'PYTHON_INTERPRETER_RESULT: FAIL — %s/SKILL.md not found\n' "$SKILL_DIR"
    exit 1
fi
if [ ! -f "$RULE_FILE" ]; then
    printf 'PYTHON_INTERPRETER_RESULT: FAIL — rule file %s not found\n' "$RULE_FILE"
    exit 1
fi

squash() { tr -s '[:space:]' ' ' | sed 's/^ //; s/ $//'; }

# The file:line: prefix ends in ": ", so ": " stands for the start of the line.
RUNS='(: |[[:space:]`(])python3?[[:space:]]+(-[A-Za-z]|"|\$|<|[A-Za-z0-9_./-]+\.py)'

# file:line: text for every line that runs Python. SKILL.md counts only after
# the frontmatter; every references/*.md counts whole.
triggers="$(
    awk -v f="$SKILL_MD" '
        { n++ } /^---$/ && !done { c++; if (c == 2) done = 1; next }
        done { print f ":" n ": " $0 }' "$SKILL_MD" | grep -E "$RUNS" || true
    for r in "$SKILL_DIR"/references/*.md; do
        [ -f "$r" ] || continue
        awk -v f="$r" '{ print f ":" NR ": " $0 }' "$r" | grep -E "$RUNS" || true
    done
)"

if [ -z "$triggers" ]; then
    printf 'PASS: python interpreter: no command in the body or references runs Python\n'
    printf 'PYTHON_INTERPRETER_RESULT: PASS\n'
    exit 0
fi

body="$(awk '/^---$/ { c++; next } c >= 2 { print }' "$SKILL_MD" | squash)"
rule="$(squash < "$RULE_FILE")"

case "$body" in
    *"$rule"*)
        printf 'PASS: python interpreter: SKILL.md carries the shared rule\n'
        printf 'PYTHON_INTERPRETER_RESULT: PASS\n'
        exit 0 ;;
esac

printf '%s\n' "$triggers" | head -5 | while IFS= read -r t; do
    printf 'FAIL: runs Python but SKILL.md lacks the shared rule — %s\n' "$t"
done
printf 'FAIL: add the paragraph in scripts/gates/python-interpreter-rule.txt to SKILL.md, before the first command\n'
printf 'PYTHON_INTERPRETER_RESULT: FAIL\n'
exit 1
